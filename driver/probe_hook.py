"""在指定函数上挂运行时探针，确认它是否收到命令字符串，以及调用者是谁。

配合官方电脑操作使用：脚本打印 READY 后等 `--go` 触发文件，然后你在游戏里发一条命令，
再创建 `--stop` 文件。脚本输出每次调用的返回地址和 rdx/rcx 处的字符串内容。

用法：
  py -3.11 driver/probe_hook.py --va 0x14df18b60 --out out/hook.json \
      --go out/hook.go --stop out/hook.stop
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


def wait_for(path: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--va", required=True, help="函数 VA（hex）")
    parser.add_argument("--out", default="out/hook.json")
    parser.add_argument("--go", default="out/hook.go")
    parser.add_argument("--stop", default="out/hook.stop")
    parser.add_argument("--max", type=int, default=40)
    args = parser.parse_args()

    go, stop = ROOT / args.go, ROOT / args.stop
    go.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)

    agent = Session().open()
    try:
        agent.wait_ticks(2, timeout=10)
        started = agent.call("probe_start", args.va, args.max)
        if not started.get("ok"):
            print(f"error: probe_start 失败: {started}", file=sys.stderr)
            return 2
        print(f"READY target={started.get('target')} waiting_for={go}", flush=True)
        if not wait_for(go, 300):
            print("error: 等触发文件超时", file=sys.stderr)
            return 2
        go.unlink(missing_ok=True)
        print("ARMED", flush=True)
        if not wait_for(stop, 120):
            print("warn: 等停止文件超时", file=sys.stderr)
        stop.unlink(missing_ok=True)
        count = agent.call("probe_stop")
        log = agent.call("probe_log")
    finally:
        agent.close()

    entries = log.get("entries") or []
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"target": started.get("target"), "entries": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"DONE calls={count.get('count')} out={out}")
    for entry in entries[:12]:
        print(
            f"  tick={entry.get('tick')} caller={entry.get('caller')} "
            f"rdx={entry.get('rdx')} str_rdx={entry.get('str_rdx')!r} str_rcx={entry.get('str_rcx')!r}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)