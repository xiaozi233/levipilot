"""用"真实按键"标定 sprint / sneak 的内存编码，再用注入复现。

之前的做法是猜 ADF 的位（按 Java ActorFlags 的 sneaking/sprinting），扫了三个 word
的所有位都没有得到原版 130% / 30% 的倍率。这里换个方向：**让电脑操作 helper 真的按住
按键**（`w` / `ctrl+w` / `shift+w`），同时 agent 逐 tick 采样（含两套 registry 的组件
原始字节），然后逐字节对比"走"和"疾跑/潜行"到底差在哪个字节——
内存编码由真实行为反推，不再靠猜。

用法：
  py -3.11 driver/probe_sprint.py                      # 默认 w / ctrl+w / w / shift+w
  py -3.11 driver/probe_sprint.py --phases "w:1500,shift+w:2500"
  py -3.11 driver/probe_sprint.py --no-rotation        # 跳过开局 /tp（朝向已知时才用）

注意：这是**标定**（测量真实行为的内存表示），不是实验控制手段；
实验里的位移/朝向仍然走 frida RPC 注入。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from driver.native_cua import NativeCua  # noqa: E402
from driver.postmsg import PostMessageChannel  # noqa: E402
from driver.session import HarnessError, Session  # noqa: E402

DEFAULT_PHASES = "w:1500,ctrl+w:2500,w:1500,shift+w:2500"
COMPONENTS = ("move_input", "adf", "abilities")
REGISTRIES = ("client", "server")


def parse_phases(spec: str) -> list[tuple[str, int]]:
    phases: list[tuple[str, int]] = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        key, _, ms = item.partition(":")
        if not key:
            raise SystemExit(f"坏掉的 phases 项: {item!r}")
        phases.append((key, int(ms or 1500)))
    if not phases:
        raise SystemExit("phases 是空的")
    return phases


def tick_of(agent: Session) -> int:
    value = agent.call("get_tick_count")
    return int(value.get("tick", 0)) if isinstance(value, dict) else int(value)


def speed_of(samples: list[dict], index: int) -> float | None:
    """第 index 个样本到下一个样本之间的水平位移（格/tick）。"""
    if index + 1 >= len(samples):
        return None
    a, b = samples[index], samples[index + 1]
    if not a.get("ok") or not b.get("ok"):
        return None
    pa, pb = a.get("pos"), b.get("pos")
    if not pa or not pb:
        return None
    dx, dz = pb[0] - pa[0], pb[2] - pa[2]
    return (dx * dx + dz * dz) ** 0.5


def diff_bytes(a: str | None, b: str | None) -> list[tuple[int, int, int]]:
    if not a or not b or len(a) != len(b):
        return []
    out: list[tuple[int, int, int]] = []
    for i in range(0, len(a), 2):
        ba, bb = int(a[i : i + 2], 16), int(b[i : i + 2], 16)
        if ba != bb:
            out.append((i // 2, ba, bb))
    return out


def fmt_diff(offset: int, before: int, after: int) -> str:
    return f"+0x{offset:02X} {before:#04x}->{after:#04x}"


def representative(samples: list[dict], lo: int, hi: int) -> dict | None:
    """取该区间里速度接近峰值的那一 tick 作为代表帧。

    不能用中位数：如果按键只在一部分 tick 落到游戏上（例如焦点被抢走），
    中位数会选到一个空转帧，差分就全是噪声。这里取"速度 >= 0.8×峰值"的中位那一个，
    也就是稳定行走/疾跑的那几帧。
    """
    speeds = [(speed_of(samples, i), i) for i in range(lo, hi)]
    speeds = [(s, i) for s, i in speeds if s is not None]
    if not speeds:
        return None
    peak = max(s for s, _ in speeds)
    if peak <= 0:
        return None
    moving = [(s, i) for s, i in speeds if s >= 0.8 * peak]
    _, index = moving[len(moving) // 2]
    return samples[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phases", default=DEFAULT_PHASES)
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--out", default="out/sprint-calib")
    parser.add_argument("--no-rotation", action="store_true")
    parser.add_argument("--settle", type=int, default=60, help="开局 /tp 后空转的 tick 数")
    args = parser.parse_args()

    phases = parse_phases(args.phases)
    agent = Session(args.pid).open()
    try:
        cua = NativeCua()
        ok, why = cua.available()
        if not ok:
            print(f"error: 电脑操作 helper 不可用: {why}", file=sys.stderr)
            return 2

        if not args.no_rotation:
            channel = PostMessageChannel(pid=agent.pid)
            usable, why = channel.available()
            if usable:
                print(f"reset rotation: {channel.send_command('tp @s ~ ~ ~ 0 0')}")
                # 聊天命令后进程会卡 ~1.2s（见 docs §6.2），先空转吸收掉。
                agent.wait_ticks(args.settle)
            else:
                print(f"warn: 命令通道不可用（{why}），朝向按当前值", file=sys.stderr)

        agent.call("stop_input")
        # 关键：agent 每 tick 都会把 state.input（默认 0）写进 MoveInput，
        # 挂着 agent 时真实按键会被零输入覆盖，所以标定期间必须开透传。
        passthrough = agent.call("input_passthrough", True)
        print(f"input passthrough: {passthrough}")
        start = agent.call("start_recording", 4000, True)
        print(f"recording from tick {start.get('start_tick')} (raw={start.get('include_raw')})")

        marks: list[tuple[str, int, int]] = []
        for key, ms in phases:
            info = cua.info()
            focused = bool(info.get("focused"))
            if not focused:
                print(f"  warn: {key} 之前窗口没有焦点，按键可能落不到游戏上", file=sys.stderr)
            t0 = tick_of(agent)
            try:
                cua.action("hold", key, str(ms))
            except Exception as exc:  # noqa: BLE001 - 记录并继续
                print(f"warn: hold {key} 失败: {exc}", file=sys.stderr)
            time.sleep(0.4)
            t1 = tick_of(agent)
            marks.append((key, t0, t1))
            print(f"  {key:<10} ticks {t0}..{t1} focused={focused}")

        stop = agent.call("stop_recording")
        samples = [s for s in stop.get("samples", [])]
        print(f"recorded {len(samples)} samples (dropped {stop.get('dropped')})")

        first = marks[0][1] if marks else 0

        # ---- 每个阶段的真实速度 ----
        table: list[dict] = []
        print()
        print(f"{'phase':<12}{'ticks':>7}{'moved':>9}{'moving':>8}{'median':>10}{'max':>10}")
        for key, t0, t1 in marks:
            lo = max(0, t0 - first)
            hi = min(len(samples), t1 - first)
            speeds = [s for s in (speed_of(samples, i) for i in range(lo, hi)) if s is not None]
            # 掐掉阶段头尾各 3 个 tick（按键生效/释放的过渡）
            core = speeds[3:-3] if len(speeds) > 8 else speeds
            if not core:
                print(f"{key:<12}{hi - lo:>7}{'-':>9}{'-':>8}{'-':>10}{'-':>10}")
                continue
            moving = [s for s in core if s > 0.02]
            median = statistics.median(moving or core)
            table.append(
                {
                    "key": key,
                    "median": median,
                    "max": max(core),
                    "moved": sum(core),
                    "moving_ticks": len(moving),
                    "ticks": hi - lo,
                }
            )
            print(
                f"{key:<12}{hi - lo:>7}{sum(core):>9.3f}{len(moving):>8}"
                f"{median:>10.4f}{max(core):>10.4f}"
            )

        reference = next((row for row in table if "+" not in row["key"] and row["moving_ticks"]), None)
        if reference and reference["median"] > 0:
            print()
            print(f"相对 [{reference['key']}]（{reference['median']:.4f} 格/tick）的倍率：")
            for row in table:
                ratio = row["median"] / reference["median"]
                print(f"  {row['key']:<12} x{ratio:.3f}   (moving {row['moving_ticks']} ticks)")
        noise = [row["key"] for row in table if not row["moving_ticks"]]
        if noise:
            print(f"\n注意：这些阶段几乎没有位移，按键没落到游戏上（焦点被抢？）：{', '.join(noise)}")

        # ---- 字节差分 ----
        print()
        print("组件字节差分（同阶段中位速度那一 tick，走 vs 带修饰键）：")
        for row in table:
            if "+" not in row["key"]:
                continue
            base_key = row["key"].split("+")[-1]
            for other in table:
                if other is row or other["key"] != base_key:
                    continue
                idx_a = next(i for i, m in enumerate(marks) if m[0] == other["key"])
                idx_b = next(i for i, m in enumerate(marks) if m[0] == row["key"])
                sa = representative(samples, marks[idx_a][1] - first, marks[idx_a][2] - first)
                sb = representative(samples, marks[idx_b][1] - first, marks[idx_b][2] - first)
                if not sa or not sb or not sa.get("raw") or not sb.get("raw"):
                    continue
                ratio = row["median"] / other["median"] if other["median"] else float("nan")
                print(f"\n  [{other['key']}] -> [{row['key']}]  x{ratio:.3f}")
                for reg in REGISTRIES:
                    for comp in COMPONENTS:
                        before = (sa["raw"].get(reg) or {}).get(comp)
                        after = (sb["raw"].get(reg) or {}).get(comp)
                        diffs = diff_bytes(before, after)
                        if diffs:
                            listed = ", ".join(fmt_diff(*d) for d in diffs[:12])
                            more = "" if len(diffs) <= 12 else f" (+{len(diffs) - 12} more)"
                            print(f"    {reg}.{comp:<11} {listed}{more}")

        out_dir = ROOT / args.out
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = out_dir.with_name(f"{out_dir.name}-{stamp}.jsonl")
        with path.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "kind": "meta",
                        "phases": phases,
                        "marks": [{"key": k, "t0": a, "t1": b} for k, a, b in marks],
                        "table": table,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            for sample in samples:
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
        print(f"\njsonl: {path}")
        return 0
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        try:
            agent.call("input_passthrough", False)
            agent.call("stop_input")
        finally:
            agent.close()


if __name__ == "__main__":
    raise SystemExit(main())