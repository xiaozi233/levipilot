"""Execute chat commands without stealing the foreground window.

Minecraft ignores synthesized *mouse* input (raw input path), but text entry is
delivered through ``WM_CHAR``, which can be posted straight to the game window.
The window has to exist; it does not have to be focused.  Because SDL derives
key codes from the scan code in ``lParam``, every posted key carries a real
scan code from ``MapVirtualKeyW``.

This is a fallback/alternative to the CUA helper (``native_cua.py``): the helper
brings the game forward and drives the real keyboard, this one only posts
messages.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_T = 0x54
VK_OEM_2 = 0xBF  # '/'
VK_CONTROL = 0x11
VK_A = 0x41

NAMED_KEYS = {
    "enter": VK_RETURN,
    "tab": VK_TAB,
    "escape": VK_ESCAPE,
    "space": VK_SPACE,
    "back": VK_BACK,
    "left": VK_LEFT,
    "right": VK_RIGHT,
    "up": VK_UP,
    "down": VK_DOWN,
}

MAPVK_VK_TO_VSC = 0

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_window(title: str = "Minecraft", pid: int | None = None) -> int:
    """Return a top-level window handle, by title, or by owning process."""
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return int(hwnd)
    if pid is None:
        return 0
    found: list[int] = []

    def callback(hwnd_, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd_, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindow(hwnd_):
            found.append(int(hwnd_))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found[0] if found else 0


class PostMessageChannel:
    """Post keystrokes/characters to the game window's message queue."""

    def __init__(
        self,
        window_title: str = "Minecraft",
        *,
        pid: int | None = None,
        open_with: str = "slash_key",
        char_delay: float = 0.012,
        settle: float = 0.25,
        poster: Callable[[int, int, int], object] | None = None,
    ) -> None:
        self.window_title = window_title
        self.pid = pid
        self.open_with = open_with
        self.char_delay = char_delay
        self.settle = settle
        self.poster = poster
        self._hwnd = 0

    # -- window ---------------------------------------------------------------
    def hwnd(self) -> int:
        if self._hwnd and user32.IsWindow(self._hwnd):
            return self._hwnd
        self._hwnd = find_window(self.window_title, self.pid)
        return self._hwnd

    def available(self) -> tuple[bool, str]:
        if self.poster is not None:
            return True, ""
        hwnd = self.hwnd()
        if not hwnd:
            return False, f"window not found: {self.window_title!r}"
        return True, ""

    def describe(self) -> dict:
        hwnd = self.hwnd()
        if not hwnd:
            return {"ok": False, "error": "window not found"}
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        return {
            "ok": True,
            "hwnd": hex(hwnd),
            "pid": owner.value,
            "foreground": int(user32.GetForegroundWindow()) == hwnd,
            "open_with": self.open_with,
        }

    # -- low level ------------------------------------------------------------
    def _post(self, message: int, wparam: int, lparam: int = 0) -> bool:
        if self.poster is not None:
            return bool(self.poster(message, wparam, lparam))
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("window not found")
        return bool(user32.PostMessageW(hwnd, message, wparam, lparam))

    @staticmethod
    def _lparam(vk: int) -> int:
        scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC) & 0xFF
        return 1 | (scan << 16)

    def post_key(self, vk: int) -> None:
        self._post(WM_KEYDOWN, vk, self._lparam(vk))
        self._post(WM_KEYUP, vk, self._lparam(vk) | (1 << 30) | (1 << 31))

    def post_char(self, char: str) -> None:
        code = ord(char)
        self._post(WM_CHAR, code, self._lparam(code) if code < 0x100 else 1)

    def type_text(self, text: str) -> int:
        for char in text:
            self.post_char(char)
            time.sleep(self.char_delay)
        return len(text)

    def clear_input(self, count: int = 48) -> None:
        """Backspace spam: harmless when no text field is open."""
        for _ in range(count):
            self._post(WM_CHAR, VK_BACK, self._lparam(VK_BACK))
            time.sleep(0.002)
        time.sleep(0.05)

    def press_named(self, name: str) -> int:
        """Press one named key (see NAMED_KEYS); used for UI navigation."""
        key = name.strip().lower()
        if key not in NAMED_KEYS:
            raise ValueError(f"unknown key {name!r}; known: {', '.join(sorted(NAMED_KEYS))}")
        vk = NAMED_KEYS[key]
        self._post(WM_KEYDOWN, vk, self._lparam(vk))
        time.sleep(0.05)
        self._post(WM_KEYUP, vk, self._lparam(vk) | (1 << 30) | (1 << 31))
        time.sleep(0.12)
        return vk

    def press_enter(self) -> None:
        """Submit the chat box.

        Only the key pair is posted: an extra ``WM_CHAR(0x0D)`` would be a second
        Enter, and in Bedrock Enter is also the "open chat" binding — a stray one
        after the box closed re-opens it and freezes the player (observed as the
        player standing still while the world keeps ticking at 20 Hz).
        """
        self._post(WM_KEYDOWN, VK_RETURN, self._lparam(VK_RETURN))
        self._post(WM_KEYUP, VK_RETURN, self._lparam(VK_RETURN) | (1 << 30) | (1 << 31))

    # -- command --------------------------------------------------------------
    def open_chat(self) -> str:
        """Open the chat screen. Returns the prefix that is already in the box."""
        mode = self.open_with
        if mode == "slash_key":
            self.post_key(VK_OEM_2)
            return "/"
        if mode == "slash_char":
            self.post_char("/")
            return "/"
        if mode == "t_key":
            self.post_key(VK_T)
            return ""
        if mode == "t_char":
            self.post_char("t")
            return ""
        raise ValueError(f"unknown open_with mode: {mode!r}")

    def send_command(self, command: str, *, open_chat: bool = True) -> dict:
        """Send one chat command. Returns a dict so it can be used as a
        ``command_sender`` for the scenario runner."""
        text = command.strip()
        if not text:
            raise RuntimeError("empty command")
        if not text.startswith("/"):
            text = "/" + text
        self.clear_input()
        prefix = self.open_chat() if open_chat else ""
        time.sleep(self.settle)
        body = text[len(prefix) :] if prefix and text.startswith(prefix) else text
        typed = self.type_text(body)
        time.sleep(0.08)
        self.press_enter()
        time.sleep(self.settle)
        return {"ok": True, "via": "postmessage", "chars": typed, "prefix": prefix}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", help="要执行的命令（含或不含前导 /）")
    parser.add_argument("--title", default="Minecraft", help="游戏窗口标题")
    parser.add_argument("--pid", type=int, default=None, help="按进程号找窗口")
    parser.add_argument(
        "--open-with",
        default="slash_key",
        choices=["slash_key", "slash_char", "t_key", "t_char"],
        help="打开聊天框的方式",
    )
    parser.add_argument("--info", action="store_true", help="只打印窗口信息")
    parser.add_argument("--no-open", action="store_true", help="假设聊天框已经打开")
    parser.add_argument(
        "--key",
        action="append",
        metavar="NAME",
        help=f"按下具名按键（可重复）：{'/'.join(sorted(NAMED_KEYS))}",
    )
    args = parser.parse_args(argv)

    channel = PostMessageChannel(args.title, pid=args.pid, open_with=args.open_with)
    info = channel.describe()
    print(f"window: {info}")
    if not info.get("ok"):
        return 2
    if args.key:
        for name in args.key:
            try:
                vk = channel.press_named(name)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"key   : {name} (vk=0x{vk:02X})")
        return 0
    if args.info or not args.command:
        return 0
    print(f"send  : {args.command!r} (open_with={args.open_with})")
    print(f"result: {channel.send_command(args.command, open_chat=not args.no_open)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
