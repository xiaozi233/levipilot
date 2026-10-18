# behavior_pack

用于场景准备和 GameTest 的行为包，放在这里开发，再拷贝/挂载到世界。

## 已验证的入口

1.26.45.01 的 exe 内确认存在以下字符串：

- `@minecraft/server-gametest`
- `spawnSimulatedPlayer`
- `inputpermission`
- `@minecraft/server-admin`
- `v8.dll`（脚本运行时）

说明客户端内置了脚本 API 和模拟玩家能力，但需要：

- 在世界设置里开启实验性玩法 / Beta APIs；
- manifest 里的模块版本号与当前游戏内置版本一致，否则模块加载失败。

## 用途分工

- `tick.json` + `functions/*.mcfunction`：摆场地、放方块、召唤实体、设初始位置。
- `@minecraft/server`：`teleport`、`applyImpulse`、`getVelocity`、`setRotation`、
  `system.runInterval` 逐 tick 驱动。
- `@minecraft/server-gametest`：`spawnSimulatedPlayer` 做脚本控制的移动/跳跃/转向。

## 注意

SimulatedPlayer 很可能不经过 LocalPlayer 的客户端预测管线。先做等价性实验，
再决定哪些机制可以完全用它跑回归。
