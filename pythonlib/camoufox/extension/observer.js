(function() {
  if (window.__obs_init) return;
  window.__obs_init = true;

  var SESSION_START = Date.now();
  var TEXT_MAX = 100;
  var HOVER_THRESHOLD = 500; // ms on same element to count as intentional hover
  var SCROLL_DEBOUNCE = 400; // ms of no scrolling to emit aggregated scroll event

  function relTime() {
    return Date.now() - SESSION_START;
  }

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

  // --- Hover detection: only emit when mouse stays on same element > HOVER_THRESHOLD ---
  var hoverState = { selector: null, enterTime: 0, timer: null };

  document.addEventListener("mousemove", function(e) {
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (!el) return;
    var sel = cssSelector(el);

    if (sel === hoverState.selector) return; // still on same element

    // Mouse left previous element: check if it was a meaningful hover
    if (hoverState.timer) clearTimeout(hoverState.timer);

    hoverState.selector = sel;
    hoverState.enterTime = Date.now();
    hoverState.timer = setTimeout(function() {
      var info = elementInfo(el);
      // Only emit hover for interactive/semantic elements
      var tag = el.tagName.toLowerCase();
      if (tag === "a" || tag === "button" || tag === "input" || tag === "select" ||
          tag === "img" || tag === "label" || tag === "textarea" ||
          info.role !== tag || info.ariaLabel) {
        send({
          t: relTime(), type: "hover",
          x: e.clientX, y: e.clientY,
          duration_ms: HOVER_THRESHOLD,
          target: info.selector, target_text: info.text,
          role: info.role, ariaLabel: info.ariaLabel
        });
      }
    }, HOVER_THRESHOLD);
  }, true);

  // --- Scroll aggregation: one event per scroll gesture ---
  var scrollState = { active: false, startY: 0, startTime: 0, timer: null };

  window.addEventListener("scroll", function() {
    var scrollY = window.scrollY || document.documentElement.scrollTop;

    if (!scrollState.active) {
      scrollState.active = true;
      scrollState.startY = scrollY;
      scrollState.startTime = relTime();
    }

    if (scrollState.timer) clearTimeout(scrollState.timer);
    scrollState.timer = setTimeout(function() {
      var endY = window.scrollY || document.documentElement.scrollTop;
      var docHeight = document.documentElement.scrollHeight;
      var vpHeight = window.innerHeight;
      var pct = docHeight > vpHeight ? Math.round((endY / (docHeight - vpHeight)) * 100) : 0;
      send({
        t: scrollState.startTime, type: "scroll",
        from_y: Math.round(scrollState.startY),
        to_y: Math.round(endY),
        viewport_pct: pct,
        direction: endY > scrollState.startY ? "down" : "up",
        duration_ms: relTime() - scrollState.startTime
      });
      scrollState.active = false;
    }, SCROLL_DEBOUNCE);
  }, true);

  // --- Click ---
  document.addEventListener("click", function(e) {
    var info = elementInfo(e.target);
    send({
      t: relTime(), type: "click",
      x: e.clientX, y: e.clientY,
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Double click ---
  document.addEventListener("dblclick", function(e) {
    var info = elementInfo(e.target);
    send({
      t: relTime(), type: "dblclick",
      x: e.clientX, y: e.clientY,
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Context menu ---
  document.addEventListener("contextmenu", function(e) {
    var info = elementInfo(e.target);
    send({
      t: relTime(), type: "contextmenu",
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
        t: relTime(), type: "input",
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
      t: relTime(), type: "focus",
      target: info.selector, target_text: info.text,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);

  // --- Keydown: only special keys and shortcuts ---
  document.addEventListener("keydown", function(e) {
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) return;
    var info = elementInfo(e.target);
    send({
      t: relTime(), type: "keydown",
      key: e.key, code: e.code,
      ctrl: e.ctrlKey, shift: e.shiftKey, alt: e.altKey, meta: e.metaKey,
      target: info.selector,
      role: info.role, ariaLabel: info.ariaLabel
    });
  }, true);
})();
