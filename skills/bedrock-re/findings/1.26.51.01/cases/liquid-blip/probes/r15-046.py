"""Analyze captured data only. No game, debugger, or disassembler access."""
from __future__ import annotations

import bisect
import json
import struct
from pathlib import Path

ROOT = Path("out/liquid-blip-dynamic-review")
PROBES = Path("skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes")
ROLE_RVA = 0x82A8D00  # Existing published role, not discovered by this script.
READ_RVA = ROLE_RVA + 0x55  # Observed dynamic checkpoint within that role.
WRITE_RVA = ROLE_RVA + 0x14D
NAMES = ["standing", "slow-a", "slow-b", "jump-a", "jump-b", "sink-a", "sink-b", "fast-lev"]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def f(value):
    return "—" if value is None else f"{value:.9f}"


def table(lines, headings, rows):
    lines.extend(["", "| " + " | ".join(headings) + " |", "| " + " | ".join("---" for _ in headings) + " |"])
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    lines.append("")


def analyze_run(directory, name, meta):
    d = load(directory / f"{name}.json")
    paired = load(directory / f"{name}-paired.json")
    snapshots = d["rpm"]
    times = [p["ns"] for p in snapshots]
    events = []
    wids = {w["id"]:w["tag"].removeprefix("review-") for w in d.get("watches", [])}
    drain = d.get("watch_rows") or {}
    frequency = drain.get("qpc_frequency", 1)
    for hit in drain.get("hits", []):
        rva = hit["rip"] - meta["module_base"]
        if not ROLE_RVA <= rva < ROLE_RVA + 0x1D0:
            continue
        assert hit.get("value_ok") and hit["value_size"] == 4
        event_ns = round(hit["qpc"] * 1_000_000_000 / frequency)
        at = bisect.bisect_left(times, event_ns)
        snap = min(snapshots[max(0, at-2):at+3], key=lambda p:abs(p["ns"]-event_ns))
        scope = wids[hit["watch"]]
        value = struct.unpack("<f", bytes.fromhex(hit["value_hex"]))[0]
        candidates = [r for r in d["record"] if max(abs(snap["client"][i]-r[k]) for i,k in enumerate(("x","y","z"))) < 1e-5]
        candidates.sort(key=lambda r:abs(r["tick"]-(hit["tick"]+1)))
        record = candidates[0] if candidates else None
        event = {"scope":scope, "kind":"read" if rva == READ_RVA else "overwrite" if rva == WRITE_RVA else "other", "rva":hex(rva), "watch_tick":hit["tick"], "qpc":hit["qpc"], "qpc_frequency":frequency, "tid":hit["tid"], "value_hex":hit["value_hex"], "raw_vel_y":value, "rpm_distance_us":(snap["ns"]-event_ns)/1000, "rpm":snap, "record":record if scope=="client" else None}
        if scope == "client" and record is not None:
            actual = record["vy"]
            event.update({"record_tick":record["tick"], "feet_y":record["feet_y"], "actual_vy":actual, "difference":value-actual, "ratio":value/actual if abs(actual)>1e-7 else None, "record_box_gap":record["feet_y"]+.6+actual+46, "raw_box_gap":record["feet_y"]+.6+value+46})
        events.append(event)
    # One read per side and watch tick in these windows; don't silently drop
    # ambiguities if a future version starts invoking the system repeatedly.
    client_reads = [e for e in events if e["scope"]=="client" and e["kind"]=="read"]
    assert len({e["watch_tick"] for e in client_reads}) == len(client_reads)
    by_tick={e["watch_tick"]:e for e in client_reads}
    first_write = next((e for e in events if e["scope"]=="client" and e["kind"]=="overwrite"), None)
    first_trigger = by_tick[first_write["watch_tick"]] if first_write else None
    previous = by_tick.get(first_write["watch_tick"]-1) if first_write else None
    api_duration=(d["api"][-1]["ns_end"]-d["api"][0]["ns_begin"])/1e9
    rpm_duration=(snapshots[-1]["ns"]-snapshots[0]["ns"])/1e9
    counter_times={}
    for p in snapshots:
        if p["tick_before"]==p["tick_after"]:
            counter_times.setdefault(p["tick_before"],p["ns"])
    first,last=min(counter_times),max(counter_times)
    rate=(last-first)/((counter_times[last]-counter_times[first])/1e9)
    model=None
    if name.startswith("slow") or name=="fast-lev":
        a,b=.64,.03 if name=="fast-lev" else .01
    elif name.startswith(("jump","sink")):
        a,b=.8,-.005
    else:
        a,b=None,None
    if a is not None:
        before=[e for e in client_reads if first_write is None or e["watch_tick"]<=first_write["watch_tick"]]
        residuals=[abs(e["raw_vel_y"]-(a*e["actual_vy"]+b)) for e in before if e.get("record")]
        model={"a":a,"b":b,"n":len(residuals),"max_abs_residual":max(residuals) if residuals else None}
    return {"name":name,"directory":directory.as_posix(),"start_height_requested":d["requested_height"],"start_state":d["start_state"],"blips":[r["tick"] for r in d["record"] if abs(r["vy"]-.3)<1e-4 or abs(r["vy"]-.34)<1e-4],"record_count":len(d["record"]),"api_hz":d["api_cycles"]/api_duration,"rpm_hz":d.get("rpm_read_cycles",len(snapshots))/rpm_duration,"tick_hz":rate,"api_tick_brackets":[[a["state_before"]["tick"],a["state_after"]["tick"]] for a in d["api"]],"watch_lost":drain.get("lost"),"events":events,"paired_post":paired,"record":d["record"],"first_trigger":first_trigger,"previous_read":previous,"model":model}


def main():
    meta=load(ROOT/"r14-values/metadata.json")
    runs={name:analyze_run(ROOT/"r14-values",name,meta) for name in NAMES}
    control_meta=load(ROOT/"r14-control/metadata.json")
    controls={name:analyze_run(ROOT/"r14-control",name,control_meta) for name in ["standing-dry","slow-b","jump-b"]}
    data={"evidence":"live-only; independent dynamic review; no new static analysis", "meta":meta,"runs":runs,"controls":controls}
    target=PROBES/"liquid-blip-raw-velocity-review.json"
    target.write_text(json.dumps(data,ensure_ascii=False,indent=1),encoding="utf-8")
    lines=["# 液体退出弹跳：原始 VelY 与录制速度的独立动态复核", "", "范围：MCBE 1.26.51.01 / LeviLamina 26.51.7，纯动态；使用现有角色定位观察区间，未反编译或解析指令。结论均为 `[实测] live-only`，不提升为新增静态机制结论。", "", "## 判决", "", "- Q1 **确认存在状态相关差值；推翻用单一固定比例换算**。慢升终端两次差值约 −1.01e−6 / +1.42e−6；深水起跳触发前约 −0.03729 / −0.03578。", "- Q2 **推翻‘慢升阈值零头由原始 VelY 小一截造成’**：替换后阈值推算仅改动约百万分之一，不能消去 0.013～0.052 的正偏差。实际检测盒边界的形成原因仍**存疑**。", "- Q3 **确认客户端、服务端各有一份且两侧都执行检测**。判断客户端录制流中的弹跳，应使用客户端原始值；服务端 runtime-id 查询不可当作客户端输入。", "- Q4 **确认本次重复性**：两种慢升起点、两种起跳起点、两种下沉起点；另有无观察点对照。只证明这些条件下的重复结果，不覆盖所有版本/配置。", "", "## 方法、采样与相位", "", "先确认生存、flying=false、mayfly=false；重置 creative→survival 后复读。输入锁定，窗口聚焦；水池水在 −48/−47，液面 −46，北墙 z=299 高至 −38，初始中心 z=300.32。方块/液位回读为 stone/water/water/air、上下层 liquid_depth=0。每次预等四 tick；有效检测段实际贴墙 z≈300.299988。", "", "普通 state→ecs_component→mem_read→state 只约 4 次/秒，跨数 tick。除保留这些括号读数，还用只读 PROCESS_VM_READ 读取已由 API 解析的两侧地址、客户端 AABB 与 harness tick 计数。记录流 period=1。观察点 mode=rw/thread=both/sample=true 记录目标四字节及 QPC，按现有角色区间筛选；未使用或解析命中的 code_hex。", "", "观察点的值是指令执行后读到的内存，并非冻结的寄存器操作数；跨线程不原子。客户端字段在该读取点所在 tick 线程内采样，另有 RPM 状态与 0.3 覆写点交叉检查。两侧值没有假装同时采集。", "", "本次检测访问的 watch_tick=w，所处位置对应录制行 R=w+1；0.3 覆写在同一 w 发生，产生的 0.3/0.34 位移通常见于 R=w+2。此关系由 QPC、位置匹配及 canary 交叉验证，不能直接把 w 与录制 tick 相等连接。下表 tick 均为 R，同时保留 w。", "", "`difference=raw−actual`；ratio 在 actual=0 时未定义。read 是检测读取检查点；overwrite 是之后 0.3 写入检查点；post/RPM 只表示稳定阶段原始字段，不能冒充检测输入。"]
    table(lines,["条件","起点脚高（请求）","录制行数","API 循环/s","RPM 循环/s","tick/s","观察环 lost","0.3/0.34 所在录制 tick"],[[n,f(r["start_height_requested"]),r["record_count"],f'{r["api_hz"]:.2f}',f'{r["rpm_hz"]:.0f}',f'{r["tick_hz"]:.2f}',r["watch_lost"],str(r["blips"])] for n,r in runs.items()])
    lines += ["## Q1 关键采样", "", "表中 read 为实际检测访问点的字节值；同位置的后续 0.3 覆写另列。"]
    key=[]
    dry=controls["standing-dry"]["paired_post"][5]
    key.append(["干地站立",dry["tick"],"—","post/RPM",f(dry["feet_y"]),f(dry["actual_vy"]),f(dry["raw_client"]),f(dry["delta_client"]),f(dry["ratio_client"])])
    wet=runs["standing"]["paired_post"][5]
    key.append(["水中站立",wet["tick"],"—","post/RPM",f(wet["feet_y"]),f(wet["actual_vy"]),f(wet["raw_client"]),f(wet["delta_client"]),f(wet["ratio_client"])])
    for name in ["slow-a","slow-b","jump-a","jump-b","sink-a","sink-b","fast-lev"]:
        r=runs[name];e=r["first_trigger"] or [e for e in r["events"] if e["scope"]=="client" and e["kind"]=="read" and e.get("record")][-1]
        key.append([name,e["record_tick"],e["watch_tick"],"read",*[f(e[k]) for k in ("feet_y","actual_vy","raw_vel_y","difference","ratio")]])
    for kind in ("read","overwrite"):
        trigger=runs["slow-a"]["first_trigger"]
        e=next(e for e in runs["slow-a"]["events"] if e["scope"]=="client" and e["kind"]==kind and e["watch_tick"]==trigger["watch_tick"]+1)
        key.append(["slow-a 弹跳下一拍",e["record_tick"],e["watch_tick"],kind,*[f(e[k]) for k in ("feet_y","actual_vy","raw_vel_y","difference","ratio")]])
    table(lines,["条件","录制 tick R","watch tick w","相位","脚高","实际 vy","原始 VelY","差","比"],key)
    lines += ["## Q2 只替换 VelY 后的阈值", "", "按题设简化式分别计算 gap_actual=feet_y+0.6+actual_vy−surface 和 gap_raw=feet_y+0.6+raw_VelY−surface；不把覆写后的 0.3 代回触发判据。触发指同一检查阶段存在 0.3 覆写，并不是该行录制速度已经变成 0.3。"]
    table(lines,["条件","R","同阶段触发","旧 gap","原始值 gap","改变"],[[name,e["record_tick"],trigger,*[f(e[k]) for k in ("record_box_gap","raw_box_gap","difference")]] for name in ["slow-a","slow-b","jump-a","jump-b","fast-lev"] for trigger,e in [(False,runs[name]["previous_read"]),(True,runs[name]["first_trigger"])]])
    lines += ["慢升两组中，原始值替换后，前一拍 gap 仍为正但未触发，后一拍 gap 更正才触发。故原文‘0.02～0.05 是引擎盒底硬阈值’仍只是按录制行推算的观测，不能当作已经闭合的边界。还缺检测入口传给碰撞/液体查询的实际六浮点盒及其同相位位置基准；普通组件 AABB 的分开读取不等于这份最终查询盒。此处不新增静态分析，也不猜隐藏常数。", "", "## Q3 两侧身份与读取", "", "读取客户端的方法：", "", "```python", "s = client.state()", "loc = client.ecs_context(s['context'], 459100728)", "raw = client.mem_read(loc['address'], 36)", "vel_y = struct.unpack('<9f', bytes.fromhex(raw['hex']))[7]", "```", "", "ecs_context 返回 address/size 等元数据，不返回 hex；其空 len 不是没有组件。ecs_registries 给出的 registry 与这个 context 的 registry 一致。runtime_id=1 的 ecs_component 则明确 scope=server，且地址、registry、entity 均独立。"]
    table(lines,["侧","registry","entity","StateVector 地址","本轮读取/覆写线程"],[["client",meta["client"]["registry"],meta["client"]["entity"],meta["client"]["address"],str(sorted({e['tid'] for r in runs.values() for e in r['events'] if e['scope']=='client'}))],["server",meta["server_entity"]["registry"],meta["server_entity"]["entity"],meta["server"]["address"],str(sorted({e['tid'] for r in runs.values() for e in r['events'] if e['scope']=='server'}))]])
    table(lines,["条件","客户端 read / overwrite 次数","服务端 read / overwrite 次数","触发前客户端 VelY","同 watch tick 服务端 VelY"],[[name,str((sum(e['scope']=='client' and e['kind']=='read' for e in r['events']),sum(e['scope']=='client' and e['kind']=='overwrite' for e in r['events']))),str((sum(e['scope']=='server' and e['kind']=='read' for e in r['events']),sum(e['scope']=='server' and e['kind']=='overwrite' for e in r['events']))),f(r['first_trigger']['raw_vel_y']) if r['first_trigger'] else '—',f(next((e['raw_vel_y'] for e in r['events'] if e['scope']=='server' and e['kind']=='read' and r['first_trigger'] and e['watch_tick']==r['first_trigger']['watch_tick']),None))] for name,r in runs.items()])
    lines += ["访问地址落在已有 @liquid_exit_blip 区间内，动态检查点为 RVA 0x82A8D55（读取）与 0x82A8E4D（覆写）；角色的静态 VA 按现有 image_base 换算，未进行新静态取证。两侧都在各自线程访问并写出 0.3。要解释本地录制流，用客户端数据；要解释服务端判定，应独立采服务端位置、AABB 和该侧访问，不能混用客户端脚高。", "", "## Q4 重复性与对照", "", "以下线性式只是对本次同相位动态数值的拟合，不是新增机制定案。residual 相对于录制的实际 vy，包含浮点位置相减造成的量化误差。"]
    table(lines,["条件","拟合 raw≈a×actual+b","样本","最大绝对残差"],[[name,f"{r['model']['a']}×actual{r['model']['b']:+}",r['model']['n'],f(r['model']['max_abs_residual'])] for name,r in runs.items() if r['model']])
    table(lines,["关闭观察点后的条件","录制行","0.3/0.34 所在 tick"],[[n,r['record_count'],str(r['blips'])] for n,r in controls.items()])
    lines += ["无观察点的慢升和起跳仍有相同行为序列。观察点和高频 RPM 都有开销，不能凭这个对照声称零扰动。既有 r12-037 的八组无观察点采样也保留在 out 原文中。", "", "## 每次原始采样表", "", "以下逐行列出各次记录：优先使用同位置对应的检测 read/overwrite；没有检测访问时列 post/RPM 阶段并注明。完整字节/QPC、两侧事件、API tick 括号和录制原文在配套 JSON；没有把未采到的访问补成零。"]
    for name,r in {**runs,"standing-dry":controls["standing-dry"]}.items():
        lines += [f"### {name}","",f"请求脚高 {r['start_height_requested']}；实际录制前脚高 {r['start_state']['feet_y']}；飞行/MayFly={r['start_state']['flying']}/{r['start_state']['mayfly']}。"]
        by_record={}
        for e in r['events']:
            if e['scope']=='client' and e.get('record_tick') is not None:
                by_record.setdefault(e['record_tick'],[]).append(e)
        by_post={p['tick']:p for p in r['paired_post']}
        rows=[]
        for rec in r['record']:
            es=by_record.get(rec['tick'],[])
            if es:
                for e in es:rows.append([rec['tick'],e['watch_tick'],e['kind'],*[f(e[k]) for k in ('feet_y','actual_vy','raw_vel_y','difference','ratio')],e['value_hex']])
            elif rec['tick'] in by_post:
                p=by_post[rec['tick']];rows.append([rec['tick'],'—','post/RPM',f(p['feet_y']),f(p['actual_vy']),f(p['raw_client']),f(p['delta_client']),f(p['ratio_client']),'—'])
            else:
                rows.append([rec['tick'],'—','未配对',f(rec['feet_y']),f(rec['vy']),'—','—','—','—'])
        table(lines,["录制 R","watch w","相位","脚高","实际 vy","原始 VelY","差","比","原始四字节 hex"],rows)
    lines += ["## 工程验证与恢复", "", "构建通过；观察点可选采样 live canary 26/26，覆盖宽度 1/2/4/8、字节、QPC、tick 相位、默认关闭和撤销。相关 Python 单测 22/22；ruff check/format、mypy 通过。没有重跑完整项目 selftest，不沿用旧 1169/1169 作为本轮验收。", "", "每轮只清理自己加的 levitation/water_breathing，恢复起点/朝向并释放输入；测试池拆除。正常保存重启过一次以加载新 DLL，未强停。末态以 summary.json 和 final-integrity.json 为准；站立后原始 VelY 留负值是本次已测现象。未 git commit，未 git push。", "", "脚本调试失败 r13-038/039/040 原样留档：分别是可选组件缺失、诊断返回结构键使用错误、观察点 head 键使用错误；修正后 r13-041/r14-042/043/044 成功。失败记录没有算作实验通过；遗留观察点已逐项清除。"]
    report=ROOT/"report.md"
    lines += ["## 可复核证据入口", "", "- [逐次录制、配对表、两侧事件与字节/QPC](liquid-blip-raw-velocity-review.json)", "- [可选观察点采样 canary 26/26](r14-042.out)", "- [八条件实测日志](r14-043.out)及[采集脚本](r14-043.py)", "- [干地与关闭观察点的对照](r14-044.out)", "", "原始高频 RPM 全量和 API 响应保留在 `out/liquid-blip-dynamic-review/r14-values/`、`r14-control/`；公开 JSON 保存录制全行、事件附近 RPM 与 API tick 括号。现场恢复快照为 `out/liquid-blip-dynamic-review/final-integrity.json`。"]
    report.write_text("\n".join(lines)+"\n",encoding="utf-8")
    public_report=target.with_suffix(".md")
    public_report.write_text(report.read_text(encoding="utf-8"),encoding="utf-8")
    print(json.dumps({"report":public_report.as_posix(),"data":target.as_posix(),"runs":{n:{"first_trigger_tick":r['first_trigger']['record_tick'] if r['first_trigger'] else None,"previous_read_tick":r['previous_read']['record_tick'] if r['previous_read'] else None,"model":r['model']} for n,r in runs.items()}},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
