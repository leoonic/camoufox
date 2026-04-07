/* global ExtensionAPI */
"use strict";

this.nativeInput = class extends ExtensionAPI {
  getAPI(context) {
    function getBrowserForTab(tabId) {
      const tab = context.extension.tabManager.get(tabId);
      if (!tab) throw new Error(`Tab ${tabId} not found`);
      return tab.nativeTab.linkedBrowser;
    }

    function getWindowUtils(browser) {
      return browser.ownerGlobal.windowUtils;
    }

    function getBrowserRect(browser) {
      return browser.getBoundingClientRect();
    }

    // Primary: synthesizeMouseEvent (no physical cursor, isTrusted=true, cross-process).
    // Fallback: sendNativeMouseEvent (moves physical cursor but guaranteed cross-process).
    function dispatchMouse(browser, type, contentX, contentY, opts = {}) {
      const win = browser.ownerGlobal;
      const rect = getBrowserRect(browser);
      const chromeX = rect.left + contentX;
      const chromeY = rect.top + contentY;

      if (typeof win.synthesizeMouseEvent === "function") {
        win.synthesizeMouseEvent(
          type, chromeX, chromeY,
          {
            button: opts.button || 0,
            buttons: opts.buttons || 0,
            clickCount: opts.clickCount || 0,
            modifiers: 0,
            pressure: (type === "mousedown" || type === "mouseup") ? 0.5 : 0.0,
            inputSource: 1,
          },
          {
            isDOMEventSynthesized: false,
            isWidgetEventSynthesized: false,
          }
        );
      } else {
        // Fallback: native OS event
        const utils = getWindowUtils(browser);
        const s = win.devicePixelRatio;
        const sx = Math.round((win.mozInnerScreenX + chromeX) * s);
        const sy = Math.round((win.mozInnerScreenY + chromeY) * s);
        let msg;
        if (type === "mousedown") msg = utils.NATIVE_MOUSE_MESSAGE_BUTTON_DOWN;
        else if (type === "mouseup") msg = utils.NATIVE_MOUSE_MESSAGE_BUTTON_UP;
        else msg = utils.NATIVE_MOUSE_MESSAGE_MOVE;
        utils.sendNativeMouseEvent(sx, sy, msg, opts.button || 0, 0, browser);
      }
    }

    return {
      nativeInput: {
        async click(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          dispatchMouse(browser, "mousemove", x, y);
          dispatchMouse(browser, "mousedown", x, y, { button, buttons: 1, clickCount: 1 });
          dispatchMouse(browser, "mouseup", x, y, { button, buttons: 0, clickCount: 1 });
        },

        async moveTo(tabId, x, y) {
          const browser = getBrowserForTab(tabId);
          dispatchMouse(browser, "mousemove", x, y);
        },

        async mouseDown(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          dispatchMouse(browser, "mousedown", x, y, { button, buttons: 1, clickCount: 1 });
        },

        async mouseUp(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          dispatchMouse(browser, "mouseup", x, y, { button, buttons: 0, clickCount: 1 });
        },

        async scroll(tabId, x, y, deltaX, deltaY) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          utils.sendWheelEvent(
            rect.left + x, rect.top + y,
            deltaX, deltaY, 0,
            0, 0, 0, 0, 0
          );
        },

        async type(tabId, text) {
          const browser = getBrowserForTab(tabId);
          const win = browser.ownerGlobal;
          const tip = Cc["@mozilla.org/text-input-processor;1"]
            .createInstance(Ci.nsITextInputProcessor);
          const begun = tip.beginInputTransactionForTests(win);
          if (!begun) throw new Error("Failed to begin input transaction");
          for (const ch of text) {
            const keyEvent = new win.KeyboardEvent("keydown", { key: ch });
            tip.keydown(keyEvent);
            tip.keyup(keyEvent);
          }
        },

        async getPort() {
          const { Services } = globalThis;
          return Services.prefs.getIntPref("extensions.input.ws_port", 8775);
        },

        async keyPress(tabId, key) {
          const browser = getBrowserForTab(tabId);
          const win = browser.ownerGlobal;
          const tip = Cc["@mozilla.org/text-input-processor;1"]
            .createInstance(Ci.nsITextInputProcessor);
          const begun = tip.beginInputTransactionForTests(win);
          if (!begun) throw new Error("Failed to begin input transaction");
          const keyEvent = new win.KeyboardEvent("keydown", { key: key });
          tip.keydown(keyEvent);
          tip.keyup(keyEvent);
        },

        async navigateTo(tabId, url) {
          const browser = getBrowserForTab(tabId);
          browser.browsingContext.fixupAndLoadURIString(url, {
            triggeringPrincipal:
              Services.scriptSecurityManager.getSystemPrincipal(),
            hasValidUserGestureActivation: true,
          });
        },
      },
    };
  }
};
