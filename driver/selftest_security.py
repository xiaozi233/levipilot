"""Live security checks: named-pipe DACL, pipe_token auth, and read/togglefly switches."""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import secrets
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

from driver.harness import ModClient
from driver.harness.errors import ModPipeError, ModProtocolError, ModTimeout
from driver.selftest_lifecycle import (
    RELOAD_COMMAND,
    _capture_command_template,
    _crash_snapshot,
    _hooks_match,
    _lifecycle,
    _reconnect,
    _sha256,
    _wait_lifecycle,
)
from driver.selftest_runtime import wait_ticks
from driver.selftest_support import Checks, preserve_failure


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", wintypes.BYTE),
        ("AceFlags", wintypes.BYTE),
        ("AceSize", wintypes.WORD),
    ]


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("Header", _AceHeader),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


def inspect_pipe_dacl(pipe_name: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Inspect the live named pipe DACL and confirm it only allows the current user SID."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    se_kernel_object = 6
    dacl_security_information = 0x00000004
    acl_size_information_class = 2
    token_query = 0x0008
    token_user_class = 1
    generic_read = 0x80000000
    generic_write = 0x40000000
    read_control = 0x00020000
    open_existing = 3
    invalid_handle = wintypes.HANDLE(-1).value

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.LPVOID),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.EqualSid.argtypes = [wintypes.LPVOID, wintypes.LPVOID]
    advapi32.EqualSid.restype = wintypes.BOOL

    error_file_not_found = 2
    error_pipe_busy = 231
    handle = invalid_handle
    last_err = 0
    deadline = time.monotonic() + timeout
    while True:
        handle = kernel32.CreateFileW(
            pipe_name,
            generic_read | generic_write | read_control,
            0,
            None,
            open_existing,
            0,
            None,
        )
        if handle != invalid_handle:
            break
        last_err = int(ctypes.get_last_error())
        if last_err not in (error_file_not_found, error_pipe_busy) or time.monotonic() >= deadline:
            return {"ok": False, "error": f"CreateFileW failed: {last_err}"}
        time.sleep(0.05)

    sd = wintypes.LPVOID()
    dacl = wintypes.LPVOID()
    token = wintypes.HANDLE()
    try:
        err = advapi32.GetSecurityInfo(
            handle,
            se_kernel_object,
            dacl_security_information,
            None,
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(sd),
        )
        if err != 0 or not dacl.value:
            return {"ok": False, "error": f"GetSecurityInfo err={err} dacl={dacl.value}"}

        info = _AclSizeInformation()
        if not advapi32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), acl_size_information_class
        ):
            return {"ok": False, "error": "GetAclInformation failed"}

        ace_ptr = wintypes.LPVOID()
        if info.AceCount < 1 or not advapi32.GetAce(dacl, 0, ctypes.byref(ace_ptr)):
            return {"ok": False, "error": f"GetAce failed ace_count={info.AceCount}"}

        if not ace_ptr.value:
            return {"ok": False, "error": "null ACE pointer"}
        ace_sid = int(ace_ptr.value) + int(_AccessAllowedAce.SidStart.offset)

        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), token_query, ctypes.byref(token)
        ):
            return {"ok": False, "error": "OpenProcessToken failed"}

        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, token_user_class, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token, token_user_class, buf, needed.value, ctypes.byref(needed)
        ):
            return {"ok": False, "error": "GetTokenInformation failed"}

        user_sid = ctypes.c_void_p.from_buffer(buf).value
        if not user_sid:
            return {"ok": False, "error": "null TokenUser SID"}
        sid_match = bool(advapi32.EqualSid(ace_sid, user_sid))
        return {
            "ok": int(info.AceCount) == 1 and sid_match,
            "ace_count": int(info.AceCount),
            "sid_match": sid_match,
        }
    finally:
        if token.value:
            kernel32.CloseHandle(token)
        if sd.value:
            kernel32.LocalFree(sd)
        kernel32.CloseHandle(handle)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def _restore_config(path: Path, existed: bool, orig_bytes: bytes | None) -> None:
    if existed and orig_bytes is not None:
        _write_bytes_atomic(path, orig_bytes)
    else:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _reload_mod(
    client: ModClient,
    directory: Path,
    *,
    next_token: str | None,
    stage: str,
    before_write_config: bytes | None = None,
    cfg_path: Path | None = None,
    timeout: float = 15.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    initial_status = client.status()
    boot_offset = (directory / "harness-boot.log").stat().st_size
    before_lc = _lifecycle(initial_status)
    client.input_lock(True)
    template = _capture_command_template(client, timeout=10)
    if not (
        template["attempts_after"] > template["attempts_before"]
        and template["faults_after"] == template["faults_before"]
        and isinstance(template["template"], dict)
        and template["template"].get("ready") is True
    ):
        raise ModProtocolError(f"{stage}: command template unavailable: {template}")

    if before_write_config is not None and cfg_path is not None:
        _write_bytes_atomic(cfg_path, before_write_config)

    preparation = client.request({"cmd": "lifecycle_prepare", "on": False}, "lifecycle_prepare")
    if not (
        preparation.get("on") is False
        and preparation.get("ok") is True
        and preparation.get("quiescing") is True
        and _hooks_match(preparation, True)
    ):
        raise ModProtocolError(f"{stage}: lifecycle_prepare failed: {preparation}")

    try:
        submitted = client.chat_send("/" + RELOAD_COMMAND, wait=True, timeout=10)
    except (ModPipeError, ModTimeout):
        submitted = {"ok": True}
    if submitted.get("ok") is not True:
        raise ModProtocolError(f"{stage}: chat_send failed: {submitted}")

    client.token = next_token
    client.close()
    _reconnect(client, timeout)
    after_status, evidence = _wait_lifecycle(client, directory, before_lc, boot_offset, timeout)
    client.wait_ready(timeout=30, require_world=True)
    return after_status, evidence


def check_security(client: ModClient, checks: Checks) -> None:
    """Verify pipe DACL, pipe_token auth, allow_mem_read and allow_togglefly across reload."""
    pipe_name = client.pipe_name
    orig_token = client.token

    client.close()
    try:
        dacl_before = inspect_pipe_dacl(pipe_name)
        checks.check("security_pipe_dacl_before", dacl_before.get("ok") is True, str(dacl_before))
    finally:
        _reconnect(client, 10.0)
        client.wait_ready(timeout=20, require_world=True)

    initial_state = client.state()
    initial_status = client.status()
    initial_macro = client.macro_status()
    initial_lifecycle = _lifecycle(initial_status)
    initial_mayfly = initial_state.get("mayfly") is True

    if not checks.check(
        "security_preflight",
        initial_state.get("in_world") is True
        and initial_state.get("on_ground") is True
        and initial_state.get("flying") is not True
        and initial_macro.get("playing") is False
        and initial_macro.get("recording") is False
        and int(initial_lifecycle.get("generation", 0)) > 0
        and initial_lifecycle.get("running") is True,
        str({"macro": initial_macro, "lifecycle": initial_lifecycle}),
    ):
        return

    directory = Path(str(initial_status["spool"])).parent
    cfg_path = directory / "harness.json"
    orig_existed = cfg_path.exists()
    orig_bytes = cfg_path.read_bytes() if orig_existed else None
    orig_config: dict[str, Any] = {}
    if orig_bytes is not None:
        loaded = json.loads(orig_bytes.decode("utf-8"))
        if isinstance(loaded, dict):
            orig_config = loaded

    before_crashes = _crash_snapshot(directory)
    before_dll = _sha256(directory / "probe.dll")

    test_token = f"selftest-sec-{secrets.token_hex(8)}"
    restricted_config = {k: v for k, v in orig_config.items() if k != "auto_enter"}
    restricted_config["pipe_token"] = test_token
    restricted_config["allow_mem_read"] = False
    restricted_config["allow_togglefly"] = False
    restricted_bytes = (json.dumps(restricted_config, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )

    reloaded_restricted = False
    try:
        after_status, evidence = _reload_mod(
            client,
            directory,
            next_token=test_token,
            stage="restrict",
            before_write_config=restricted_bytes,
            cfg_path=cfg_path,
        )
        reloaded_restricted = True
        after_lc = _lifecycle(after_status)
        checks.check(
            "security_reload_restricted",
            int(after_lc.get("generation", 0)) > int(initial_lifecycle.get("generation", 0))
            and _hooks_match(after_lc, True)
            and not evidence.get("boot_truncated"),
            str(after_lc),
        )

        # Disconnect the owned session while testing DACL and unauthenticated/bad-token handshakes
        # because PipeChannel only serves one instance at a time.
        client.close()
        try:
            dacl_after = inspect_pipe_dacl(pipe_name)
            checks.check(
                "security_pipe_dacl_after_reload", dacl_after.get("ok") is True, str(dacl_after)
            )

            raw = ModClient(pipe_name=pipe_name, token="", auto_auth=False, timeout=10.0)
            try:
                raw.connect()
                checks.check(
                    "security_hello_auth_required",
                    raw.hello.get("auth_required") is True,
                    str(raw.hello),
                )
                pong = raw.ping()
                checks.check("security_unauth_ping_allowed", pong.get("event") == "pong", str(pong))
                unauth_state = raw.request({"cmd": "state"}, "error")
                checks.check(
                    "security_unauth_state_blocked",
                    unauth_state.get("reason") == "auth_required",
                    str(unauth_state),
                )
                bad_auth = raw.request({"cmd": "auth", "token": test_token + "-bad"}, "error")
                checks.check(
                    "security_bad_token_rejected",
                    bad_auth.get("reason") == "bad_token",
                    str(bad_auth),
                )
                still_blocked = raw.request({"cmd": "state"}, "error")
                checks.check(
                    "security_bad_token_stays_unauthed",
                    still_blocked.get("reason") == "auth_required",
                    str(still_blocked),
                )
                good_auth = raw.request({"cmd": "auth", "token": test_token}, "auth_result")
                checks.check(
                    "security_valid_token_auths",
                    good_auth.get("ok") is True,
                    str(good_auth),
                )
                post_auth_state = raw.state()
                checks.check(
                    "security_post_auth_state_ok",
                    post_auth_state.get("in_world") is True,
                    str(post_auth_state),
                )
            finally:
                raw.close()

            missing = ModClient(pipe_name=pipe_name, token="", connect_timeout=10.0, timeout=10.0)
            try:
                try:
                    missing.connect()
                    missing_err = ""
                except ModProtocolError as exc:
                    missing_err = str(exc)
                checks.check(
                    "security_client_missing_token_fails",
                    "auth_required" in missing_err,
                    missing_err or "connected without token",
                )
            finally:
                missing.close()

            wrong = ModClient(
                pipe_name=pipe_name, token=test_token + "-bad", connect_timeout=10.0, timeout=10.0
            )
            try:
                try:
                    wrong.connect()
                    wrong_err = ""
                except ModProtocolError as exc:
                    wrong_err = str(exc)
                checks.check(
                    "security_client_bad_token_fails",
                    "bad_token" in wrong_err,
                    wrong_err or "connected with wrong token",
                )
            finally:
                wrong.close()
        finally:
            client.token = test_token
            _reconnect(client, 10.0)
            client.wait_ready(timeout=20, require_world=True)

        restricted_status = client.status()
        checks.check(
            "security_status_flags",
            restricted_status.get("auth_required") is True
            and restricted_status.get("allow_mem_read") is False
            and restricted_status.get("allow_togglefly") is False,
            str(
                {
                    k: restricted_status.get(k)
                    for k in ("auth_required", "allow_mem_read", "allow_togglefly")
                }
            ),
        )

        mem_err = client.request({"cmd": "mem_read", "addr": "0x10", "len": 4}, "error")
        checks.check(
            "security_mem_read_disabled",
            mem_err.get("reason") == "read_disabled"
            and "allow_mem_read" in str(mem_err.get("hint")),
            str(mem_err),
        )

        probe_err = client.request(
            {"cmd": "probe", "addr": "0x10", "kind": "raw", "len": 4}, "error"
        )
        checks.check(
            "security_probe_disabled",
            probe_err.get("reason") == "read_disabled"
            and "allow_mem_read" in str(probe_err.get("hint")),
            str(probe_err),
        )

        mayfly_before = client.state().get("mayfly") is True
        toggle_reply = client.command("togglefly")
        wait_ticks(client, 5)
        mayfly_after = client.state().get("mayfly") is True
        checks.check(
            "security_togglefly_disabled",
            toggle_reply.get("success") is False and mayfly_after is mayfly_before,
            f"reply={toggle_reply} mayfly={mayfly_before}->{mayfly_after}",
        )
    finally:
        with preserve_failure(checks, "security"):
            _restore_config(cfg_path, orig_existed, orig_bytes)
            if reloaded_restricted:
                client.token = test_token
                if not client.connected:
                    _reconnect(client, 10.0)
                    client.wait_ready(timeout=20, require_world=True)
                restored_status, _ = _reload_mod(
                    client,
                    directory,
                    next_token=orig_token,
                    stage="restore",
                )
                checks.check(
                    "security_restored_config",
                    ((cfg_path.read_bytes() if cfg_path.exists() else None) == orig_bytes)
                    if orig_existed
                    else (not cfg_path.exists()),
                )
                expected_auth = bool(orig_config.get("pipe_token"))
                expected_read = bool(orig_config.get("allow_mem_read", True))
                expected_togglefly = bool(orig_config.get("allow_togglefly", True))
                checks.check(
                    "security_restored_auth",
                    restored_status.get("auth_required") is expected_auth,
                    str(restored_status.get("auth_required")),
                )
                if expected_read:
                    mem_restored = client.request(
                        {"cmd": "mem_read", "addr": "0x10", "len": 4}, "mem_read"
                    )
                    checks.check(
                        "security_restored_mem_read",
                        restored_status.get("allow_mem_read") is True
                        and mem_restored.get("ok") is False
                        and mem_restored.get("reason") == "bad_addr",
                        str(mem_restored),
                    )
                if expected_togglefly:
                    fly_reply = client.command("togglefly")
                    wait_ticks(client, 6)
                    flipped = client.state().get("mayfly") is True
                    if flipped is not initial_mayfly:
                        client.command("togglefly")
                        wait_ticks(client, 6)
                    restored_mayfly = client.state().get("mayfly") is True
                    checks.check(
                        "security_restored_togglefly",
                        restored_status.get("allow_togglefly") is True
                        and fly_reply.get("success") is True
                        and flipped is (not initial_mayfly)
                        and restored_mayfly is initial_mayfly,
                        f"reply={fly_reply} flipped={flipped} restored={restored_mayfly}",
                    )
                checks.check(
                    "security_restored_integrity",
                    _sha256(directory / "probe.dll") == before_dll
                    and _crash_snapshot(directory) == before_crashes,
                )
            client.token = orig_token
            if client.connected:
                with contextlib.suppress(ModPipeError):
                    client.input_lock(False)
