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


def representative(
    samples: list[dict], ids: list[int], *, need_all: bool = False
) -> dict | None:
    """在给定下标里挑中间那一帧；need_all 时只接受带 raw.all 的帧。

    dump 是每 N tick 做一次，某个速度簇可能整段都没撞上 dump 帧；这时退回到
    **离该簇中点最近**的带 all 的帧，避免整块分析被跳过。
    """
    if not ids:
        return None
    if need_all:
        have = [i for i in ids if (samples[i].get("raw") or {}).get("all")]
        if not have:
            candidates = [
                i for i in range(len(samples)) if (samples[i].get("raw") or {}).get("all")
            ]
            if not candidates:
                return None
            mid = ids[len(ids) // 2]
            return samples[min(candidates, key=lambda i: abs(i - mid))]
        ids = have
    return samples[ids[len(ids) // 2]]


def pick_segments(
    rows: list[tuple[float, list[int]]],
    *,
    min_samples: int = 10,
    ref_speed: float | None = None,
    top_speed: float | None = None,
) -> tuple[float, float, list[int], list[int], float]:
    """选"最慢的常见段"作参考、"最快的常见段"作对比（walk vs sprint）。

    不能取"样本最多"的簇：疾跑往往比走路跑得更久，会自己和自己比。
    """
    by_center = {round(c, 3): ids for c, ids in rows}
    eligible = [(c, ids) for c, ids in rows if len(ids) >= min_samples and c >= 0.05]
    if not eligible:
        eligible = [(c, ids) for c, ids in rows if c >= 0.05] or rows
    ref_c = ref_speed if ref_speed is not None else min(c for c, _ in eligible)
    top_c = top_speed if top_speed is not None else max(c for c, _ in eligible)
    ref_ids = by_center.get(round(ref_c, 3), rows[0][1])
    top_ids = by_center.get(round(top_c, 3), [])
    ratio = (top_c / ref_c) if ref_c else float("nan")
    return ref_c, top_c, ref_ids, top_ids, ratio


def control_hits(
    all_a: dict, all_b: dict, all_c: dict
) -> list[tuple[int, str, str, list[tuple[int, int, int]], list[tuple[int, int, int]]]]:
    """A=参考段前半、B=参考段后半（控制组）、C=对比段。

    返回"只在 C 出现或明显更多"的变化；A->B 也有的变化（位移/时间噪声）被过滤掉。
    """
    hits = []
    for reg in ("client", "server"):
        ma, mb, mc = all_a.get(reg) or {}, all_b.get(reg) or {}, all_c.get(reg) or {}
        for cid in sorted(set(ma) & set(mc)):
            d_test = diff_bytes(ma[cid], mc[cid])
            d_ctrl = diff_bytes(ma[cid], mb.get(cid))
            if not d_test:
                continue
            if not d_ctrl or len(d_test) > len(d_ctrl) + 1:
                hits.append((len(d_test) - len(d_ctrl), reg, cid, d_test, d_ctrl))
    hits.sort(reverse=True)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--ref-speed", type=float, default=None, help="参考段速度（默认取样本最多的簇）")
    parser.add_argument("--top-speed", type=float, default=None, help="对比段速度（默认取最快且样本>=3 的簇）")
    parser.add_argument("--min-samples", type=int, default=10)
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
    ref_c, top_c, ref_ids, top_ids, ratio = pick_segments(
        rows,
        min_samples=args.min_samples,
        ref_speed=args.ref_speed,
        top_speed=args.top_speed,
    )
    print(f"\n参考段 ~{ref_c:.3f} ({len(ref_ids)} ticks)  vs  对比段 ~{top_c:.3f} ({len(top_ids)} ticks)")
    if ref_ids and top_ids and abs(top_c - ref_c) > 0.005:
        print(f"倍率: x{ratio:.3f}")
    else:
        print("两段速度相同，没有可对比的状态差异")

    sa = representative(samples, ref_ids)
    sb = representative(samples, top_ids)
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

    # 疾跑专有差异：把"走"参考段按时间一分为二当作控制组。
    # A->B 的差异只来自位移/时间，A->C（疾跑）里额外出现的差异才是疾跑专有。
    half = len(ref_ids) // 2
    ca = representative(samples, ref_ids[:half], need_all=True)
    cb = representative(samples, ref_ids[half:], need_all=True)
    ct = representative(samples, top_ids, need_all=True)
    if ca and cb and ct:
        print("\n疾跑专有差异（A=走前半 / B=走后半 控制组 / C=疾跑）：")
        all_a = (ca.get("raw") or {}).get("all") or {}
        all_b = (cb.get("raw") or {}).get("all") or {}
        all_c = (ct.get("raw") or {}).get("all") or {}
        hits = control_hits(all_a, all_b, all_c)
        if not hits:
            print("  没有找到疾跑专有的组件差异（说明倍率可能在包/属性路径，不在组件里）")
        for score, reg, cid, d_test, d_ctrl in hits[:12]:
            listed = ", ".join(f"+0x{o:02X} {a:#04x}->{b:#04x}" for o, a, b in d_test[:8])
            print(f"  {reg} {cid}  score={score}  {listed}")
            if d_ctrl:
                ctrl = ", ".join(f"+0x{o:02X} {a:#04x}->{b:#04x}" for o, a, b in d_ctrl[:6])
                print(f"      控制组也有: {ctrl}")
    aa = representative(samples, ref_ids, need_all=True)
    ab = representative(samples, top_ids, need_all=True)
    if aa and ab:
        all_a = (aa.get("raw") or {}).get("all") or {}
        all_b = (ab.get("raw") or {}).get("all") or {}
        rows_all = []
        for reg in ("client", "server"):
            ma, mb = all_a.get(reg) or {}, all_b.get(reg) or {}
            for cid in sorted(set(ma) & set(mb)):
                d = diff_bytes(ma[cid], mb[cid])
                if d:
                    rows_all.append((len(d), reg, cid, d))
        rows_all.sort(reverse=True)
        print(f"\n全组件差分：{len(rows_all)} 个 storage 有变化（按变化字节数排序）")
        for _, reg, cid, d in rows_all[:16]:
            listed = ", ".join(f"+0x{o:02X} {a:#04x}->{b:#04x}" for o, a, b in d[:10])
            more = "" if len(d) <= 10 else f" (+{len(d) - 10} more)"
            print(f"  {reg} {cid}  {listed}{more}")
    if len(ref_ids) > 1:
        ref_vals = [speed[i] for i in ref_ids if speed[i] is not None]
        if ref_vals:
            print(f"\n参考段中位速度: {statistics.median(ref_vals):.4f} 格/tick")
        else:
            print("\n参考段没有有效位移样本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())