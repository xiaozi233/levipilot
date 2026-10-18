// Minimal driver for ZCode's native Windows CUA helper.
//
// Usage (ELECTRON_RUN_AS_NODE=1 ZCode.exe cua.js <ax_native.node> <action> ...):
//   info
//   shot  <outPath>     窗口级 verified 截图（需要窗口在前台）
//   click <x> <y>       激活窗口后按物理屏幕像素点击
//   key   <combo>       激活窗口后按键（"Return" / "Down" / "ctrl+a"），可重复
//   hold  <key> <ms>    按住某个键一段时间（游戏内走路/前进的兜底手段）
//   type  <text>        激活窗口后输入文本
//   chat <command without leading slash>
//
// 菜单/对话框导航优先用 key：Minecraft 的 UI 收键盘消息，但对合成鼠标
// （Raw Input）不响应，点不到的时候不要反复点，换键盘。
//
// 坐标一律用**物理屏幕像素**：verified 截图返回的就是窗口的物理像素，
// 加窗口 bounds 原点即可得到 click 的坐标。不要用 PowerShell 截屏（DPI 虚拟化）
// 再乘缩放系数，那正是之前点错的原因。
//
// The helper is only used for server-authoritative setup commands (/tp, /fill)
// and for the rare UI step; movement, look and sampling stay on the frida RPC path.

const fs = require("fs");

const addonPath = process.argv[2];
const action = process.argv[3];
const addon = require(addonPath);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function findMinecraft() {
  const apps = addon.listApplications();
  const app =
    apps.find((a) => a.bundle_id && /Minecraft\.Windows\.exe$/i.test(a.bundle_id)) ||
    apps.find((a) => /^minecraft$/i.test((a.name || "").trim()));
  if (!app) throw new Error("Minecraft.Windows.exe is not running");
  const windows = addon.listWindows(app.pid) || [];
  const win = windows.find((w) => w.main) || windows[0] || null;
  return { app, windows, win };
}

(async () => {
  if (action === "info") {
    const { app, windows, win } = findMinecraft();
    console.log(
      JSON.stringify(
        {
          pid: app.pid,
          bundle_id: app.bundle_id,
          window_id: win ? win.window_id : null,
          bounds: win ? win.bounds : null,
          focused: win ? !!win.focused : false,
          windows,
        },
        null,
        2,
      ),
    );
    return;
  }

  if (action === "shot") {
    const outPath = process.argv[4];
    if (!outPath) throw new Error("shot needs an output path");
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    // 注意参数顺序：windowId, pid, canonicalBundleId, bounds。
    // 传错顺序只会得到 invalid_target。
    const shot = await addon.captureWindowPngVerifiedAsync(
      win.window_id,
      app.pid,
      app.bundle_id,
      win.bounds,
    );
    if (!shot || shot.ok === false) {
      throw new Error(`capture failed: ${shot && shot.error ? shot.error : "no payload"}`);
    }
    const raw = shot.data !== undefined ? shot.data : shot.base64 || shot.png || shot.image;
    let buffer;
    if (Buffer.isBuffer(raw)) buffer = raw;
    else if (typeof raw === "string") buffer = Buffer.from(raw, "base64");
    else if (raw && raw.type === "Buffer" && Array.isArray(raw.data)) buffer = Buffer.from(raw.data);
    else {
      throw new Error(`capture payload is ${typeof raw}; keys=${Object.keys(shot).join(",")}`);
    }
    fs.writeFileSync(outPath, buffer);
    console.log(
      JSON.stringify({
        ok: true,
        path: outPath,
        width: shot.width,
        height: shot.height,
        bounds: shot.bounds || win.bounds,
        bytes: buffer.length,
      }),
    );
    return;
  }

  if (action === "click") {
    const x = Number(process.argv[4]);
    const y = Number(process.argv[5]);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      throw new Error("click needs numeric x y (physical pixels)");
    }
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    const windows = addon.listWindows(app.pid) || [];
    const active = windows.find((w) => w.main) || windows[0] || {};
    if (!active.focused) {
      // 失焦时 Minecraft 会忽略合成鼠标事件（Raw Input）。
      throw new Error(
        `window did not take focus (pid=${app.pid}); activate it first, then retry`,
      );
    }
    addon.moveTo(x, y);
    await sleep(80);
    const clicked = await addon.clickAtPoint(x, y, "left", 1);
    await sleep(150);
    console.log(JSON.stringify({ ok: true, x, y, clicked, bounds: win.bounds }));
    return;
  }

  if (action === "key") {
    const combo = process.argv[4];
    if (!combo) throw new Error('key needs a combo, e.g. "Return" or "Down"');
    const repeat = Number(process.argv[5] || 1);
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    for (let i = 0; i < repeat; i += 1) {
      addon.pressKeyGlobal(combo);
      await sleep(120);
    }
    await sleep(250);
    console.log(JSON.stringify({ ok: true, combo, repeat }));
    return;
  }

  if (action === "hold") {
    const key = process.argv[4];
    const ms = Number(process.argv[5] || 1000);
    if (!key) throw new Error('hold needs a key, e.g. "w"');
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    addon.holdKeyGlobal(key, ms);
    await sleep(ms + 400);
    console.log(JSON.stringify({ ok: true, key, ms }));
    return;
  }

  if (action === "type") {
    const text = process.argv.slice(4).join(" ");
    if (!text) throw new Error("type needs text");
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    addon.typeTextGlobal(text);
    await sleep(250);
    console.log(JSON.stringify({ ok: true, text }));
    return;
  }

  if (action === "chat") {
    const command = process.argv.slice(4).join(" ").replace(/^\/+/, "");
    if (!command) throw new Error("empty command");
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    if (!win.focused) {
      throw new Error(
        `Minecraft window is not focused (pid=${app.pid}, window=${win.window_id})`,
      );
    }
    addon.pressKeyGlobal("t");
    await sleep(500);
    addon.pressKeyGlobal("ctrl+a");
    await sleep(120);
    addon.pressKeyGlobal("Backspace");
    await sleep(120);
    addon.typeTextGlobal("/" + command);
    await sleep(300);
    addon.pressKeyGlobal("Return");
    await sleep(500);
    console.log(
      JSON.stringify({ ok: true, pid: app.pid, window_id: win.window_id, command: "/" + command }),
    );
    return;
  }

  throw new Error(`unknown action: ${action}`);
})().catch((error) => {
  console.error(error && error.message ? error.message : String(error));
  process.exitCode = 1;
});
