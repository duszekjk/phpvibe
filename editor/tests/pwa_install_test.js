"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("editor/static/editor/pwa-install.js", "utf8");

function environment({ userAgent, platform = "", maxTouchPoints = 0 }) {
  class Element {
    constructor() {
      this.hidden = true;
      this.disabled = false;
      this.textContent = "";
      this.listeners = {};
    }
    addEventListener(type, callback) { this.listeners[type] = callback; }
  }

  const elements = {
    "pwa-install-prompt": new Element(),
    "pwa-install-description": new Element(),
    "pwa-install-button": new Element(),
    "pwa-install-dismiss": new Element(),
  };
  const windowListeners = {};
  const context = {
    console,
    navigator: { userAgent, platform, maxTouchPoints, standalone: false },
    document: {
      documentElement: { classList: { add() {} } },
      getElementById(id) { return elements[id] || null; },
    },
    window: {
      matchMedia(query) { return { matches: query.includes("max-width") }; },
      addEventListener(type, callback) { windowListeners[type] = callback; },
      sessionStorage: { getItem() { return null; }, setItem() {} },
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: "pwa-install.js" });
  return { elements, windowListeners };
}

(async () => {
  const ios = environment({ userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X)" });
  assert.equal(ios.elements["pwa-install-prompt"].hidden, false);
  assert.match(ios.elements["pwa-install-description"].textContent, /Udostępnij/);
  assert.equal(ios.elements["pwa-install-button"].hidden, true);
  ios.windowListeners.beforeinstallprompt({ preventDefault() {} });
  assert.equal(ios.elements["pwa-install-button"].hidden, true);

  const android = environment({ userAgent: "Mozilla/5.0 (Linux; Android 16) Chrome/140" });
  let prompted = 0;
  const installEvent = {
    preventDefault() {},
    async prompt() { prompted += 1; },
    userChoice: Promise.resolve({ outcome: "accepted" }),
  };
  android.windowListeners.beforeinstallprompt(installEvent);
  assert.equal(android.elements["pwa-install-prompt"].hidden, false);
  assert.equal(android.elements["pwa-install-button"].hidden, false);
  await android.elements["pwa-install-button"].listeners.click();
  assert.equal(prompted, 1);
  assert.equal(android.elements["pwa-install-prompt"].hidden, true);
  console.log("PWA installation regression test: OK");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
