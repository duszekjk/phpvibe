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
  let initialPreviewPageSeen = false;
  const navigationGuardKey = `phpvibe-preview-navigation:${window.location.pathname}`;

  const copyProgress = document.getElementById("copy-progress");
  if (copyProgress && root.dataset.progressUrl) {
    const pollProgress = async () => {
      try {
        const response = await fetch(root.dataset.progressUrl, { headers: { Accept: "application/json" } });
        const data = await response.json();
        const total = Number(data.bytes_total || 0);
        const done = Number(data.bytes_done || 0);
        document.getElementById("copy-progress-stage").textContent = data.stage || "Przygotowywanie kopii roboczej…";
        document.getElementById("copy-progress-bar").value = total > 0 ? Math.min(100, done / total * 100) : 0;
        document.getElementById("copy-progress-size").textContent = total > 0 ? `${(done / 1048576).toFixed(1)} MB z ${(total / 1048576).toFixed(1)} MB · ${data.files_done} z ${data.files_total} plików` : "Obliczanie rozmiaru strony…";
        if (data.status === "active" || data.status === "failed") window.location.reload();
        else window.setTimeout(pollProgress, 650);
      } catch (_error) { window.setTimeout(pollProgress, 1500); }
    };
    pollProgress();
  }

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

  function comparablePageUrl(rawUrl) {
    try {
      const url = new URL(rawUrl);
      url.hash = "";
      url.searchParams.delete("__vibe_token");
      const parameters = [...url.searchParams.entries()].sort(([leftKey, leftValue], [rightKey, rightValue]) =>
        leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue));
      url.search = "";
      for (const [key, value] of parameters) url.searchParams.append(key, value);
      url.hostname = url.hostname.toLowerCase();
      if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, "");
      return url.href;
    } catch (_error) {
      return null;
    }
  }

  function wasJustNavigated(targetKey) {
    try {
      const previous = JSON.parse(window.sessionStorage.getItem(navigationGuardKey) || "null");
      return previous?.target === targetKey && Date.now() - Number(previous.at) < 10000;
    } catch (_error) {
      return false;
    }
  }

  function rememberNavigation(targetKey) {
    try {
      window.sessionStorage.setItem(navigationGuardKey, JSON.stringify({ target: targetKey, at: Date.now() }));
    } catch (_error) { /* Nawigacja nadal zadziała bez sessionStorage. */ }
  }

  async function switchConversation(targetUrl) {
    const targetKey = comparablePageUrl(targetUrl);
    const currentKey = comparablePageUrl(root.dataset.targetUrl);
    if (navigating || !targetKey || targetKey === currentKey || wasJustNavigated(targetKey)) return;
    navigating = true;
    const body = new FormData();
    body.append("url", targetUrl);
    body.append("csrfmiddlewaretoken", csrfToken());
    try {
      const response = await fetch(root.dataset.navigateUrl, { method: "POST", body, headers: { Accept: "application/json" } });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "Nie udało się otworzyć podstrony.");
      rememberNavigation(comparablePageUrl(data.target_url) || targetKey);
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
      if (!initialPreviewPageSeen) {
        initialPreviewPageSeen = true;
        return;
      }
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
