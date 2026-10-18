from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.run import _coerce, _parse_rpc  # noqa: E402


class CoerceTest(unittest.TestCase):
    def test_literals(self) -> None:
        self.assertEqual(_coerce("15"), 15)
        self.assertEqual(_coerce("-3"), -3)
        self.assertEqual(_coerce("0.5"), 0.5)
        self.assertIs(_coerce("true"), True)
        self.assertIs(_coerce("FALSE"), False)
        self.assertIsNone(_coerce("null"))
        self.assertEqual(_coerce("stone"), "stone")


class ParseRpcTest(unittest.TestCase):
    def test_bare_name(self) -> None:
        self.assertEqual(_parse_rpc("get_state"), ("get_state", []))

    def test_name_with_typed_args(self) -> None:
        self.assertEqual(_parse_rpc("block_at 1 -64 2"), ("block_at", [1, -64, 2]))
        self.assertEqual(_parse_rpc("tp 0.5 -49 0.5 server"),
                         ("tp", [0.5, -49, 0.5, "server"]))

    def test_json_form(self) -> None:
        name, args = _parse_rpc('{"name": "adf_override", "args": [0, 15]}')
        self.assertEqual(name, "adf_override")
        self.assertEqual(args, [0, 15])

    def test_json_without_args(self) -> None:
        self.assertEqual(_parse_rpc('{"name": "diagnose"}'), ("diagnose", []))

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_rpc("   ")


if __name__ == "__main__":
    unittest.main()
