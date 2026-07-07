// SIRIUS desktop shell
// -----------------------------------------------------------------------------
// Launches the SIRIUS FastAPI backend as a child process, waits for it to come
// up, then opens a native window pointing at the local server. The same web UI
// (src/web/static) is served, so the desktop app and the browser app stay in
// lock-step.
//
// Two backend modes, resolved at runtime:
//   * Packaged binary  — set SIRIUS_BACKEND_BIN to a self-contained backend
//     executable (e.g. produced by PyInstaller). Recommended for shipping.
//   * Python interpreter — falls back to `python -m uvicorn app:app`, using
//     SIRIUS_PYTHON, a repo virtualenv, or python3/python on PATH. Handy in dev.
// -----------------------------------------------------------------------------

const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const net = require("net");
const path = require("path");
const fs = require("fs");

const isPackaged = app.isPackaged;
const HOST = "127.0.0.1";

let backendProc = null;
let mainWindow = null;
let backendPort = null;

// ---- helpers ---------------------------------------------------------------

// Resolve the directory that holds app.py + src + data.
function backendRoot() {
  if (isPackaged) {
    // extraResources are copied under <resources>/backend
    return path.join(process.resourcesPath, "backend");
  }
  // dev: repo root is the parent of desktop/
  return path.join(__dirname, "..");
}

function firstExisting(candidates) {
  return candidates.find((p) => {
    try {
      return p && fs.existsSync(p);
    } catch (e) {
      return false;
    }
  });
}

// Locate a Python interpreter for the fallback (non-bundled) mode.
function resolvePython(root) {
  if (process.env.SIRIUS_PYTHON) return process.env.SIRIUS_PYTHON;
  const win = process.platform === "win32";
  const venvCandidates = win
    ? [path.join(root, "venv", "Scripts", "python.exe"), path.join(root, ".venv", "Scripts", "python.exe")]
    : [path.join(root, "venv", "bin", "python"), path.join(root, ".venv", "bin", "python")];
  return firstExisting(venvCandidates) || (win ? "python" : "python3");
}

// Grab a free TCP port so multiple instances / other apps never collide.
function findFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.on("error", reject);
    srv.listen(0, HOST, () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

// Poll the server until it answers (or we time out).
function waitForServer(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get({ host: HOST, port, path: "/", timeout: 2000 }, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", retry);
      req.on("timeout", () => {
        req.destroy();
        retry();
      });
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error("后端启动超时 / backend did not start in time"));
      } else {
        setTimeout(attempt, 400);
      }
    };
    attempt();
  });
}

function startBackend(port) {
  const root = backendRoot();
  const env = {
    ...process.env,
    SIRIUS_PORT: String(port),
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };

  const bundled = process.env.SIRIUS_BACKEND_BIN;
  let cmd;
  let args;
  if (bundled && fs.existsSync(bundled)) {
    // Self-contained backend binary (PyInstaller etc.)
    cmd = bundled;
    args = ["--host", HOST, "--port", String(port)];
  } else {
    // Python fallback: uvicorn app:app
    cmd = resolvePython(root);
    args = ["-m", "uvicorn", "app:app", "--host", HOST, "--port", String(port)];
  }

  backendProc = spawn(cmd, args, { cwd: root, env, windowsHide: true });

  backendProc.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backendProc.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backendProc.on("exit", (code, signal) => {
    console.log(`[backend] exited code=${code} signal=${signal}`);
    backendProc = null;
    // If the backend dies while the window is open, surface it and quit.
    if (mainWindow && !app.isQuiting) {
      dialog.showErrorBox("SIRIUS", "后端进程已退出。\nThe SIRIUS backend process has stopped.");
      app.quit();
    }
  });

  return backendProc;
}

function stopBackend() {
  if (!backendProc) return;
  const proc = backendProc;
  backendProc = null;
  try {
    if (process.platform === "win32") {
      // Kill the whole tree on Windows (uvicorn may spawn a reloader child).
      spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"]);
    } else {
      proc.kill("SIGTERM");
      setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch (e) {}
      }, 3000);
    }
  } catch (e) {
    console.error("Failed to stop backend:", e);
  }
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: "#faf9f5",
    title: "SIRIUS",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(`http://${HOST}:${port}/`);
  mainWindow.once("ready-to-show", () => mainWindow.show());

  // Open external links in the system browser, keep app links in-window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(`http://${HOST}:${port}`)) return { action: "allow" };
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    { role: "fileMenu" },
    { role: "editMenu" },
    {
      label: "View / 视图",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
        { role: "toggleDevTools" },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        {
          label: "SIRIUS 使用指南 / User Guide",
          click: () => {
            if (backendPort) shell.openExternal(`http://${HOST}:${backendPort}/static/guide.html`);
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---- lifecycle -------------------------------------------------------------

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    buildMenu();
    try {
      backendPort = process.env.SIRIUS_PORT ? Number(process.env.SIRIUS_PORT) : await findFreePort();
      startBackend(backendPort);
      await waitForServer(backendPort);
      createWindow(backendPort);
    } catch (err) {
      dialog.showErrorBox("SIRIUS", `无法启动应用 / Failed to start:\n\n${err.message}`);
      stopBackend();
      app.quit();
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0 && backendPort) createWindow(backendPort);
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", () => {
    app.isQuiting = true;
    stopBackend();
  });

  app.on("quit", stopBackend);
  process.on("exit", stopBackend);
}
