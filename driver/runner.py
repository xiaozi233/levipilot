"""Execute a normalized scenario against an agent-like object.

The agent is duck-typed so the runner can be unit-tested without the game:

    call(name, *args) -> Any
    wait_ticks(ticks) -> int
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from driver.scenario import Scenario, Step


class AgentLike(Protocol):
    def call(self, name: str, *args: Any) -> Any: ...

    def wait_ticks(self, ticks: int, *, timeout: float | None = None) -> int: ...


class RunError(RuntimeError):
    pass


@dataclass
class Mark:
    index: int
    type: str
    start_tick: int
    end_tick: int
    result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.type,
            "start_tick": self.start_tick,
            "end_tick": self.end_tick,
            "result": self.result,
        }


@dataclass
class RunResult:
    scenario: Scenario
    marks: list[Mark]
    recording: dict[str, Any]
    start_state: dict[str, Any]
    end_state: dict[str, Any]
    started_at: str
    finished_at: str
    warnings: list[str] = field(default_factory=list)


def _ok(result: Any, where: str) -> Any:
    if isinstance(result, dict) and result.get("ok") is False:
        raise RunError(f"{where}: {result.get('error')}")
    return result


def execute_step(agent: AgentLike, step: Step, command_sender=None) -> Any:
    p = step.params
    if step.type == "tp":
        mode = p["mode"]
        if mode == "chat":
            if command_sender is not None:
                command_sender(f"tp @s {p['pos'][0]} {p['pos'][1]} {p['pos'][2]}")
                return {"ok": True, "via": "chat"}
            mode = "server"
        result = _ok(agent.call("tp", p["pos"][0], p["pos"][1], p["pos"][2], mode), "tp")
        if mode != "server" and isinstance(result, dict):
            result = dict(result)
            result.setdefault(
                "warning",
                "客户端写位置会被服务端权威位置覆盖；默认 tp 走服务端 registry",
            )
        return result
    if step.type == "look":
        yaw, pitch = p["yaw"], p["pitch"]
        mode = p.get("mode") or ("command" if command_sender is not None else "memory")
        if mode == "command":
            if command_sender is None:
                raise RunError("look mode=command 需要命令通道（--command-channel none？）")
            # 记忆里改写 ActorRotation 组件不管用：游戏每帧从相机写回，服务端
            # 仍按真实朝向解释移动输入，两侧世界方向会分叉（表现为橡皮筋）。
            # 只有走服务端的 /tp 才能同时改掉客户端与服务端的权威朝向。
            command_sender(f"tp @s ~ ~ ~ {yaw} {pitch}")
            agent.call("clear_look")
            agent.wait_ticks(3)
            return {"ok": True, "via": "chat", "yaw": yaw, "pitch": pitch}
        result = _ok(agent.call("set_yaw_pitch", yaw, pitch), "look")
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault(
                "warning",
                "look mode=memory 只改组件，服务端仍按真实朝向移动（可能产生橡皮筋）；"
                "建议使用命令通道",
            )
        return result
    if step.type == "vel":
        return _ok(agent.call("set_vel", p["v"][0], p["v"][1], p["v"][2]), "vel")
    if step.type == "input":
        if p.get("world") is not None:
            return _ok(
                agent.call("move_world", p["world"][0], p["world"][1]),
                "input(world)",
            )
        payload = {
            "forward": p["forward"],
            "strafe": p["strafe"],
            "jump": p["jump"],
            "sneak": p["sneak"],
            "sprint": p["sprint"],
        }
        return _ok(agent.call("set_input", payload), "input")
    if step.type == "jump":
        return _ok(agent.call("jump"), "jump")
    if step.type == "wait":
        return {"ok": True}
    if step.type == "stop":
        return _ok(agent.call("stop_input"), "stop")
    raise RunError(f"unknown step type {step.type!r}")


def _block_label(result: Any) -> str:
    if not isinstance(result, dict):
        return "unknown"
    if not result.get("ok"):
        return f"error: {result.get('error')}"
    if result.get("air"):
        return "air"
    return str(result.get("name") or "unknown")


def _block_matches(expected: str, result: Any) -> bool:
    want = expected.strip().lower()
    if not isinstance(result, dict) or not result.get("ok"):
        return False
    if want in ("air", "minecraft:air"):
        return bool(result.get("air"))
    if result.get("air"):
        return False
    name = str(result.get("name") or "").lower()
    return name in (want, f"minecraft:{want}")


def _verify_setup(
    agent: AgentLike,
    scenario: Scenario,
    block_reader,
    warnings: list[str],
    strict: bool,
    *,
    timeout_ticks: int = 60,
    interval: int = 5,
) -> None:
    """Check the world really matches ``setup.verify`` after the commands ran.

    Commands are executed asynchronously by the server, so every expectation is
    polled until it holds (or ``timeout_ticks`` is used up).
    """
    verify = scenario.setup.get("verify") or []
    if not verify:
        return
    if block_reader is None:
        message = "setup.verify 未执行：没有方块读取器"
        scenario.setup["verified"] = False
        if strict:
            raise RunError(message)
        warnings.append(message)
        return

    failures: list[str] = []
    for index, item in enumerate(verify):
        x, y, z = (float(value) for value in item["pos"])
        expected = str(item["block"])
        remaining = timeout_ticks
        result: Any = None
        error: str | None = None
        while True:
            try:
                result = block_reader(x, y, z)
                error = None
            except Exception as exc:  # noqa: BLE001 - surfaced as a verification failure
                result, error = None, str(exc)
            if error is None and _block_matches(expected, result):
                break
            if remaining <= 0:
                break
            agent.wait_ticks(interval)
            remaining -= interval
        if error is not None:
            failures.append(f"setup.verify[{index}] ({x:g},{y:g},{z:g}) 读取失败: {error}")
        elif not _block_matches(expected, result):
            failures.append(
                f"setup.verify[{index}] ({x:g},{y:g},{z:g}) 期望 {expected}"
                f" 实际 {_block_label(result)}"
                f"（等待 {timeout_ticks} tick 仍未生效）"
            )
    scenario.setup["verified"] = not failures
    if failures:
        if strict:
            raise RunError("; ".join(failures))
        warnings.extend(failures)


def _run_commands(
    agent: AgentLike,
    commands: list[str],
    command_sender,
    emit,
    label: str,
) -> None:
    for index, command in enumerate(commands):
        emit(label, index, command)
        try:
            outcome = command_sender(command)
        except Exception as exc:
            raise RunError(f"{label}[{index}] 失败: {exc}") from exc
        if isinstance(outcome, dict) and outcome.get("ok") is False:
            raise RunError(f"{label}[{index}] 失败: {outcome.get('error')}")
        # commands are typed into chat and executed by the server asynchronously
        agent.wait_ticks(6)


def run_scenario(
    agent: AgentLike,
    scenario: Scenario,
    *,
    log=None,
    command_sender=None,
    block_reader=None,
) -> RunResult:
    """Run all steps, recording every tick. Raises RunError on agent failure.

    ``command_sender`` executes a server command through the game's chat box
    (see ``driver.postmsg``). It is used for ``setup.commands`` and ``tp`` steps
    because client-side position writes are corrected by the server.
    ``block_reader`` verifies ``setup.verify`` entries through the block query.
    """
    emit = log or (lambda *_: None)
    warnings: list[str] = []
    strict = bool(scenario.setup.get("strict"))

    setup_commands = scenario.setup.get("commands") or []
    if setup_commands:
        if command_sender is None:
            message = "setup.commands 未执行：没有可用的命令通道（--command-channel none？）"
            scenario.setup["commands_executed"] = False
            if strict:
                raise RunError(message)
            warnings.append(message)
        else:
            _run_commands(agent, setup_commands, command_sender, emit, "setup")
            scenario.setup["commands_executed"] = True

    _verify_setup(agent, scenario, block_reader, warnings, strict)

    start_tick = agent.call("get_tick_count")
    start_state = agent.call("get_state")
    _ok(start_state, "get_state(start)")

    # +20 ticks of headroom so the tail is not clipped by RPC latency.
    rec = _ok(agent.call("start_recording", scenario.total_ticks + 20), "start_recording")
    started_at = _now()

    marks: list[Mark] = []
    failure: BaseException | None = None
    try:
        for index, step in enumerate(scenario.steps):
            mark_start = agent.call("get_tick_count")
            emit("step", index, step.type, step.params)
            result = execute_step(agent, step, command_sender)
            if isinstance(result, dict):
                for warning in result.get("warnings") or []:
                    warnings.append(f"step {index} ({step.type}): {warning}")
                if result.get("warning"):
                    warnings.append(f"step {index} ({step.type}): {result['warning']}")
            if step.ticks > 0:
                agent.wait_ticks(step.ticks)
            if step.type == "tp":
                state_now = agent.call("get_state")
                if isinstance(state_now, dict) and state_now.get("ok"):
                    pos = state_now.get("pos")
                    target = step.params["pos"]
                    if isinstance(pos, (list, tuple)) and len(pos) == 3:
                        gap = max(abs(float(pos[i]) - target[i]) for i in range(3))
                        if gap > 1.0:
                            warnings.append(
                                f"step {index} (tp): 目标 {target} 实际 {list(pos)}"
                                f"（偏差 {gap:.2f}，可能被橡皮筋/碰撞复位）"
                            )
            mark_end = agent.call("get_tick_count")
            marks.append(Mark(index, step.type, mark_start, mark_end, result))
        _ok(agent.call("stop_input"), "stop_input")
    except BaseException as exc:  # remember it, teardown must still run
        failure = exc
    finally:
        try:
            agent.call("stop_input")
        except Exception:
            pass

    # Close the measured span *before* cleanup: teardown moves the player and
    # would otherwise show up as displacement/speed of the run.
    end_state = agent.call("get_state")
    recording = _ok(agent.call("stop_recording"), "stop_recording")
    finished_at = _now()

    teardown = scenario.setup.get("teardown") or []
    if teardown and command_sender is not None:
        try:
            _run_commands(agent, teardown, command_sender, emit, "teardown")
            scenario.setup["teardown_done"] = True
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort
            warnings.append(f"teardown 未完成: {exc}")
    elif teardown:
        warnings.append("teardown 未执行：没有可用的命令通道")

    if failure is not None:
        raise failure

    return RunResult(
        scenario=scenario,
        marks=marks,
        recording=recording,
        start_state=start_state,
        end_state=end_state,
        started_at=started_at,
        finished_at=finished_at,
        warnings=warnings,
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def attribute_step(tick: int, marks: list[Mark]) -> int | None:
    for mark in marks:
        if mark.start_tick <= tick < mark.end_tick:
            return mark.index
    if marks and tick >= marks[-1].end_tick:
        return marks[-1].index
    return None
