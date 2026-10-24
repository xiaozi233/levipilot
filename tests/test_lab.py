"""Offline tests for the experiment helpers (no game, no pipe)."""

from __future__ import annotations

import itertools
import math
import unittest

from driver.harness.lab import (
    ACTOR_DATA_FLAG_COMPONENT,
    KEY_SPACE,
    KEY_SPRINT,
    KEY_W,
    Builder,
    RecordSession,
    fit_recursion,
    fnv1a,
    liquid_kind,
    move_and_wait,
    recursion,
    segments,
    steady,
    swim_drive,
    swim_stop,
    swimming_state,
)
from driver.harness.record import (
    FLAG_IN_LAVA,
    FLAG_IN_WATER,
    FLAG_ON_GROUND,
    RecordEntry,
)


def entry(
    tick: int, *, vy: float = 0.0, vx: float = 0.0, flags: int = FLAG_ON_GROUND
) -> RecordEntry:
    return RecordEntry(tick, 0.0, 0.0, 0.0, vx, vy, 0.0, 0.0, 0.0, 0.0, flags, 0, 0, 0, 20000)


class FakeClient:
    """Records calls; ``blocks``/dumps come from scripted replies."""

    def __init__(
        self, block_replies: list[dict] | None = None, entries: list[RecordEntry] | None = None
    ):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.block_replies = list(block_replies or [])
        self.entries = list(entries or [])

    def __getattr__(self, name: str):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "blocks":
                return self.block_replies.pop(0) if self.block_replies else {"blocks": []}
            if name == "record_dump_all":
                return self.entries
            if name == "state":
                return {"event": "state"}
            return {"event": name}

        return call


def block(x: int, y: int, z: int, name: str) -> dict:
    return {"x": x, "y": y, "z": z, "name": name}


class FnvTests(unittest.TestCase):
    def test_block_hashes_match_record_ring(self) -> None:
        # Values observed from the live ring's block_feet field.
        self.assertEqual(fnv1a("minecraft:water"), 0xA32DED41)
        self.assertEqual(fnv1a("minecraft:flowing_water"), 0xA1A8C422)
        self.assertEqual(fnv1a("minecraft:lava"), 0xF6054D80)
        self.assertEqual(fnv1a("minecraft:flowing_lava"), 0x855EC245)


class AnalysisTests(unittest.TestCase):
    def test_fit_recursion_recovers_water_sink(self) -> None:
        # vy' = 0.8*(vy - 0.00625) sampled from rest (values from the live probe).
        series = [0.0]
        for _ in range(9):
            series.append(0.8 * (series[-1] - 0.00625))
        pairs = list(itertools.pairwise(series))
        a, b, residual = fit_recursion(pairs)
        self.assertAlmostEqual(a, 0.8, places=6)
        self.assertAlmostEqual(b, -0.005, places=6)
        self.assertLess(residual, 1e-6)

    def test_fit_recursion_insufficient(self) -> None:
        a, b, _residual = fit_recursion([(1.0, 0.9)])
        self.assertTrue(math.isnan(a))
        self.assertTrue(math.isnan(b))

    def test_recursion_and_steady(self) -> None:
        rows = [entry(i, vy=-0.025) for i in range(10)]
        pairs = recursion(rows, "vy")
        self.assertEqual(len(pairs), 9)
        self.assertAlmostEqual(steady(rows, "vy"), -0.025)

    def test_segments(self) -> None:
        rows = [entry(i, flags=FLAG_ON_GROUND) for i in range(3)] + [
            entry(i + 3, flags=FLAG_IN_WATER) for i in range(2)
        ]
        runs = list(segments(rows, lambda row: row.in_water))
        self.assertEqual(len(runs), 1)
        self.assertEqual(len(runs[0]), 2)

    def test_liquid_kind(self) -> None:
        self.assertEqual(liquid_kind(entry(0, flags=FLAG_IN_WATER)), "water")
        self.assertEqual(liquid_kind(entry(0, flags=FLAG_IN_LAVA)), "lava")
        self.assertEqual(liquid_kind(entry(0)), "")


class BuilderTests(unittest.TestCase):
    def test_build_retries_until_readback_matches(self) -> None:
        client = FakeClient(
            block_replies=[
                {"blocks": [block(1, 2, 3, "minecraft:air")]},
                {"blocks": [block(1, 2, 3, "minecraft:water")]},
            ]
        )
        builder = Builder(client, settle_ticks=1)
        checks = builder.build(["setblock 1 2 3 water"], {(1, 2, 3): "minecraft:water"}, retries=1)
        self.assertTrue(all(check.ok for check in checks))
        self.assertEqual([c[0] for c in client.calls].count("command"), 2)

    def test_build_reports_mismatch_after_retries(self) -> None:
        client = FakeClient(
            block_replies=[
                {"blocks": [block(1, 2, 3, "minecraft:air")]},
                {"blocks": [block(1, 2, 3, "minecraft:air")]},
            ]
        )
        builder = Builder(client, settle_ticks=1)
        checks = builder.build(
            ["fill 1 2 3 1 2 3 water"], {(1, 2, 3): "minecraft:water"}, retries=1
        )
        self.assertFalse(any(check.ok for check in checks))
        self.assertEqual(checks[0].actual, "minecraft:air")

    def test_poke_toggles_neighbour(self) -> None:
        client = FakeClient()
        Builder(client, settle_ticks=1).poke(5, 6, 7, settle_ticks=1)
        commands = [c[1][0] for c in client.calls if c[0] == "command"]
        self.assertEqual(commands, ["setblock 5 6 7 stone", "setblock 5 6 7 air"])


class SessionTests(unittest.TestCase):
    def test_record_session_dumps_from_zero(self) -> None:
        rows = [entry(100), entry(101)]
        client = FakeClient(entries=rows)
        with RecordSession(client) as session:
            pass
        self.assertEqual(session.entries, rows)
        dump_calls = [c for c in client.calls if c[0] == "record_dump_all"]
        self.assertEqual(dump_calls[0][2]["since"], 0)
        self.assertEqual(len(session.rows), 2)

    def test_move_and_wait_covers_input_window(self) -> None:
        client = FakeClient()
        move_and_wait(client, ticks=30, sprint=True, settle=2)
        names = [c[0] for c in client.calls]
        self.assertEqual(names, ["set_input", "wait_ticks"])
        _, _args, kwargs = client.calls[0]
        self.assertEqual(kwargs["ticks"], 30)
        self.assertIs(kwargs["sprint"], True)
        self.assertEqual(client.calls[1][1], (32,))


class FlagTests(unittest.TestCase):
    def test_medium_flags(self) -> None:
        self.assertTrue(entry(0, flags=FLAG_IN_WATER).in_water)
        self.assertTrue(entry(0, flags=FLAG_IN_LAVA).in_lava)
        self.assertFalse(entry(0, flags=FLAG_ON_GROUND).in_water)


if __name__ == "__main__":
    unittest.main()


class SwimDriveTests(unittest.TestCase):
    """SWIMMING is driven by real keys; the fake models the ADF bit flip."""

    def _client(self, *, swim_after: int) -> FakeClient:
        client = FakeClient()
        client.swim_after = swim_after
        client.mem_reads = 0
        client.swim = False

        def mem_read(addr, length=16):
            client.calls.append(("mem_read", (addr, length), {}))
            client.mem_reads += 1
            if client.mem_reads > swim_after:
                client.swim = True
            data = bytearray(24)
            if client.swim:
                data[7] = 0x02  # bit 57
            return {"hex": data.hex()}

        def ecs_context(context, component_id):
            client.calls.append(("ecs_context", (context, component_id), {}))
            return {"address": 0x1000}

        def state():
            return {"context": 0x1234}

        client.mem_read = mem_read
        client.ecs_context = ecs_context
        client.state = state
        return client

    def test_swimming_state_reads_bit57(self) -> None:
        client = self._client(swim_after=1)
        self.assertFalse(swimming_state(client))
        self.assertTrue(swimming_state(client))
        ecs_calls = [call for call in client.calls if call[0] == "ecs_context"]
        self.assertEqual(ecs_calls[-1][1][1], ACTOR_DATA_FLAG_COMPONENT)

    def test_drive_holds_keys_after_entering(self) -> None:
        client = self._client(swim_after=1)
        result = swim_drive(client, timeout=0.5)
        self.assertTrue(result["entered"])
        self.assertTrue(result["held"])
        downs = [c for c in client.calls if c[0] == "key_event" and c[1][1] is True]
        self.assertEqual([c[1][0] for c in downs], [KEY_W, KEY_SPRINT])

    def test_drive_releases_when_never_entered(self) -> None:
        client = self._client(swim_after=10_000)
        result = swim_drive(client, timeout=0.2, poll_ticks=2)
        self.assertFalse(result["entered"])
        self.assertFalse(result["held"])
        ups = [c for c in client.calls if c[0] == "key_event" and c[1][1] is False]
        self.assertEqual({c[1][0] for c in ups}, {KEY_W, KEY_SPRINT})

    def test_stop_releases_all_swim_keys(self) -> None:
        client = self._client(swim_after=0)
        swim_stop(client, settle=1)
        released = {c[1][0] for c in client.calls if c[0] == "key_event" and c[1][1] is False}
        self.assertEqual(released, {KEY_W, KEY_SPRINT, KEY_SPACE})
