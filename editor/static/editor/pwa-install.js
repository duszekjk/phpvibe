(() => {
  "use strict";

  const promptBox = document.getElementById("pwa-install-prompt");
  const description = document.getElementById("pwa-install-description");
  const installButton = document.getElementById("pwa-install-button");
  const dismissButton = document.getElementById("pwa-install-dismiss");
  if (!promptBox || !description || !installButton || !dismissButton) return;

  const standalone = window.matchMedia?.("(display-mode: standalone)")?.matches || navigator.standalone === true;
  if (standalone) {
    document.documentElement.classList.add("pwa-standalone");
    return;
  }

  try {
    if (window.sessionStorage.getItem("phpvibe-install-dismissed") === "1") return;
  } catch (_error) { /* Podpowiedź może działać bez sessionStorage. */ }

  const appleMobile = /iPhone|iPad|iPod/i.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const phoneLike = window.matchMedia?.("(max-width: 900px)")?.matches !== false;
  let deferredInstall = null;

  function showPrompt(kind) {
    if (!phoneLike) return;
    promptBox.hidden = false;
    if (kind === "ios") {
      description.textContent = "Na iPhonie stuknij Udostępnij, a następnie „Dodaj do ekranu początkowego”.";
      installButton.hidden = true;
    } else {
      description.textContent = "Dodaj PHP Vibe do ekranu głównego i otwieraj edytor bez paska przeglądarki.";
      installButton.hidden = false;
    }
  }

  if (appleMobile) showPrompt("ios");

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    if (appleMobile) return;
    deferredInstall = event;
    showPrompt("browser");
  });

  installButton.addEventListener("click", async () => {
    if (!deferredInstall) return;
    installButton.disabled = true;
    await deferredInstall.prompt();
    const choice = await deferredInstall.userChoice;
    deferredInstall = null;
    installButton.disabled = false;
    if (choice?.outcome === "accepted") promptBox.hidden = true;
  });

  dismissButton.addEventListener("click", () => {
    promptBox.hidden = true;
    try { window.sessionStorage.setItem("phpvibe-install-dismissed", "1"); } catch (_error) { /* Bez znaczenia. */ }
  });

  window.addEventListener("appinstalled", () => {
    deferredInstall = null;
    promptBox.hidden = true;
  });
})();
