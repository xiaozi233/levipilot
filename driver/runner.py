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
        return _ok(agent.call("set_yaw_pitch", p["yaw"], p["pitch"]), "look")
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


def run_scenario(
    agent: AgentLike,
    scenario: Scenario,
    *,
    log=None,
    command_sender=None,
) -> RunResult:
    """Run all steps, recording every tick. Raises RunError on agent failure.

    ``command_sender`` executes a server command through the game's chat UI.
    It is used for ``setup.commands`` and ``tp`` steps because client-side
    position writes are corrected by the server.
    """
    emit = log or (lambda *_: None)
    warnings: list[str] = []

    setup_commands = scenario.setup.get("commands") or []
    if setup_commands:
        if command_sender is None:
            scenario.setup["commands_executed"] = False
            warnings.append(
                "setup.commands 未执行：没有可用的命令发送器（--no-chat 或 CUA helper 不可用）"
            )
        else:
            for index, command in enumerate(setup_commands):
                emit("setup", index, command)
                try:
                    command_sender(command)
                except Exception as exc:
                    raise RunError(f"setup.commands[{index}] 失败: {exc}") from exc
                agent.wait_ticks(3)
            scenario.setup["commands_executed"] = True

    start_tick = agent.call("get_tick_count")
    start_state = agent.call("get_state")
    _ok(start_state, "get_state(start)")

    # +20 ticks of headroom so the tail is not clipped by RPC latency.
    rec = _ok(agent.call("start_recording", scenario.total_ticks + 20), "start_recording")
    started_at = _now()

    marks: list[Mark] = []
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
    finally:
        # Always release input even if a step failed.
        try:
            agent.call("stop_input")
        except Exception:
            pass

    end_state = agent.call("get_state")
    recording = _ok(agent.call("stop_recording"), "stop_recording")
    finished_at = _now()

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
