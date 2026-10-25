"""CLI argument definitions, preserving command and help order."""

from __future__ import annotations

import argparse
from pathlib import Path

from .protocol import PIPE_NAME


def _add_macro_parser(parent: argparse._SubParsersAction) -> None:
    macro = parent.add_parser("macro", help="macro file playback/recording")
    sub = macro.add_subparsers(dest="macro_command", required=True)

    load = sub.add_parser("load")
    load.add_argument("--name", default="default")
    load.add_argument("--path")

    run = sub.add_parser("run")
    run.add_argument("--start", type=int, default=0)
    run.add_argument("--end", type=int)
    run.add_argument("--loop", action="store_true")

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("list")

    rec_start = sub.add_parser("rec-start")
    rec_start.add_argument("--max-ticks", type=int, default=0)

    rec_stop = sub.add_parser("rec-stop")
    rec_stop.add_argument("--name", default="recorded")


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--pipe", default=PIPE_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("diag")
    sub.add_parser("diagnose")
    profile_dump = sub.add_parser("profile-dump", help="dump the live version profile")
    profile_dump.add_argument("--write", help="write the saveable profile JSON to this path")
    profile_probe = sub.add_parser("profile-probe", help="which profile a build id would select")
    profile_probe.add_argument("--build-id", required=True)
    signature_check = sub.add_parser("signature-check")
    signature_check.add_argument("--name", required=True)
    signature_check.add_argument("--expected-hex", required=True)
    sub.add_parser("state")
    chat_history = sub.add_parser("chat-history", help="read retained client chat messages")
    chat_history.add_argument("--limit", type=int, default=32)

    command = sub.add_parser("command", help="run a server command (fill/tp/...)")
    command.add_argument("text")

    sub.add_parser("mouse")
    sub.add_parser("mouse-clear")

    key_event = sub.add_parser("key-event", help="inject a keyboard event (allow_input_inject)")
    key_event.add_argument("code", type=int)
    key_event.add_argument("--up", action="store_true", help="send key up instead of down")
    key_event.add_argument("--repeat", action="store_true")
    key_event.add_argument("--repeat-ms", type=int, default=0)

    key_state = sub.add_parser("key-state", help="read the game-side key state")
    key_state.add_argument("code", type=int)

    sub.add_parser("screen", help="current game screen name")

    packet_capture = sub.add_parser("packet-capture", help="toggle the packet identity tap")
    packet_capture.add_argument("--off", action="store_true")
    packet_capture.add_argument("--ids", type=int, nargs="*", default=None)
    packet_capture.add_argument("--sample", type=int, default=1)
    packet_capture.add_argument(
        "--directions",
        nargs="*",
        choices=["send", "recv"],
        default=None,
        help="tap directions; default is send only",
    )
    packet_capture.add_argument("--serialize-ids", type=int, nargs="*", default=None)

    packet_drain = sub.add_parser("packet-drain")
    packet_drain.add_argument("--since", type=int, default=0)
    packet_drain.add_argument("--limit", type=int, default=1024)

    packet_bytes = sub.add_parser("packet-bytes")
    packet_bytes.add_argument("--since", type=int, default=0)
    packet_bytes.add_argument("--limit", type=int, default=256)

    sub.add_parser("packet-stats")

    packet_decode = sub.add_parser("packet-decode", help="decode a packet payload hex")
    packet_decode.add_argument("hex")
    packet_decode.add_argument("--id", type=int, required=True)

    mouse_event = sub.add_parser("mouse-event", help="inject a mouse event (allow_input_inject)")
    mouse_event.add_argument("action")
    mouse_event.add_argument("--data", default=None)
    mouse_event.add_argument("--up", action="store_true")
    mouse_event.add_argument("--down", action="store_true")
    mouse_event.add_argument("--dx", type=int, default=0)
    mouse_event.add_argument("--dy", type=int, default=0)
    mouse_event.add_argument("--x", type=int, default=None)
    mouse_event.add_argument("--y", type=int, default=None)

    mem_read = sub.add_parser("mem-read", help="read process memory (hex output)")
    mem_read.add_argument("addr")
    mem_read.add_argument("--len", type=int, default=16)

    mem_write = sub.add_parser("mem-write", help="write process memory (allow_write)")
    mem_write.add_argument("addr")
    mem_write.add_argument("hex")

    probe = sub.add_parser("probe", help="structured memory probe (raw/block/legacy)")
    probe.add_argument("addr")
    probe.add_argument("--kind", default="raw", choices=["raw", "block", "legacy"])
    probe.add_argument("--len", type=int, default=16)

    watch_set = sub.add_parser("watch-set", help="install a hardware watchpoint")
    watch_set.add_argument("addr")
    watch_set.add_argument("--size", type=int, default=4, choices=[1, 2, 4, 8])
    watch_set.add_argument("--mode", default="w", choices=["w", "rw"])
    watch_set.add_argument("--thread", default="tick")
    watch_set.add_argument("--tag", default="")

    watch_clear = sub.add_parser("watch-clear")
    watch_clear.add_argument("--id", type=int)
    watch_clear.add_argument("--all", action="store_true")

    sub.add_parser("watch-list")

    watch_drain = sub.add_parser("watch-drain")
    watch_drain.add_argument("--since", type=int, default=0)
    watch_drain.add_argument("--limit", type=int, default=4096)

    hook_install = sub.add_parser("hook-install", help="install an enter hook")
    hook_install.add_argument("--symbol", default="MobJump::setJumpPendingScale")
    hook_install.add_argument("--va")
    hook_install.add_argument("--tag", default="")
    hook_install.add_argument("--sample", type=int, default=1)
    hook_install.add_argument("--dry-run", action="store_true")

    hook_rm = sub.add_parser("hook-rm")
    hook_rm.add_argument("--id", type=int, required=True)
    sub.add_parser("hook-list")

    hook_drain = sub.add_parser("hook-drain")
    hook_drain.add_argument("--since", type=int, default=0)
    hook_drain.add_argument("--limit", type=int, default=4096)

    hook_call = sub.add_parser("hook-call", help="call a hooked symbol for verification")
    hook_call.add_argument("--symbol", default="MobJump::setJumpPendingScale")
    hook_call.add_argument("--scale", type=float, default=1.0)

    panic = sub.add_parser("panic", help="remove hooks/watches and cancel tasks")
    panic.add_argument("--level", default="all", choices=["hooks", "tasks", "all"])

    sub.add_parser("overhead", help="tick/hook timing percentiles")
    busy_test = sub.add_parser("busy-test", help="burn microseconds in the tick")
    busy_test.add_argument("--us", type=int, default=1000)
    busy_test.add_argument("--ticks", type=int, default=20)

    sub.add_parser("spool-flush")
    sub.add_parser("selftest-av")

    spool = sub.add_parser("spool", help="summarize a spool jsonl file (works after a crash)")
    spool.add_argument("path", type=Path)
    spool.add_argument("--tail", type=int, default=10)
    spool.add_argument("--all", action="store_true", help="print every row")

    entities = sub.add_parser("entities")
    entities.add_argument("--radius", type=float, default=16.0)
    entities.add_argument("--limit", type=int, default=64)

    blocks = sub.add_parser("blocks")
    blocks.add_argument("positions", nargs="+", type=float, help="x y z [x y z ...]")

    scan = sub.add_parser("scan", help="scan an inclusive block region")
    scan.add_argument("coords", nargs=6, type=float, metavar=("X1", "Y1", "Z1", "X2", "Y2", "Z2"))
    scan.add_argument("--include-air", action="store_true")
    scan.add_argument("--limit", type=int, default=4096)

    checkpoint = sub.add_parser("checkpoint", help="named feet poses persisted in the mod dir")
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)
    checkpoint_save = checkpoint_sub.add_parser("save")
    checkpoint_save.add_argument("name")
    checkpoint_load = checkpoint_sub.add_parser("load")
    checkpoint_load.add_argument("name")
    checkpoint_sub.add_parser("list")
    checkpoint_delete = checkpoint_sub.add_parser("delete")
    checkpoint_delete.add_argument("name")

    slot = sub.add_parser("slot")
    slot.add_argument("slot", type=int)

    sub.add_parser("inventory")

    give = sub.add_parser("give")
    give.add_argument("item")
    give.add_argument("--count", type=int, default=1)
    give.add_argument("--aux", type=int, default=0)

    equip = sub.add_parser("equip", help="move a held/inventory stack into a worn slot")
    equip.add_argument("target", choices=["head", "torso", "legs", "feet", "offhand"])
    equip.add_argument("--slot", type=int, default=-1, help="inventory slot (-1 = selected)")

    set_item = sub.add_parser("set-item")
    set_item.add_argument("slot", type=int)
    set_item.add_argument("item")
    set_item.add_argument("--count", type=int, default=1)
    set_item.add_argument("--aux", type=int, default=0)
    set_item.add_argument(
        "--container", default="inventory", choices=["inventory", "armor", "offhand"]
    )

    clear_item = sub.add_parser("clear-item")
    clear_item.add_argument("slot", type=int)
    clear_item.add_argument(
        "--container", default="inventory", choices=["inventory", "armor", "offhand"]
    )

    sub.add_parser("clear-inventory")

    entity = sub.add_parser("entity")
    entity.add_argument("runtime_id", type=int)

    attack = sub.add_parser("attack")
    attack.add_argument("runtime_id", type=int)

    interact = sub.add_parser("interact")
    interact.add_argument("runtime_id", type=int)

    look_entity = sub.add_parser("look-entity")
    look_entity.add_argument("runtime_id", type=int)

    sub.add_parser("ecs-registries")
    ecs_entity = sub.add_parser("ecs-entity", help="entity/registry/context addresses")
    ecs_entity.add_argument("--runtime-id", type=int, required=True)
    ecs_components = sub.add_parser("ecs-components", help="runtime component IDs and strides")
    ecs_components.add_argument("--runtime-id", type=int, required=True)
    ecs_component = sub.add_parser("ecs-component", help="read raw component bytes")
    ecs_component.add_argument("--runtime-id", type=int, required=True)
    ecs_component.add_argument("--component-id", type=int, required=True)
    ecs_component.add_argument("--len", type=int, default=0)

    click = sub.add_parser("click")
    click.add_argument("button", choices=["left", "right"])
    click.add_argument("--reach", type=float, default=5.5)

    navigate = sub.add_parser("navigate", help="closed-loop walk to x,z")
    navigate.add_argument("x", type=float)
    navigate.add_argument("z", type=float)
    navigate.add_argument("--tolerance", type=float, default=0.35)
    navigate.add_argument("--sprint", action="store_true")
    navigate.add_argument("--max-ticks", type=int, default=600)
    navigate.add_argument("--jump", default="auto", choices=["auto", "distance", "edge", "never"])
    navigate.add_argument("--jump-distance", type=float, default=1.0)
    navigate.add_argument("--edge-lookahead", type=float, default=0.55)

    jump_when = sub.add_parser("jump-when", help="jump at a distance/edge/tick")
    jump_when.add_argument("--mode", default="now", choices=["now", "tick", "distance", "edge"])
    jump_when.add_argument("--x", type=float)
    jump_when.add_argument("--z", type=float)
    jump_when.add_argument("--distance", type=float, default=0.6)
    jump_when.add_argument("--forward", type=float, default=0.0)
    jump_when.add_argument("--sprint", action="store_true")
    jump_when.add_argument("--yaw", type=float)
    jump_when.add_argument("--after-ticks", type=int, default=1)
    jump_when.add_argument("--max-ticks", type=int, default=60)

    route = sub.add_parser("route", help="run a waypoint route inside the mod")
    route.add_argument("waypoints", help="JSON array or a path to a JSON file")
    route.add_argument("--sprint", action="store_true")

    mine = sub.add_parser("mine", help="break one block")
    mine.add_argument("x", type=int)
    mine.add_argument("y", type=int)
    mine.add_argument("z", type=int)
    mine.add_argument("--max-ticks", type=int, default=400)

    place = sub.add_parser("place", help="place the held block into x,y,z")
    place.add_argument("x", type=int)
    place.add_argument("y", type=int)
    place.add_argument("z", type=int)
    place.add_argument("--against", nargs=3, type=int, metavar=("AX", "AY", "AZ"))
    place.add_argument("--face", type=int, choices=range(6))
    place.add_argument("--expected")
    place.add_argument("--slot", type=int)
    place.add_argument("--max-ticks", type=int, default=30)

    use = sub.add_parser("use", help="hold and release the held item")
    use.add_argument("--ticks", type=int, default=32)

    sub.add_parser("task-status")
    sub.add_parser("task-stop")

    sub.add_parser("worlds")
    sub.add_parser("signin", help="Xbox Live sign-in state at the main menu")
    enter_world = sub.add_parser("enter-world", help="start an existing world without the menu UI")
    enter_world.add_argument("--name")
    enter_world.add_argument("--id")
    enter_world.add_argument("--intent", type=int, default=0)
    enter_world.add_argument("--force", action="store_true", help="skip the sign-in gate")
    enter_world.add_argument(
        "--wait",
        action="store_true",
        help="wait for enter_world_settled and retry the sign-in race",
    )
    sub.add_parser("leave-world")

    options_set = sub.add_parser("options-set", help="apply option save-tag values")
    options_set.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    options_set.add_argument("--text", help="raw options.txt-style key:value lines")
    sub.add_parser("options-save")

    fly = sub.add_parser("fly")
    fly.add_argument("--off", action="store_true")

    teleport = sub.add_parser("teleport")
    teleport.add_argument("x", type=float)
    teleport.add_argument("y", type=float)
    teleport.add_argument("z", type=float)
    teleport.add_argument("--yaw", type=float)
    teleport.add_argument("--pitch", type=float)

    reset = sub.add_parser("reset", help="clear tasks/input and teleport to a feet pose")
    reset.add_argument("x", type=float)
    reset.add_argument("y", type=float)
    reset.add_argument("z", type=float)
    reset.add_argument("--yaw", type=float)
    reset.add_argument("--pitch", type=float)

    set_input = sub.add_parser("set")
    set_input.add_argument("--forward", type=float, default=0.0)
    set_input.add_argument("--strafe", type=float, default=0.0)
    set_input.add_argument("--sprint", action="store_true")
    set_input.add_argument("--sneak", action="store_true")
    set_input.add_argument("--jump", action="store_true")
    set_input.add_argument("--rmb", action="store_true")
    set_input.add_argument("--lmb", action="store_true")
    set_input.add_argument("--ticks", type=int, default=1)

    look = sub.add_parser("look")
    look.add_argument("--yaw", type=float, required=True)
    look.add_argument("--pitch", type=float, default=0.0)
    look.add_argument("--absolute", action="store_true", help="treat yaw/pitch as absolute")
    look.add_argument("--ticks", type=int, default=1)

    input_cfg = sub.add_parser("input-cfg", help="configure synthetic input routing")
    input_cfg.add_argument("--source", type=int)
    input_cfg.add_argument("--controller", type=int)
    input_cfg.add_argument("--focus", type=int)
    input_cfg.add_argument("--mode", type=int)
    input_cfg.add_argument(
        "--jump-impulse",
        type=int,
        choices=(0, 1, 2, 3),
        help="1 = jumpFromGround (default), 3 = +setJumpPendingScale; 0/2 are the measured-broken takeoff paths",
    )
    input_cfg.add_argument(
        "--force",
        action="store_true",
        help="allow the measured-broken jump impulse modes 0/2",
    )

    sub.add_parser("jump")
    sub.add_parser("jump-key", help="tap the Space binding (needs allow_input_inject)")
    togglefly = sub.add_parser("togglefly", help="grant/remove double-space flight (non-spectator)")
    togglefly_mode = togglefly.add_mutually_exclusive_group()
    togglefly_mode.add_argument("--on", action="store_true")
    togglefly_mode.add_argument("--off", action="store_true")
    input_lock = sub.add_parser("input-lock", help="drop real hardware keys/mouse")
    input_lock.add_argument("--off", action="store_true", help="release the lock (default: on)")
    sub.add_parser("stopmove")

    swim = sub.add_parser("swim", help="enter/leave SWIMMING with real key events")
    swim_mode = swim.add_mutually_exclusive_group()
    swim_mode.add_argument(
        "--drive",
        action="store_true",
        help="hold W (+ sprint key) until ActorDataFlag bit57 (SWIMMING) is set",
    )
    swim_mode.add_argument("--stop", action="store_true", help="release the swim keys")
    swim_mode.add_argument("--status", action="store_true", help="report SWIMMING (default)")
    swim.add_argument("--no-sprint", action="store_true", help="drive without the sprint key")
    swim.add_argument(
        "--release", action="store_true", help="release the keys right after entering"
    )
    swim.add_argument("--timeout", type=float, default=2.0)

    record = sub.add_parser("record")
    record.add_argument("--ticks", type=int, default=40)
    record.add_argument("--out", type=Path, default=None)
    record.add_argument("--move-forward", type=float, default=0.0)
    record.add_argument("--move-strafe", type=float, default=0.0)
    record.add_argument("--move-ticks", type=int, default=0)

    record_start = sub.add_parser("record-start", help="start the binary tick ring")
    record_start.add_argument("--period", type=int, default=1)
    sub.add_parser("record-stop", help="stop the binary tick ring")

    record_dump = sub.add_parser("record-dump", help="dump the binary tick ring")
    record_dump.add_argument("--since", type=int, default=0)
    record_dump.add_argument("--limit", type=int, default=1024)
    record_dump.add_argument("--format", default="jsonl", choices=["jsonl", "bin", "hex"])
    record_dump.add_argument("--all", action="store_true", help="drain up to the head")
    record_dump.add_argument("--out", type=Path)

    _add_macro_parser(sub)
    return parser
