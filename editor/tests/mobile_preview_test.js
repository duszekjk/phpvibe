"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const windowListeners = {};
const frameListeners = {};
const properties = new Map();
const viewport = { clientWidth: 375 };
const root = {
  dataset: {},
  style: {
    setProperty(name, value) { properties.set(name, value); },
    removeProperty(name) { properties.delete(name); },
  },
};
const frame = {
  parentElement: viewport,
  addEventListener(type, callback) { frameListeners[type] = callback; },
};

global.window = {
  location: { pathname: "/rozmowy/test/" },
  matchMedia() { return { matches: true }; },
  addEventListener(type, callback) { windowListeners[type] = callback; },
  clearTimeout() {},
  setTimeout() {},
  sessionStorage: { getItem() { return null; }, setItem() {} },
};
global.document = {
  getElementById(id) {
    if (id === "editor-workbench") return root;
    if (id === "site-preview") return frame;
    return null;
  },
  querySelector() { return null; },
};

vm.runInThisContext(fs.readFileSync("editor/static/editor/workbench.js", "utf8"), {
  filename: "workbench.js",
});

assert.equal(Number(properties.get("--mobile-preview-scale")), 375 / 1280);
viewport.clientWidth = 320;
windowListeners.resize();
assert.equal(Number(properties.get("--mobile-preview-scale")), 320 / 1280);
assert.equal(typeof frameListeners.load, "function");
console.log("mobile desktop-preview scaling regression test: OK");
