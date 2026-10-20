"""Scenario-DSL adapter for the in-game probe mod.

``driver.runner`` only needs a duck-typed agent (``call`` + ``wait_ticks``).
This module implements that interface on top of the named pipe transport, so
the existing scenarios (``scenarios/*.json``) and the JSONL/summary writer can
run against the LeviLamina mod instead of the retired frida agent.

Example::

    from driver.mod_agent import ModAgent
    from driver.runner import run_scenario
    from driver.scenario import load

    with ModAgent() as agent:
        result = run_scenario(
            agent,
            load("scenarios/example.json"),
            command_sender=agent.command,
            block_reader=agent.block_at,
        )
"""

from __future__ import annotations

import math
import time
from typing import Any

from driver.transport_mod import Harness, ModPipeError

MOVE_FLAGS = {"none", "sneak"}


class AgentError(RuntimeError):
    pass


def _tick_row(message: dict[str, Any]) -> dict[str, Any]:
    """Convert a mod ``tick`` event into the sample shape used by output.py."""
    w = bool(message.get("w"))
    a = bool(message.get("a"))
    s = bool(message.get("s"))
    d = bool(message.get("d"))
    sprint = bool(message.get("sprint"))
    sneak = bool(message.get("sneak"))
    jump = bool(message.get("jump"))
    return {
        "t": int(message.get("tick", 0)),
        "ok": True,
        "pos": [message.get("x"), message.get("y"), message.get("z")],
        "vel": [message.get("vx"), message.get("vy"), message.get("vz")],
        "on_ground": bool(message.get("on_ground")),
        "yaw": message.get("yaw"),
        "pitch": message.get("pitch"),
        "health": message.get("health"),
        "flags_lo": (
            (1 if w else 0)
            | (2 if a else 0)
            | (4 if s else 0)
            | (8 if d else 0)
            | (16 if sprint else 0)
            | (32 if sneak else 0)
            | (64 if jump else 0)
        ),
        "flags_mid": 0,
        "flags_hi": 0,
        "input": {
            "w": w,
            "a": a,
            "s": s,
            "d": d,
            "sprint": sprint,
            "sneak": sneak,
            "jump": jump,
            "forward": (1.0 if w else 0.0) - (1.0 if s else 0.0),
            "strafe": (1.0 if d else 0.0) - (1.0 if a else 0.0),
        },
    }


class ModAgent:
    """Agent-like wrapper around :class:`driver.transport_mod.Harness`."""

    def __init__(self, pipe_name: str | None = None) -> None:
        self.harness = Harness() if pipe_name is None else Harness(pipe_name)
        self.client = self.harness.client
        self._samples: list[dict[str, Any]] = []
        self._recording = False
        self._move_flags = "none"
        self._raw_readline = self.client.readline
        self.client.readline = self._readline  # type: ignore[method-assign]

    # -- transport plumbing -------------------------------------------------
    def _readline(self) -> dict[str, Any]:
        message = self._raw_readline()
        if message.get("event") == "tick" and self._recording:
            self._samples.append(_tick_row(message))
        return message

    def close(self) -> None:
        self.harness.close()

    def __enter__(self) -> "ModAgent":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- agent interface ----------------------------------------------------
    def get_state(self) -> dict[str, Any]:
        state = self.client.state()
        if not state.get("in_world"):
            return {"ok": False, "error": "not in world", "in_world": False}
        state = dict(state)
        state["ok"] = True
        # feet coordinates: the same convention as /tp and the tp DSL step
        state["pos"] = [state.get("feet_x"), state.get("feet_y"), state.get("feet_z")]
        state["vel"] = [state.get("vx"), state.get("vy"), state.get("vz")]
        return state

    def get_tick_count(self) -> int:
        return int(self.client.status().get("tick", 0))

    def wait_ticks(self, ticks: int, *, timeout: float | None = None) -> int:
        if ticks <= 0:
            return 0
        start = self.get_tick_count()
        # 20 ticks per second; allow 3x plus a fixed margin for server hitches.
        limit = timeout if timeout is not None else max(3.0, ticks / 20.0 * 3.0 + 2.0)
        deadline = time.monotonic() + limit
        while True:
            current = self.get_tick_count()
            if current - start >= ticks:
                return current - start
            if time.monotonic() > deadline:
                raise AgentError(
                    f"wait_ticks({ticks}) 超时：tick 只前进了 {current - start}"
                )
            time.sleep(0.01)

    def start_recording(self, max_ticks: int = 0) -> dict[str, Any]:
        self._samples.clear()
        self._recording = True
        try:
            self.client.start(max_ticks)
        except Exception:
            self._recording = False
            raise
        return {"ok": True, "max_ticks": max_ticks}

    def stop_recording(self) -> dict[str, Any]:
        self._recording = False
        try:
            self.client.request({"cmd": "stop"}, "stopped")
        except ModPipeError:
            pass
        samples = list(self._samples)
        self._samples.clear()
        return {
            "ok": True,
            "samples": samples,
            "ticks": samples,
            "count": len(samples),
            "dropped": 0,
            "start_tick": samples[0]["t"] if samples else None,
            "end_tick": samples[-1]["t"] if samples else None,
        }

    def command(self, command: str) -> dict[str, Any]:
        reply = self.client.command(command)
        # Block-editing commands are deferred by the command system: the reply
        # arrives before execution and reports success=false even when the
        # command works. Treat submission as success and let setup.verify /
        # block reads decide whether the effect actually happened.
        success = bool(reply.get("success"))
        return {
            "ok": True,
            "success": success,
            "reported_success": success,
            "command": command,
            "code": reply.get("code"),
            "error": None,
        }

    def block_at(self, x: float, y: float, z: float) -> dict[str, Any]:
        reply = self.client.blocks([(x, y, z)])
        blocks = reply.get("blocks") or []
        if not blocks:
            return {"ok": False, "error": "no block returned"}
        name = str(blocks[0].get("name") or "")
        air = name in ("minecraft:air", "air", "")
        return {
            "ok": True,
            "name": "minecraft:air" if air else name,
            "air": air,
        }

    # -- DSL operations -----------------------------------------------------
    def tp(self, x: float, y: float, z: float, mode: str = "server") -> dict[str, Any]:
        # The mod always teleports through the integrated server; client-side
        # writes are corrected back, so the other modes are accepted but unused.
        self.client.teleport(x, y, z)
        return {"ok": True, "via": "server", "pos": [x, y, z], "requested_mode": mode}

    def set_yaw_pitch(self, yaw: float, pitch: float) -> dict[str, Any]:
        self.client.look(yaw, pitch, relative=False, ticks=1)
        return {"ok": True, "yaw": yaw, "pitch": pitch}

    def clear_look(self) -> dict[str, Any]:
        return {"ok": True}

    def move_flags(self, mode: str) -> dict[str, Any]:
        if mode not in MOVE_FLAGS:
            raise AgentError(f"unsupported move mode: {mode}")
        self._move_flags = mode
        return {"ok": True, "mode": mode}

    def _local_vector(self, world_x: float, world_z: float) -> tuple[float, float]:
        state = self.get_state()
        if not state.get("ok"):
            raise AgentError("无法读取朝向")
        yaw = math.radians(float(state.get("yaw") or 0.0))
        # Bedrock yaw 0 faces +Z, 90 faces -X.
        forward = -world_x * math.sin(yaw) + world_z * math.cos(yaw)
        strafe = -world_x * math.cos(yaw) - world_z * math.sin(yaw)
        return forward, strafe

    def move_world(self, world_x: float, world_z: float) -> dict[str, Any]:
        forward, strafe = self._local_vector(world_x, world_z)
        sneak = self._move_flags == "sneak"
        return self._apply_input(forward, strafe, jump=False, sneak=sneak, sprint=False)

    def set_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._apply_input(
            float(payload.get("forward", 0.0)),
            float(payload.get("strafe", 0.0)),
            jump=bool(payload.get("jump", False)),
            sneak=bool(payload.get("sneak", False)),
            sprint=bool(payload.get("sprint", False)),
        )

    def _apply_input(
        self,
        forward: float,
        strafe: float,
        *,
        jump: bool,
        sneak: bool,
        sprint: bool,
    ) -> dict[str, Any]:
        # The scenario runner pre-scales sneak input to 0.3 because the old
        # frida agent wrote the input vector directly. The game itself applies
        # the sneak multiplier based on the sneaking flag, so undo that scale
        # here to avoid a 0.09x walk.
        if sneak and self._move_flags == "sneak":
            forward /= 0.3
            strafe /= 0.3
        self.client.set_input(
            forward,
            strafe,
            sprint=sprint,
            sneak=sneak,
            jump=jump,
            ticks=-1,
        )
        return {
            "ok": True,
            "forward": forward,
            "strafe": strafe,
            "jump": jump,
            "sneak": sneak,
            "sprint": sprint,
        }

    def jump(self) -> dict[str, Any]:
        self.client.jump()
        return {"ok": True}

    def stop_input(self) -> dict[str, Any]:
        self.client.stop_move()
        self._move_flags = "none"
        return {"ok": True}

    def set_vel(self, x: float, y: float, z: float) -> dict[str, Any]:
        # No direct velocity write exists in the mod; scenarios do not rely on
        # it (velocity is measured, not imposed).
        return {
            "ok": True,
            "skipped": True,
            "warning": "set_vel 未实现：mod agent 不直接写速度",
            "v": [x, y, z],
        }

    # -- generic dispatch ---------------------------------------------------
    def call(self, name: str, *args: Any) -> Any:
        method = getattr(self, name, None)
        if method is None:
            raise AgentError(f"unknown agent call: {name}")
        return method(*args)