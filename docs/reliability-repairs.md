# 可靠性与验收边界

本文只保留当前可复现的验收基线、前提和未覆盖范围。旧修复轮次与历史报告以 Git 历史和
`out/` 的原始日志为准，不再逐轮追加。

## 验收规则

- 离线、native 模型、基础现场和完整现场是不同层次，不能互相替代。
- PASS/FAIL/SKIP 分开统计；`--require-complete` 将 required skip 判为失败。
- 生命周期测试必须检查唯一终结、拒绝/超时、旧编号或旧世界隔离、恢复和实际副作用回读。
- 保存状态不确定时保留游戏进程；只有明确放弃未保存更改才允许 force-stop。
- 原生模型中的零分配、并发和容量结论，不证明真实游戏中的性能或所有 SDK 边界安全。

## 验收基线与局部专项

以下数字只在对应代码、游戏版本（`1.26.51.01`）与权限前提下有效；任何代码或配置变更后都应按影响范围重新运行，不能把旧基线当作未来提交的保证。

| 范围 | 当前结果 | 覆盖重点与前提 |
| --- | --- | --- |
| 完整离线 | **496/496**（native ASAN **3/3** 为既有基线，本轮未设 `LEVIPILOT_NATIVE_ASAN`） | Python、native 模型、CLI、协议、安全/格式化检查与资源生命周期；ASAN 回归需显式开启 |
| 静态与文档检查 | `driver/check.py --cpp-format`（**16/16**） | `ruff check`/`format`、`mypy`、`profiles`、`roles`、`lint_public`、`command docs`、机制守卫、`probes ruff`、C++ `clang-format 22.1.8`、Markdown 相对链接；另经 LLVM 22 C++ 构建 |
| 完整现场 `--require-complete` | **1173/1173**，0 fail、0 skip、0 required skip | 当前默认全套，含权限/空槽前提；未开 `LEVIPILOT_VIOLATION_DUMP`；`packets` 段行走后复位到本段起点，行走不再把跳跃/落地检查带到有顶棚或水中的位置 |
| 安全与鉴权 `--security-only --require-complete` | **22/22**，0 fail、0 skip、0 required skip | 最终 DLL 实测：管道 1-ACE DACL、`pipe_token` 未认证/错令牌拒止、`allow_mem_read=false` 与 `allow_togglefly=false` 及两次热重载完整还原 |
| 受控空闲真实热重载 `--hot-reload-only --require-complete` | **22/22**，0 fail、0 skip、0 required skip | 最终 DLL 实测：拒绝不存在模块、静默准入、`/ll reload probe` 同 PID 切换代次/日志/四 hook/tick/`/togglefly` 恢复及配置/DLL/crash 一致 |
| 连接生命周期 `--connection-only --require-complete` | **11/11**，0 fail、0 skip、0 required skip | 最终 DLL 实测：弃用句柄、正常与异常退出后的管道释放与重连 |
| SDK 局部边界 | **69/69**（既有历史基线，未单跑） | client/server lookup、submit、run 的 SEH/C++ 故障，唯一失败回执、所有权退休、后续任务及真实 `/togglefly` 恢复；默认完整现场含相同内核覆盖 |
| 领域效果保留 | **126/126**；进入/退档 **35/35** | 夜视跨查询/聊天退档、宏播放、任务及世界进入恢复仍存在，结束只清理测试效果 |
| 朝向/传送专项 | **45/45** | 保持 0.1° 容差与连续三个 tick；移除 state 快照之后的额外延迟 |
| 有界查询/重连 | **8/8** | 四轮共 1040 次查询，约 24–25 次/秒，显式关闭并重连；仅该专项内未复现失联 |
| `lifecycle-prepare-only --require-complete` | **8/8**，0 fail、0 skip、0 required skip | 最终 DLL 实测：持续/到期输入、事件持键、hook 恢复与姿态恢复 |
| 守望开启时的真实重载 | **25/25**（既有历史基线，本轮未重跑） | 旧环 checkpoint、明确观察空窗、新 DLL 环游标归零、重新武装和最终导出/停止 |
| 在途回调竞争专项（`--callback-drain-only`） | **132/132**（既有历史基线，**本轮未重跑**） | 人为延迟回调/同线程卸载拒绝压力专项；本轮仅跑常规热重载与安全重载，未重跑该压力专项 |
| 输入事件专项 | **46/46** | 真实键状态、raw/注入对照、标记恢复和输入释放 |
| tick 客户端读取恢复 | **55/55** | pre/post 局部 SEH 与既有恢复链，不是整个 tick 的 SEH |
| 世界进入/退档 | **32/32** | `leave_id`、菜单快照和正常恢复；异常 HBUI 根因不在覆盖内 |
| 飞行短/长复验 | **50/50** | 效果保留、自然到期、哨兵占用与姿态恢复 |
| 长录制 | **9/9**（既有记录，近期未复跑） | 同一 `record_id` 的 200000 tick、保存、重载与等待器接续 |
| 最终环境恢复 | **17/17** | 原配置逐字节恢复并重启加载，姿态/能力/效果/背包/输入回读，四 hook、空回调所有权、DLL 一致和 crash 文件无变化 |
| 观察点采样与准入专项 | canary **26/26**；准入/撤销 **11/11**；离线回归 **22/22** / **23/23** | `watch_set sample=true`（1/2/4/8 字节、变化值、QPC、tick 相位、默认关闭及撤销），六条件同步入口 202 条；未在全套完整现场中重跑，详见[动态复核](../skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes/liquid-blip-raw-velocity-review.md)与[运算顺序动态对质](../skills/bedrock-re/findings/1.26.51.01/cases/liquid-blip/probes/liquid-blip-operation-order.md) |
| 游标与被动采集器离线回归 | **51/51** | `test_hooks`、`test_delta_watch`、`test_landing`、`test_scenario`：默认/显式游标、交替安装、全局分页后过滤、分轴范围、部分安装失败、context 改变、首错/清理失败与丢失统计；`driver.delta_watch` 尚未现场重采 |

常用复核入口：

```bash
py -3.11 driver/check.py --cpp-format-all
py -3.11 driver/selftest.py --require-complete
py -3.11 driver/selftest.py --hot-reload-only --require-complete
py -3.11 driver/selftest.py --security-only --require-complete
py -3.11 driver/selftest.py --sdk-boundaries-only --require-complete
py -3.11 driver/selftest.py --callback-drain-only --require-complete
```

## 仍未覆盖或未闭合

| 项目 | 当前边界 |
| --- | --- |
| 任意外部回调时序 | 已接入统一计数与所有权，现场覆盖 client/server/tick 竞争；未逐个注入所有 SDK、第三方回调和任意异常栈时序 |
| 活跃诊断下卸载 | 有明确拒绝及保持 hook/准入的现场验收；需先显式停止诊断，不支持把活跃诊断会话事务性迁移到新 DLL |
| HBUI 异常退档 | 正常退档/恢复通过，`worlds.ready=false` 异常状态的形成根因未闭合 |
| 显式诊断开销 | `serialize_ids` 可能分配且受默认 1 秒累计预算；正在执行的 SDK 调用不能被预算中断。没有可重复的提速结论 |
| 游戏网络竞态 | 共享序列化缓冲的跨线程使用是外部限制；有限未复现不等于根治 |
| 高频管道查询 | 历史高频查询后失联只保留诊断；客户端 GC/EOF 恢复不等于所有 Errno 22 已解决 |

## 取证与恢复

`LEVIPILOT_VIOLATION_DUMP` 在所有 live 入口启用增量导出，报告包含真实/合成记录、丢失和
观察空窗。合成违规与手动路由只验证设施，不能当作自然断开。

BadPacket 的静态/实测证据见 `notes/badpacket-setactordata-20261031.md`：本地深拷贝路径没有
帧长度前缀，粘连帧来自游戏共享序列化缓冲竞态。邻接堆字节不得解释为声明长度。

恢复验收应同时保存首错、配置/姿态/能力/背包/输入状态和 crash 文件清单。恢复失败不能覆盖
原始失败，也不能为通过检查而放宽保存或进入门禁。
