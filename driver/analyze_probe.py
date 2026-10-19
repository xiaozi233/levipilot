"""分析 record_probe.py 录下来的 JSONL：按速度自动分段 + 逐字节差分。

因为按键是外部（官方 CUA）按的，录制里没有 phase 标记，所以按**逐 tick 速度聚类**
自动找出"走 / 疾跑 / 潜行"等区间，再把速度最高的一段与最常见的一段做字节对比。

用法：
  py -3.11 driver/analyze_probe.py out/probe.jsonl
  py -3.11 driver/analyze_probe.py out/probe.jsonl --ref-speed 0.216 --top-speed 0.28
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def load(path: Path) -> tuple[dict, list[dict]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = rows[0] if rows and rows[0].get("kind") == "meta" else {}
    samples = [r for r in rows if r.get("ok")]
    return meta, samples


def steps(samples: list[dict]) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(samples)):
        if i + 1 >= len(samples):
            out.append(None)
            continue
        a, b = samples[i].get("pos"), samples[i + 1].get("pos")
        if not a or not b:
            out.append(None)
            continue
        out.append(((b[0] - a[0]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5)
    return out


def clusters(speed: list[float | None]) -> list[tuple[float, list[int]]]:
    """按 0.01 格/tick 的粒度分桶；下标是**原始 samples** 的下标。"""
    buckets: dict[int, list[int]] = {}
    for i, v in enumerate(speed):
        if v is None or v <= 0.005:
            continue
        buckets.setdefault(int(round(v / 0.01)), []).append(i)
    rows = [(k * 0.01, idx) for k, idx in buckets.items() if idx]
    rows.sort(key=lambda item: len(item[1]), reverse=True)
    return rows


def diff_bytes(a: str | None, b: str | None) -> list[tuple[int, int, int]]:
    if not a or not b or len(a) != len(b):
        return []
    out = []
    for i in range(0, len(a), 2):
        ba, bb = int(a[i : i + 2], 16), int(b[i : i + 2], 16)
        if ba != bb:
            out.append((i // 2, ba, bb))
    return out


def representative(samples: list[dict], speed: list[float | None], ids: list[int]) -> dict | None:
    """在给定下标里挑速度最接近该簇中心、且带 raw 的那一帧。"""
    if not ids:
        return None
    return samples[ids[len(ids) // 2]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--ref-speed", type=float, default=None, help="参考段速度（默认取样本最多的簇）")
    parser.add_argument("--top-speed", type=float, default=None, help="对比段速度（默认取最快且样本>=3 的簇）")
    parser.add_argument("--min-samples", type=int, default=3)
    args = parser.parse_args()

    meta, samples = load(Path(args.path))
    print(f"meta: {json.dumps(meta, ensure_ascii=False)}")
    if not samples:
        print("没有样本")
        return 2
    speed = steps(samples)
    vals = [v for v in speed if v is not None and v > 0.005]
    if not vals:
        print("没有位移（按键没落到游戏上？）")
        return 2

    rows = clusters(speed)
    print(f"\n速度分布（步长 0.01，共 {len(vals)} 个有效 tick）：")
    for center, ids in rows[:6]:
        print(f"  ~{center:.3f}  ticks={len(ids):<4} 区间 {ids[0]}..{ids[-1]}")

    by_center = {round(c, 3): ids for c, ids in rows}
    eligible = [(c, ids) for c, ids in rows if len(ids) >= args.min_samples and c >= 0.05]
    if not eligible:
        eligible = [(c, ids) for c, ids in rows if c >= 0.05] or rows
    ref_c = args.ref_speed if args.ref_speed is not None else min(c for c, _ in eligible)
    top_c = args.top_speed if args.top_speed is not None else max(c for c, _ in eligible)
    ref_ids = by_center.get(round(ref_c, 3), rows[0][1])
    top_ids = by_center.get(round(top_c, 3), [])
    print(f"\n参考段 ~{ref_c:.3f} ({len(ref_ids)} ticks)  vs  对比段 ~{top_c:.3f} ({len(top_ids)} ticks)")
    if ref_ids and top_ids and abs(top_c - ref_c) > 0.005:
        print(f"倍率: x{top_c / ref_c:.3f}")
    else:
        print("两段速度相同，没有可对比的状态差异")

    sa = representative(samples, speed, ref_ids)
    sb = representative(samples, speed, top_ids)
    if not sa or not sb:
        return 0
    print("\n组件字节差分：")
    for reg in ("client", "server"):
        for comp in ("move_input", "adf", "abilities"):
            before = (sa.get("raw") or {}).get(reg, {}).get(comp)
            after = (sb.get("raw") or {}).get(reg, {}).get(comp)
            diffs = diff_bytes(before, after)
            if diffs:
                listed = ", ".join(f"+0x{o:02X} {a:#04x}->{b:#04x}" for o, a, b in diffs[:16])
                more = "" if len(diffs) <= 16 else f" (+{len(diffs) - 16} more)"
                print(f"  {reg}.{comp:<11} {listed}{more}")
    stored_a = (sa.get("raw") or {}).get("storage", {}).get("hex")
    stored_b = (sb.get("raw") or {}).get("storage", {}).get("hex")
    diffs = diff_bytes(stored_a, stored_b)
    if diffs:
        cid = (sb.get("raw") or {}).get("storage", {}).get("cid", "?")
        listed = ", ".join(f"+0x{o:02X} {a:#04x}->{b:#04x}" for o, a, b in diffs[:24])
        more = "" if len(diffs) <= 24 else f" (+{len(diffs) - 24} more)"
        print(f"  storage({cid}) {listed}{more}")
    if len(ref_ids) > 1:
        ref_vals = [speed[i] for i in ref_ids if speed[i] is not None]
        if ref_vals:
            print(f"\n参考段中位速度: {statistics.median(ref_vals):.4f} 格/tick")
        else:
            print("\n参考段没有有效位移样本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())