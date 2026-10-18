// Minimal driver for ZCode's native Windows CUA helper.
//
// Usage (ELECTRON_RUN_AS_NODE=1 ZCode.exe cua.js <ax_native.node> <action> ...):
//   info
//   shot  <outPath>     窗口级 verified 截图（需要窗口在前台）
//   cursor              读当前光标屏幕坐标（标定/排查用）
//   point <rx> <ry>     把光标移到截图里的某个像素（光栅坐标）
//   click <rx> <ry>     同 point，再点一下左键
//   key   <combo>       激活窗口后按键（"Return" / "Down" / "ctrl+a"），可重复
//   hold  <key> <ms>    按住某个键一段时间（游戏内走路/前进的兜底手段）
//   type  <text>        激活窗口后输入文本
//   chat <command without leading slash>
//
// 坐标一律用**截图里的光栅坐标**（就是在 shot 出的那张 png 上数出来的像素），
// 由本脚本换算成屏幕物理坐标，见 RASTER_EDGE_*。
// 不要用 PowerShell 截屏（DPI 虚拟化）再乘缩放系数，那正是之前点错的原因。
//
// 菜单/对话框导航优先用 key：Minecraft 的 UI 收键盘消息，但对合成鼠标
// （Raw Input）不响应，点不到的时候不要反复点，换键盘。

// 截图（窗口内容）原点相对窗口 bounds 原点的偏移，单位物理像素，实测得出。
// 显示器 2560x1600 @ scale 1.5；窗口 bounds=[528,144,1609,971]，截图 1591x962：
//   bounds 比截图宽 18、高 9，即左右各有 9px 不可见的 DWM 拖拽边框，
//   下面的 9px 边框不在截图里 ⇒ 水平偏移 +9、垂直偏移 0。
// 标定方法：moveTo 到两个已知屏幕点 → 各截一图 → 差分定位光标箭头（工具见
// driver/native/check_cua_pipes.py 同目录的思路，脚本在 out/_calib.js）：
//   屏幕 (1228,944) → 光栅箭头左上 (691,800)
//   屏幕 (1428,944) → 光栅箭头左上 (891,800)   ⇒ 尺度 1.0、偏移 (537,144)-(528,144)=(+9,0)
const RASTER_EDGE_X = 9;
const RASTER_EDGE_Y = 0;
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

// 截图光栅坐标 → 屏幕物理坐标。bounds = [left, top, width, height]（含边框）。
function rasterToScreen(win, rx, ry) {
  return [win.bounds[0] + RASTER_EDGE_X + rx, win.bounds[1] + RASTER_EDGE_Y + ry];
}

// 移动并回读光标位置，返回 {want, got, dx, dy}；dx/dy 非 0 说明坐标系变了。
async function moveAndVerify(x, y) {
  addon.moveTo(x, y);
  await sleep(120);
  let got = null;
  try {
    got = addon.cursorPoint();
  } catch (err) {
    return { want: [x, y], got: null };
  }
  const dx = got && typeof got.x === "number" ? got.x - x : null;
  const dy = got && typeof got.y === "number" ? got.y - y : null;
  return { want: [x, y], got, dx, dy };
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

  if (action === "cursor") {
    console.log(JSON.stringify(addon.cursorPoint()));
    return;
  }

  if (action === "point" || action === "click") {
    const rx = Number(process.argv[4]);
    const ry = Number(process.argv[5]);
    if (!Number.isFinite(rx) || !Number.isFinite(ry)) {
      throw new Error(`${action} needs numeric rx ry (screenshot pixels)`);
    }
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    const [x, y] = rasterToScreen(win, rx, ry);
    const moved = await moveAndVerify(x, y);
    const report = {
      ok: true,
      raster: [rx, ry],
      screen: [x, y],
      cursor: moved.got,
      offset: [moved.dx, moved.dy],
      bounds: win.bounds,
    };
    if (action === "point") {
      console.log(JSON.stringify(report));
      return;
    }
    const windows = addon.listWindows(app.pid) || [];
    const active = windows.find((w) => w.main) || windows[0] || {};
    if (!active.focused) {
      // 失焦时 Minecraft 会忽略合成鼠标事件（Raw Input）。
      throw new Error(
        `window did not take focus (pid=${app.pid}); activate it first, then retry`,
      );
    }
    report.clicked = await addon.clickAtPoint(x, y, "left", 1);
    await sleep(150);
    console.log(JSON.stringify(report));
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
    const spec = process.argv[4];
    const ms = Number(process.argv[5] || 1000);
    if (!spec) throw new Error('hold needs a key or combo, e.g. "w" / "ctrl+w"');
    const keys = spec
      .split("+")
      .map((k) => k.trim())
      .filter(Boolean);
    const { app, win } = findMinecraft();
    if (!win) throw new Error("Minecraft has no top-level window");
    addon.activateApplication(app.pid);
    await sleep(700);
    if (keys.length === 1) {
      addon.holdKeyGlobal(keys[0], ms);
    } else {
      for (const k of keys) addon.keyDownGlobal(k);
      await sleep(ms);
      for (const k of keys.slice().reverse()) addon.keyUpGlobal(k);
    }
    await sleep(ms + 400);
    console.log(JSON.stringify({ ok: true, keys, ms }));
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
