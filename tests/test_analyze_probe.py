"""analyze_probe 的核心逻辑单测（不需要游戏）。

这些逻辑踩过两个坑，所以固化下来：
  - 聚类下标必须是**原始 samples 的下标**（曾经用过滤后列表的下标，差分全错）；
  - dump 是每 N tick 一次，某个速度簇可能没有 dump 帧，要回退到最近的一帧。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from driver.analyze_probe import (  # noqa: E402
    clusters,
    control_hits,
    diff_bytes,
    pick_segments,
    representative,
    steps,
)


def make_samples(speeds: list[float]) -> list[dict]:
    """按每 tick 的位移生成 pos 序列（最后一个样本没有后继）。"""
    samples = []
    z = 0.0
    for i, step in enumerate(speeds):
        samples.append({"t": i, "ok": True, "pos": [0.0, -49.0, z]})
        z += step
    samples.append({"t": len(speeds), "ok": True, "pos": [0.0, -49.0, z]})
    return samples


class StepsTest(unittest.TestCase):
    def test_steps_are_horizontal(self) -> None:
        samples = make_samples([0.2, 0.2])
        got = steps(samples)
        self.assertAlmostEqual(got[0], 0.2)
        self.assertAlmostEqual(got[1], 0.2)
        self.assertIsNone(got[2])


class ClustersTest(unittest.TestCase):
    def test_indices_point_into_samples(self) -> None:
        # 20 tick 走（0.216）+ 20 tick 疾跑（0.280）
        samples = make_samples([0.216] * 20 + [0.280] * 20)
        rows = clusters(steps(samples))
        by_center = {round(c, 2): ids for c, ids in rows}
        self.assertIn(0.22, by_center)
        self.assertIn(0.28, by_center)
        # 疾跑段的第一个 tick 下标应当是 20，而不是过滤后列表里的 0
        self.assertEqual(by_center[0.28][0], 20)

    def test_near_zero_ticks_ignored(self) -> None:
        samples = make_samples([0.0, 0.0, 0.2])
        rows = clusters(steps(samples))
        self.assertEqual([c for c, _ in rows], [0.2])


class PickSegmentsTest(unittest.TestCase):
    def test_slowest_vs_fastest_not_most_common(self) -> None:
        # 疾跑样本更多（30）也不能被选成参考段
        samples = make_samples([0.216] * 10 + [0.280] * 30)
        ref_c, top_c, ref_ids, top_ids, ratio = pick_segments(
            clusters(steps(samples)), min_samples=5
        )
        self.assertAlmostEqual(ref_c, 0.22, places=2)
        self.assertAlmostEqual(top_c, 0.28, places=2)
        self.assertAlmostEqual(ratio, 0.28 / 0.22, places=3)
        self.assertEqual(len(ref_ids), 10)
        self.assertEqual(len(top_ids), 30)

    def test_low_sample_cluster_is_not_reference(self) -> None:
        # 3 tick 的加速帧（0.15）不应成为参考段（min_samples=10）
        samples = make_samples([0.150] * 3 + [0.216] * 20 + [0.280] * 20)
        ref_c, _, _, _, _ = pick_segments(clusters(steps(samples)), min_samples=10)
        self.assertAlmostEqual(ref_c, 0.22, places=2)


class RepresentativeTest(unittest.TestCase):
    def test_falls_back_to_nearest_dump_frame(self) -> None:
        samples = make_samples([0.2] * 10)
        for i, s in enumerate(samples):
            if i % 5 == 0:
                s["raw"] = {"all": {"client": {}, "server": {}}}
        # 簇里只有 1,2,3 这些没有 all 的帧 → 回退到最近的 0 或 5
        got = representative(samples, [1, 2, 3], need_all=True)
        self.assertIsNotNone(got)
        self.assertIn(got["t"], (0, 5))

    def test_returns_none_without_any_dump(self) -> None:
        samples = make_samples([0.2] * 5)
        self.assertIsNone(representative(samples, [0, 1], need_all=True))


class DiffTest(unittest.TestCase):
    def test_diff_bytes(self) -> None:
        self.assertEqual(diff_bytes("0020", "0021"), [(1, 0x20, 0x21)])
        self.assertEqual(diff_bytes("00", "0000"), [])
        self.assertEqual(diff_bytes(None, "00"), [])


class ControlHitsTest(unittest.TestCase):
    def test_filters_position_noise(self) -> None:
        # A(走前半) / B(走后半) / C(疾跑)
        all_a = {
            "client": {
                "0xpos": "0102",  # 位移组件：A->B 也变
                "0xsprint": "0000",  # 疾跑专有
                "0xsame": "aa",
            }
        }
        all_b = {"client": {"0xpos": "0304", "0xsprint": "0000", "0xsame": "aa"}}
        all_c = {"client": {"0xpos": "0506", "0xsprint": "0001", "0xsame": "aa"}}
        hits = control_hits(all_a, all_b, all_c)
        cids = [cid for _, _, cid, _, _ in hits]
        self.assertIn("0xsprint", cids)
        self.assertNotIn("0xpos", cids)  # 控制组也变了 → 噪声
        self.assertNotIn("0xsame", cids)  # 完全没变


if __name__ == "__main__":
    unittest.main()