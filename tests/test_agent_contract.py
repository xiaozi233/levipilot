"""Static contract checks between the Python driver and the frida agent.

These catch the two easiest ways to break the harness without the game running:

1. a driver RPC name that has no matching camelCase JS export;
2. a hook signature whose VA/SIG tables drift apart.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AGENT = ROOT / "agent" / "agent.js"
DRIVER = ROOT / "driver"


def to_camel_case(name: str) -> str:
    """Same conversion frida-python applies to snake_case export names."""
    result = ""
    capitalize = False
    for ch in name:
        if ch == "_":
            capitalize = True
        elif capitalize:
            result += ch.upper()
            capitalize = False
        else:
            result += ch
    return result


class AgentContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = AGENT.read_text(encoding="utf-8")

    def test_js_exports_cover_driver_calls(self) -> None:
        export_block = re.search(r"rpc\.exports = \{(.*?)\n\};", self.source, re.S)
        self.assertIsNotNone(export_block)
        exports = set(re.findall(r"^\s{2}(\w+)\(", export_block.group(1), re.M))
        self.assertIn("getState", exports)
        self.assertIn("setInput", exports)

        called: set[str] = set()
        for path in DRIVER.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            called.update(re.findall(r"\.call\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"", text))

        missing = sorted(c for c in called if to_camel_case(c) not in exports)
        self.assertEqual(
            missing, [], f"driver 调用了不存在的 JS export: {missing}"
        )

    def test_va_and_sig_tables_align(self) -> None:
        va_block = re.search(r"const VA = \{(.*?)\};", self.source, re.S).group(1)
        sig_block = re.search(r"const SIG = \{(.*?)\};", self.source, re.S).group(1)

        va_names = set(re.findall(r"(\w+):\s*0x[0-9A-Fa-f]+", va_block))
        sigs = dict(
            re.findall(r"(\w+):\s*'([0-9A-Fa-f ]+)'", sig_block)
        )

        self.assertEqual(va_names, set(sigs))
        for name, pattern in sigs.items():
            length = len(pattern.split())
            self.assertEqual(length, 24, f"{name} 指纹应为 24 字节，实际 {length}")

    def test_every_hook_has_a_signature(self) -> None:
        sig_block = re.search(r"const SIG = \{(.*?)\};", self.source, re.S).group(1)
        sig_names = set(re.findall(r"(\w+):\s*'[0-9A-Fa-f ]+'", sig_block))

        hook_names = set(re.findall(r"installHook\(\s*'(\w+)'", self.source))
        self.assertTrue(
            hook_names <= sig_names,
            f"installHook 用了未声明的指纹: {sorted(hook_names - sig_names)}",
        )
        # enttEmplace 通过 NativeFunction 调用，不经过 installHook，但同样需要指纹。
        self.assertIn("enttEmplace", sig_names)


if __name__ == "__main__":
    unittest.main()
