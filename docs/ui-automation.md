# UI 自动化（启动 / 进存档 / 退档）

实验控制走 mod API；**启动游戏、主菜单、世界列表、暂停菜单、对话框**这类 UI 仍需要真正的
桌面操作。这里记录当前机器上验证过的做法；完整流程和排查顺序见 skill
**`mcbe-desktop-control`**（`~/.agents/skills/mcbe-desktop-control/SKILL.md`）。

## 规则

- UI 操作**只用 ZCode 官方电脑操作** `agent.computerUse`（`mcp__node_repl__js` + bootstrap）。
- **按按钮 = 悬停 + 回车**：合成鼠标的悬停生效并接管键盘焦点，点击会被 Minecraft 丢弃。
- 坐标只按**本次返回的光栅**读；窗口移动不用改数字，但不要混用 helper 的物理像素。
- 世界内鼠标被游戏捕获：悬停无效，位移会变成转视角。要操作 UI 先按 `Escape` 开菜单。

## Bootstrap

```js
const root = process.env.ZCODE_CUA_PLUGIN_ROOT;
const { join } = await import("node:path");
const { pathToFileURL } = await import("node:url");
const { setupComputerUseRuntime } = await import(
  pathToFileURL(join(root, "scripts", "computer-use-client.mjs")).href,
);
await setupComputerUseRuntime({ globals: globalThis });

const listed = await agent.computerUse.listApps({ emit: false });
const list = Array.isArray(listed) ? listed : listed.apps;
const app = await agent.computerUse.getApp({
  pid: list.find((a) => /Minecraft\.Windows\.exe$/i.test(a.bundle_id || "")).pid,
});
await app.click([640, 385]);   // 悬停“游戏”
await app.pressKey("Return");  // 激活
await app.getScreenshot();     // 官方截图会自己显示
```

## 已验证流程（官方 1280×764 光栅）

- 主菜单 → 游戏：悬停 `(640, 369)` → `Return`
- 世界列表：卡片顺序是 `[游玩][铅笔][游玩][铅笔]…`；方向键移动焦点，`Return` 进入。
  列表会滚动，落点前先截图看焦点环，**不要盲按**。
- 暂停菜单：`Escape` → 悬停“保存并退出” `(374, 521)` → `Return`
- 退出确认框：`Escape` 取消不了；悬停“取消”再 `Return`
- 存档加载期间可能出现“线上游戏未分级”提示；用方向键把焦点移到“继续”再 `Return`

## 已知限制

- Minecraft 不暴露无障碍元素（`getAXState` 只有 7 个窗口边框），只能按坐标。
- 官方路径报 `Computer Use is unavailable for this node_repl session` 时，说明本会话的
  node-repl 宿主没拿到 CUA 权限代理（长寿命进程，补不上）→ 重启 ZCode 后重新 bootstrap。
- 离线兜底脚本 `driver/bringup.py` 在 `legacy/frida` 分支；日常 UI 操作不要用它。
- CUA 的合成点击在后台只产生鼠标移动事件；录制真实按键需要窗口在前台并使用 raw 输入。
