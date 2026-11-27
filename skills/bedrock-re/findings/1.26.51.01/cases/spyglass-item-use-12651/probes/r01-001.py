"""Round 1 closure for ``spyglass-item-use-12651``: ItemUseSlowdown pipeline + spyglass boost hunt.

Static model under test (addresses are ``1.26.51.01``; see
``docs/notes/spyglass-item-use-12651.md``):

* ``ItemUseSlowdownSystem`` (19th system in ``0x14681da00``) writes
  ``ItemUseSlowdownModifierComponent (0x867fc92c).+0x00`` from the held item's
  ``minecraft:use_modifiers`` movement modifier, defaulting to ``0.35f`` and
  skipping the write when the modifier is ``1.0``.
* ``ItemUseSlowdownApplySystem`` (20th) squares that value and multiplies
  ``MoveInputComponent (0x018b1887).+0x24/+0x28`` (``mMoveVector.x/z``).
* ``ItemUseSlowdownClearSystem`` (34th) empties the whole storage, so the
  component never survives to a tick boundary and is unreadable from the pipe.

The probe therefore reads the *applied* multiplier: with forward held and no
other multiplier active, ``MoveInput.mMoveVector.z`` is ``m**2``.  Item use is
asserted independently through ``ItemInUseComponent (0x05b90aef)`` presence via
``ecs_component``, so "held rmb but never used" can never be read as ``1.0x``.

Run with the game already in a world **and focused** (``py -3.11 driver/dev.py focus``):
an unfocused window still ticks but drops injected movement, so every segment would read
``vx = vz = 0`` and the probe would look like a total slowdown::

    py -3.11 driver/dev.py focus
    py -3.11 skills/bedrock-re/findings/1.26.51.01/cases/spyglass-item-use-12651/probes/verify_spyglass_item_use.py

Every number quoted by the report is copied from ``out/spyglass-item-use-1.26.51.01.json``.
"""

from __future__ import annotations

import itertools
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "driver").is_dir())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.harness.client import ModClient  # noqa: E402
from driver.harness.lab import LabSession, RecordSession, settle  # noqa: E402

OUT = ROOT / "out" / "spyglass-item-use-1.26.51.01.json"

SLOWDOWN = 0x867FC92C  # ItemUseSlowdownModifierComponent
MOVEINPUT = 0x018B1887  # MoveInputComponent
ITEM_IN_USE = 0x05B90AEF  # ItemInUseComponent
STATE_VECTOR = 0x1B5D5238  # StateVectorComponent
SWIFT_SNEAK = 0xCDD62A2C  # SwiftSneakComponent

WATCH = [
    {"component_id": MOVEINPUT, "offset": 0x24, "size": 4, "name": "mi_x", "fmt": "f32"},
    {"component_id": MOVEINPUT, "offset": 0x28, "size": 4, "name": "mi_z", "fmt": "f32"},
    {"component_id": SLOWDOWN, "offset": 0x00, "size": 4, "name": "slow_m", "fmt": "f32"},
    {"component_id": STATE_VECTOR, "offset": 0x18, "size": 4, "name": "mpd_x", "fmt": "f32"},
]

GROUND_DECAY = 0.546
AIR_DECAY = 0.91
ROW_FIELDS = (
    "tick",
    "vx",
    "vy",
    "vz",
    "on_ground",
    "sprint",
    "sneak",
    "mi_x",
    "mi_z",
    "slow_m",
)

ITEMS: list[tuple[str, str]] = [
    ("minecraft:spyglass", "hand"),
    ("minecraft:bow", "arrows"),
    ("minecraft:crossbow", "arrows"),
    ("minecraft:trident", "hand"),
    ("minecraft:shield", "hand"),
    ("minecraft:shield", "offhand"),
    ("minecraft:golden_apple", "hand"),
    ("minecraft:potion", "hand"),
    ("minecraft:milk_bucket", "hand"),
    ("minecraft:goat_horn", "hand"),
    ("minecraft:brush", "brush_block"),
    ("minecraft:bread", "hand"),
]

RESULTS: dict[str, Any] = {}


# ---------------------------------------------------------------- helpers ----
def speed(row: dict[str, Any]) -> float:
    return math.hypot(float(row["vx"]), float(row["vz"]))


def steady(values: list[float]) -> dict[str, Any]:
    clean = [value for value in values if math.isfinite(value)]
    if not clean:
        return {"n": 0}
    return {
        "n": len(clean),
        "median": round(statistics.median(clean), 6),
        "min": round(min(clean), 6),
        "max": round(max(clean), 6),
    }


def modal(values: list[float], tol: float = 5e-4) -> tuple[float | None, int]:
    """Most frequent value within ``tol``; reads back the applied multiplier."""
    if not values:
        return None, 0
    buckets: list[list[float]] = []
    for value in sorted(values):
        if buckets and abs(value - buckets[-1][-1]) <= tol:
            buckets[-1].append(value)
        else:
            buckets.append([value])
    best = max(buckets, key=len)
    return round(statistics.median(best), 6), len(best)


def accel(rows: list[dict[str, Any]], axis: str, decay: float) -> list[float]:
    """``a_i = v[i+1] - decay * v[i]`` over consecutive recorded ticks."""
    out: list[float] = []
    for prev, cur in itertools.pairwise(rows):
        if int(cur["tick"]) - int(prev["tick"]) != 1:
            continue
        out.append(float(cur[axis]) - decay * float(prev[axis]))
    return out


def collect(rec: RecordSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in rec.rows:
        row = {key: raw.get(key) for key in ROW_FIELDS}
        row["input_w"] = bool(raw["input"]["w"])
        row["input_jump"] = bool(raw["input"]["jump"])
        rows.append(row)
    return rows


def summarise(rows: list[dict[str, Any]], factor: float | None = None) -> dict[str, Any]:
    mi = [float(row["mi_z"]) for row in rows if row["mi_z"] is not None]
    top, hits = modal(mi)
    moving = [row for row in rows if bool(row["input_w"]) and speed(row) > 1e-6]
    out: dict[str, Any] = {
        "rows": len(rows),
        "modal_mi_z": top,
        "modal_hits": hits,
        "mi_z_values": sorted({round(value, 6) for value in mi}),
        "slow_m_values": sorted(
            {round(float(row["slow_m"]), 6) for row in rows if row["slow_m"] is not None}
        ),
        "first_moving_speed": round(speed(moving[0]), 6) if moving else None,
        "steady_speed": steady([speed(row) for row in moving[-5:]]),
        "max_speed": round(max((speed(row) for row in rows), default=0.0), 6),
        "sprint_ticks": sum(1 for row in rows if bool(row["sprint"])),
        "air_ticks": sum(1 for row in rows if not bool(row["on_ground"])),
    }
    on_use = (
        rows
        if factor is None
        else [
            row
            for row in rows
            if row["mi_z"] is not None and abs(float(row["mi_z"]) - factor) <= 5e-4
        ]
    )
    ground = [row for row in on_use if bool(row["on_ground"]) and bool(row["input_w"])]
    air = [row for row in on_use if not bool(row["on_ground"]) and bool(row["input_w"])]
    if ground:
        out["ground_accel"] = steady(accel(ground, "vz", GROUND_DECAY))
    if air:
        out["air_accel"] = steady(accel(air, "vz", AIR_DECAY))
    return out


def phase_rows(
    rows: list[dict[str, Any]], marks: list[tuple[str, int]], name: str
) -> list[dict[str, Any]]:
    for index, (label, tick) in enumerate(marks):
        if label != name:
            continue
        low = marks[index - 1][1] if index else -1 << 62
        return [row for row in rows if low < int(row["tick"]) <= tick]
    return []


# ------------------------------------------------------------------ probe ----
def main() -> int:
    with ModClient(timeout=60.0).connect() as client:
        client.wait_ready(timeout=60.0, require_world=True)
        state = dict(client.state())
        rid = int(state["runtime_id"])
        origin = (float(state["feet_x"]), float(state["feet_y"]), float(state["feet_z"]))
        RESULTS["setup"] = {
            "feet": [round(value, 4) for value in origin],
            "gamemode_before": state.get("gamemode"),
            "bindings": state.get("bindings"),
            "ground_decay": GROUND_DECAY,
            "air_decay": AIR_DECAY,
            "watch": WATCH,
        }

        def use_state() -> dict[str, Any]:
            try:
                reply = client.ecs_component(rid, ITEM_IN_USE, length=8)
            except Exception as exc:
                text = str(exc)
                if "component_not_found" in text:
                    return {"present": False, "hex": None}
                return {"present": None, "error": text}
            return {"present": True, "hex": reply.get("hex")}

        def run_phases(
            phases: list[dict[str, Any]],
            *,
            probe_names: tuple[str, ...] = (),
            tail: int = 5,
        ) -> tuple[list[dict[str, Any]], list[tuple[str, int]], dict[str, Any]]:
            """Run input phases back to back.

            A probed phase is sampled **inside** its input window: the window is
            issued once for ``ticks`` and ``use_state()`` runs halfway through, so
            the probe never races the auto-release that ``set_input(ticks=N)`` does
            at the end of the window.  ``action`` hooks run after ``ticks`` and are
            followed by ``after`` ticks.
            """
            marks: list[tuple[str, int]] = []
            seen: dict[str, Any] = {}
            with RecordSession(client, period=1, watch=WATCH) as rec:
                for phase in phases:
                    name = str(phase["name"])
                    kwargs = dict(phase.get("input", {}))
                    ticks = int(phase.get("ticks", 0))
                    if name in probe_names and ticks >= 2:
                        client.set_input(ticks=ticks, **kwargs)
                        client.wait_ticks(ticks // 2)
                        seen[name] = use_state()
                        client.wait_ticks(ticks - ticks // 2)
                    else:
                        client.set_input(ticks=ticks, **kwargs)
                        client.wait_ticks(ticks)
                    action = phase.get("action")
                    if callable(action):
                        action()
                        client.wait_ticks(int(phase.get("after", 0)))
                    marks.append((name, int(dict(client.state())["tick"])))
                client.set_input()
                client.wait_ticks(tail)
            return collect(rec), marks, seen

        with LabSession(client):
            client.command("gamerule falldamage false")
            client.command("gamemode survival @s")
            client.wait_ticks(4)
            assert not bool(dict(client.state()).get("flying", False))

            def reset(x_off: float = 0.0, *, yaw: float = 0.0) -> None:
                client.set_input()
                client.teleport(origin[0] + x_off, origin[1], origin[2] + 6.0, yaw=yaw, pitch=0.0)
                client.wait_ticks(6)

            def hold(item: str, *, mode: str = "hand", slot: int = 0) -> None:
                """Equip ``item`` for the requested use mode and verify the slots."""
                client.set_input()
                client.set_item(1, "minecraft:air", count=0)
                client.set_item(0, "minecraft:air", count=0)
                client.set_item(0, item, container="offhand" if mode == "offhand" else "inventory")
                if mode == "arrows":
                    client.set_item(1, "minecraft:arrow", count=32)
                if mode == "brush_block":
                    # Brushing only starts when the crosshair is on a brushable
                    # block: stand in place first (chunk loaded), then place it.
                    client.teleport(
                        origin[0] + 0.5,
                        origin[1],
                        origin[2] + 6.5,
                        yaw=0.0,
                        pitch=0.0,
                    )
                    client.wait_ticks(4)
                    client.command(
                        f"setblock {int(origin[0])} {int(origin[1]) + 1} {int(origin[2]) + 8} "
                        "minecraft:suspicious_sand"
                    )
                    client.wait_ticks(2)
                    readback = client.blocks(
                        [(int(origin[0]), int(origin[1]) + 1, int(origin[2]) + 8)]
                    )
                    names = [str(row["name"]) for row in readback.get("blocks", [])]
                    # Recorded, not asserted: the brush is a peripheral row and the
                    # placement is a fixture detail, not a claim of this study.
                    RESULTS.setdefault("fixture_notes", {})["brush_block_readback"] = names
                client.select_slot(slot)
                client.wait_ticks(5)
                selected = client.inventory().get("selected_slot")
                assert int(selected) == slot, f"{item}: selected_slot={selected}"

            def assert_idle(label: str) -> None:
                assert settle(client, on_ground=True, input_sprint=False)["ok"], (
                    f"{label}: fixture did not settle"
                )
                assert use_state()["present"] is False, f"{label}: item already in use"

            def applied(rows: list[dict[str, Any]], name: str = "use") -> float | None:
                """Applied multiplier over the rows of phase ``name`` with W held."""
                picked = [
                    float(row["mi_z"])
                    for row in rows
                    if row["mi_z"] is not None and bool(row["input_w"])
                ]
                value, _ = modal(picked)
                if value is None or abs(value) < 1e-6:
                    return None
                return value

            # ---------------------------------------------------------- A ----
            # Baselines on the same ground, then the held-item table.
            matrix: dict[str, Any] = {}
            reset()
            hold("minecraft:air")
            assert_idle("baseline_walk")
            rows, marks, _ = run_phases([{"name": "walk", "input": {"forward": 1.0}, "ticks": 18}])
            RESULTS["baseline_walk"] = {
                "summary": summarise(rows),
                "walk_phase": summarise(phase_rows(rows, marks, "walk")),
                "tick_series": rows,
            }
            reset()
            hold("minecraft:air")
            assert_idle("baseline_air")
            rows, marks, _ = run_phases(
                [
                    {"name": "jump", "input": {"forward": 1.0, "jump": True}, "ticks": 1},
                    {"name": "air", "input": {"forward": 1.0}, "ticks": 14},
                ]
            )
            RESULTS["baseline_air"] = {
                "summary": summarise(rows),
                "air_phase": summarise(phase_rows(rows, marks, "air")),
                "tick_series": rows,
            }

            for item, mode in ITEMS:
                label = item.split(":", 1)[1] + (f"_{mode}" if mode != "hand" else "")
                reset()
                hold(item, mode=mode)
                assert_idle(label)
                rows, marks, seen = run_phases(
                    [
                        {"name": "start", "input": {"rmb": True}, "ticks": 8},
                        {"name": "use", "input": {"rmb": True, "forward": 1.0}, "ticks": 16},
                        {"name": "release", "input": {"forward": 1.0}, "ticks": 12},
                    ],
                    probe_names=("start", "use", "release"),
                )
                use_rows = phase_rows(rows, marks, "use")
                factor = applied(use_rows)
                summary = summarise(rows, factor)
                summary.update(
                    {
                        "item": item,
                        "in_use_during_hold": seen.get("start", {}).get("present"),
                        "in_use_during_walk": seen.get("use", {}).get("present"),
                        "in_use_after_release": seen.get("release", {}).get("present"),
                        "in_use_hex": seen.get("start", {}).get("hex"),
                        "applied_factor": factor,
                        "item_modifier_m": (
                            round(math.sqrt(factor), 6)
                            if factor is not None and factor < 0.999
                            else None
                        ),
                        "use_phase": summarise(use_rows, factor),
                        "tick_series": rows,
                    }
                )
                matrix[label] = summary
            RESULTS["item_matrix"] = matrix

            measured = {
                label: float(row["applied_factor"])
                for label, row in matrix.items()
                if row["applied_factor"] is not None
            }
            RESULTS["applied_factor_table"] = measured
            # A multiplier below 1.0 is only believable if the segment saw a real use
            # window.  Food at full hunger can abort mid-way, so either phase counts;
            # ``golden_apple`` is asserted separately because it is always edible and
            # therefore stays in use across the whole walk.
            for label, row in matrix.items():
                if row["applied_factor"] is not None and row["applied_factor"] < 0.999:
                    assert row["in_use_during_hold"] or row["in_use_during_walk"], (
                        f"{label}: slowdown {row['applied_factor']} without any ItemInUse window"
                    )
            spy = matrix["spyglass"]
            spy_factor = float(spy["applied_factor"])
            assert spy["in_use_during_walk"] is True, "spyglass never entered ItemInUseComponent"
            assert matrix["golden_apple"]["in_use_during_walk"] is True, (
                "golden_apple must stay in use for the whole walk"
            )
            assert abs(spy_factor - 0.1225) <= 1e-4, (
                f"spyglass applied factor {spy_factor} != 0.35**2"
            )
            assert abs(float(matrix["bread"]["applied_factor"]) - spy_factor) <= 1e-6, (
                "bread and spyglass must share the same movement modifier"
            )

            # ---------------------------------------------------------- B ----
            combos: dict[str, Any] = {}

            def ground_combo(
                label: str,
                *,
                pre: dict[str, Any] | None = None,
                pre_ticks: int = 0,
                **walk: Any,
            ) -> None:
                reset()
                hold("minecraft:spyglass")
                assert_idle(label)
                phases: list[dict[str, Any]] = []
                if pre_ticks:
                    phases.append({"name": "pre", "input": dict(pre or {}), "ticks": pre_ticks})
                phases.append({"name": "start", "input": {"rmb": True, **(pre or {})}, "ticks": 8})
                phases.append(
                    {
                        "name": "use",
                        "input": {"rmb": True, "forward": 1.0, **(pre or {}), **walk},
                        "ticks": 18,
                    }
                )
                rows, marks, seen = run_phases(phases, probe_names=("start",))
                use_rows = phase_rows(rows, marks, "use")
                summary = summarise(rows, applied(use_rows))
                summary["use_phase"] = summarise(use_rows, applied(use_rows))
                summary.update(
                    {
                        "label": label,
                        "in_use": seen.get("start", {}).get("present"),
                        "requested": {**(pre or {}), **walk},
                        "tick_series": rows,
                    }
                )
                combos[label] = summary

            ground_combo("walk")
            ground_combo("sneak", pre={"sneak": True}, pre_ticks=6)
            ground_combo("diag45", strafe=1.0)
            ground_combo("sprint_request", sprint=True)
            RESULTS["ground_combos"] = combos

            # Swift Sneak III on leggings, then the same sneak+scope run.
            clear_slot = int(dict(client.state()).get("selected_slot", 0) or 0)
            client.set_item(clear_slot, "minecraft:leather_leggings")
            client.wait_ticks(2)
            client.command("/enchant @s swift_sneak 3")
            client.wait_ticks(2)
            client.equip("legs", slot=clear_slot)
            client.wait_ticks(3)
            comp = client.ecs_component(rid, SWIFT_SNEAK, length=8)
            raw = bytes.fromhex(str(comp.get("hex") or ""))
            level = int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None
            assert level == 3, f"swift_sneak not applied: {level} {comp}"
            RESULTS["swift_sneak_level"] = level
            ground_combo("sneak_ss3", pre={"sneak": True}, pre_ticks=8)
            client.clear_inventory()
            client.wait_ticks(4)
            hold("minecraft:spyglass")

            # ---------------------------------------------------------- C ----
            reset()
            hold("minecraft:spyglass")
            assert_idle("sprint_then_scope")
            rows, marks, seen = run_phases(
                [
                    {
                        "name": "sprint",
                        "input": {"forward": 1.0, "sprint": True},
                        "ticks": 12,
                    },
                    {
                        "name": "scope",
                        "input": {"forward": 1.0, "sprint": True, "rmb": True},
                        "ticks": 14,
                    },
                    {
                        "name": "hold",
                        "input": {"forward": 1.0, "rmb": True},
                        "ticks": 12,
                    },
                    {"name": "off", "input": {"forward": 1.0}, "ticks": 10},
                ],
                probe_names=("scope",),
            )
            RESULTS["sprint_then_scope"] = {
                "in_use": seen.get("scope", {}).get("present"),
                "summary": summarise(rows),
                "phases": {
                    name: {
                        "summary": summarise(phase_rows(rows, marks, name)),
                        "sprint_series": [
                            int(bool(row["sprint"])) for row in phase_rows(rows, marks, name)
                        ],
                        "mi_z_series": [row["mi_z"] for row in phase_rows(rows, marks, name)],
                    }
                    for name in ("sprint", "scope", "hold", "off")
                },
                "tick_series": rows,
            }

            # ---------------------------------------------------------- D ----
            reset()
            hold("minecraft:spyglass")
            # Slot 1 = bread (also 0.35 -> same factor), slot 2 = shield (no use
            # window of its own), slot 3 = goat horn (the only item measured at 1.0).
            # The horn switch is the discriminating case: if the multiplier keeps
            # following the held item, the factor must leave 0.1225 while the use
            # window is still open.
            client.set_item(1, "minecraft:bread")
            client.set_item(2, "minecraft:shield")
            client.set_item(3, "minecraft:goat_horn")
            client.wait_ticks(3)
            assert_idle("hotbar_switch")
            rows, marks, seen = run_phases(
                [
                    {
                        "name": "scoped",
                        "input": {"rmb": True, "forward": 1.0},
                        "ticks": 10,
                    },
                    {
                        "name": "bread",
                        "input": {"rmb": True, "forward": 1.0},
                        "ticks": 10,
                        "action": lambda: client.select_slot(1),
                        "after": 2,
                    },
                    {
                        "name": "shield",
                        "input": {"rmb": True, "forward": 1.0},
                        "ticks": 12,
                        "action": lambda: client.select_slot(2),
                        "after": 2,
                    },
                    {
                        "name": "horn",
                        "input": {"rmb": True, "forward": 1.0},
                        "ticks": 12,
                        "action": lambda: client.select_slot(3),
                        "after": 2,
                    },
                ],
                probe_names=("scoped", "bread", "shield", "horn"),
            )
            RESULTS["hotbar_switch"] = {
                "in_use_scoped": seen.get("scoped", {}).get("present"),
                "in_use_bread": seen.get("bread", {}).get("present"),
                "in_use_shield": seen.get("shield", {}).get("present"),
                "in_use_horn": seen.get("horn", {}).get("present"),
                "scoped_summary": summarise(phase_rows(rows, marks, "scoped")),
                "bread_summary": summarise(phase_rows(rows, marks, "bread")),
                "shield_summary": summarise(phase_rows(rows, marks, "shield")),
                "horn_summary": summarise(phase_rows(rows, marks, "horn")),
                "mi_z_series": [row["mi_z"] for row in rows],
                "tick_series": rows,
            }
            client.select_slot(0)

            # ---------------------------------------------------------- E ----
            air: dict[str, Any] = {}
            for label, sprint in (("walk", False), ("sprint", True)):
                reset()
                hold("minecraft:spyglass")
                assert_idle(f"air_{label}")
                rows, marks, seen = run_phases(
                    [
                        {"name": "start", "input": {"rmb": True}, "ticks": 6},
                        {
                            "name": "jump",
                            "input": {
                                "forward": 1.0,
                                "sprint": sprint,
                                "jump": True,
                            },
                            "ticks": 1,
                        },
                        {
                            "name": "air",
                            "input": {
                                "forward": 1.0,
                                "sprint": sprint,
                                "rmb": True,
                            },
                            "ticks": 14,
                        },
                        {
                            "name": "land",
                            "input": {"forward": 1.0, "rmb": True},
                            "ticks": 10,
                        },
                    ],
                    probe_names=("air",),
                )
                air_rows = phase_rows(rows, marks, "air")
                summary = summarise(rows, applied(air_rows))
                summary.update(
                    {
                        "label": label,
                        "in_use": seen.get("air", {}).get("present"),
                        "air_phase": summarise(air_rows, applied(air_rows)),
                        "vy_series": [round(float(row["vy"]), 5) for row in rows],
                        "tick_series": rows,
                    }
                )
                air[label] = summary
            RESULTS["air"] = air

            # ---------------------------------------------------------- F ----
            reset()
            hold("minecraft:spyglass")
            assert_idle("tap_spam")
            phases: list[dict[str, Any]] = []
            for index in range(6):
                phases.append(
                    {
                        "name": f"on{index}",
                        "input": {"forward": 1.0, "rmb": True},
                        "ticks": 2,
                    }
                )
                phases.append({"name": f"off{index}", "input": {"forward": 1.0}, "ticks": 2})
            rows, marks, _ = run_phases(phases)
            rates = [
                (row["tick"], row["mi_z"], round(speed(row), 5))
                for row in rows
                if bool(row["input_w"])
            ]
            RESULTS["tap_spam"] = {
                "summary": summarise(rows),
                "mi_z_values": sorted(
                    {round(float(row["mi_z"]), 6) for row in rows if row["mi_z"] is not None}
                ),
                "rate_rows": [list(item) for item in rates],
                "tick_series": rows,
            }

            # ---------------------------------------------------------- G ----
            # How soon can sprint resume after the scope is released?
            reset()
            hold("minecraft:spyglass")
            assert_idle("sprint_recovery")
            rows, marks, seen = run_phases(
                [
                    {
                        "name": "scoped",
                        "input": {"rmb": True, "forward": 1.0},
                        "ticks": 12,
                    },
                    {
                        "name": "resume",
                        "input": {"forward": 1.0, "sprint": True},
                        "ticks": 16,
                    },
                ],
                probe_names=("scoped",),
            )
            resume = phase_rows(rows, marks, "resume")
            scoped = phase_rows(rows, marks, "scoped")
            first_sprint = next(
                (index for index, row in enumerate(resume) if bool(row["sprint"])), None
            )
            RESULTS["sprint_recovery"] = {
                "in_use_scoped": seen.get("scoped", {}).get("present"),
                "scoped_summary": summarise(scoped),
                "resume_summary": summarise(resume),
                "first_sprint_offset": first_sprint,
                "resume_mi_z": [row["mi_z"] for row in resume],
                "resume_sprint": [int(bool(row["sprint"])) for row in resume],
                "resume_speed": [round(speed(row), 5) for row in resume],
                "tick_series": rows,
            }

        RESULTS["ok"] = True
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(RESULTS, indent=2, sort_keys=True), encoding="utf-8")

        digest = {
            "applied_factor_table": RESULTS.get("applied_factor_table"),
            "item_use_windows": {
                label: {
                    "hold": row["in_use_during_hold"],
                    "walk": row["in_use_during_walk"],
                    "after": row["in_use_after_release"],
                }
                for label, row in RESULTS["item_matrix"].items()
            },
            "baselines": {
                "walk": RESULTS["baseline_walk"]["walk_phase"].get("ground_accel")
                or RESULTS["baseline_walk"]["walk_phase"]["first_moving_speed"],
                "air": RESULTS["baseline_air"]["air_phase"].get("air_accel"),
            },
            "ground_combos": {
                label: {
                    "factor": row["use_phase"]["modal_mi_z"],
                    "steady": row["use_phase"]["steady_speed"],
                    "accel": row["use_phase"].get("ground_accel"),
                }
                for label, row in RESULTS["ground_combos"].items()
            },
            "air": {
                label: {
                    "factor": row["air_phase"]["modal_mi_z"],
                    "accel": row["air_phase"].get("air_accel"),
                }
                for label, row in RESULTS["air"].items()
            },
            "sprint_then_scope": {
                name: {
                    "sprint_ticks": data["summary"]["sprint_ticks"],
                    "factor": data["summary"]["modal_mi_z"],
                }
                for name, data in RESULTS["sprint_then_scope"]["phases"].items()
            },
            "hotbar_switch": {
                "in_use_scoped": RESULTS["hotbar_switch"]["in_use_scoped"],
                "in_use_bread": RESULTS["hotbar_switch"]["in_use_bread"],
                "in_use_shield": RESULTS["hotbar_switch"]["in_use_shield"],
                "in_use_horn": RESULTS["hotbar_switch"]["in_use_horn"],
                "factors": {
                    name: RESULTS["hotbar_switch"][f"{name}_summary"]["modal_mi_z"]
                    for name in ("scoped", "bread", "shield", "horn")
                },
            },
            "tap_spam_mi_z": RESULTS["tap_spam"]["mi_z_values"],
            "sprint_recovery": {
                "first_sprint_offset": RESULTS["sprint_recovery"]["first_sprint_offset"],
                "resume_sprint": RESULTS["sprint_recovery"]["resume_sprint"],
                "resume_mi_z": RESULTS["sprint_recovery"]["resume_mi_z"],
            },
        }
        print(json.dumps(digest, indent=2))
        print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
