from __future__ import annotations

import struct
import unittest
from typing import Any

from driver.harness.commands.hooks import HookCommands
from driver.harness.commands.watch import WatchCommands
from driver.harness.hooks import HookHit, decode_hook_hits, float_from_bits


def bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


class HookDecodeTests(unittest.TestCase):
    def test_float_from_bits(self) -> None:
        self.assertAlmostEqual(float_from_bits(bits(1.0)), 1.0)
        self.assertAlmostEqual(float_from_bits(bits(-0.5)), -0.5)

    def test_decode_hook_hits(self) -> None:
        hits = decode_hook_hits(
            {
                "event": "hook_hits",
                "hits": [
                    {
                        "seq": 3,
                        "hook": 7,
                        "tag": "jps",
                        "tid": 42,
                        "tick": 100,
                        "args": [0x1234, bits(1.0), 0, 0],
                        "b_float": 1.0,
                        "ret": 0x140000000,
                        "stack": [1, 2, 3],
                    }
                ],
            }
        )
        self.assertEqual(len(hits), 1)
        hit: HookHit = hits[0]
        self.assertEqual(hit.seq, 3)
        self.assertEqual(hit.hook, 7)
        self.assertEqual(hit.args[0], 0x1234)
        self.assertAlmostEqual(hit.b_float, 1.0)
        self.assertEqual(hit.stack, (1, 2, 3))

    def test_short_args_are_padded(self) -> None:
        hit = HookHit.from_json({"seq": 1, "args": [5]})
        self.assertEqual(hit.args, (5, 0, 0, 0))
        self.assertEqual(hit.b_float, 0.0)

    def test_mem_snapshot_decodes(self) -> None:
        raw = struct.pack("<6f", 1.5, -2.25, 3.0, 4.0, 5.0, 6.0)
        hit = HookHit.from_json(
            {
                "seq": 9,
                "args": [0x1000, 0, 0, 0],
                "mem": {
                    "arg": 0,
                    "offset": 4,
                    "len": len(raw),
                    "ok": True,
                    "addr": 0x1004,
                    "hex": raw.hex(),
                    "f32": list(struct.unpack("<6f", raw)),
                },
            }
        )
        self.assertTrue(hit.mem_ok)
        self.assertEqual(hit.mem_arg, 0)
        self.assertEqual(hit.mem_offset, 4)
        self.assertEqual(hit.mem_addr, 0x1004)
        self.assertEqual(hit.mem, raw)
        self.assertEqual(hit.mem_u32, struct.unpack("<6I", raw))
        self.assertAlmostEqual(hit.mem_f32[1], -2.25)

    def test_mem_snapshot_failure_keeps_metadata(self) -> None:
        hit = HookHit.from_json(
            {
                "seq": 10,
                "mem": {"arg": 2, "offset": 0, "len": 8, "ok": False, "addr": 0x40, "hex": ""},
            }
        )
        self.assertFalse(hit.mem_ok)
        self.assertEqual(hit.mem, b"")
        self.assertEqual(hit.mem_u32, ())
        self.assertEqual(hit.mem_addr, 0x40)

    def test_unrelated_message_returns_empty(self) -> None:
        self.assertEqual(decode_hook_hits({"event": "tick"}), [])


if __name__ == "__main__":
    unittest.main()


class _RingStub(HookCommands, WatchCommands):  # type: ignore[misc]
    """Records requests and replays canned replies."""

    def __init__(self, replies: dict[str, dict[str, object]]) -> None:
        self.replies = replies
        self.sent: list[dict[str, object]] = []

    def request(
        self,
        message: dict[str, Any],
        expected: str | tuple[str, ...] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.sent.append(dict(message))
        return dict(self.replies[message["cmd"]])


class RingHeadTests(unittest.TestCase):
    """The hook/watch rings are process-global; drain must not replay old rows."""

    def _stub(self) -> _RingStub:
        return _RingStub(
            {
                "hook_install": {"event": "hook_installed", "id": 7},
                "watch_set": {"event": "watch_installed", "id": 3},
                "hook_list": {"event": "hook_list", "head": 500},
                "watch_list": {"event": "watch_list", "head": 42},
                "hook_drain": {"event": "hook_hits", "hits": [{"hook": 7}, {"hook": 3}]},
                "watch_drain": {"event": "watch_hits", "hits": [{"watch": 3}, {"watch": 1}]},
            }
        )

    def test_hook_drain_defaults_to_install_head(self) -> None:
        client = self._stub()
        client.hook_install(va=0x140000000)
        client.hook_drain()
        self.assertEqual(client.sent[-1]["since"], 500)

    def test_hook_drain_keeps_explicit_since(self) -> None:
        client = self._stub()
        client.hook_install(va=0x140000000)
        client.hook_drain(0)
        self.assertEqual(client.sent[-1]["since"], 0)

    def test_hook_drain_filters_by_id(self) -> None:
        client = self._stub()
        client.hook_install(va=0x140000000)
        out = client.hook_drain(hook_id=7)
        self.assertEqual(client.sent[-1]["since"], 500)
        self.assertEqual(out["hits"], [{"hook": 7}])

    def test_watch_drain_defaults_to_install_head(self) -> None:
        client = self._stub()
        client.watch_set(0x2000)
        out = client.watch_drain(watch_id=3)
        self.assertEqual(client.sent[-1]["since"], 42)
        self.assertEqual(out["hits"], [{"watch": 3}])

    def test_drain_without_install_replays_from_zero(self) -> None:
        client = self._stub()
        client.hook_drain()
        self.assertEqual(client.sent[-1]["since"], 0)


if __name__ == "__main__":
    unittest.main()
