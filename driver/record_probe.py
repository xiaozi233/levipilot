"""被动录制器：只开 frida 会话逐 tick 采样，按键由**官方电脑操作**（agent.computerUse）来做。

这样标定流程里不再需要 helper CLI：
  1. 本脚本后台运行：reset 朝向（可选）→ input_passthrough → start_recording → 打印 READY
  2. 你在会话里用官方 computerUse 按住 ctrl+w / shift+w 等真实按键
  3. 本脚本等够 --seconds 后 stop_recording，把 samples 写成 JSONL
  4. 用 driver/analyze_probe.py 分析（按速度自动分段 + 逐字节差分）

用法：
  py -3.11 driver/record_probe.py --seconds 25 --out out/probe.jsonl
  py -3.11 driver/record_probe.py --seconds 25 --dump-cid 0x3613e513 --dump-len 1024
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from driver.postmsg import PostMessageChannel  # noqa: E402
from driver.session import HarnessError, Session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=25.0, help="录制窗口长度")
    parser.add_argument("--out", default="out/probe.jsonl")
    parser.add_argument("--dump-cid", default=None, help="额外整块 dump 的组件 id（hex）")
    parser.add_argument("--dump-len", type=int, default=1024)
    parser.add_argument(
        "--dump-all-every",
        type=int,
        default=0,
        help="每 N tick 枚举所有已知 storage 的 chunk（0=关；建议 5，配 analyze_probe 自动找变化组件）",
    )
    parser.add_argument("--dump-all-len", type=int, default=512)
    parser.add_argument("--no-rotation", action="store_true")
    parser.add_argument("--settle", type=int, default=60)
    args = parser.parse_args()

    agent = Session().open()
    try:
        if not args.no_rotation:
            channel = PostMessageChannel(pid=agent.pid)
            usable, why = channel.available()
            if usable:
                channel.send_command("tp @s ~ ~ ~ 0 0")
                agent.wait_ticks(args.settle)
            else:
                print(f"warn: 命令通道不可用（{why}）", file=sys.stderr)

        agent.call("stop_input")
        agent.call("input_passthrough", True)
        if args.dump_cid:
            agent.call("set_dump_cid", int(str(args.dump_cid), 0), args.dump_len)
        if args.dump_all_every > 0:
            info = agent.call("set_dump_all", args.dump_all_every, args.dump_all_len)
            print(f"dump all storages: {info}", flush=True)

        start = agent.call("start_recording", int(args.seconds * 40) + 200, True)
        start_tick = int(start.get("start_tick") or 0)

        # 给外部（官方 CUA）一个明确的开始信号。
        print(f"READY tick={start_tick} seconds={args.seconds}", flush=True)
        time.sleep(args.seconds)
        stop = agent.call("stop_recording")
        agent.call("input_passthrough", False)
        agent.call("set_dump_cid", 0)
        agent.call("stop_input")
    finally:
        agent.close()

    samples = stop.get("samples", [])
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "kind": "meta",
                    "start_tick": int(stop.get("start_tick") or 0),
                    "end_tick": int(stop.get("end_tick") or 0),
                    "dump_cid": args.dump_cid,
                    "samples": len(samples),
                    "dropped": stop.get("dropped"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        for sample in samples:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
    print(f"DONE samples={len(samples)} dropped={stop.get('dropped')} out={out}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)