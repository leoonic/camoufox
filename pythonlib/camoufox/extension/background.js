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

// --- Network response capture ---
let capturePatterns = [];   // URL substrings to match
let capturedResponses = []; // {url, status, body, timestamp}
const MAX_CAPTURES = 50;

// --- Request spy: captures outgoing request headers + body + response for URL patterns ---
let spyPatterns = [];
let spyPending = new Map();   // requestId -> partial entry
let spyResults = [];          // completed {url, method, headers, body, responseBody, timestamp}
const MAX_SPY = 100;

// --- Observer: content script event buffer ---
let observerActive = false;
let observationEvents = [];
const MAX_OBSERVATIONS = 500;
const SPA_SNAPSHOT_DELAY = 800;  // ms after SPA nav to let new content render
const HASH_SNAPSHOT_DELAY = 200;

// Tree walker injected into pages. Skips tracking IDs, hidden elements, and
// non-semantic subtrees so the snapshot stays small enough for an LLM.
const TREE_WALKER_CODE = `(function() {
  var TRACKING_RE = /^(bat[Bb]eacon|criteo|_hj|tm-remote-storage|gtm-|google_ads|fbq|hotjar|sift|optimizely|segment-|onetrust|adroll|gtag|_ga|amzn-)/;
  var SKIP_TAGS = { script:1, style:1, noscript:1, iframe:1, svg:1, link:1, meta:1, template:1 };

  function isHidden(el) {
    if (!el || el.nodeType !== 1) return true;
    if (el.hasAttribute("hidden")) return true;
    if (el.getAttribute("aria-hidden") === "true") return true;
    var st;
    try { st = window.getComputedStyle(el); } catch (_) { return false; }
    if (!st) return false;
    if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return true;
    return false;
  }

  function walk(el, depth) {
    if (!el || depth > 10) return [];
    var tag = el.tagName ? el.tagName.toLowerCase() : "";
    if (!tag || SKIP_TAGS[tag]) return [];
    if (isHidden(el)) return [];

    var id = el.id || "";
    if (id && TRACKING_RE.test(id)) return [];

    var role = el.getAttribute ? (el.getAttribute("role") || "") : "";
    var ariaLabel = el.getAttribute ? (el.getAttribute("aria-label") || "") : "";
    var text = "";
    for (var i = 0; i < el.childNodes.length; i++) {
      if (el.childNodes[i].nodeType === 3) text += el.childNodes[i].textContent;
    }
    text = text.trim().replace(/\\s+/g, " ").slice(0, 80);

    var cls = "";
    if (el.className && typeof el.className === "string") {
      cls = el.className.split(" ").slice(0, 3).join(" ");
    }

    var isEmptyLazy = (id === "lazy-component" && !text && el.children.length === 0);

    var include = !isEmptyLazy && (
      tag === "a" || tag === "button" || tag === "input" || tag === "select" ||
      tag === "textarea" || tag === "img" || tag === "h1" || tag === "h2" ||
      tag === "h3" || tag === "h4" || tag === "label" || tag === "nav" ||
      tag === "main" || tag === "header" || tag === "footer" || tag === "section" ||
      tag === "article" || role || ariaLabel || (id && !TRACKING_RE.test(id)) || text
    );

    var nodes = [];
    if (include) {
      var node = { tag: tag };
      if (id) node.id = id;
      if (cls) node.cls = cls;
      if (role) node.role = role;
      if (ariaLabel) node.aria = ariaLabel;
      if (text) node.text = text;
      if (tag === "a") node.href = (el.getAttribute("href") || "").slice(0, 100);
      if (tag === "img") node.alt = (el.getAttribute("alt") || "").slice(0, 80);
      if (tag === "input") { node.type = el.type || "text"; node.value = (el.value || "").slice(0, 50); }
      nodes.push(node);
    }

    var children = el.children;
    if (children) {
      for (var j = 0; j < children.length; j++) {
        nodes = nodes.concat(walk(children[j], depth + 1));
      }
    }
    return nodes;
  }

  return JSON.stringify(walk(document.body, 0));
})()`;

function captureTreeSnapshot(tabId, delayMs) {
  if (!observerActive || !tabId) return;
  setTimeout(function() {
    if (!observerActive) return;
    browser.tabs.executeScript(tabId, {
      code: TREE_WALKER_CODE,
      runAt: "document_idle"
    }).then(function(treeResult) {
      if (!observerActive) return;
      var tree;
      try { tree = JSON.parse(treeResult[0]); } catch (_) { tree = []; }
      browser.tabs.get(tabId).then(function(tab) {
        observationEvents.push({
          t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
          type: "dom_snapshot",
          url: tab.url || "",
          title: tab.title || "",
          tree: tree,
          node_count: tree.length
        });
        if (observationEvents.length > MAX_OBSERVATIONS) {
          observationEvents = observationEvents.slice(-MAX_OBSERVATIONS);
        }
      }).catch(function() {});
    }).catch(function() {});
  }, delayMs || 0);
}

function setupCaptureListener() {
  // Remove existing listener if any
  if (browser.webRequest.onBeforeRequest.hasListener(onBeforeRequestCapture)) {
    browser.webRequest.onBeforeRequest.removeListener(onBeforeRequestCapture);
  }
  if (capturePatterns.length === 0) return;

  browser.webRequest.onBeforeRequest.addListener(
    onBeforeRequestCapture,
    { urls: ["<all_urls>"] },
    ["blocking"]
  );
}

function onBeforeRequestCapture(details) {
  const url = details.url;
  const matched = capturePatterns.some(p => url.includes(p));
  if (!matched) return {};

  const filter = browser.webRequest.filterResponseData(details.requestId);
  const chunks = [];

  filter.ondata = (event) => {
    chunks.push(new Uint8Array(event.data));
    filter.write(event.data); // pass through to browser
  };

  filter.onstop = () => {
    filter.close();
    // Decode the full response
    try {
      const totalLen = chunks.reduce((s, c) => s + c.byteLength, 0);
      const merged = new Uint8Array(totalLen);
      let offset = 0;
      for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.byteLength;
      }
      const body = new TextDecoder("utf-8").decode(merged);
      capturedResponses.push({
        url: url,
        status: null,
        body: body,
        timestamp: Date.now(),
      });
      // Trim old entries
      if (capturedResponses.length > MAX_CAPTURES) {
        capturedResponses = capturedResponses.slice(-MAX_CAPTURES);
      }
    } catch (e) {
      console.error("[cap] decode error:", e);
    }
  };

  filter.onerror = () => {
    try { filter.close(); } catch (_) {}
  };

  return {};
}

// --- Spy listeners ---

function setupSpyListeners() {
  if (browser.webRequest.onBeforeRequest.hasListener(onSpyRequest)) {
    browser.webRequest.onBeforeRequest.removeListener(onSpyRequest);
  }
  if (browser.webRequest.onSendHeaders.hasListener(onSpyHeaders)) {
    browser.webRequest.onSendHeaders.removeListener(onSpyHeaders);
  }
  if (spyPatterns.length === 0) return;

  browser.webRequest.onBeforeRequest.addListener(
    onSpyRequest,
    { urls: ["<all_urls>"] },
    ["blocking", "requestBody"]
  );
  browser.webRequest.onSendHeaders.addListener(
    onSpyHeaders,
    { urls: ["<all_urls>"] },
    ["requestHeaders"]
  );
}

function onSpyRequest(details) {
  if (!spyPatterns.some(p => details.url.includes(p))) return {};

  let bodyText = null;
  if (details.requestBody && details.requestBody.raw) {
    try {
      const parts = details.requestBody.raw.map(p => new Uint8Array(p.bytes));
      const total = parts.reduce((s, p) => s + p.byteLength, 0);
      const merged = new Uint8Array(total);
      let off = 0;
      for (const p of parts) { merged.set(p, off); off += p.byteLength; }
      bodyText = new TextDecoder("utf-8").decode(merged);
    } catch (_) { bodyText = "[decode error]"; }
  } else if (details.requestBody && details.requestBody.formData) {
    bodyText = JSON.stringify(details.requestBody.formData);
  }

  const entry = {
    url: details.url,
    method: details.method,
    body: bodyText,
    headers: null,
    responseBody: null,
    timestamp: Date.now(),
  };
  spyPending.set(details.requestId, entry);

  // Also capture response body via filterResponseData
  try {
    const filter = browser.webRequest.filterResponseData(details.requestId);
    const chunks = [];
    filter.ondata = (event) => {
      chunks.push(new Uint8Array(event.data));
      filter.write(event.data);
    };
    filter.onstop = () => {
      filter.close();
      try {
        const totalLen = chunks.reduce((s, c) => s + c.byteLength, 0);
        const merged = new Uint8Array(totalLen);
        let off = 0;
        for (const c of chunks) { merged.set(c, off); off += c.byteLength; }
        entry.responseBody = new TextDecoder("utf-8").decode(merged);
      } catch (_) {}
      spyResults.push(entry);
      spyPending.delete(details.requestId);
      if (spyResults.length > MAX_SPY) spyResults = spyResults.slice(-MAX_SPY);
    };
    filter.onerror = () => {
      try { filter.close(); } catch (_) {}
      spyResults.push(entry);
      spyPending.delete(details.requestId);
    };
  } catch (_) {
    // filterResponseData not available, save without response
    spyResults.push(entry);
    spyPending.delete(details.requestId);
  }

  return {};
}

function onSpyHeaders(details) {
  const entry = spyPending.get(details.requestId);
  if (!entry) return;
  entry.headers = {};
  for (const h of details.requestHeaders) {
    entry.headers[h.name] = h.value;
  }
}

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
    console.log(`[ext] Connected on port ${wsPort}`);
    ws.send(JSON.stringify({ type: "hello", extensionId: "{d4a1e2b3-8f7c-4e5d-9a6b-3c2d1e0f4a5b}" }));
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
            params.tabId, params.x, params.y, params.button || 0, params.modifiers || 0
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

        case "listTabs": {
          // Todas las tabs de la ventana actual, incluidas las que abrio el sitio
          // con window.open (que el wrapper RDP no trackea). Ordenadas por indice.
          const tabs = await browser.tabs.query({ currentWindow: true });
          tabs.sort((a, b) => a.index - b.index);
          result = {
            tabs: tabs.map(t => ({
              tabId: t.id, url: t.url, title: t.title,
              active: t.active, index: t.index,
            })),
          };
          break;
        }

        case "createTab": {
          const t = await browser.tabs.create({
            url: params.url || "about:blank",
            active: params.active !== false,
          });
          result = { tabId: t.id, url: t.url, index: t.index };
          break;
        }

        case "activateTab": {
          await browser.tabs.update(params.tabId, { active: true });
          result = { ok: true, tabId: params.tabId };
          break;
        }

        case "closeTab": {
          await browser.tabs.remove(params.tabId);
          result = { ok: true };
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

        case "startCapture":
          capturePatterns = params.patterns || [];
          capturedResponses = [];
          setupCaptureListener();
          result = { ok: true, patterns: capturePatterns };
          break;

        case "stopCapture":
          capturePatterns = [];
          setupCaptureListener();
          result = { ok: true };
          break;

        case "getCapturedResponses": {
          const minTs = params.since || 0;
          const filtered = capturedResponses.filter(r => r.timestamp > minTs);
          result = { responses: filtered };
          break;
        }

        case "clearCaptures":
          capturedResponses = [];
          result = { ok: true };
          break;

        case "navigate":
          await browser.nativeInput.navigateTo(params.tabId, params.url);
          result = { ok: true };
          break;

        case "startSpy":
          spyPatterns = params.patterns || [];
          spyPending = new Map();
          spyResults = [];
          setupSpyListeners();
          result = { ok: true, patterns: spyPatterns };
          break;

        case "stopSpy":
          spyPatterns = [];
          setupSpyListeners();
          result = { ok: true };
          break;

        case "getSpiedRequests": {
          const spySince = params.since || 0;
          const spyFiltered = spyResults.filter(r => r.timestamp > spySince);
          result = { requests: spyFiltered };
          break;
        }

        case "clearSpied":
          spyResults = [];
          spyPending = new Map();
          result = { ok: true };
          break;

        case "bgFetch": {
          // Fetch from extension background (bypasses page JS monkey-patches,
          // not bound to page origin/CSP, uses browser cookie jar).
          const resp = await fetch(params.url, {
            method: params.method || "GET",
            headers: params.headers || {},
            credentials: params.credentials || "include",
            redirect: params.redirect || "follow",
            cache: params.cache || "no-store",
            body: params.body || undefined
          });
          // opaqueredirect responses have no readable body
          const text = (resp.type === "opaqueredirect") ? "" : await resp.text();
          result = {
            status: resp.status,
            url: resp.url,
            type: resp.type,
            body: text.substring(0, params.maxBody || 200000)
          };
          break;
        }

        case "clearCookies": {
          const domain = params.domain || null;
          const url = params.url || null;
          let removed = 0;
          let cookies;
          if (domain) {
            cookies = await browser.cookies.getAll({ domain });
          } else if (url) {
            cookies = await browser.cookies.getAll({ url });
          } else {
            cookies = await browser.cookies.getAll({});
          }
          for (const c of cookies) {
            const proto = c.secure ? "https://" : "http://";
            const cookieUrl = proto + c.domain.replace(/^\./, "") + c.path;
            try {
              await browser.cookies.remove({ url: cookieUrl, name: c.name });
              removed++;
            } catch (_) {}
          }
          result = { ok: true, removed };
          break;
        }

        case "minimizeWindow": {
          // Minimize current window. Triggers real window.blur,
          // document.visibilitychange, visibilityState=hidden, and rAF throttle.
          // Anti-bot systems (PerimeterX, Shopee SFU) track these events.
          try {
            const win = await browser.windows.getCurrent();
            await browser.windows.update(win.id, { state: "minimized" });
            result = { ok: true, windowId: win.id };
          } catch (e) {
            error = e.message || String(e);
          }
          break;
        }

        case "restoreWindow": {
          try {
            const win = await browser.windows.getCurrent();
            await browser.windows.update(win.id, { state: "normal" });
            // Focus it too so window.focus fires
            await browser.windows.update(win.id, { focused: true });
            result = { ok: true };
          } catch (e) {
            error = e.message || String(e);
          }
          break;
        }

        case "maximizeWindow": {
          // Maximize via WebExtensions API. Funciona para tablas anchas tipo
          // matcheador donde el viewport default no muestra todas las columnas.
          try {
            const win = await browser.windows.getCurrent();
            await browser.windows.update(win.id, { state: "maximized", focused: true });
            result = { ok: true, windowId: win.id };
          } catch (e) {
            error = e.message || String(e);
          }
          break;
        }

        case "closeOtherTabs": {
          const allTabs = await browser.tabs.query({ currentWindow: true });
          const keepId = params.keepTabId || null;
          let closed = 0;
          for (const t of allTabs) {
            if (keepId && t.id === keepId) continue;
            if (t.active && !keepId) continue;
            try {
              await browser.tabs.remove(t.id);
              closed++;
            } catch (_) {}
          }
          result = { ok: true, closed };
          break;
        }

        case "startObserving": {
          observerActive = true;
          observationEvents = [];
          navSessionStart = Date.now();
          lastNavUrl = "";
          // Inject observer.js into the specified tab (or active tab) and
          // capture an initial DOM snapshot so the timeline starts with the
          // page as it is at start_observing time.
          let initialTabId = params.tabId;
          if (!initialTabId) {
            const activeTabs = await browser.tabs.query({ active: true, currentWindow: true });
            if (activeTabs.length > 0) initialTabId = activeTabs[0].id;
          }
          if (initialTabId) {
            injectObserver(initialTabId);
            captureTreeSnapshot(initialTabId, 100);
          }
          result = { ok: true };
          break;
        }

        case "stopObserving":
          observerActive = false;
          result = { ok: true, count: observationEvents.length };
          break;

        case "getObservations": {
          const obsSince = params.since || 0;
          const filtered = observationEvents.filter(e => e.t > obsSince);
          if (params.clear) observationEvents = [];
          result = { events: filtered };
          break;
        }

        case "getAccessibilityTree": {
          const atTabId = params.tabId;
          let targetTab = atTabId;
          if (!targetTab) {
            const aTabs = await browser.tabs.query({ active: true, currentWindow: true });
            if (aTabs.length > 0) targetTab = aTabs[0].id;
          }
          if (targetTab) {
            const treeResult = await browser.tabs.executeScript(targetTab, {
              code: TREE_WALKER_CODE,
              runAt: "document_idle"
            });
            try {
              result = { tree: JSON.parse(treeResult[0]) };
            } catch(_) {
              result = { tree: treeResult[0] };
            }
          } else {
            result = { tree: [] };
          }
          break;
        }

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

// Cursor indicator: inject into every page via closed Shadow DOM
function injectCursor(tabId) {
  browser.tabs.executeScript(tabId, {
    file: "cursor.js",
    runAt: "document_idle",
    allFrames: false
  }).catch(() => {});
}

browser.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "complete") {
    injectCursor(tabId);
    if (observerActive) injectObserver(tabId);
  }
});

// Observer: listen for events from observer.js content script.
// observer.js sends wallclock (Date.now()); we convert to t relative to
// navSessionStart so all events (page + navigation) share one timeline.
browser.runtime.onMessage.addListener((message) => {
  if (!observerActive) return;
  if (message && message.type === "obs_event") {
    var d = message.data;
    if (typeof d.wallclock === "number") {
      d.t = navSessionStart > 0 ? Math.max(0, d.wallclock - navSessionStart) : 0;
      delete d.wallclock;
    } else if (typeof d.t !== "number") {
      d.t = navSessionStart > 0 ? Date.now() - navSessionStart : 0;
    }
    observationEvents.push(d);
    if (observationEvents.length > MAX_OBSERVATIONS) {
      observationEvents = observationEvents.slice(-MAX_OBSERVATIONS);
    }
  }
});

// Navigation tracking via webNavigation API (no polling)
var navSessionStart = 0;
var lastNavUrl = "";

function pushNavEvent(data) {
  if (!observerActive) return false;
  if (data.url && data.url === lastNavUrl) return false; // deduplicate
  if (data.url) lastNavUrl = data.url;
  observationEvents.push(data);
  if (observationEvents.length > MAX_OBSERVATIONS) {
    observationEvents = observationEvents.slice(-MAX_OBSERVATIONS);
  }
  return true;
}

// Full page navigation completed
browser.webNavigation.onCompleted.addListener((details) => {
  if (details.frameId !== 0) return;
  browser.tabs.get(details.tabId).then((tab) => {
    var isRedirect = details.transitionQualifiers &&
      (details.transitionQualifiers.includes("server_redirect") ||
       details.transitionQualifiers.includes("client_redirect"));
    var pushed = pushNavEvent({
      t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
      type: "page_load",
      url: details.url,
      title: tab.title || "",
      transition: details.transitionType || "",
      redirect: !!isRedirect
    });
    if (pushed) captureTreeSnapshot(details.tabId, 0);
  }).catch(() => {});
}, { url: [{ schemes: ["http", "https"] }] });

// SPA navigation (pushState / replaceState)
browser.webNavigation.onHistoryStateUpdated.addListener((details) => {
  if (details.frameId !== 0) return;
  browser.tabs.get(details.tabId).then((tab) => {
    var pushed = pushNavEvent({
      t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
      type: "spa_navigation",
      url: details.url,
      title: tab.title || ""
    });
    if (pushed) captureTreeSnapshot(details.tabId, SPA_SNAPSHOT_DELAY);
  }).catch(() => {});
}, { url: [{ schemes: ["http", "https"] }] });

// Link opened in new tab (ctrl+click, target=_blank, window.open)
browser.webNavigation.onCreatedNavigationTarget.addListener((details) => {
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: "new_tab",
    url: details.url,
    title: "",
    sourceTabId: details.sourceTabId
  });
});

// Hash fragment changes (#section)
browser.webNavigation.onReferenceFragmentUpdated.addListener((details) => {
  if (details.frameId !== 0) return;
  browser.tabs.get(details.tabId).then((tab) => {
    var pushed = pushNavEvent({
      t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
      type: "hash_navigation",
      url: details.url,
      title: tab.title || ""
    });
    if (pushed) captureTreeSnapshot(details.tabId, HASH_SNAPSHOT_DELAY);
  }).catch(() => {});
}, { url: [{ schemes: ["http", "https"] }] });

// Tab switch: user changed active tab
browser.tabs.onActivated.addListener((activeInfo) => {
  if (!observerActive) return;
  browser.tabs.get(activeInfo.tabId).then((tab) => {
    var pushed = pushNavEvent({
      t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
      type: "tab_switch",
      url: tab.url || "",
      title: tab.title || "",
      tabId: activeInfo.tabId
    });
    if (pushed) captureTreeSnapshot(activeInfo.tabId, 0);
    // Re-inject observer into newly focused tab
    injectObserver(activeInfo.tabId);
  }).catch(() => {});
});

// Tab lifecycle
browser.tabs.onCreated.addListener((tab) => {
  if (!observerActive) return;
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: "tab_created",
    url: tab.url || "",
    title: tab.title || "",
    tabId: tab.id
  });
});

browser.tabs.onRemoved.addListener((tabId, removeInfo) => {
  if (!observerActive) return;
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: "tab_closed",
    url: "",
    title: "",
    tabId: tabId,
    windowClosing: removeInfo.isWindowClosing
  });
});

// Window focus: detect when user leaves/returns to browser
browser.windows.onFocusChanged.addListener((windowId) => {
  if (!observerActive) return;
  var left = windowId === browser.windows.WINDOW_ID_NONE;
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: left ? "browser_blur" : "browser_focus",
    url: "",
    title: "",
    windowId: windowId
  });
});

// Idle detection: user inactive / screen locked
browser.idle.setDetectionInterval(30); // 30 seconds threshold
browser.idle.onStateChanged.addListener((newState) => {
  if (!observerActive) return;
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: "idle_state",
    url: "",
    title: "",
    state: newState // "active", "idle", "locked"
  });
});

// Downloads
browser.downloads.onCreated.addListener((downloadItem) => {
  if (!observerActive) return;
  pushNavEvent({
    t: navSessionStart > 0 ? Date.now() - navSessionStart : 0,
    type: "download",
    url: downloadItem.url || "",
    title: downloadItem.filename || "",
    fileSize: downloadItem.fileSize || 0,
    mime: downloadItem.mime || ""
  });
});

function injectObserver(tabId) {
  browser.tabs.executeScript(tabId, {
    file: "observer.js",
    runAt: "document_idle",
    allFrames: false
  }).catch(() => {});
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
