// Minimal, locked-down preload. The SIRIUS UI is a standard web app talking to
// the local backend over HTTP, so no privileged bridge is exposed here. Keep
// this file as the single, auditable seam if native capabilities are ever
// needed (expose only explicit, validated channels via contextBridge).
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("sirius", {
  desktop: true,
  platform: process.platform,
  version: process.versions.electron,
});
