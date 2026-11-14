"""Compare detector-entry arguments with recorded movement; data analysis only."""
import json
import struct
from pathlib import Path

ROOT = Path("out/liquid-blip-dynamic-review/r19-pinned")
PUBLIC = Path("skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes/liquid-blip-operation-order")


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def floats(snapshot):
    assert snapshot["ok"]
    return list(struct.unpack(f"<{snapshot['size']//4}f", bytes.fromhex(snapshot["hex"])))


def main():
    meta = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    runs = {}
    for name in ("slow-a", "slow-b", "jump-a", "jump-b", "sink-a", "fast-lev"):
        data = json.loads((ROOT / f"{name}.json").read_text(encoding="utf-8"))
        values = []
        hits = data["watch_rows"]["hits"]
        tags = {w["id"]: w["tag"] for w in data["watches"]}
        write_threads = {tid for w in data["watches"] if w["tag"] == "review-overwrite-entry" for tid in w["threads"]}
        for hit in hits:
            if tags.get(hit["watch"]) != "review-entry":
                continue
            snaps = {s["reg"]: s for s in hit["snapshots"]}
            scope = next((scope for scope in ("client", "server") if snaps["r9"]["address"] == meta[scope]["address"]), None)
            if scope is None:
                continue
            box, travel, state = (floats(snaps[reg]) for reg in ("rdx", "r8", "r9"))
            candidates = [r for r in data["record"] if max(abs(state[i] - r[k]) for i, k in enumerate(("x", "y", "z"))) < 1e-5]
            candidates.sort(key=lambda r: abs(r["tick"] - hit["tick"] - 1))
            record = candidates[0] if candidates and scope == "client" else None
            dy = f32(f32(f32(travel[5] - state[1]) + f32(.6)) + state[7])
            predicted = f32(box[1] + dy)
            next_entry = min((h["qpc"] for h in hits if tags.get(h["watch"]) == "review-entry" and h["tid"] == hit["tid"] and h["qpc"] > hit["qpc"]), default=float("inf"))
            overwritten = any(h["tid"] == hit["tid"] and hit["qpc"] < h["qpc"] < next_entry and tags.get(h["watch"]) == "review-overwrite-entry" for h in hits)
            row = {
                "scope": scope, "watch_tick": hit["tick"], "qpc": hit["qpc"], "tid": hit["tid"],
                "snapshots": hit["snapshots"], "box": box, "travel": travel, "state": state,
                "position_y": state[1], "previous_position_y": state[4], "travel_y": travel[5],
                "raw_vy": state[7], "position_delta_y": state[1] - state[4],
                "travel_minus_position": travel[5] - state[1], "travel_minus_previous": travel[5] - state[4],
                "dy": dy, "predicted_box_min_y": predicted, "predicted_gap": predicted + 46,
                "record": record,
                "overwritten": (overwritten if hit["tid"] in write_threads else None) if meta.get("entry_only") else any(tags.get(h["watch"]) == f"review-{scope}" and h["tick"] == hit["tick"] and h["rip"] - meta["module_base"] == 0x82A8E4D for h in hits),
            }
            if record:
                row.update({"record_tick": record["tick"], "feet_y": record["feet_y"], "actual_vy": record["vy"],
                    "bbox_minus_feet": box[1] - record["feet_y"],
                    "position_minus_feet": state[1] - record["feet_y"],
                    "raw_minus_actual": state[7] - record["vy"],
                    "position_delta_minus_actual": state[1] - state[4] - record["vy"],
                    "old_gap": record["feet_y"] + .6 + record["vy"] + 46})
            values.append(row)
        client = [v for v in values if v["scope"] == "client" and v["record"]]
        first = next((v for v in client if v["overwritten"]), None)
        previous = next((v for v in client if first and v["watch_tick"] == first["watch_tick"] - 1), None)
        runs[name] = {"lost": data["watch_rows"]["lost"], "values": values, "first_trigger": first, "previous": previous, "record": data["record"]}
        for label, row in (("previous", previous), ("trigger", first)):
            if row:
                print(json.dumps({"run": name, "label": label, **{k:v for k,v in row.items() if k not in ("snapshots", "record", "box", "travel", "state")}}))
    target = ROOT / "phase-analysis.json"
    target.write_text(json.dumps({"meta": meta, "runs": runs}, indent=1), encoding="utf-8")
    public_data = {"evidence_tier": "live", "status": "live-only", "static_analysis": "none; formula/signature/addresses reused from R-F16", "meta": meta, "runs": runs}
    PUBLIC.with_suffix(".json").write_text(json.dumps(public_data, indent=1), encoding="utf-8")
    lines = [
        "# 液体退出检测：位置、速度相位与运算顺序的动态对质", "",
        "本报告补全上一轮只比较 VelY 的测量。范围为 MCBE 1.26.51.01 / LeviLamina 26.51.7；纯动态，不新增反编译、指令解码或静态调度结论。公式与五参签名复用 R-F16。", "",
        "## 结论与公式", "",
        "**确认（实测 live-only）**：检测入口的 `MobTravel+0x14` 等于本次移动前 Y（同时等于 StateVector+0x10），`StateVector+0x04` 是移动后 Y；AABB 底等于移动后的脚 Y。两种眼睛位置相减，1.6200104 的眼高抵消。", "",
        "```text", "v_move = Y_after - Y_before  # 本次已发生的竖直位移，与配对录制 vy 一致", "v_det  = StateVector.VelY    # 检测时已更新的速度", "dy = (Y_before - Y_after) + 0.6 + v_det", "   = 0.6 + v_det - v_move", "box_min_y = AABB.min_y + dy", "```", "",
        "因此净偏移既不是一律 0.6，也不是一律 0.6+录制 vy。慢升终端 v_det≈v_move，净偏移≈0.6；深水跳中 v_det≈0.8v_move−0.005，净偏移≈0.595−0.2v_move。公式按 float32 逐步重建，位置差本身也受 float32 量化。", "",
        "**更正**：之前把 `MobTravelY−PositionY≈0` 当作简化前提，漏掉了约一拍位移。‘只换原始 VelY 仍解释不了零头’这一对比本身成立，但不能据此把零头归为未知引擎阈值。补回位置差后，旧正 gap 的未触发样本变成负 gap。", "",
        "## 触发前后实参", "",
        "tick 为匹配位置的录制 R，检测入口 watch tick=w=R−1；写点在同一 w，位移效果通常在 R+1。所有位置/速度以同侧入口实参比较，未把不同 API 响应假装同时采样。", "",
        "| 条件/相位 | R | PositionY（眼） | MobTravelY（眼） | 录制 vy | 检测 VelY | 位置差 | 净 dy | 旧 gap | 完整 gap | 写点 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, run in runs.items():
        for phase, value in (("前一拍", run["previous"]), ("触发", run["first_trigger"])):
            if value:
                nums = [f"{value[k]:.9f}" for k in ("position_y", "travel_y", "actual_vy", "raw_vy", "travel_minus_position", "dy", "old_gap", "predicted_gap")]
                lines.append("| " + " | ".join([name+" "+phase, str(value["record_tick"]), *nums, str(value["overwritten"])]) + " |")
    all_values = [v for run in runs.values() for v in run["values"]]
    paired = [v for v in all_values if v["record"]]
    mismatches = [v for v in all_values if v["overwritten"] is not None and (v["predicted_gap"] > 0) != v["overwritten"]]
    assert not mismatches
    lines += ["", "## 覆盖与限制", "",
        f"本轮 {len(all_values)} 条两侧入口、其中 {len(paired)} 条配对到客户端录制；全部 MobTravelY−StateVector.previousY 为 0。所有安装了写点观察的行，完整 gap>0 与命中写点一致（{len(mismatches)} 个反例）；各运行环 lost=0。", "",
        "入口/已知写点使用执行 DR；VEH 固定拷贝 rdx/r8/r9 指向的 32/40/36 字节，恢复原指令执行。原始字节在配套 JSON。快照在当前线程停住时顺序取得，不与其他线程原子同步；观察点有时序开销。没有直接钩住块查询虚槽，完整盒是以入口实参和既有公式重建。", "",
        "这只验证当前无遮挡、源水池几何；gap 恰好为零未采到，不区分严格 >0 与 >=0；不能用它代替其他墙体/头顶碰撞门。`thread=both` 仅覆盖安装回执所列线程：jump-a 本轮未覆盖服务端入口，其余数据不补零、不算‘没执行’。", "",
        "## 同线程速度更新轨迹", "",
        "r17-056 的 jump-b，watch tick 2061，tid 37160：更新前 raw=0.1539755166 → 水阻力后 0.1231804118 → 重力后 0.1181804091 → 检测读取相同值 → blip 覆写 0.3000000119。同位置录制 vy=0.1539764404，两者最初的小差是位置量化范围。对应命中 RVA 为 0x2DEE7C1/0x2DEE7CE/0x2E0D28A/0x2E0D290/0x82A8D55/0x82A8E4D，按既有 R-F1/R-F3/R-F16 标识访问区域，未解析指令。", "",
        "同轮 slow-a，watch tick 1659：0.0277773906 → 水阻力后 0.0222219136 → 悬浮乘 0.8 后 0.0177775305 → 加 0.01 后 0.0277775303 → 检测读取 → 覆写 0.3。因此这里是位置已推进、随后速度已更新的混合相位；不能把检测速度当成刚完成移动所用的速度。", "",
        "这是这些同线程轨迹中的动态先后，不新增所有 ECS pass 的静态注册顺序结论。0.3 写入后的位移发生在随后阶段；若其后又有水跳 +0.04，就会观察到 0.34，不能把写入/录制行编号等同。", "",
        "## 逐次原始配对表", "",
        "完整 32/40/36 字节、QPC、两侧参数及录制原文见 [JSON](liquid-blip-operation-order.json)。下表保留每条客户端已配对入口；没有把未配对行补成零。",
    ]
    for name, run in runs.items():
        lines += ["", f"### {name}", "", "| R/w | 脚 Y | PositionY | 移动前 Y | 实际 vy | 检测 VelY | 净 dy | 完整 gap | 写点 |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for v in run["values"]:
            if not v["record"]:
                continue
            nums = [f"{v[k]:.9f}" for k in ("feet_y", "position_y", "travel_y", "actual_vy", "raw_vy", "dy", "predicted_gap")]
            lines.append("| " + " | ".join([f"{v['record_tick']}/{v['watch_tick']}", *nums, str(v["overwritten"])]) + " |")
    lines += ["", "## 证据与验证", "", "- [r19-058：六条件、入口/写点相同线程集合](r19-058.out)；[采集脚本](r19-058.py)", "- [r17-056：混合观察点，含同线程速度数值轨迹](r17-056.out)；该轮 jump-a 缺客户端入口，不拿缺采作阴性。", "- [r18-057：纯执行断点对照](r18-057.out)；该轮部分写点未覆盖服务端，后续 r19-058 显式按入口回执线程武装。", "- [r17-055：执行观察点准入与清理 11/11](r17-055.out)。r17-054 的失败是脚本误期望清理回执有 ok 字段；原文保留，修正为事件名与实际无观察点回读。", "- C++ 构建、相关 Python 单测 23/23；ruff/format/mypy。完整项目离线/live 套件未重跑。正常保存重启加载探针，使用 --no-focus，实验前确认后台 tick 推进。", "", "本轮不 git push，不新增静态逆向。最终现场恢复见 out/liquid-blip-dynamic-review/phase-final-integrity.json。"]
    PUBLIC.with_suffix(".md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"entry_samples": len(all_values), "paired": len(paired), "gap_mismatches": len(mismatches), "report": PUBLIC.with_suffix('.md').as_posix()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
