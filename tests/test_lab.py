"""Offline tests for the experiment helpers (no game, no pipe)."""

from __future__ import annotations

import itertools
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from driver.harness import ModProtocolError
from driver.harness.lab import (
    ACTOR_DATA_FLAG_COMPONENT,
    KEY_SPACE,
    KEY_SPRINT,
    KEY_W,
    Builder,
    RecordSession,
    declared_length,
    decode_framed_slice,
    decode_inbound_slice,
    double_space_fly,
    fit_recursion,
    fnv1a,
    inbound_slices,
    liquid_kind,
    move_and_wait,
    read_varint,
    recursion,
    segments,
    slice_verdict,
    steady,
    summarize_inbound_slices,
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
    def test_failed_record_session_retains_partial_rows(self) -> None:
        rows = [entry(100)]
        client = FakeClient(entries=rows)
        session = RecordSession(client)
        with (
            patch.object(client, "record_bin_stop", side_effect=ModProtocolError("capture_fault")),
            self.assertRaisesRegex(ModProtocolError, "capture_fault"),
            session,
        ):
            pass
        self.assertEqual(session.entries, rows)
        dump = next(c for c in client.calls if c[0] == "record_dump_all")
        self.assertTrue(dump[2]["allow_partial"])

    def test_failed_record_cleanup_preserves_body_exception(self) -> None:
        client = FakeClient(entries=[entry(100)])
        session = RecordSession(client)
        with (
            patch.object(client, "record_bin_stop", side_effect=ModProtocolError("capture_fault")),
            self.assertRaisesRegex(ValueError, "experiment") as caught,
            session,
        ):
            raise ValueError("experiment")
        self.assertEqual(len(session.entries), 1)
        self.assertIn("capture_fault", caught.exception.__notes__[0])

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

    def test_drive_sets_sprint_state_after_entering(self) -> None:
        client = self._client(swim_after=1)
        result = swim_drive(client, timeout=0.5)
        self.assertTrue(result["entered"])
        self.assertTrue(result["held"])
        drives = [
            c
            for c in client.calls
            if c[0] == "set_input" and c[2].get("forward") == 1.0 and c[2].get("sprint") is True
        ]
        self.assertEqual(len(drives), 1)
        self.assertEqual(drives[0][2].get("ticks"), -1)
        # A pure key-event drive cannot enter SWIMMING: it never sets sprint.
        self.assertEqual([c for c in client.calls if c[0] == "key_event"], [])

    def test_drive_releases_when_never_entered(self) -> None:
        client = self._client(swim_after=10_000)
        result = swim_drive(client, timeout=0.2, poll_ticks=2)
        self.assertFalse(result["entered"])
        self.assertFalse(result["held"])
        releases = [c for c in client.calls if c[0] == "set_input" and not c[2]]
        self.assertEqual(len(releases), 1)

    def test_stop_releases_all_swim_keys(self) -> None:
        client = self._client(swim_after=0)
        swim_stop(client, settle=1)
        released = {c[1][0] for c in client.calls if c[0] == "key_event" and c[1][1] is False}
        self.assertEqual(released, {KEY_W, KEY_SPRINT, KEY_SPACE})
        self.assertTrue(any(c[0] == "set_input" and not c[2] for c in client.calls))


class DoubleSpaceTests(unittest.TestCase):
    class Client:
        def __init__(self, flying_after: int = 1):
            self.calls: list[tuple] = []
            self.flying_after = flying_after
            self.reads = 0

        def key_event(self, code: int, down: bool = True, *, via: str = "hid"):
            self.calls.append(("key", code, down, via))
            return {}

        def wait_ticks(self, ticks: int) -> int:
            self.calls.append(("wait", ticks))
            return ticks

        def state(self):
            self.reads += 1
            return {"flying": self.reads >= self.flying_after, "mayfly": True}

    def test_presses_use_hardware_origin_and_hold(self) -> None:
        client = self.Client()
        result = double_space_fly(client)
        self.assertTrue(result["flying"])
        self.assertTrue(result["mayfly"])
        keys = [call for call in client.calls if call[0] == "key"]
        self.assertEqual([call[1] for call in keys], [KEY_SPACE] * 4)
        self.assertEqual([call[2] for call in keys], [True, False, True, False])
        # Only the Hardware origin flips flight, so every press must request it.
        self.assertEqual({call[3] for call in keys}, {"hardware"})
        # Every press has to span at least one tick, so no zero-length tap.
        self.assertTrue(all(call[1] >= 1 for call in client.calls if call[0] == "wait"))

    def test_retries_while_still_grounded(self) -> None:
        client = self.Client(flying_after=2)
        result = double_space_fly(client, attempts=3)
        self.assertTrue(result["flying"])
        self.assertEqual(len([c for c in client.calls if c[0] == "key"]), 8)

    def test_stops_retrying_when_gate_never_engages(self) -> None:
        client = self.Client(flying_after=99)
        result = double_space_fly(client, attempts=2)
        self.assertFalse(result["flying"])
        self.assertEqual(len([c for c in client.calls if c[0] == "key"]), 8)


class DisconnectReportTests(unittest.TestCase):
    def test_failed_state_probe_preserves_packet_fault_evidence(self):
        import json
        import tempfile
        from unittest.mock import Mock

        from driver.harness import ModClient, ModPipeError
        from driver.harness.lab import capture_disconnect_report

        client = Mock(spec=ModClient)
        client.state.side_effect = ModPipeError("level gone")
        for name in ("screen", "worlds", "status", "diag"):
            getattr(client, name).return_value = {}
        client.packet_stats.return_value = {"id_faults": 2, "byte_faults": 3}
        client.packet_drain.return_value = {"hits": [{"seq": 4, "id": 144}]}
        with tempfile.TemporaryDirectory() as directory:
            path = capture_disconnect_report(client, directory)
            report = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(report["state"]["event"], "probe_failed")
        self.assertEqual(report["packet_stats"], {"id_faults": 2, "byte_faults": 3})
        self.assertEqual(report["packet_tap"]["hits"][0]["seq"], 4)
        self.assertTrue(path.is_absolute())
        dump = next(
            call.args[0]
            for call in client.request.call_args_list
            if call.args[0].get("cmd") == "inbound_dump"
        )
        self.assertEqual(Path(dump["path"]), path.with_suffix(".inbound.json"))


class WorldStateTests(unittest.TestCase):
    """The disconnect error page is HBUI too, so it must not read as a menu."""

    def client(self, *, in_world, ready, screen, error=False, diag=None):
        from driver.harness.errors import ModPipeError

        class Client:
            def state(self):
                return {"in_world": in_world}

            def worlds(self):
                if error:
                    raise ModPipeError("pipe gone")
                return {"ready": ready}

            def screen(self):
                return {"name": screen}

            def diag(self):
                return (
                    diag
                    if diag is not None
                    else dict.fromkeys(
                        ("minecraft", "server_instance", "level", "server_level"), False
                    )
                )

        return Client()

    def test_in_world(self) -> None:
        from driver.harness.lab import world_state

        self.assertEqual(world_state(self.client(in_world=True, ready=False, screen="")), "world")

    def test_menu_needs_ready_world_list(self) -> None:
        from driver.harness.lab import world_state

        self.assertEqual(
            world_state(self.client(in_world=False, ready=True, screen="ui://./hbui/index.html")),
            "menu",
        )

    def test_hbui_without_world_list_is_the_error_page(self) -> None:
        from driver.harness.lab import world_state

        self.assertEqual(
            world_state(self.client(in_world=False, ready=False, screen="ui://./hbui/index.html")),
            "disconnect_error",
        )

    def test_dead_pipe_is_exiting(self) -> None:
        from driver.harness.lab import world_state

        self.assertEqual(
            world_state(self.client(in_world=False, ready=False, screen="", error=True)), "exiting"
        )

    def test_hbui_with_live_server_is_still_exiting(self) -> None:
        from driver.harness.lab import world_state

        for diag in ({}, {"server_instance": True}):
            self.assertEqual(
                world_state(
                    self.client(in_world=False, ready=False, screen="ui://hbui", diag=diag)
                ),
                "exiting",
            )

    def test_hbui_progress_is_never_an_error_page(self) -> None:
        from driver.harness.lab import world_state

        self.assertEqual(
            world_state(
                self.client(
                    in_world=False, ready=False, screen="ui://hbui/world_saving_progress_screen"
                )
            ),
            "exiting",
        )


class InboundSliceTests(unittest.TestCase):
    """The local deep-copy boundary gives [id][payload] and no frame prefix."""

    def test_varint_roundtrip(self) -> None:
        self.assertEqual(read_varint(bytes([0x05])), (5, 1))
        self.assertEqual(read_varint(bytes([0xBC, 0x01])), (188, 2))
        self.assertEqual(read_varint(bytes([0x80])), (None, 0))

    def test_slice_starts_with_packet_id(self) -> None:
        decoded = decode_inbound_slice("2706010007078080c08480838003000000")
        assert decoded is not None
        self.assertEqual(decoded["packet_id"], 39)  # SetActorData
        self.assertEqual(decoded["id_bytes"], 1)
        self.assertEqual(decoded["payload_bytes"], 16)

    def test_two_byte_id_is_why_188_slices_are_payload_plus_two(self) -> None:
        decoded = decode_inbound_slice("bc01" + "00" * 5)
        assert decoded is not None
        self.assertEqual(decoded["packet_id"], 188)
        self.assertEqual(decoded["id_bytes"], 2)
        self.assertEqual(decoded["payload_bytes"], 5)
        self.assertEqual(decoded["slice_bytes"], 7)

    def test_declared_length_must_end_at_the_boundary(self) -> None:
        self.assertEqual(declared_length("0010"), {"candidates": [{"bytes": 1, "value": 16}]})
        self.assertIsNone(declared_length(""))

    def test_framed_when_declared_matches(self) -> None:
        decoded = decode_framed_slice("bc01" + "00" * 5, "05")
        self.assertEqual(slice_verdict(decoded), "framed")

    def test_clipped_when_declared_exceeds_delivery(self) -> None:
        decoded = decode_framed_slice("bc01", "05")
        self.assertEqual(slice_verdict(decoded), "clipped")

    def test_multibyte_framing_uses_the_real_prefix_start(self) -> None:
        decoded = decode_framed_slice("00" * 128, "8001")
        assert decoded is not None
        self.assertEqual(decoded["declared"], 128)
        self.assertEqual(slice_verdict(decoded), "framed")
        decoded = decode_framed_slice("00" * 16384, "808001")
        assert decoded is not None
        self.assertEqual(decoded["declared"], 16384)
        self.assertEqual(slice_verdict(decoded), "framed")

    def test_suffix_candidate_without_prefix_start_stays_unknown(self) -> None:
        decoded = decode_framed_slice("00" * 16, "0010")
        assert decoded is not None
        self.assertEqual(decoded["declared_candidates"], [{"bytes": 1, "value": 16}])
        self.assertIsNone(decoded["declared"])
        self.assertEqual(slice_verdict(decoded), "unknown")

    def test_heap_neighbours_are_not_framing_evidence(self) -> None:
        decoded = decode_inbound_slice("bc01" + "00" * 5, "05")
        assert decoded is not None
        self.assertIsNone(decoded["declared"])
        self.assertFalse(decoded["framing_applicable"])
        self.assertEqual(slice_verdict(decoded), "unknown")

    def test_garbage_slice_is_none(self) -> None:
        self.assertIsNone(decode_inbound_slice(""))
        self.assertIsNone(decode_inbound_slice("zz"))

    def test_actual_boundaries_sequence_and_summary(self) -> None:
        class Client:
            def inbound_drain(self, since: int, limit: int) -> dict:
                return {
                    "hits": [
                        {
                            "source": "inbound",
                            "index": 8,
                            "seq": 8,
                            "tick": 10,
                            "tid": 42,
                            "packet_id": 2,
                            "packet_size": 7,
                            "error_value": 0,
                            "context_hex": "bc010000000001",
                            "context_truncated": False,
                            "before_hex": "07",
                        },
                        {
                            "source": "inbound",
                            "index": 9,
                            "seq": 9,
                            "tick": 11,
                            "tid": 42,
                            "packet_id": 2,
                            "packet_size": 7,
                            "error_value": 0,
                            "context_hex": "bc01",
                            "context_truncated": True,
                            "before_hex": "05",
                        },
                    ]
                }

        rows = inbound_slices(Client(), since=0, limit=2)  # type: ignore[arg-type]
        self.assertEqual([row["seq"] for row in rows], [8, 9])
        self.assertEqual([row["decoded"]["sample_complete"] for row in rows], [True, False])
        self.assertEqual([row["decoded"]["truncated"] for row in rows], [False, True])
        self.assertTrue(all(row["decoded"]["declared"] is None for row in rows))
        summary = summarize_inbound_slices(rows, limit=1)
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["decoded"], 2)
        self.assertEqual(summary["complete"], 1)
        self.assertEqual(summary["truncated"], 1)
        self.assertEqual(summary["framing_applicable"], 0)
        self.assertEqual(summary["by_id_size"], {"188:7": 2})
        self.assertEqual(summary["tail"][0]["seq"], 9)
        self.assertEqual(summary["tail"][0]["captured_bytes"], 2)


class RecordWatchAndCleanupTests(unittest.TestCase):
    def test_record_session_with_watch_returns_aligned_watch_entries(self) -> None:
        from driver.harness.record import WatchSample

        class WatchFakeClient(FakeClient):
            def record_dump_all(self, *_args, include_watch: bool = False, **_kwargs):
                self.calls.append(("record_dump_all", _args, {"include_watch": include_watch}))
                if include_watch:
                    return self.entries, [WatchSample((0x3F800000,), 1)]
                return self.entries

        client = WatchFakeClient(entries=[entry(1)])
        with RecordSession(client, watch=[{"component_id": 0x60FC5A9A, "size": 4}]) as rec:
            pass
        self.assertEqual(len(rec.entries), 1)
        self.assertEqual(len(rec.watch_entries), 1)
        self.assertAlmostEqual(rec.watch_entries[0].as_f32(0), 1.0)

    def test_record_session_rows_merge_named_watch_specs(self) -> None:
        from driver.harness.record import WatchSample

        class NamedWatchClient(FakeClient):
            def record_dump_all(self, *_args, include_watch: bool = False, **_kwargs):
                if include_watch:
                    return self.entries, [WatchSample((0x3FC00000, 1 << 5), 0b11)]
                return self.entries

        client = NamedWatchClient(entries=[entry(1)])
        specs = [
            {"name": "slow_y", "fmt": "f32"},
            {
                "name": "flags",
                "omit_raw": True,
                "bits": {"in_scaf": 5, "over_scaf": 6},
            },
        ]
        with RecordSession(client, watch=specs) as rec:
            pass
        self.assertEqual(rec.decoded_watch, [{"slow_y": 1.5, "in_scaf": True, "over_scaf": False}])
        rows = rec.rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slow_y"], 1.5)
        self.assertTrue(rows[0]["in_scaf"])
        self.assertFalse(rows[0]["over_scaf"])
        self.assertEqual(rows[0]["watch_mask"], 0b11)

    def test_heal_clean_and_clear_armor_and_lab_session(self) -> None:
        from driver.harness.lab import LabSession, clear_armor_and_wait, heal_clean

        inv_replies = [
            {"armor": [{"slot": 3, "item": {"type": "minecraft:leather_boots"}}]},
            {"armor": [{"slot": 3, "item": None}]},
        ]

        class LabFakeClient(FakeClient):
            def focus_cfg(self, **kwargs):
                self.calls.append(("focus_cfg", (), kwargs))
                return {
                    "background_guard": True,
                    "suppress_cursor_grab": False,
                    "suppress_focus_pause": False,
                }

            def state(self):
                self.calls.append(("state", (), {}))
                return {
                    "gamemode": 0,
                    "feet_x": 1.0,
                    "feet_y": -60.0,
                    "feet_z": 2.0,
                    "yaw": 90.0,
                    "pitch": 5.0,
                    "health": 20.0,
                }

            def inventory(self):
                self.calls.append(("inventory", (), {}))
                return inv_replies.pop(0) if inv_replies else {"armor": [{"slot": 3, "item": None}]}

        client = LabFakeClient()
        st = heal_clean(client, settle_ticks=1)
        self.assertEqual(st["health"], 20.0)
        armor = clear_armor_and_wait(client, settle_ticks=3, max_wait_ticks=10)
        self.assertEqual(armor, [{"slot": 3, "item": None}])
        client.calls.clear()

        with LabSession(client, clear_box=((0, -57, 0), (5, -50, 5)), clear_inventory=True):
            pass

        names = [name for name, _, _ in client.calls]
        self.assertEqual(
            names,
            [
                "focus_cfg",
                "state",
                "focus_cfg",
                "input_lock",
                "set_input",
                "clear_inventory",
                "command",
                "wait_ticks",
                "command",
                "wait_ticks",
                "teleport",
                "focus_cfg",
                "input_lock",
            ],
        )
        self.assertEqual(client.calls[3], ("input_lock", (True,), {}))
        self.assertEqual(client.calls[-2][2]["suppress_focus_pause"], False)
        self.assertEqual(client.calls[-1], ("input_lock", (False,), {}))

    def test_lab_session_lock_input_opt_out(self) -> None:
        from driver.harness.lab import LabSession

        class MinimalClient(FakeClient):
            def focus_cfg(self, **kwargs):
                self.calls.append(("focus_cfg", (), kwargs))
                return {"suppress_cursor_grab": False, "suppress_focus_pause": False}

            def state(self):
                self.calls.append(("state", (), {}))
                return {"gamemode": 1}

        client = MinimalClient()
        with LabSession(
            client,
            lock_input=False,
            restore_position=False,
            restore_gamemode=False,
        ):
            pass

        self.assertNotIn("input_lock", [name for name, _, _ in client.calls])

    def test_remove_entities_teleports_then_kills(self) -> None:
        from driver.harness.lab import remove_entities

        client = FakeClient()
        remove_entities(client, "@e[type=dolphin]", void_pos=(10, -100, 20), wait_ticks=3)
        self.assertEqual(
            [(name, args) for name, args, _ in client.calls],
            [
                ("command", ("/tp @e[type=dolphin] 10 -100 20",)),
                ("command", ("/kill @e[type=dolphin]",)),
                ("wait_ticks", (3,)),
            ],
        )

    def test_lab_session_chunks_oversized_clear_box(self) -> None:
        from driver.harness.lab import FILL_VOLUME_LIMIT, LabSession

        class MinimalFakeClient(FakeClient):
            def focus_cfg(self, **kwargs):
                self.calls.append(("focus_cfg", (), kwargs))
                return {"suppress_cursor_grab": False, "suppress_focus_pause": False}

            def state(self):
                self.calls.append(("state", (), {}))
                return {"gamemode": 1}

        client = MinimalFakeClient()
        p1, p2 = (600, -60, 600), (640, -44, 660)
        with LabSession(
            client,
            clear_box=(p1, p2),
            restore_position=False,
            restore_gamemode=False,
        ):
            pass

        fill_cmds = [
            args[0]
            for name, args, _ in client.calls
            if name == "command" and args[0].startswith("fill ")
        ]
        self.assertGreater(len(fill_cmds), 1)
        covered: set[tuple[int, int, int]] = set()
        total_cells = 0
        for cmd in fill_cmds:
            parts = cmd.split()
            self.assertEqual(parts[0], "fill")
            self.assertEqual(parts[-1], "air")
            x1, y1, z1, x2, y2, z2 = (int(v) for v in parts[1:7])
            volume = (x2 - x1 + 1) * (y2 - y1 + 1) * (z2 - z1 + 1)
            self.assertLessEqual(volume, FILL_VOLUME_LIMIT)
            self.assertGreater(volume, 0)
            total_cells += volume
            for x in range(x1, x2 + 1):
                for y in range(y1, y2 + 1):
                    for z in range(z1, z2 + 1):
                        covered.add((x, y, z))
        expected_volume = (640 - 600 + 1) * (-44 - (-60) + 1) * (660 - 600 + 1)
        self.assertEqual(total_cells, expected_volume)
        self.assertEqual(len(covered), expected_volume)
