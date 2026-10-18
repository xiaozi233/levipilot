"""Scenario DSL: parsing, normalisation and validation.

Two shapes are accepted.

Modern::

    {
      "name": "flat-walk",
      "steps": [
        {"type": "tp", "pos": [0, -60, 0]},
        {"type": "look", "yaw": 0, "pitch": 0},
        {"type": "wait", "ticks": 20},
        {"type": "input", "forward": 1.0, "ticks": 60}
      ],
      "sample": ["pos", "vel", "on_ground"]
    }

Legacy (the original skeleton)::

    {"name": "...", "input": {...}, "ticks": 60, "sample": [...]}

The legacy form becomes one ``input`` step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STEP_TYPES = {"tp", "look", "vel", "input", "jump", "wait", "stop"}

DEFAULT_SAMPLE = ["pos", "vel", "on_ground", "flags", "yaw", "pitch", "input"]


class ScenarioError(ValueError):
    pass


@dataclass
class Step:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def ticks(self) -> int:
        if self.type == "wait":
            return int(self.params.get("ticks", 1))
        if self.type in {"input", "jump"}:
            return int(self.params.get("ticks", 1))
        if self.type == "tp":
            return int(self.params.get("settle_ticks", 5))
        if self.type in {"look", "vel"}:
            return int(self.params.get("settle_ticks", 1))
        return 0


@dataclass
class Scenario:
    name: str
    description: str
    steps: list[Step]
    sample: list[str]
    setup: dict[str, Any]
    output: str | None = None
    source: Path | None = None

    @property
    def total_ticks(self) -> int:
        return sum(step.ticks for step in self.steps)


def _num(value: Any, path: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"{path} 必须是数字，得到 {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ScenarioError(f"{path} 必须是有限数字，得到 {value!r}")
    return number


def _vec3(value: Any, path: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ScenarioError(f"{path} 必须是 [x, y, z]")
    return [_num(v, f"{path}[{i}]") for i, v in enumerate(value)]


def normalize_step(raw: Any, index: int) -> Step:
    if not isinstance(raw, dict):
        raise ScenarioError(f"steps[{index}] 必须是对象")
    kind = raw.get("type")
    if kind not in STEP_TYPES:
        raise ScenarioError(
            f"steps[{index}].type={kind!r} 不支持；可用: {', '.join(sorted(STEP_TYPES))}"
        )
    prefix = f"steps[{index}]"

    if kind == "tp":
        params: dict[str, Any] = {"pos": _vec3(raw.get("pos"), f"{prefix}.pos")}
        mode = raw.get("mode", "server")
        if mode not in {"server", "request", "direct", "chat"}:
            raise ScenarioError(
                f"{prefix}.mode 必须是 server / request / direct / chat"
            )
        params["mode"] = mode
        if "settle_ticks" in raw:
            params["settle_ticks"] = int(raw["settle_ticks"])
        return Step(kind, params)

    if kind == "look":
        params = {"yaw": _num(raw.get("yaw", 0), f"{prefix}.yaw"),
                  "pitch": _num(raw.get("pitch", 0), f"{prefix}.pitch")}
        if "settle_ticks" in raw:
            params["settle_ticks"] = int(raw["settle_ticks"])
        return Step(kind, params)

    if kind == "vel":
        params = {"v": _vec3(raw.get("v"), f"{prefix}.v")}
        if "settle_ticks" in raw:
            params["settle_ticks"] = int(raw["settle_ticks"])
        return Step(kind, params)

    if kind == "input":
        params = {
            "forward": _num(raw.get("forward", 0), f"{prefix}.forward"),
            "strafe": _num(raw.get("strafe", 0), f"{prefix}.strafe"),
            "jump": bool(raw.get("jump", False)),
            "sneak": bool(raw.get("sneak", False)),
            "sprint": bool(raw.get("sprint", False)),
            "ticks": int(raw.get("ticks", 1)),
        }
        if "world" in raw:
            world = raw["world"]
            if not isinstance(world, (list, tuple)) or len(world) != 2:
                raise ScenarioError(f"{prefix}.world 必须是 [dx, dz]")
            params["world"] = [
                _num(world[0], f"{prefix}.world[0]"),
                _num(world[1], f"{prefix}.world[1]"),
            ]
        if params["ticks"] < 0:
            raise ScenarioError(f"{prefix}.ticks 不能为负")
        return Step(kind, params)

    if kind == "jump":
        params = {"ticks": int(raw.get("ticks", 1))}
        if params["ticks"] < 1:
            raise ScenarioError(f"{prefix}.ticks 必须 >= 1")
        return Step(kind, params)

    if kind == "wait":
        params = {"ticks": int(raw.get("ticks", 1))}
        if params["ticks"] < 0:
            raise ScenarioError(f"{prefix}.ticks 不能为负")
        return Step(kind, params)

    return Step(kind, {})


def normalize(doc: dict[str, Any], *, source: Path | None = None) -> Scenario:
    if not isinstance(doc, dict):
        raise ScenarioError("scenario 顶层必须是对象")
    name = str(doc.get("name") or (source.stem if source else "scenario"))

    raw_steps = doc.get("steps")
    if raw_steps is None:
        # legacy: single input + ticks
        legacy_input = doc.get("input") or {}
        ticks = int(doc.get("ticks", 1))
        if ticks < 0:
            raise ScenarioError("ticks 不能为负")
        raw_steps = [
            {
                "type": "input",
                "forward": legacy_input.get("forward", 0),
                "strafe": legacy_input.get("strafe", 0),
                "jump": legacy_input.get("jump", False),
                "sneak": legacy_input.get("sneak", False),
                "sprint": legacy_input.get("sprint", False),
                "ticks": ticks,
            },
            {"type": "stop"},
        ]
        legacy = True
    else:
        legacy = False

    if not isinstance(raw_steps, list) or not raw_steps:
        raise ScenarioError("steps 必须是非空数组")
    steps = [normalize_step(raw, i) for i, raw in enumerate(raw_steps)]

    sample = doc.get("sample", DEFAULT_SAMPLE)
    if not isinstance(sample, list) or not all(isinstance(s, str) for s in sample):
        raise ScenarioError("sample 必须是字符串数组")
    unknown = [s for s in sample if s not in DEFAULT_SAMPLE]
    if unknown:
        raise ScenarioError(f"sample 含未知字段: {unknown}；可用: {DEFAULT_SAMPLE}")

    setup = doc.get("setup") or {}
    if not isinstance(setup, dict):
        raise ScenarioError("setup 必须是对象")

    output = doc.get("output")
    if output is not None:
        output = str(output)

    scenario = Scenario(
        name=name,
        description=str(doc.get("description", "")),
        steps=steps,
        sample=list(sample),
        setup=setup,
        output=output,
        source=source,
    )
    _validate_setup(scenario, legacy=legacy)
    return scenario


def _validate_setup(scenario: Scenario, *, legacy: bool) -> None:
    commands = scenario.setup.get("commands")
    if commands is None:
        return
    if not isinstance(commands, list):
        raise ScenarioError("setup.commands 必须是字符串数组")
    # The harness cannot execute in-game commands yet. Refuse silently pretending.
    if scenario.setup.get("strict", False):
        raise ScenarioError(
            "setup.commands 目前不会被执行；请用行为包/手动准备场地，或去掉 setup.strict"
        )
    scenario.setup["commands_executed"] = False


def load(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{path}: JSON 解析失败: {exc}") from exc
    return normalize(doc, source=path)


def project_sample(sample: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    """Flatten one recorded tick into the fields requested by the scenario."""
    out: dict[str, Any] = {"t": sample.get("t")}
    if not sample.get("ok", True):
        out["error"] = sample.get("error")
        return out
    for name in fields:
        if name == "flags":
            out["flags"] = {
                "lo": sample.get("flags_lo"),
                "mid": sample.get("flags_mid"),
                "hi": sample.get("flags_hi"),
            }
        else:
            out[name] = sample.get(name)
    return out
