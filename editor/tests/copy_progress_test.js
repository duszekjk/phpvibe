"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const elements = {
  "copy-progress": {},
  "copy-progress-stage": { textContent: "" },
  "copy-progress-bar": { value: 0 },
  "copy-progress-size": { textContent: "" },
  "editor-workbench": {
    dataset: {
      progressUrl: "/rozmowy/test/postep/",
      targetUrl: "https://example.test/",
    },
  },
};
const scheduled = [];
const replacements = [];
const responses = [
  {
    status: "preparing",
    stage: "Kopiowanie plików…",
    bytes_total: 10 * 1048576,
    bytes_done: 4 * 1048576,
    files_total: 10,
    files_done: 4,
  },
  {
    status: "active",
    stage: "Gotowe",
    bytes_total: 10 * 1048576,
    bytes_done: 10 * 1048576,
    files_total: 10,
    files_done: 10,
  },
];
let fetchCalls = 0;

global.document = {
  getElementById(id) { return elements[id] || null; },
  querySelector() { return null; },
};
global.window = {
  location: {
    href: "https://phpvibe.example.test/rozmowy/test/",
    pathname: "/rozmowy/test/",
    replace(url) { replacements.push(url); },
  },
  addEventListener() {},
  clearTimeout() {},
  setTimeout(callback, delay) { scheduled.push({ callback, delay }); },
  sessionStorage: { getItem() { return null; }, setItem() {} },
};
global.fetch = async (_url, options) => {
  assert.equal(options.cache, "no-store");
  const data = responses[fetchCalls++];
  return { async json() { return data; } };
};

const flushPromises = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  vm.runInThisContext(fs.readFileSync("editor/static/editor/workbench.js", "utf8"), {
    filename: "workbench.js",
  });
  await flushPromises();

  assert.equal(fetchCalls, 1);
  assert.equal(elements["copy-progress-stage"].textContent, "Kopiowanie plików…");
  assert.equal(elements["copy-progress-bar"].value, 40);
  assert.equal(elements["copy-progress-size"].textContent, "4.0 MB z 10.0 MB · 4 z 10 plików");
  assert.equal(replacements.length, 0, "Widok przeładował się podczas kopiowania");
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].delay, 650);

  scheduled.shift().callback();
  await flushPromises();

  assert.equal(fetchCalls, 2);
  assert.equal(elements["copy-progress-bar"].value, 100);
  assert.equal(replacements.length, 1, "Widok nie przeszedł do gotowego podglądu dokładnie raz");
  assert.match(replacements[0], /_vibe_copy_finished=/);
  assert.equal(scheduled.length, 0, "Polling nie zatrzymał się po zakończeniu kopiowania");
  console.log("copy progress regression test: OK");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
