(() => {
  "use strict";
  const root = document.getElementById("editor-workbench");
  if (!root) return;
  const frame = document.getElementById("site-preview");
  const modal = document.getElementById("inline-editor");
  const modalForm = document.getElementById("inline-editor-form");
  const toggle = document.getElementById("edit-mode-toggle");
  const toast = document.getElementById("workbench-toast");
  const loading = document.getElementById("preview-loading");
  const chat = document.getElementById("chat-messages");
  let editMode = false;
  let bridgeReady = false;
  let navigating = false;

  if (chat) chat.scrollTop = chat.scrollHeight;
  document.getElementById("page-chat-select")?.addEventListener("change", event => { window.location.href = event.target.value; });

  function csrfToken() {
    return document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  }

  function showToast(message, kind = "info") {
    toast.textContent = message;
    toast.dataset.kind = kind;
    toast.classList.add("visible");
    window.clearTimeout(showToast.timeout);
    showToast.timeout = window.setTimeout(() => toast.classList.remove("visible"), 4200);
  }

  function sendToPreview(data) {
    if (!frame?.contentWindow) return;
    const origin = root.dataset.previewBase ? new URL(root.dataset.previewBase).origin : "*";
    frame.contentWindow.postMessage(data, origin);
  }

  function previewToCanonical(pageUrl) {
    const base = new URL(root.dataset.previewBase);
    const page = new URL(pageUrl);
    if (page.origin !== base.origin) return null;
    const prefix = base.pathname.replace(/\/+$/, "");
    if (page.pathname !== prefix && !page.pathname.startsWith(`${prefix}/`)) return null;
    const remainder = page.pathname.slice(prefix.length);
    const signedMarker = "/__vibe_token/";
    let suffix = remainder || "/";
    if (remainder.startsWith(signedMarker)) {
      const afterTokenMarker = remainder.slice(signedMarker.length);
      const pathStart = afterTokenMarker.indexOf("/");
      suffix = pathStart >= 0 ? afterTokenMarker.slice(pathStart) : "/";
    }
    const target = new URL(root.dataset.targetUrl);
    page.searchParams.delete("__vibe_token");
    return `${target.protocol}//${target.host}${suffix}${page.search}`;
  }

  async function switchConversation(targetUrl) {
    if (navigating || targetUrl === root.dataset.targetUrl) return;
    navigating = true;
    const body = new FormData();
    body.append("url", targetUrl);
    body.append("csrfmiddlewaretoken", csrfToken());
    try {
      const response = await fetch(root.dataset.navigateUrl, { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Nie udało się otworzyć podstrony.");
      window.location.href = data.url;
    } catch (error) {
      navigating = false;
      showToast(error.message, "error");
    }
  }

  window.addEventListener("message", event => {
    if (!frame || event.source !== frame.contentWindow || event.data?.source !== "phpvibe-preview") return;
    const expectedOrigin = root.dataset.previewBase ? new URL(root.dataset.previewBase).origin : "";
    if (event.origin !== expectedOrigin) return;
    if (event.data.type === "ready") {
      bridgeReady = true;
      loading?.classList.add("hidden");
      sendToPreview({ type: "phpvibe:set-edit-mode", enabled: editMode });
    }
    if (event.data.type === "page-changed") {
      loading?.classList.add("hidden");
      const canonical = previewToCanonical(event.data.pageUrl);
      if (canonical) switchConversation(canonical);
    }
    if (event.data.type === "link-clicked") {
      try {
        const target = new URL(event.data.href);
        const allowedHosts = new Set((root.dataset.allowedHosts || "").split(",").filter(Boolean));
        if (allowedHosts.has(target.hostname.toLowerCase())) switchConversation(target.href);
        else window.open(target.href, "_blank", "noopener");
      } catch (_error) { showToast("Nieprawidłowy adres odnośnika.", "error"); }
    }
    if (event.data.type === "text-selected") {
      const selection = event.data.selection || {};
      document.getElementById("inline-old-text").value = selection.text || "";
      document.getElementById("inline-new-text").value = selection.text || "";
      document.getElementById("inline-selector").value = selection.selector || "";
      document.getElementById("inline-tag").value = selection.tagName || "";
      document.getElementById("inline-html").value = selection.outerHTML || "";
      document.getElementById("selected-element-label").textContent = `<${selection.tagName || "tekst"}>`;
      document.getElementById("inline-error").textContent = "";
      modal.showModal();
      window.setTimeout(() => document.getElementById("inline-new-text").focus(), 50);
    }
  });

  frame?.addEventListener("load", () => {
    if (bridgeReady) {
      loading?.classList.add("hidden");
    } else {
      loading?.classList.remove("hidden");
      window.setTimeout(() => loading?.classList.add("hidden"), 2500);
    }
  });

  toggle?.addEventListener("click", () => {
    editMode = !editMode;
    toggle.classList.toggle("active", editMode);
    toggle.setAttribute("aria-pressed", String(editMode));
    sendToPreview({ type: "phpvibe:set-edit-mode", enabled: editMode });
    showToast(editMode ? "Tryb edycji włączony — kliknij tekst w podglądzie." : "Tryb edycji wyłączony.");
  });

  document.getElementById("preview-back")?.addEventListener("click", () => sendToPreview({ type: "phpvibe:navigate", action: "back" }));
  document.getElementById("preview-forward")?.addEventListener("click", () => sendToPreview({ type: "phpvibe:navigate", action: "forward" }));
  document.getElementById("preview-refresh")?.addEventListener("click", () => sendToPreview({ type: "phpvibe:navigate", action: "refresh" }));

  modalForm?.addEventListener("submit", async event => {
    const submitter = event.submitter;
    if (submitter?.value === "cancel") return;
    event.preventDefault();
    const button = document.getElementById("inline-save");
    const errorBox = document.getElementById("inline-error");
    button.disabled = true;
    button.textContent = "AI wprowadza zmianę…";
    errorBox.textContent = "";
    const body = new FormData(modalForm);
    body.append("csrfmiddlewaretoken", csrfToken());
    try {
      const response = await fetch(root.dataset.inlineEditUrl, { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Nie udało się zmienić tekstu.");
      modal.close();
      showToast("Zmiana zapisana. Odświeżam podgląd…", "success");
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      errorBox.textContent = error.message;
      button.disabled = false;
      button.textContent = "Zmień przez AI";
    }
  });
})();
