"""跑一遍全部场景并对照实测基准值，作为 harness 的回归门禁。

验证的是"注入 → 物理 → 采样 → 汇总"整条链没有退化：
  wall-stop      撞墙停在 x≈5.7，位移 +5.2
  jump-distance  水平速度仍是原版 0.2159（跳跃段额外有滞空样本）
  step-up        一格台阶上不去，位移被挡在 +3.2
  walk-sneak     走 0.2159 / 潜行 0.0648（0.3 倍）

用法：
  py -3.11 driver/regress.py            # 全部
  py -3.11 driver/regress.py wall-stop  # 只跑指定场景

退出码：0 全过，4 有失败（和 run.py 的 stalled 退出码区分开）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "regress"

# (场景, [(summary 里的路径, 期望值, 容差)])
CASES: list[tuple[str, list[tuple[str, float, float]]]] = [
    (
        "wall-stop",
        [
            ("displacement[0]", 5.2, 0.35),
            ("max_h_speed", 0.2159, 0.010),
            ("on_ground_ratio", 1.0, 0.02),
        ],
    ),
    (
        "jump-distance",
        [
            ("max_h_speed", 0.2159, 0.010),
        ],
    ),
    (
        "step-up",
        [
            ("displacement[0]", 3.2, 0.35),
            ("max_h_speed", 0.2159, 0.010),
        ],
    ),
    (
        "walk-sneak",
        [
            ("steps[1].max_h_speed", 0.2159, 0.010),
            ("steps[4].max_h_speed", 0.0648, 0.006),
        ],
    ),
]


def dig(summary: dict, path: str) -> float:
    """取 "step[1].max_h_speed" / "displacement[0]" 这类路径的值。"""
    current: object = summary
    token = ""
    for ch in path + ".":
        if ch == ".":
            if token.endswith("]"):
                name, _, idx = token.partition("[")
                current = current[name]  # type: ignore[index]
                current = current[int(idx[:-1])]  # type: ignore[index]
            elif token:
                current = current[token]  # type: ignore[index]
            token = ""
        else:
            token += ch
    return float(current)  # type: ignore[arg-type]


def newest_summary(prefix: str) -> Path | None:
    files = sorted(OUT.glob(f"{prefix}-*.summary.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def run_case(name: str) -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "driver" / "run.py"),
            "--scenario",
            str(ROOT / "scenarios" / f"{name}.json"),
            "--out-dir",
            str(OUT),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - started
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        print(f"  FAIL {name}: 退出码 {proc.returncode} ({elapsed:.0f}s)")
        for line in tail:
            print(f"       {line}")
        return False
    path = newest_summary(name)
    if path is None:
        print(f"  FAIL {name}: 没找到 summary")
        return False
    summary = json.loads(path.read_text(encoding="utf-8"))
    ok = True
    for expr, want, tol in CASES_named(name):
        try:
            got = dig(summary, expr)
        except (KeyError, IndexError, TypeError, ValueError):
            print(f"  FAIL {name}: summary 里没有 {expr}")
            ok = False
            continue
        good = abs(got - want) <= tol
        print(f"  {'ok  ' if good else 'FAIL'} {name:<14} {expr:<22} {got:8.4f} (want {want:.4f}±{tol})")
        ok = ok and good
    if ok:
        print(f"  PASS {name} ({elapsed:.0f}s)")
    return ok


def CASES_named(name: str) -> list[tuple[str, float, float]]:
    for case_name, checks in CASES:
        if case_name == name:
            return checks
    raise KeyError(name)


def main() -> int:
    wanted = sys.argv[1:] or [name for name, _ in CASES]
    failed = []
    for name in wanted:
        print(f"=== {name} ===")
        if not run_case(name):
            failed.append(name)
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 4
    print(f"all {len(wanted)} cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())