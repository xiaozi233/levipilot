"""Experiment helpers: verified building, recording sessions, tick-data fits.

These wrap :class:`ModClient` so a one-off in-game experiment does not repeat
the same footwork every time:

* building is fragile in this world (fills into unloaded chunks fail silently,
  chunk reload can roll edits back, and ``/setblock`` water never spreads
  until a neighbour update), so :class:`Builder` runs commands, reads the
  blocks back and retries, and offers :meth:`Builder.poke` to wake liquids;
* the binary tick ring resets its head on every ``record_start`` (``since`` is
  a ring position, not a game tick), so :class:`RecordSession` always dumps
  from 0 and hands back the session's entries;
* ``set_input``/``move`` return before their ticks have elapsed, so
  :func:`move_and_wait` blocks for the input window.

Analysis helpers are pure functions so they can be unit-tested offline.
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .record import RecordEntry

if TYPE_CHECKING:
    from .client import ModClient


def fnv1a(name: str) -> int:
    """FNV-1a 32 of a block type name, matching the record ring's block hashes."""
    value = 0x811C9DC5
    for byte in name.encode():
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


WATER_HASH = fnv1a("minecraft:water")
FLOWING_WATER_HASH = fnv1a("minecraft:flowing_water")
LAVA_HASH = fnv1a("minecraft:lava")
FLOWING_LAVA_HASH = fnv1a("minecraft:flowing_lava")


@dataclass
class BlockCheck:
    position: tuple[int, int, int]
    expected: str
    actual: str | None

    @property
    def ok(self) -> bool:
        return self.actual == self.expected


class Builder:
    """Command building with chunk-load awareness, readback and liquid poke."""

    def __init__(self, client: ModClient, *, settle_ticks: int = 15) -> None:
        self.client = client
        self.settle_ticks = settle_ticks

    def ensure_loaded(self, x: float, z: float, *, y: float = -58.0) -> Any:
        """Teleport near ``(x, z)`` so fills target loaded, simulating chunks."""
        self.client.teleport(float(x), float(y), float(z))
        self.client.wait_ticks(self.settle_ticks)
        return self.client.state()

    def run(self, *commands: str) -> None:
        for command in commands:
            self.client.command(command)

    def blocks(self, positions: Iterable[Sequence[int]]) -> dict[tuple[int, int, int], str]:
        payload = [list(position) for position in positions]
        reply = self.client.blocks(payload)
        return {
            (int(block["x"]), int(block["y"]), int(block["z"])): str(block["name"])
            for block in reply.get("blocks", [])
        }

    def check(self, expect: dict[Sequence[int], str]) -> list[BlockCheck]:
        expected: dict[tuple[int, int, int], str] = {}
        for position, name in expect.items():
            px, py, pz = position
            expected[(int(px), int(py), int(pz))] = name
        actual = self.blocks(expected)
        return [
            BlockCheck(position, name, actual.get(position)) for position, name in expected.items()
        ]

    def build(
        self,
        commands: Sequence[str],
        expect: dict[Sequence[int], str],
        *,
        retries: int = 2,
    ) -> list[BlockCheck]:
        """Run ``commands`` until ``expect`` reads back, re-running on mismatch.

        Unloaded chunks swallow fills silently, so the first attempt after a
        teleport may land before simulation reaches the area; the retry costs
        one settle window and usually fixes it.
        """
        checks: list[BlockCheck] = []
        for attempt in range(retries + 1):
            self.run(*commands)
            self.client.wait_ticks(self.settle_ticks)
            checks = self.check(expect)
            if all(check.ok for check in checks):
                return checks
            if attempt < retries:
                self.client.wait_ticks(self.settle_ticks)
        return checks

    def poke(self, x: int, y: int, z: int, *, settle_ticks: int = 10) -> None:
        """Force a neighbour update so command-placed liquids start flowing."""
        self.client.command(f"setblock {x} {y} {z} stone")
        self.client.wait_ticks(2)
        self.client.command(f"setblock {x} {y} {z} air")
        self.client.wait_ticks(settle_ticks)

    def flow_sheet(
        self,
        source: tuple[int, int, int],
        *,
        poke: tuple[int, int, int] | None = None,
        settle_ticks: int = 40,
    ) -> dict[tuple[int, int, int], str]:
        """Place one liquid source, poke it and return the spread row it made."""
        x, y, z = source
        self.client.command(f"setblock {x} {y} {z} water")
        self.client.wait_ticks(2)
        px, py, pz = poke if poke is not None else (x, y, z + 1)
        self.poke(px, py, pz, settle_ticks=settle_ticks)
        return self.blocks([(sx, y, z) for sx in range(x - 1, x + 9)])


class RecordSession:
    """Binary ring session with automatic cursor (``record_start`` resets head)."""

    def __init__(self, client: ModClient, *, period: int = 1) -> None:
        self.client = client
        self.period = period
        self.entries: list[RecordEntry] = []

    def __enter__(self) -> RecordSession:
        self.client.record_bin_start(period=self.period)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.client.record_bin_stop()
        self.entries = self.client.record_dump_all(since=0, chunk=8192)

    @property
    def rows(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]


def record_session(client: ModClient, *, period: int = 1) -> RecordSession:
    return RecordSession(client, period=period)


def move_and_wait(
    client: ModClient,
    forward: float = 1.0,
    strafe: float = 0.0,
    ticks: int = 20,
    *,
    sprint: bool = False,
    sneak: bool = False,
    jump: bool = False,
    settle: int = 2,
) -> None:
    """Hold an input for ``ticks`` game ticks, then return.

    ``set_input`` only queues the window; callers that record immediately need
    the wait, otherwise the recording stops while the input is still running.
    """
    client.set_input(
        forward,
        strafe,
        sprint=sprint,
        sneak=sneak,
        jump=jump,
        ticks=int(ticks),
    )
    client.wait_ticks(int(ticks) + settle)


# -- analysis -----------------------------------------------------------------


def fit_recursion(pairs: Iterable[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares fit of ``v(n+1) = a*v(n) + b``; returns (a, b, max_residual)."""
    values = list(pairs)
    if len(values) < 3:
        return (math.nan, math.nan, math.nan)
    n = len(values)
    sx = sum(x for x, _ in values)
    sy = sum(y for _, y in values)
    sxx = sum(x * x for x, _ in values)
    sxy = sum(x * y for x, y in values)
    denom = n * sxx - sx * sx
    if denom == 0:
        return (math.nan, math.nan, math.nan)
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    residual = max(abs(y - (a * x + b)) for x, y in values)
    return (a, b, residual)


def recursion(
    rows: Sequence[RecordEntry],
    attr: str,
    pred: Callable[[RecordEntry], bool] | None = None,
) -> list[tuple[float, float]]:
    """Consecutive pairs of ``attr``, optionally restricted to matching rows."""
    pairs: list[tuple[float, float]] = []
    for previous, current in itertools.pairwise(rows):
        if pred is not None and not (pred(previous) and pred(current)):
            continue
        pairs.append((float(getattr(previous, attr)), float(getattr(current, attr))))
    return pairs


def steady(rows: Sequence[RecordEntry], attr: str, n: int = 20) -> float:
    """Mean of the last ``n`` samples of ``attr``."""
    tail = rows[-n:] if n > 0 else []
    if not tail:
        return math.nan
    return sum(float(getattr(row, attr)) for row in tail) / len(tail)


def segments(
    rows: Sequence[RecordEntry],
    pred: Callable[[RecordEntry], bool],
) -> Iterator[list[RecordEntry]]:
    """Yield maximal consecutive runs of rows matching ``pred``."""
    run: list[RecordEntry] = []
    for row in rows:
        if pred(row):
            run.append(row)
        elif run:
            yield run
            run = []
    if run:
        yield run


def liquid_kind(entry: RecordEntry) -> str:
    """'water' / 'lava' / '' from the medium bits."""
    if entry.in_water:
        return "water"
    if entry.in_lava:
        return "lava"
    return ""


# -- player-faithful state drives --------------------------------------------
#
# Movement states are reproduced with the *keys a player would press* (HID key
# events), never by writing state: SWIMMING only exists when the game's own
# input pipeline puts it there, which keeps experiments on reachable ground.

ACTOR_DATA_FLAG_COMPONENT = fnv1a("ActorDataFlagComponent")
SWIMMING_FLAG_BIT = 57  # index into the ADF flag names table (SWIMMING)

KEY_W = 0x57
KEY_SPRINT = 0x11  # default sprint binding (Ctrl)
KEY_SPACE = 0x20


def ensure_protection(client: ModClient, *, seconds: int = 900) -> dict[str, Any]:
    """Fire resistance + water breathing so lava/water experiments cannot kill
    the player.

    The effects show up in ``state.effects``; keep them out of runs that assert
    an effect-free state (the selftest does).
    """
    return {
        "fire_resistance": client.command(f"effect @s fire_resistance {int(seconds)} 0 true"),
        "water_breathing": client.command(f"effect @s water_breathing {int(seconds)} 0 true"),
    }


def jump_key(client: ModClient, *, hold_ticks: int = 2) -> dict[str, Any]:
    """Tap Space exactly like a player press (needs ``allow_input_inject``).

    The selftest compares the resulting arc with the ``jump`` impulse path
    (``jump_motion_matches_space``, 1e-4 blocks).
    """
    client.key_event(KEY_SPACE, True)
    client.wait_ticks(hold_ticks)
    client.key_event(KEY_SPACE, False)
    return {"event": "jump_ok", "via": "hid"}


def double_space_fly(
    client: ModClient,
    *,
    hold_ticks: int = 1,
    gap_ticks: int = 2,
    settle: int = 6,
    attempts: int = 3,
) -> dict[str, Any]:
    """Double-tap Space and report whether the flight gate engaged.

    ``via="hardware"`` is mandatory: measured on 1.26.51.01, a Virtual (``hid``)
    press jumps but never flips flight, while the Hardware origin toggles it.
    Each press must also stay down for at least one tick -- a zero-length
    down/up pair in the same tick is dropped (measured).
    Pressed while input_lock is on: our injections carry ``mInjectingInput``,
    so they still pass while the guest's own keyboard is dropped.
    """
    flying = False
    mayfly = False
    for _ in range(max(1, attempts)):
        for _ in range(2):
            client.key_event(KEY_SPACE, True, via="hardware")
            client.wait_ticks(max(hold_ticks, 1))
            client.key_event(KEY_SPACE, False, via="hardware")
            client.wait_ticks(max(gap_ticks, 1))
        client.wait_ticks(settle)
        state = client.state()
        flying = bool(state.get("flying"))
        mayfly = bool(state.get("mayfly"))
        if flying:
            break
    return {"event": "double_space", "flying": flying, "mayfly": mayfly}


def swimming_state(client: ModClient) -> bool | None:
    """ActorDataFlag bit 57 (SWIMMING) for the local player, or None."""
    state = client.state()
    context = int(state.get("context", 0))
    if not context:
        return None
    reply = client.ecs_context(context, ACTOR_DATA_FLAG_COMPONENT)
    address = int(reply.get("address", 0) or 0)
    if not address:
        return None
    data = bytes.fromhex(str(client.mem_read(address, 24).get("hex") or ""))
    if len(data) * 8 <= SWIMMING_FLAG_BIT:
        return None
    return bool(data[SWIMMING_FLAG_BIT // 8] & (1 << (SWIMMING_FLAG_BIT % 8)))


def swim_stop(client: ModClient, *, settle: int = 10) -> dict[str, Any]:
    """Release the swim drive; SWIMMING clears within a few ticks."""
    client.set_input()  # drops forward/sprint flags and toggles sprint back off
    for code in (KEY_W, KEY_SPRINT, KEY_SPACE):
        client.key_event(code, False)
    client.wait_ticks(settle)
    return {"event": "swim_stop", "swimming": swimming_state(client)}


def swim_drive(
    client: ModClient,
    *,
    sprint: bool = True,
    timeout: float = 2.0,
    hold: bool = True,
    poll_ticks: int = 4,
) -> dict[str, Any]:
    """Enter SWIMMING the way the sprint key does: forward + sprint state.

    ``set_input(forward=1, sprint=True)`` drives the same path a held sprint key
    uses (``MoveInputComponentSprinting`` / ``MoveInputStateSprintDown``).
    Measured on 1.26.51.01: while submerged, HID W +
    Ctrl (and a double-tapped W) leave ``state.sprinting`` false, so a pure
    key-event drive never enters SWIMMING; the sprint *state* is what gates it.
    Water must be deep enough that the player is off the ground; from the bottom
    hold Space to swim up. ``hold=False`` stops right after entering (the state
    then clears); the drive is always released when the trigger does not fire.
    """
    client.set_input(forward=1.0, sprint=sprint, ticks=-1)
    entered = False
    elapsed = 0
    limit = max(poll_ticks, int(timeout * 20))
    while elapsed < limit:
        client.wait_ticks(poll_ticks)
        elapsed += poll_ticks
        if swimming_state(client):
            entered = True
            break
    if entered and hold:
        held = True
    else:
        held = False
        client.set_input()
    return {
        "event": "swim_drive",
        "entered": entered,
        "swimming": swimming_state(client),
        "ticks": elapsed,
        "held": held,
    }


def settle(
    client: ModClient,
    *,
    ticks: int = 5,
    timeout: float = 6.0,
    on_ground: bool | None = True,
    still: float | None = 1e-3,
    include_vertical: bool = True,
    in_water: bool | None = None,
    in_lava: bool | None = None,
    sprinting: bool | None = None,
    clear_input: bool = True,
) -> dict[str, Any]:
    """Release inputs, then require the requested state for N consecutive ticks.

    Every experiment step should start from a *verified* state instead of
    "probably still". ``still`` applies to ``vx``/``vz`` (and ``vy`` when
    ``include_vertical``). ``in_water`` / ``in_lava`` / ``sprinting`` are read
    from the same ``state`` reply (no extra record session), so the gate does
    not disturb the tick it is checking.

    Returns ``{"ok", "ticks", "state"}``; callers assert ``ok``.
    A liquid precondition usually needs ``on_ground=None`` and
    ``include_vertical=False`` (passive sink is not zero).
    """
    if clear_input:
        client.set_input()
        for code in (KEY_W, KEY_SPRINT, KEY_SPACE):
            client.key_event(code, False)
    deadline = time.monotonic() + timeout
    streak = 0
    last_tick: int | None = None
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = client.state()
        last = dict(state)
        tick = int(state.get("tick", -1))
        if tick == last_tick:
            time.sleep(0.01)
            continue
        last_tick = tick
        ok = True
        if on_ground is not None:
            ok = ok and bool(state.get("on_ground")) == on_ground
        if still is not None:
            ok = ok and abs(float(state.get("vx", 0.0))) <= still
            ok = ok and abs(float(state.get("vz", 0.0))) <= still
            if include_vertical:
                ok = ok and abs(float(state.get("vy", 0.0))) <= still
        if in_water is not None:
            ok = ok and bool(state.get("in_water")) == in_water
        if in_lava is not None:
            feet = str(state.get("block_feet", ""))
            head = str(state.get("block_head", ""))
            lava = feet in ("minecraft:lava", "minecraft:flowing_lava") or head in (
                "minecraft:lava",
                "minecraft:flowing_lava",
            )
            ok = ok and lava == in_lava
        if sprinting is not None:
            ok = ok and bool(state.get("sprinting")) == sprinting
        streak = streak + 1 if ok else 0
        if streak >= ticks:
            break
    return {
        "event": "settle",
        "ok": streak >= ticks,
        "ticks": streak,
        "state": last,
    }
