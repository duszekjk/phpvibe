"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor() {
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
  }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  dispatchEvent(event) {
    if (this.listeners[event.type]) this.listeners[event.type](event);
    return true;
  }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this[name] = value; }
  focus() {}
}

const elements = {
  id_site: new Element(),
  id_target_url: new Element(),
  "url-suggestions": new Element(),
  "url-suggestions-list": new Element(),
  "url-suggestions-status": new Element(),
  "url-suggestions-count": new Element(),
};
elements.id_site.value = "7";
elements["url-suggestions"].dataset.urlTemplate = "/strony/0/podpowiedzi-url/";

global.Event = class Event { constructor(type) { this.type = type; } };
global.document = {
  getElementById(id) { return elements[id] || null; },
  createElement() { return new Element(); },
};
global.fetch = async url => {
  assert.equal(url, "/strony/7/podpowiedzi-url/");
  return {
    ok: true,
    async json() {
      return { suggestions: [
        { label: "Wspólnota", url: "https://example.org/?strona=wspolnota" },
        { label: "Wspólnota → Przymierze", url: "https://example.org/?strona=wspolnota&podstrona=przymierze" },
        { label: "Kontakt", url: "https://example.org/?strona=kontakt" },
      ] };
    },
  };
};

const flushPromises = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  vm.runInThisContext(fs.readFileSync("editor/static/editor/start-session.js", "utf8"), {
    filename: "start-session.js",
  });
  await flushPromises();

  const input = elements.id_target_url;
  const list = elements["url-suggestions-list"];
  assert.equal(list.children.length, 3);

  input.value = "wspolnota";
  input.dispatchEvent(new Event("input"));
  assert.equal(list.children.length, 2, "Search did not ignore Polish diacritics");

  input.value = "przymierze";
  input.dispatchEvent(new Event("input"));
  assert.equal(list.children.length, 1);
  list.children[0].listeners.click();
  assert.equal(input.value, "https://example.org/?strona=wspolnota&podstrona=przymierze");
  console.log("URL suggestions regression test: OK");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
