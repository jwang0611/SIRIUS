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
let backendReady = false; // flips true once the window has loaded successfully
let lastBackendErr = ""; // tail of stderr, surfaced when the backend dies early

// ---- helpers ---------------------------------------------------------------

// Resolve the read-only directory that holds app.py + src + bundled seed data.
function backendSourceRoot() {
  if (isPackaged) {
    // extraResources are copied under <resources>/backend
    return path.join(process.resourcesPath, "backend");
  }
  // dev: repo root is the parent of desktop/
  return path.join(__dirname, "..");
}

// Resolve the working directory used by the backend process. In packaged apps,
// this must be writable because the web backend stores uploads, sessions,
// caches, and generated outputs under relative data/* paths.
function backendRuntimeRoot() {
  if (isPackaged) {
    return path.join(app.getPath("userData"), "backend");
  }
  return backendSourceRoot();
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

function copyMissingRecursive(src, dest) {
  if (!fs.existsSync(src)) return;
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyMissingRecursive(srcPath, destPath);
    } else if (!fs.existsSync(destPath)) {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function prepareBackendRuntime(sourceRoot, runtimeRoot) {
  if (!isPackaged) return;
  fs.mkdirSync(runtimeRoot, { recursive: true });

  // Seed mutable data only when files are absent, so user uploads and generated
  // outputs survive app updates.
  copyMissingRecursive(path.join(sourceRoot, "data"), path.join(runtimeRoot, "data"));

  // Helper scripts are executable code, not user data. Refresh them on every
  // launch so installed app updates pick up script fixes.
  const scriptsSrc = path.join(sourceRoot, "scripts");
  const scriptsDest = path.join(runtimeRoot, "scripts");
  if (fs.existsSync(scriptsSrc)) {
    fs.cpSync(scriptsSrc, scriptsDest, { recursive: true, force: true });
  }
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

// Locate a self-contained backend binary, either from SIRIUS_BACKEND_BIN or,
// in a packaged app, a conventional name under resources/backend/. This lets a
// PyInstaller binary bundled via extraResources be picked up automatically —
// so a Finder/Start-Menu launch does not silently depend on a system Python.
function resolveBackendBinary(sourceRoot) {
  if (process.env.SIRIUS_BACKEND_BIN && fs.existsSync(process.env.SIRIUS_BACKEND_BIN)) {
    return process.env.SIRIUS_BACKEND_BIN;
  }
  const exe = process.platform === "win32" ? "sirius-backend.exe" : "sirius-backend";
  return firstExisting([path.join(sourceRoot, exe), path.join(sourceRoot, "backend", exe)]);
}

// Resolve the backend command + args for the current mode. Prefers a
// self-contained binary, else falls back to `python -m uvicorn app:app`.
function resolveBackendCommand(sourceRoot, port) {
  const bin = resolveBackendBinary(sourceRoot);
  if (bin) {
    // Self-contained backend binary (PyInstaller etc.)
    return { cmd: bin, args: ["--host", HOST, "--port", String(port)] };
  }
  // Python fallback: uvicorn app:app
  return {
    cmd: resolvePython(sourceRoot),
    args: ["-m", "uvicorn", "app:app", "--host", HOST, "--port", String(port)],
  };
}

// Start the backend. `onFatal(message)` is invoked if the process fails to
// spawn (e.g. Python missing → ENOENT) or exits before the window is ready,
// so startup failures surface immediately instead of waiting out the
// health-check timeout.
function startBackend(port, onFatal) {
  const sourceRoot = backendSourceRoot();
  const runtimeRoot = backendRuntimeRoot();
  prepareBackendRuntime(sourceRoot, runtimeRoot);

  const env = {
    ...process.env,
    // Note: the port reaches the backend via the --port CLI arg below; this
    // env var is exported only for backend code that may want to know it.
    SIRIUS_PORT: String(port),
    SIRIUS_BACKEND_SOURCE_ROOT: sourceRoot,
    SIRIUS_BACKEND_RUNTIME_ROOT: runtimeRoot,
    // Ensure `import app` / `import src...` resolve even when packaged apps run
    // from a writable app-data directory instead of resources/backend.
    PYTHONPATH: sourceRoot + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ""),
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };

  const { cmd, args } = resolveBackendCommand(sourceRoot, port);

  backendProc = spawn(cmd, args, { cwd: runtimeRoot, env, windowsHide: true });

  backendProc.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backendProc.stderr.on("data", (d) => {
    const text = d.toString();
    // Keep the last chunk of stderr so an early crash can show the real cause.
    lastBackendErr = (lastBackendErr + text).slice(-2000);
    process.stderr.write(`[backend] ${text}`);
  });

  // spawn() failure (bad interpreter path, missing Python, EACCES) arrives as
  // an 'error' event — without a listener Node throws and takes down the main
  // process, bypassing the friendly dialog.
  backendProc.on("error", (err) => {
    console.error("[backend] spawn error:", err);
    backendProc = null;
    if (!backendReady) {
      onFatal(`无法启动后端 / Failed to launch backend (${cmd}):\n\n${err.message}`);
    }
  });

  backendProc.on("exit", (code, signal) => {
    console.log(`[backend] exited code=${code} signal=${signal}`);
    backendProc = null;
    if (app.isQuiting) return;
    if (!backendReady) {
      // Died during startup — report the actual stderr rather than a timeout.
      const detail = lastBackendErr.trim();
      onFatal(
        `后端启动失败 / The SIRIUS backend failed to start (code ${code}).` +
          (detail ? `\n\n${detail}` : "")
      );
    } else if (mainWindow) {
      // Died while the window was open.
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
    let fatalReported = false;
    const fail = (message) => {
      if (fatalReported) return;
      fatalReported = true;
      dialog.showErrorBox("SIRIUS", message);
      stopBackend();
      app.quit();
    };
    try {
      backendPort = process.env.SIRIUS_PORT ? Number(process.env.SIRIUS_PORT) : await findFreePort();
      // Race the health check against an early backend crash so a spawn error
      // or import failure surfaces immediately instead of after the 60s poll.
      const earlyExit = new Promise((_, reject) => {
        startBackend(backendPort, (message) => reject(new Error(message)));
      });
      await Promise.race([waitForServer(backendPort), earlyExit]);
      backendReady = true;
      createWindow(backendPort);
    } catch (err) {
      fail(`无法启动应用 / Failed to start:\n\n${err.message}`);
      return;
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
