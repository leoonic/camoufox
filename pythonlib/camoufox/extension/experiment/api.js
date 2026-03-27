/* global ExtensionAPI */
"use strict";

this.nativeInput = class extends ExtensionAPI {
  getAPI(context) {
    const { Services } = globalThis;

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

    return {
      nativeInput: {
        async click(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          const absX = rect.left + x;
          const absY = rect.top + y;

          utils.sendMouseEventToWindow("mousemove", absX, absY, 0, 0, 0);
          utils.sendMouseEventToWindow("mousedown", absX, absY, button, 1, 0);
          utils.sendMouseEventToWindow("mouseup", absX, absY, button, 1, 0);
        },

        async moveTo(tabId, x, y) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          utils.sendMouseEventToWindow(
            "mousemove", rect.left + x, rect.top + y, 0, 0, 0
          );
        },

        async mouseDown(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          utils.sendMouseEventToWindow(
            "mousedown", rect.left + x, rect.top + y, button, 1, 0
          );
        },

        async mouseUp(tabId, x, y, button = 0) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          utils.sendMouseEventToWindow(
            "mouseup", rect.left + x, rect.top + y, button, 1, 0
          );
        },

        async scroll(tabId, x, y, deltaX, deltaY) {
          const browser = getBrowserForTab(tabId);
          const utils = getWindowUtils(browser);
          const rect = getBrowserRect(browser);
          utils.sendWheelEvent(
            rect.left + x, rect.top + y,
            deltaX, deltaY, 0,
            0, /* DOM_DELTA_PIXEL */
            0, 0, 0, 0
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
      },
    };
  }
};
