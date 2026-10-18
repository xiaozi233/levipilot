"""JSONL / summary output for a scenario run."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from driver.runner import Mark, RunResult, attribute_step
from driver.scenario import project_sample


def _sanitize(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return name or "scenario"


def _speed(v: Any) -> float:
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return 0.0
    try:
        return math.hypot(float(v[0]), float(v[2]))
    except (TypeError, ValueError):
        return 0.0


def _step_windows(marks: list[Mark], samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mark in marks:
        window = [s for s in samples if mark.start_tick <= s.get("t", -1) < mark.end_tick]
        if not window and samples:
            # RPC latency can leave a zero-width window; fall back to the nearest tick.
            nearest = min(samples, key=lambda s: abs(s.get("t", 0) - mark.start_tick))
            window = [nearest]
        first = window[0] if window else {}
        last = window[-1] if window else {}
        speeds = [_speed(s.get("vel")) for s in window]
        pos_first = first.get("pos")
        pos_last = last.get("pos")
        delta = None
        distance = None
        if (
            isinstance(pos_first, (list, tuple))
            and isinstance(pos_last, (list, tuple))
            and len(pos_first) == 3
            and len(pos_last) == 3
        ):
            delta = [pos_last[i] - pos_first[i] for i in range(3)]
            distance = math.sqrt(sum(d * d for d in delta))
        rows.append(
            {
                "index": mark.index,
                "type": mark.type,
                "step_ticks": mark.end_tick - mark.start_tick,
                "samples": len(window),
                "pos_start": pos_first,
                "pos_end": pos_last,
                "delta": delta,
                "distance": distance,
                "max_h_speed": max(speeds) if speeds else None,
                "mean_h_speed": (sum(speeds) / len(speeds)) if speeds else None,
                "on_ground_ratio": (
                    sum(1 for s in window if s.get("on_ground")) / len(window)
                    if window
                    else None
                ),
            }
        )
    return rows


def summarize(result: RunResult) -> dict[str, Any]:
    samples = [
        s for s in result.recording.get("samples", []) if isinstance(s, dict) and s.get("ok")
    ]
    steps = _step_windows(result.marks, samples)

    first = samples[0] if samples else {}
    last = samples[-1] if samples else {}
    speeds = [_speed(s.get("vel")) for s in samples]
    delta = None
    if first.get("pos") and last.get("pos"):
        delta = [last["pos"][i] - first["pos"][i] for i in range(3)]

    return {
        "scenario": result.scenario.name,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "ticks_recorded": len(samples),
        "ticks_dropped": result.recording.get("dropped", 0),
        "tick_start": result.recording.get("start_tick"),
        "tick_end": result.recording.get("end_tick"),
        "start_state": result.start_state,
        "end_state": result.end_state,
        "displacement": delta,
        "distance": math.sqrt(sum(d * d for d in delta)) if delta else None,
        "max_h_speed": max(speeds) if speeds else None,
        "mean_h_speed": (sum(speeds) / len(speeds)) if speeds else None,
        "on_ground_ratio": (
            sum(1 for s in samples if s.get("on_ground")) / len(samples) if samples else None
        ),
        "steps": steps,
        "warnings": result.warnings,
    }


def write_run(
    result: RunResult,
    out_dir: str | Path,
    *,
    sample_fields: list[str],
    agent_status: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _sanitize(result.scenario.output or result.scenario.name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl_path = out_dir / f"{base}-{stamp}.jsonl"
    summary_path = out_dir / f"{base}-{stamp}.summary.json"

    summary = summarize(result)

    meta = {
        "type": "meta",
        "scenario": result.scenario.name,
        "description": result.scenario.description,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "sample_fields": sample_fields,
        "setup": result.scenario.setup,
        "steps": [step.to_dict() for step in result.marks],
        "agent": agent_status or {},
    }

    samples = result.recording.get("samples", [])
    with jsonl_path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            row = {
                "type": "sample",
                "step": attribute_step(int(sample.get("t", 0)), result.marks),
            }
            row.update(project_sample(sample, sample_fields))
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"type": "summary", **summary}, ensure_ascii=False) + "\n")

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonl_path, summary_path
