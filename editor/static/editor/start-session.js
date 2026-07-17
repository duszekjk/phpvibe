(() => {
  "use strict";

  const site = document.getElementById("id_site");
  const input = document.getElementById("id_target_url");
  const panel = document.getElementById("url-suggestions");
  const list = document.getElementById("url-suggestions-list");
  const status = document.getElementById("url-suggestions-status");
  const count = document.getElementById("url-suggestions-count");
  if (!site || !input || !panel || !list || !status || !count) return;

  let suggestions = [];
  let controller = null;

  const searchable = value => value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pl")
    .trim();

  const render = () => {
    const query = searchable(input.value);
    const words = query.split(/\s+/).filter(Boolean);
    const visible = suggestions.filter(item => {
      const haystack = searchable(`${item.label} ${item.url}`);
      return words.every(word => haystack.includes(word));
    });
    list.replaceChildren();
    count.textContent = suggestions.length ? `${visible.length} z ${suggestions.length}` : "";
    if (!visible.length) {
      status.hidden = false;
      status.textContent = suggestions.length
        ? "Brak pasujących podstron. Możesz nadal wkleić dowolny poprawny adres tej strony."
        : "Nie znaleziono linków na stronie głównej. Możesz wpisać adres ręcznie.";
      return;
    }
    status.hidden = true;
    visible.forEach(item => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "url-suggestion";
      button.setAttribute("role", "option");
      button.title = `Wstaw ${item.url}`;
      const label = document.createElement("span");
      label.textContent = item.label;
      const url = document.createElement("small");
      url.textContent = item.url;
      button.append(label, url);
      button.addEventListener("click", () => {
        input.value = item.url;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      });
      list.append(button);
    });
  };

  const load = async () => {
    suggestions = [];
    list.replaceChildren();
    count.textContent = "";
    if (!site.value) {
      status.hidden = false;
      status.textContent = "Wybierz stronę, aby zobaczyć jej podstrony.";
      return;
    }
    if (controller) controller.abort();
    controller = new AbortController();
    status.hidden = false;
    status.textContent = "Wczytywanie podpowiedzi…";
    const url = panel.dataset.urlTemplate.replace(/\/0\//, `/${site.value}/`);
    try {
      const response = await fetch(url, { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      suggestions = Array.isArray(data.suggestions) ? data.suggestions : [];
      if (data.error) {
        status.hidden = false;
        status.textContent = data.error;
        return;
      }
      render();
    } catch (error) {
      if (error.name === "AbortError") return;
      status.hidden = false;
      status.textContent = "Nie udało się wczytać podpowiedzi. Możesz wpisać pełny adres ręcznie.";
    }
  };

  input.addEventListener("input", render);
  site.addEventListener("change", () => {
    input.value = "";
    load();
  });
  load();
})();
