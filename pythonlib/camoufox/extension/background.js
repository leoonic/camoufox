/* Camoufox RDP Input - WebSocket bridge to Python controller */
"use strict";

const RECONNECT_MS = 2000;
const PREF_KEY = "extensions.camoufox.ws_port";
let ws = null;
let wsPort = 8775;
let reconnectTimer = null;

// Proxy config (injected by RDPBrowser into this file before launch)
let proxyConfig = null;
let proxyCredentials = null;

async function initPort() {
  // Read port from Firefox pref (set by RDPBrowser per-instance)
  try {
    const port = await browser.nativeInput.getPort();
    if (port && port > 0) {
      wsPort = port;
      return;
    }
  } catch (_) {}
  // Fallback: scan ports 8775-8790
  for (let port = 8775; port <= 8790; port++) {
    try {
      const testWs = new WebSocket(`ws://127.0.0.1:${port}`);
      await new Promise((resolve, reject) => {
        testWs.addEventListener("open", () => {
          wsPort = port;
          testWs.close();
          resolve();
        });
        testWs.addEventListener("error", () => reject());
        setTimeout(() => reject(), 300);
      });
      break;
    } catch (_) {}
  }
}

function connect() {
  if (ws && ws.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(`ws://127.0.0.1:${wsPort}`);
  } catch (_) {
    return;
  }

  ws.addEventListener("open", () => {
    console.log(`[CamoufoxInput] Connected on port ${wsPort}`);
    ws.send(JSON.stringify({ type: "hello", extensionId: "camoufox-rdp-input@local" }));
  });

  ws.addEventListener("message", async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (_) {
      return;
    }

    const { id, cmd, params } = msg;
    let result = null;
    let error = null;

    try {
      switch (cmd) {
        case "click":
          await browser.nativeInput.click(
            params.tabId, params.x, params.y, params.button || 0
          );
          result = { ok: true };
          break;

        case "moveTo":
          await browser.nativeInput.moveTo(params.tabId, params.x, params.y);
          result = { ok: true };
          break;

        case "mouseDown":
          await browser.nativeInput.mouseDown(
            params.tabId, params.x, params.y, params.button || 0
          );
          result = { ok: true };
          break;

        case "mouseUp":
          await browser.nativeInput.mouseUp(
            params.tabId, params.x, params.y, params.button || 0
          );
          result = { ok: true };
          break;

        case "scroll":
          await browser.nativeInput.scroll(
            params.tabId, params.x, params.y, params.deltaX, params.deltaY
          );
          result = { ok: true };
          break;

        case "type":
          await browser.nativeInput.type(params.tabId, params.text);
          result = { ok: true };
          break;

        case "keyPress":
          await browser.nativeInput.keyPress(params.tabId, params.key);
          result = { ok: true };
          break;

        case "getActiveTab": {
          const tabs = await browser.tabs.query({ active: true, currentWindow: true });
          result = tabs.length > 0 ? { tabId: tabs[0].id, url: tabs[0].url } : null;
          break;
        }

        case "screenshot": {
          const dataUrl = await browser.tabs.captureVisibleTab(null, {
            format: "png",
          });
          result = { dataUrl };
          break;
        }

        case "setProxyAuth":
          proxyAuth = {
            username: params.username,
            password: params.password,
          };
          result = { ok: true };
          break;

        case "ping":
          result = { pong: true };
          break;

        default:
          error = `Unknown command: ${cmd}`;
      }
    } catch (e) {
      error = e.message || String(e);
    }

    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ id, result, error }));
    }
  });

  ws.addEventListener("close", () => {
    ws = null;
  });

  ws.addEventListener("error", () => {
    try { ws.close(); } catch (_) {}
    ws = null;
  });
}

// Init: find port then connect
initPort().then(() => {
  connect();
  reconnectTimer = setInterval(() => {
    if (!ws || ws.readyState === WebSocket.CLOSED) {
      connect();
    }
  }, RECONNECT_MS);
});
