(() => {
  "use strict";
  if (window.__phpVibeBridgeLoaded) return;
  window.__phpVibeBridgeLoaded = true;

  let editMode = false;
  let highlighted = null;
  const panelOrigin = "__PHPVIBE_PANEL_ORIGIN__";
  const candidates = "h1,h2,h3,h4,h5,h6,p,a,button,label,li,td,th,figcaption,blockquote,span,div";

  function post(type, payload = {}) {
    window.parent.postMessage({ source: "phpvibe-preview", type, ...payload }, panelOrigin);
  }

  function clearHighlight() {
    if (!highlighted) return;
    highlighted.classList.remove("__phpvibe_selected");
    highlighted = null;
  }

  function highlight(element) {
    if (highlighted === element) return;
    clearHighlight();
    highlighted = element;
    element.classList.add("__phpvibe_selected");
  }

  function usefulElement(target) {
    const element = target.closest?.(candidates);
    if (!element || !element.innerText?.trim()) return null;
    if (element.innerText.trim().length > 10000) return null;
    return element;
  }

  function cssPath(element) {
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
      if (current.id && /^[A-Za-z][\w:.-]*$/.test(current.id)) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      let part = current.tagName.toLowerCase();
      const siblings = current.parentElement ? [...current.parentElement.children].filter(item => item.tagName === current.tagName) : [];
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      parts.unshift(part);
      current = current.parentElement;
      if (parts.length >= 8) break;
    }
    return parts.join(" > ");
  }

  document.addEventListener("mouseover", event => {
    if (!editMode) return;
    const element = usefulElement(event.target);
    if (element) highlight(element);
  }, true);

  document.addEventListener("mouseout", event => {
    if (editMode && highlighted && !highlighted.contains(event.relatedTarget)) clearHighlight();
  }, true);

  document.addEventListener("click", event => {
    const anchor = event.target.closest?.("a[href]");
    const token = new URL(window.location.href).searchParams.get("__vibe_token");
    if (anchor && token && !editMode && !event.shiftKey) {
      try {
        const targetUrl = new URL(anchor.href, window.location.href);
        if (targetUrl.origin === window.location.origin) {
          targetUrl.searchParams.set("__vibe_token", token);
          anchor.href = targetUrl.href;
        } else if (["http:", "https:"].includes(targetUrl.protocol)) {
          event.preventDefault();
          event.stopImmediatePropagation();
          post("link-clicked", { href: targetUrl.href });
          return;
        }
      } catch (_error) { /* Nie blokuj zwykłej nawigacji dla nietypowego href. */ }
    }
    if (!editMode && !event.shiftKey) return;
    const element = usefulElement(event.target);
    if (!element) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    highlight(element);
    post("text-selected", {
      pageUrl: window.location.href,
      selection: {
        text: element.innerText.trim().slice(0, 10000),
        tagName: element.tagName.toLowerCase(),
        selector: cssPath(element).slice(0, 2000),
        outerHTML: element.outerHTML.slice(0, 8000),
      },
    });
  }, true);

  document.addEventListener("submit", event => {
    const token = new URL(window.location.href).searchParams.get("__vibe_token");
    const form = event.target;
    if (!token || !(form instanceof HTMLFormElement)) return;
    try {
      const action = new URL(form.action || window.location.href, window.location.href);
      if (action.origin !== window.location.origin) return;
      if ((form.method || "get").toLowerCase() === "get") {
        let input = form.querySelector('input[name="__vibe_token"]');
        if (!input) {
          input = document.createElement("input");
          input.type = "hidden";
          input.name = "__vibe_token";
          form.appendChild(input);
        }
        input.value = token;
      } else {
        action.searchParams.set("__vibe_token", token);
        form.action = action.href;
      }
    } catch (_error) { /* Nietypowe action pozostaje bez zmian. */ }
  }, true);

  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== panelOrigin || !event.data) return;
    if (event.data.type === "phpvibe:set-edit-mode") {
      editMode = Boolean(event.data.enabled);
      document.documentElement.classList.toggle("phpvibe-edit-mode", editMode);
      if (!editMode) clearHighlight();
    }
    if (event.data.type === "phpvibe:navigate") {
      if (event.data.action === "back") history.back();
      if (event.data.action === "forward") history.forward();
      if (event.data.action === "refresh") location.reload();
    }
  });

  const referrerMeta = document.createElement("meta");
  referrerMeta.name = "referrer";
  referrerMeta.content = "no-referrer";
  document.head?.appendChild(referrerMeta);

  function notifyPage() { post("page-changed", { pageUrl: window.location.href }); }
  window.addEventListener("pageshow", notifyPage);
  window.addEventListener("hashchange", notifyPage);
  const ready = () => post("ready", { pageUrl: window.location.href });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
