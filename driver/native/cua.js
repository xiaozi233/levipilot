// Minimal driver for ZCode's native Windows CUA helper.
//
// Usage (ELECTRON_RUN_AS_NODE=1 ZCode.exe cua.js <ax_native.node> <action> ...):
//   info
//   chat <command without leading slash>
//
// The helper is only used for server-authoritative setup commands (/tp, /fill).
// Movement, look and sampling stay on the frida RPC path.

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
