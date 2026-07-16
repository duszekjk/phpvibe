"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const listeners = {};
const previewWindow = {};
let fetchCalls = 0;

const root = {
  dataset: {
    previewBase: "https://tmp.example.test/vibe/9abeb6c9-4529-4a16-a408-529101b3bd40/",
    targetUrl: "https://example.test/?strona=wspolnota&podstrona=diakonie&diakonie=medialna",
    navigateUrl: "/navigate/",
    allowedHosts: "example.test",
  },
};
const frame = { contentWindow: previewWindow, addEventListener() {} };

global.window = {
  location: { pathname: "/rozmowy/test/" },
  addEventListener(type, callback) { listeners[type] = callback; },
  clearTimeout,
  setTimeout,
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
global.fetch = async () => {
  fetchCalls += 1;
  return {
    ok: true,
    async json() {
      return { ok: true, url: "/rozmowy/inna/", target_url: "https://example.test/kontakt" };
    },
  };
};

const source = fs.readFileSync("editor/static/editor/workbench.js", "utf8");
vm.runInThisContext(source, { filename: "workbench.js" });

listeners.message({
  source: previewWindow,
  origin: "https://tmp.example.test",
  data: {
    source: "phpvibe-preview",
    type: "page-changed",
    pageUrl: "https://tmp.example.test/vibe/9abeb6c9-4529-4a16-a408-529101b3bd40/"
      + "__vibe_token/signed-token/unexpected-initial-path"
      + "?__vibe_token=signed-token",
  },
});

assert.equal(fetchCalls, 0, "Initial preview synchronization caused a reload");

listeners.message({
  source: previewWindow,
  origin: "https://tmp.example.test",
  data: {
    source: "phpvibe-preview",
    type: "page-changed",
    pageUrl: "https://tmp.example.test/vibe/9abeb6c9-4529-4a16-a408-529101b3bd40/"
      + "__vibe_token/signed-token/"
      + "?diakonie=medialna&__vibe_token=signed-token&podstrona=diakonie&strona=wspolnota",
  },
});

assert.equal(fetchCalls, 0, "Equivalent URLs with reordered parameters caused a reload loop");

listeners.message({
  source: previewWindow,
  origin: "https://tmp.example.test",
  data: {
    source: "phpvibe-preview",
    type: "page-changed",
    pageUrl: "https://tmp.example.test/vibe/9abeb6c9-4529-4a16-a408-529101b3bd40/"
      + "__vibe_token/signed-token/kontakt?__vibe_token=signed-token",
  },
});

assert.equal(fetchCalls, 0, "A page-changed event caused an automatic iframe reload");

listeners.message({
  source: previewWindow,
  origin: "https://tmp.example.test",
  data: {
    source: "phpvibe-preview",
    type: "link-clicked",
    href: "https://tmp.example.test/vibe/9abeb6c9-4529-4a16-a408-529101b3bd40/"
      + "__vibe_token/signed-token/kontakt?__vibe_token=signed-token",
  },
});

assert.equal(fetchCalls, 1, "An explicit preview link click did not navigate to its page conversation");
console.log("workbench navigation regression test: OK");
