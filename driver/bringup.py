"""把游戏带到"可以跑实验"的状态：必要时启动进程、从主菜单进存档、等 agent 就绪。

这一串手动操作每次游戏重启都要重来一遍（实测很容易卡在主菜单/世界列表），
所以固化成脚本。流程与坐标都是实测过的：

  1. 进程不在 → 启动 `Minecraft.Windows.exe`（游戏目录只读，只启动，不写任何东西）
  2. 轮询等窗口出现（cua info）
  3. 已经能 attach 且 tick/player 就绪 → 直接成功返回
  4. 否则用"悬停 + 回车"进存档：主菜单"游戏"(795,480) → 第一张存档卡名称区(160,550)
     （坐标是 1591x962 光栅下的实测值；窗口尺寸变了要重新截图读数）
  5. 等 agent 就绪（默认 45s）

用法：
  py -3.11 driver/bringup.py                      # 只检查（不启动、不点）
  py -3.11 driver/bringup.py --launch             # 进程不在就启动
  py -3.11 driver/bringup.py --launch --enter-world
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from driver.native_cua import NativeCua, NativeCuaError  # noqa: E402
from driver.session import HarnessError, Session  # noqa: E402

GAME_DIR = Path(
    r"%APPDATA%\levilauncher.exe\versions\1.26.45.01"
)
GAME_EXE = GAME_DIR / "Minecraft.Windows.exe"

# 主菜单 / 世界列表里两个按钮的截图像素（1591x962 窗口，实测）。
PLAY_BUTTON = ("795", "480")
FIRST_WORLD_CARD = ("160", "550")


def game_running() -> bool:
    try:
        cua = NativeCua()
        cua.info()
        return True
    except (NativeCuaError, OSError):
        return False


def launch_game() -> None:
    if not GAME_EXE.exists():
        raise SystemExit(f"游戏 exe 不存在: {GAME_EXE}")
    subprocess.Popen([str(GAME_EXE)], cwd=str(GAME_DIR))
    print(f"launched {GAME_EXE.name}")


def wait_window(cua: NativeCua, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            info = cua.info()
            if info.get("window_id"):
                return info
        except (NativeCuaError, OSError):
            pass
        time.sleep(1.0)
    raise SystemExit(f"等窗口超时（{timeout:.0f}s）")


def probe_ready(timeout: float) -> dict | None:
    """能 attach 且玩家已解析就返回 status，否则返回 None。"""
    agent = Session()
    try:
        agent.open()
        return agent.wait_ready(timeout=timeout, require_player=True)
    except (HarnessError, Exception):
        return None
    finally:
        agent.close()


def enter_world(cua: NativeCua) -> None:
    print("click path: 主菜单[游戏] → 世界列表[第一张卡]")
    cua.action("point", *PLAY_BUTTON)
    time.sleep(0.5)
    cua.action("key", "Return")
    time.sleep(3.0)
    cua.action("point", *FIRST_WORLD_CARD)
    time.sleep(0.5)
    cua.action("key", "Return")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true", help="进程不在就启动")
    parser.add_argument("--enter-world", action="store_true", help="必要时从主菜单进存档")
    parser.add_argument("--ready-timeout", type=float, default=90.0,
                        help="进世界后等 agent 就绪的秒数（冷启动加载实测可到 60s+）")
    parser.add_argument("--probe-timeout", type=float, default=5.0)
    args = parser.parse_args()

    if not game_running():
        if not args.launch:
            print("error: 游戏没在运行（加 --launch 自动启动）", file=sys.stderr)
            return 2
        launch_game()

    cua = NativeCua()
    ok, why = cua.available()
    if not ok:
        print(f"error: 电脑操作 helper 不可用: {why}", file=sys.stderr)
        return 2
    info = wait_window(cua)
    print(f"window: pid={info.get('pid')} focused={info.get('focused')}")

    status = probe_ready(args.probe_timeout)
    if status:
        print(f"already in world: tick={status.get('tick')} pos={status.get('pos')}")
        return 0

    if not args.enter_world:
        print("error: 不在世界里（加 --enter-world 自动进存档）", file=sys.stderr)
        return 2

    enter_world(cua)
    deadline = time.monotonic() + args.ready_timeout
    while time.monotonic() < deadline:
        status = probe_ready(args.probe_timeout)
        if status:
            print(
                f"in world: tick={status.get('tick')} pos={status.get('pos')}"
            )
            return 0
        time.sleep(2.0)
    print("error: 进世界超时（可能卡在主菜单/世界列表，看 out/ 截图）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())