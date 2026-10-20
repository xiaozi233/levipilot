"""动态定位"提交聊天/命令"路径：对主线程做 Stalker 调用摘要，两次运行做差分。

用法：
  py -3.11 driver/probe_submit.py record --seconds 8 --out out/stalk_cmd.json
  py -3.11 driver/probe_submit.py diff out/stalk_plain.json out/stalk_cmd.json

record 会打印 READY，然后用官方电脑操作（agent.computerUse）在游戏里发一条消息；
8 秒后停止并落盘。diff 输出只在"命令"那次出现、或计数显著更高的目标函数——
这些就是命令提交路径的候选，再拿地址去 IDA 反编译确认哪个收 std::string。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from driver.session import HarnessError, Session  # noqa: E402


def _wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def record(seconds: float, out: Path, *, go: Path | None, stop: Path | None) -> int:
    agent = Session().open()
    try:
        # 等一个 tick，让 hook 记下主线程 id。
        agent.wait_ticks(2, timeout=10)
        info = agent.call("main_thread")
        if not info.get("ok"):
            print("error: 主线程未知（世界没在 tick？）", file=sys.stderr)
            return 2

        if go is not None:
            print(f"READY thread={info.get('thread')} waiting_for={go}", flush=True)
            if not _wait_for(go, 300.0):
                print("error: 等触发文件超时", file=sys.stderr)
                return 2
            go.unlink(missing_ok=True)
            start = agent.call("stalk_start")
            if not start.get("ok"):
                print(f"error: stalk_start 失败: {start}", file=sys.stderr)
                return 2
            print("STALKING", flush=True)
            if stop is not None:
                if not _wait_for(stop, 120.0):
                    print("warn: 等停止文件超时，按 120s 停止", file=sys.stderr)
                stop.unlink(missing_ok=True)
            else:
                time.sleep(seconds)
        else:
            start = agent.call("stalk_start")
            if not start.get("ok"):
                print(f"error: stalk_start 失败: {start}", file=sys.stderr)
                return 2
            print(f"READY thread={info.get('thread')} seconds={seconds}", flush=True)
            time.sleep(seconds)
        result = agent.call("stalk_stop")
    finally:
        agent.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"thread": result.get("thread"), "count": result.get("count"), "summary": result.get("summary")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"DONE targets={result.get('count')} out={out}", flush=True)
    return 0


def diff(plain_path: Path, cmd_path: Path) -> int:
    plain = json.loads(plain_path.read_text(encoding="utf-8")).get("summary") or {}
    cmd = json.loads(cmd_path.read_text(encoding="utf-8")).get("summary") or {}
    # 两次录制时长可能不同（Stalker 窗口由触发文件控制），所以按总调用数归一化。
    plain_total = sum(plain.values()) or 1
    cmd_total = sum(cmd.values()) or 1
    rows = []
    for target, count in cmd.items():
        if count < 5:
            continue
        before = plain.get(target, 0)
        plain_frac = before / plain_total
        cmd_frac = count / cmd_total
        if plain_frac == 0 or cmd_frac > plain_frac * 5:
            rows.append((cmd_frac - plain_frac, count, before, target))
    rows.sort(reverse=True)
    print(
        f"命令路径候选（{len(rows)} 个；plain 总调用 {plain_total}，cmd 总调用 {cmd_total}）"
    )
    for delta, count, before, target in rows[:60]:
        print(f"  {target}  cmd={count:<7} plain={before:<7} dfrac={delta:.3e}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("--seconds", type=float, default=8.0)
    rec.add_argument("--out", default="out/stalk.json")
    rec.add_argument("--go", default="", help="等到这个文件出现才开始（文件触发模式）")
    rec.add_argument("--stop", default="", help="这个文件出现就停止")
    dif = sub.add_parser("diff")
    dif.add_argument("plain")
    dif.add_argument("cmd")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "record":
            return record(
                args.seconds,
                ROOT / args.out,
                go=ROOT / args.go if args.go else None,
                stop=ROOT / args.stop if args.stop else None,
            )
        return diff(ROOT / args.plain, ROOT / args.cmd)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))