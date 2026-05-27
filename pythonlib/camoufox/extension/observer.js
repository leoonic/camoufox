(function() {
  if (window.__obs_init) return;
  window.__obs_init = true;

  var TEXT_MAX = 100;
  var HOVER_THRESHOLD = 500; // ms on same element to count as intentional hover
  var SCROLL_DEBOUNCE = 400; // ms of no scrolling to emit aggregated scroll event
  var DBLCLICK_WINDOW = 250; // ms to wait after click in case a dblclick follows

  function truncate(s) {
    if (!s) return "";
    s = s.trim().replace(/\s+/g, " ");
    return s.length > TEXT_MAX ? s.slice(0, TEXT_MAX) + "..." : s;
  }

  function cssSelector(el) {
    if (!el || el === document || el === document.documentElement) return "html";
    if (el.id) return "#" + CSS.escape(el.id);
    var parts = [];
    var cur = el;
    while (cur && cur !== document.documentElement) {
      var tag = cur.tagName.toLowerCase();
      var seg = tag;
      if (cur.id) {
        parts.unshift("#" + CSS.escape(cur.id));
        break;
      }
      var parent = cur.parentElement;
      if (parent) {
        var siblings = parent.children;
        var sameTag = 0;
        var idx = 0;
        for (var i = 0; i < siblings.length; i++) {
          if (siblings[i].tagName === cur.tagName) {
            sameTag++;
            if (siblings[i] === cur) idx = sameTag;
          }
        }
        if (sameTag > 1) seg += ":nth-of-type(" + idx + ")";
      }
      parts.unshift(seg);
      cur = parent;
    }
    return parts.join(" > ");
  }

  function elementInfo(el) {
    if (!el) return { selector: "", text: "", role: "", ariaLabel: "" };
    return {
      selector: cssSelector(el),
      text: truncate(el.textContent || el.value || ""),
      role: el.getAttribute("role") || el.tagName.toLowerCase(),
      ariaLabel: el.getAttribute("aria-label") || ""
    };
  }

  function send(evt) {
    try {
      browser.runtime.sendMessage({ type: "obs_event", data: evt });
    } catch(e) {}
  }

  function now() { return Date.now(); }

  // --- Hover detection: only emit when mouse stays on same element > HOVER_THRESHOLD ---
  var hoverState = { selector: null, enterTime: 0, timer: null };

  document.addEventListener("mousemove", function(e) {
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el) return;
    var sel = cssSelector(el);

    if (sel === hoverState.selector) return; // still on same element

    if (hoverState.timer) clearTimeout(hoverState.timer);

    hoverState.selector = sel;
    hoverState.enterTime = now();
    hoverState.timer = setTimeout(function() {
      var info = elementInfo(el);
      var tag = el.tagName.toLowerCase();
      if (tag === "a" || tag === "button" || tag === "input" || tag === "select" ||
          tag === "img" || tag === "label" || tag === "textarea" ||
          info.role !== tag || info.ariaLabel) {
        send({
          wallclock: now(), type: "hover",
          x: e.clientX, y: e.clientY,
          duration_ms: HOVER_THRESHOLD,
          target: info.selector, target_text: info.text,
          role: info.role, ariaLabel: info.ariaLabel
        });
      }
    }, HOVER_THRESHOLD);
  }, true);

  // --- Scroll aggregation: one event per scroll gesture ---
  var scrollState = { active: false, startY: 0, startWall: 0, timer: null };

  window.addEventListener("scroll", function() {
    var scrollY = window.scrollY || document.documentElement.scrollTop;

    if (!scrollState.active) {
      scrollState.active = true;
      scrollState.startY = scrollY;
      scrollState.startWall = now();
    }

    if (scrollState.timer) clearTimeout(scrollState.timer);
    scrollState.timer = setTimeout(function() {
      var endY = window.scrollY || document.documentElement.scrollTop;
      var docHeight = document.documentElement.scrollHeight;
      var vpHeight = window.innerHeight;
      var pct = docHeight > vpHeight ? Math.round((endY / (docHeight - vpHeight)) * 100) : 0;
      send({
        wallclock: scrollState.startWall, type: "scroll",
        from_y: Math.round(scrollState.startY),
        to_y: Math.round(endY),
        viewport_pct: pct,
        direction: endY > scrollState.startY ? "down" : "up",
        duration_ms: now() - scrollState.startWall
      });
      scrollState.active = false;
    }, SCROLL_DEBOUNCE);
  }, true);

  // --- Click vs dblclick deduplication ---
  // Buffer clicks for DBLCLICK_WINDOW ms. If a dblclick on the same selector
  // arrives in that window, drop the pending clicks and emit only the dblclick.
  var pendingClicks = [];

  function flushClick(entry) {
    pendingClicks = pendingClicks.filter(function(c) { return c !== entry; });
    if (entry.cancelled) return;
    send(entry.evt);
  }

  document.addEventListener("click", function(e) {
    var info = elementInfo(e.target);
    var entry = {
      selector: info.selector,
      cancelled: false,
      evt: {
        wallclock: now(), type: "click",
        x: e.clientX, y: e.clientY,
        target: info.selector, target_text: info.text,
        role: info.role, ariaLabel: info.ariaLabel
      }
    };
    pendingClicks.push(entry);
    setTimeout(function() { flushClick(entry); }, DBLCLICK_WINDOW);
  }, true);

  document.addEventListener("dblclick", function(e) {
    var info = elementInfo(e.target);
    pendingClicks.forEach(function(c) {
      if (c.selector === info.selector) c.cancelled = true;
    });
    send({
      wallclock: now(), type: "dblclick",
      x: e.clientX, y: e.clientY,
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Context menu ---
  document.addEventListener("contextmenu", function(e) {
    var info = elementInfo(e.target);
    send({
      wallclock: now(), type: "contextmenu",
      x: e.clientX, y: e.clientY,
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Input: debounced, emits final value ---
  var inputTimers = {};
  document.addEventListener("input", function(e) {
    var el = e.target;
    var sel = cssSelector(el);
    if (inputTimers[sel]) clearTimeout(inputTimers[sel]);
    inputTimers[sel] = setTimeout(function() {
      delete inputTimers[sel];
      var info = elementInfo(el);
      send({
        wallclock: now(), type: "input",
        target: info.selector,
        value: truncate(el.value || ""),
        role: info.role, ariaLabel: info.ariaLabel
      });
    }, 500);
  }, true);

  // --- Focus: only interactive elements ---
  document.addEventListener("focusin", function(e) {
    var tag = e.target.tagName.toLowerCase();
    if (tag !== "input" && tag !== "textarea" && tag !== "select" &&
        tag !== "button" && tag !== "a" && !e.target.getAttribute("contenteditable")) return;
    var info = elementInfo(e.target);
    send({
      wallclock: now(), type: "focus",
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Keydown: only special keys and shortcuts ---
  document.addEventListener("keydown", function(e) {
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) return;
    var info = elementInfo(e.target);
    send({
      wallclock: now(), type: "keydown",
      key: e.key, code: e.code,
      ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, meta: e.metaKey,
      target: info.selector,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);
})();
