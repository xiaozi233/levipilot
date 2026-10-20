"""Named-pipe client for the in-game harness/probe mod.

The mod listens on ``\\\\.\\pipe\\mcbe-harness`` and speaks JSON lines.
This module exposes a small, script-friendly API plus a CLI.

Typical use::

    py -3.11 driver/transport_mod.py state
    py -3.11 driver/transport_mod.py set --forward 1 --ticks 40
    py -3.11 driver/transport_mod.py look --yaw 90 --pitch 0 --absolute
    py -3.11 driver/transport_mod.py macro load --name default
    py -3.11 driver/transport_mod.py macro run
    py -3.11 driver/transport_mod.py macro rec-start
    py -3.11 driver/transport_mod.py macro rec-stop --name my_run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

PIPE_NAME = r"\\.\pipe\mcbe-harness"


class ModPipeError(RuntimeError):
    pass


class ModClient:
    def __init__(self, pipe_name: str = PIPE_NAME, connect_timeout: float = 10.0) -> None:
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self._file = None

    def connect(self) -> "ModClient":
        deadline = time.monotonic() + self.connect_timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                self._file = open(self.pipe_name, "r+b", buffering=0)
                return self
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        raise ModPipeError(f"无法连接 {self.pipe_name}: {last_error}")

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None

    def send(self, message: dict[str, Any]) -> None:
        if self._file is None:
            raise ModPipeError("尚未连接")
        raw = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        self._file.write(raw)
        self._file.flush()

    def readline(self) -> dict[str, Any]:
        if self._file is None:
            raise ModPipeError("尚未连接")
        raw = self._file.readline()
        if not raw:
            raise ModPipeError("pipe 已关闭")
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ModPipeError(f"非法 JSON: {raw!r}") from exc

    def request(self, message: dict[str, Any], expect: str | None = None) -> dict[str, Any]:
        self.send(message)
        while True:
            reply = self.readline()
            event = reply.get("event")
            if event == "error" and expect != "error":
                raise ModPipeError(str(reply.get("reason", reply)))
            if expect is None or event == expect:
                return reply

    def ping(self) -> dict[str, Any]:
        return self.request({"cmd": "ping"}, "pong")

    def status(self) -> dict[str, Any]:
        return self.request({"cmd": "status"}, "status")

    def diag(self) -> dict[str, Any]:
        """Report which server-side objects the mod can see (teleport diagnostics)."""
        return self.request({"cmd": "diag"}, "diag")

    def command(self, command: str, player: str | None = None) -> dict[str, Any]:
        """Run a server command (``/fill``, ``/tp`` ...) on the integrated server."""
        message: dict[str, Any] = {"cmd": "command", "command": command}
        if player:
            message["player"] = player
        return self.request(message, "command_done")

    def mouse(self) -> dict[str, Any]:
        """Mouse input diagnostics (event counts per MouseAction id)."""
        return self.request({"cmd": "mouse"}, "mouse")

    def mouse_clear(self) -> dict[str, Any]:
        return self.request({"cmd": "mouse_clear"}, "mouse_cleared")

    def state(self) -> dict[str, Any]:
        return self.request({"cmd": "state"}, "state")

    def start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request({"cmd": "start", "max_ticks": int(max_ticks)}, "started")

    def stop(self) -> dict[str, Any]:
        return self.request({"cmd": "stop"}, "stopped")

    def move(self, forward: float = 0.0, strafe: float = 0.0, ticks: int = 1) -> dict[str, Any]:
        return self.request(
            {"cmd": "move", "forward": float(forward), "strafe": float(strafe), "ticks": int(ticks)},
            "moving",
        )

    def stop_move(self) -> dict[str, Any]:
        return self.request({"cmd": "stopmove"}, "stopping")

    def set_input(
        self,
        forward: float = 0.0,
        strafe: float = 0.0,
        *,
        sprint: bool = False,
        sneak: bool = False,
        jump: bool = False,
        rmb: bool = False,
        lmb: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "set",
                "forward": float(forward),
                "strafe": float(strafe),
                "sprint": bool(sprint),
                "sneak": bool(sneak),
                "jump": bool(jump),
                "rmb": bool(rmb),
                "lmb": bool(lmb),
                "ticks": int(ticks),
            },
            "set_ok",
        )

    def jump(self) -> dict[str, Any]:
        return self.request({"cmd": "jump"}, "jump_ok")

    def look(
        self,
        yaw: float,
        pitch: float = 0.0,
        *,
        relative: bool = False,
        ticks: int = 1,
    ) -> dict[str, Any]:
        return self.request(
            {
                "cmd": "look",
                "yaw": float(yaw),
                "pitch": float(pitch),
                "relative": bool(relative),
                "ticks": int(ticks),
            },
            "look_ok",
        )

    def entities(self, radius: float = 16.0, limit: int = 64) -> dict[str, Any]:
        self.send({"cmd": "entities", "radius": float(radius), "limit": int(limit)})
        while True:
            reply = self.readline()
            if reply.get("event") == "entities":
                return reply

    def blocks(self, positions: Iterable[Iterable[float]]) -> dict[str, Any]:
        self.send({"cmd": "blocks", "positions": [list(p) for p in positions]})
        while True:
            reply = self.readline()
            if reply.get("event") == "blocks":
                return reply

    def select_slot(self, slot: int) -> dict[str, Any]:
        return self.request({"cmd": "select_slot", "slot": int(slot)}, "select_slot_ok")

    def inventory(self) -> dict[str, Any]:
        """Full inventory snapshot: slots, armor, offhand, selected slot."""
        return self.request({"cmd": "inventory"}, "inventory")

    def set_item(
        self,
        slot: int,
        item: str,
        *,
        count: int = 1,
        aux: int = 0,
        container: str = "inventory",
    ) -> dict[str, Any]:
        """Set one slot. ``count=0`` clears it. container: inventory/armor/offhand."""
        return self.request(
            {
                "cmd": "set_item",
                "slot": int(slot),
                "item": item,
                "count": int(count),
                "aux": int(aux),
                "container": container,
            },
            "inventory_set",
        )

    def give(self, item: str, *, count: int = 1, aux: int = 0) -> dict[str, Any]:
        return self.request(
            {"cmd": "give", "item": item, "count": int(count), "aux": int(aux)},
            "inventory_given",
        )

    def clear_item(self, slot: int, *, container: str = "inventory") -> dict[str, Any]:
        return self.request(
            {"cmd": "clear_item", "slot": int(slot), "container": container},
            "inventory_set",
        )

    def clear_inventory(self) -> dict[str, Any]:
        return self.request({"cmd": "clear_inventory"}, "inventory_cleared")

    def entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request({"cmd": "entity", "runtime_id": int(runtime_id)}, "entity_info")

    def attack_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "attack_entity", "runtime_id": int(runtime_id)}, "entity_attacked"
        )

    def interact_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "interact_entity", "runtime_id": int(runtime_id)}, "entity_interacted"
        )

    def look_at_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.request(
            {"cmd": "look_at_entity", "runtime_id": int(runtime_id)}, "look_at_done"
        )

    def click(self, button: str = "left", reach: float = 5.5) -> dict[str, Any]:
        return self.request(
            {"cmd": "click", "button": button, "reach": float(reach)}, "click_result"
        )

    def fly(self, enabled: bool = True) -> dict[str, Any]:
        return self.request({"cmd": "fly", "enabled": bool(enabled)}, "fly_ok")

    def teleport(
        self,
        x: float,
        y: float,
        z: float,
        *,
        yaw: float | None = None,
        pitch: float | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "teleport", "x": float(x), "y": float(y), "z": float(z)}
        if yaw is not None:
            message["yaw"] = float(yaw)
        if pitch is not None:
            message["pitch"] = float(pitch)
        return self.request(message, "teleported")

    def macro_load(self, name: str = "default", path: str | None = None) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "macro_load", "name": name}
        if path is not None:
            message["path"] = path
        return self.request(message, "macro_loaded")

    def macro_run(self, *, start: int = 0, end: int | None = None, loop: bool = False) -> dict[str, Any]:
        message: dict[str, Any] = {"cmd": "macro_run", "start": int(start), "loop": bool(loop)}
        if end is not None:
            message["end"] = int(end)
        return self.request(message, "macro_started")

    def macro_stop(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_stop"}, "macro_stopped")

    def macro_status(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_status"}, "macro_status")

    def macro_list(self) -> dict[str, Any]:
        return self.request({"cmd": "macro_list"}, "macro_list")

    def macro_record_start(self, max_ticks: int = 0) -> dict[str, Any]:
        return self.request(
            {"cmd": "macro_rec_start", "max_ticks": int(max_ticks)}, "macro_rec_started"
        )

    def macro_record_stop(self, name: str = "recorded") -> dict[str, Any]:
        return self.request({"cmd": "macro_rec_stop", "name": name}, "macro_rec_saved")

    def macro_save(self, name: str, ticks: list[list[str]]) -> dict[str, Any]:
        return self.request({"cmd": "macro_save", "name": name, "ticks": ticks}, "macro_saved")

    def record(
        self,
        max_ticks: int,
        on_sample: Callable[[dict[str, Any]], None] | None = None,
        move_forward: float = 0.0,
        move_strafe: float = 0.0,
        move_ticks: int = 0,
    ) -> list[dict[str, Any]]:
        self.start(max_ticks)
        if move_ticks:
            self.send(
                {
                    "cmd": "move",
                    "forward": move_forward,
                    "strafe": move_strafe,
                    "ticks": move_ticks,
                }
            )
        samples: list[dict[str, Any]] = []
        while True:
            message = self.readline()
            event = message.get("event")
            if event == "error":
                raise ModPipeError(str(message.get("reason", message)))
            if event in {"moving", "stopping"}:
                continue
            if event == "tick":
                samples.append(message)
                if on_sample is not None:
                    on_sample(message)
            elif event == "stopped":
                break
        return samples

    def __enter__(self) -> "ModClient":
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()


class Harness:
    """Convenience wrapper for LLM/script use.

    Example::

        with Harness() as h:
            print(h.state()["x"], h.state()["z"])
            h.walk(forward=1.0, ticks=40)
            print(h.state()["x"], h.state()["z"])
    """

    def __init__(self, pipe_name: str = PIPE_NAME) -> None:
        self.client = ModClient(pipe_name).connect()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def state(self) -> dict[str, Any]:
        return self.client.state()

    def entities(self, radius: float = 16.0, limit: int = 64) -> list[dict[str, Any]]:
        return list(self.client.entities(radius, limit).get("entities", []))

    def inventory(self) -> dict[str, Any]:
        return self.client.inventory()

    def give(self, item: str, count: int = 1, aux: int = 0) -> dict[str, Any]:
        return self.client.give(item, count=count, aux=aux)

    def entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.entity(runtime_id)

    def attack(self, runtime_id: int) -> dict[str, Any]:
        return self.client.attack_entity(runtime_id)

    def interact(self, runtime_id: int) -> dict[str, Any]:
        return self.client.interact_entity(runtime_id)

    def look_at_entity(self, runtime_id: int) -> dict[str, Any]:
        return self.client.look_at_entity(runtime_id)

    def command(self, command: str) -> dict[str, Any]:
        return self.client.command(command)

    def walk(
        self,
        forward: float = 1.0,
        strafe: float = 0.0,
        ticks: int = 20,
        *,
        sprint: bool = False,
        sneak: bool = False,
    ) -> dict[str, Any]:
        return self.client.set_input(forward, strafe, sprint=sprint, sneak=sneak, ticks=ticks)

    def stop(self) -> dict[str, Any]:
        return self.client.stop_move()

    def jump(self) -> dict[str, Any]:
        return self.client.jump()

    def look_at(self, yaw: float, pitch: float = 0.0, *, ticks: int = 1) -> dict[str, Any]:
        return self.client.look(yaw, pitch, relative=False, ticks=ticks)

    def turn(self, yaw_delta: float, pitch_delta: float = 0.0, *, ticks: int = 1) -> dict[str, Any]:
        return self.client.look(yaw_delta, pitch_delta, relative=True, ticks=ticks)

    def play_macro(self, name: str | None = None, *, loop: bool = False) -> dict[str, Any]:
        if name is not None:
            self.client.macro_load(name)
        return self.client.macro_run(loop=loop)

    def stop_macro(self) -> dict[str, Any]:
        return self.client.macro_stop()

    def record_macro(self, name: str, *, max_ticks: int = 0) -> dict[str, Any]:
        self.client.macro_record_start(max_ticks)
        input("recording... press Enter to stop")
        return self.client.macro_record_stop(name)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default=PIPE_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping")
    sub.add_parser("status")
    sub.add_parser("diag")
    sub.add_parser("state")

    command = sub.add_parser("command", help="run a server command (fill/tp/...)")
    command.add_argument("text")

    sub.add_parser("mouse")
    sub.add_parser("mouse-clear")

    entities = sub.add_parser("entities")
    entities.add_argument("--radius", type=float, default=16.0)
    entities.add_argument("--limit", type=int, default=64)

    blocks = sub.add_parser("blocks")
    blocks.add_argument("positions", nargs="+", type=float, help="x y z [x y z ...]")

    slot = sub.add_parser("slot")
    slot.add_argument("slot", type=int)

    sub.add_parser("inventory")

    give = sub.add_parser("give")
    give.add_argument("item")
    give.add_argument("--count", type=int, default=1)
    give.add_argument("--aux", type=int, default=0)

    set_item = sub.add_parser("set-item")
    set_item.add_argument("slot", type=int)
    set_item.add_argument("item")
    set_item.add_argument("--count", type=int, default=1)
    set_item.add_argument("--aux", type=int, default=0)
    set_item.add_argument("--container", default="inventory", choices=["inventory", "armor", "offhand"])

    clear_item = sub.add_parser("clear-item")
    clear_item.add_argument("slot", type=int)
    clear_item.add_argument("--container", default="inventory", choices=["inventory", "armor", "offhand"])

    sub.add_parser("clear-inventory")

    entity = sub.add_parser("entity")
    entity.add_argument("runtime_id", type=int)

    attack = sub.add_parser("attack")
    attack.add_argument("runtime_id", type=int)

    interact = sub.add_parser("interact")
    interact.add_argument("runtime_id", type=int)

    look_entity = sub.add_parser("look-entity")
    look_entity.add_argument("runtime_id", type=int)

    click = sub.add_parser("click")
    click.add_argument("button", choices=["left", "right"])
    click.add_argument("--reach", type=float, default=5.5)

    fly = sub.add_parser("fly")
    fly.add_argument("--off", action="store_true")

    teleport = sub.add_parser("teleport")
    teleport.add_argument("x", type=float)
    teleport.add_argument("y", type=float)
    teleport.add_argument("z", type=float)
    teleport.add_argument("--yaw", type=float)
    teleport.add_argument("--pitch", type=float)

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

    sub.add_parser("jump")
    sub.add_parser("stopmove")

    record = sub.add_parser("record")
    record.add_argument("--ticks", type=int, default=40)
    record.add_argument("--out", type=Path, default=None)
    record.add_argument("--move-forward", type=float, default=0.0)
    record.add_argument("--move-strafe", type=float, default=0.0)
    record.add_argument("--move-ticks", type=int, default=0)

    _add_macro_parser(sub)

    args = parser.parse_args(argv)

    try:
        with ModClient(args.pipe) as client:
            if args.command == "ping":
                print(json.dumps(client.ping(), ensure_ascii=False))
            elif args.command == "status":
                print(json.dumps(client.status(), ensure_ascii=False))
            elif args.command == "diag":
                print(json.dumps(client.diag(), ensure_ascii=False))
            elif args.command == "command":
                print(json.dumps(client.command(args.text), ensure_ascii=False))
            elif args.command == "mouse":
                print(json.dumps(client.mouse(), ensure_ascii=False))
            elif args.command == "mouse-clear":
                print(json.dumps(client.mouse_clear(), ensure_ascii=False))
            elif args.command == "state":
                print(json.dumps(client.state(), ensure_ascii=False, indent=2))
            elif args.command == "entities":
                print(json.dumps(client.entities(args.radius, args.limit), ensure_ascii=False, indent=2))
            elif args.command == "blocks":
                if len(args.positions) % 3:
                    parser.error("blocks 需要 3 的倍数个坐标")
                positions = [args.positions[i : i + 3] for i in range(0, len(args.positions), 3)]
                print(json.dumps(client.blocks(positions), ensure_ascii=False, indent=2))
            elif args.command == "slot":
                print(json.dumps(client.select_slot(args.slot)))
            elif args.command == "inventory":
                print(json.dumps(client.inventory(), ensure_ascii=False, indent=2))
            elif args.command == "give":
                print(
                    json.dumps(
                        client.give(args.item, count=args.count, aux=args.aux),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "set-item":
                print(
                    json.dumps(
                        client.set_item(
                            args.slot,
                            args.item,
                            count=args.count,
                            aux=args.aux,
                            container=args.container,
                        ),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "clear-item":
                print(
                    json.dumps(
                        client.clear_item(args.slot, container=args.container),
                        ensure_ascii=False,
                    )
                )
            elif args.command == "clear-inventory":
                print(json.dumps(client.clear_inventory(), ensure_ascii=False))
            elif args.command == "entity":
                print(json.dumps(client.entity(args.runtime_id), ensure_ascii=False, indent=2))
            elif args.command == "attack":
                print(json.dumps(client.attack_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "interact":
                print(json.dumps(client.interact_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "look-entity":
                print(json.dumps(client.look_at_entity(args.runtime_id), ensure_ascii=False))
            elif args.command == "click":
                print(json.dumps(client.click(args.button, args.reach)))
            elif args.command == "fly":
                print(json.dumps(client.fly(not args.off)))
            elif args.command == "teleport":
                print(json.dumps(client.teleport(args.x, args.y, args.z, yaw=args.yaw, pitch=args.pitch)))
            elif args.command == "set":
                print(
                    json.dumps(
                        client.set_input(
                            args.forward,
                            args.strafe,
                            sprint=args.sprint,
                            sneak=args.sneak,
                            jump=args.jump,
                            rmb=args.rmb,
                            lmb=args.lmb,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "look":
                print(
                    json.dumps(
                        client.look(
                            args.yaw,
                            args.pitch,
                            relative=not args.absolute,
                            ticks=args.ticks,
                        )
                    )
                )
            elif args.command == "jump":
                print(json.dumps(client.jump()))
            elif args.command == "stopmove":
                print(json.dumps(client.stop_move()))
            elif args.command == "record":
                samples = client.record(
                    args.ticks,
                    move_forward=args.move_forward,
                    move_strafe=args.move_strafe,
                    move_ticks=args.move_ticks,
                )
                if args.out is not None:
                    _write_jsonl(args.out, samples)
                print(f"samples={len(samples)}")
                if samples:
                    print(json.dumps(samples[0], ensure_ascii=False))
                    print(json.dumps(samples[-1], ensure_ascii=False))
            elif args.command == "macro":
                if args.macro_command == "load":
                    print(json.dumps(client.macro_load(args.name, args.path), ensure_ascii=False))
                elif args.macro_command == "run":
                    print(json.dumps(client.macro_run(start=args.start, end=args.end, loop=args.loop)))
                elif args.macro_command == "stop":
                    print(json.dumps(client.macro_stop()))
                elif args.macro_command == "status":
                    print(json.dumps(client.macro_status(), ensure_ascii=False, indent=2))
                elif args.macro_command == "list":
                    print(json.dumps(client.macro_list(), ensure_ascii=False, indent=2))
                elif args.macro_command == "rec-start":
                    print(json.dumps(client.macro_record_start(args.max_ticks)))
                elif args.macro_command == "rec-stop":
                    print(json.dumps(client.macro_record_stop(args.name)))
    except ModPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("提示：游戏内是否已加载 probe/harness mod，并且已经进入存档？", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())