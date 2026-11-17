import { wireAppearance } from "/research/appearance.js";
import { wireTemplates } from "/research/templates.js";
import { h, icon } from "/research/ui.js";

export function wireSettings({ api, post, onApply, currentProject }) {
  const toggle = document.getElementById("settingsButton");
  const container = document.getElementById("settingsControls");
  const tabs = {};
  const panes = {};
  const names = ["Templates", "Appearance"];
  let category = "Templates";
  let project = null;

  const tablist = h("div", { class: "settings-tabs", role: "tablist", "aria-label": "Settings categories" });
  for (const name of names) {
    const key = name.toLowerCase();
    panes[name] = h("section", {
      id: `settings-${key}`, role: "tabpanel", "aria-labelledby": `tab-${key}`, hidden: true,
    });
    tabs[name] = h("button", {
      class: "settings-tab", type: "button", role: "tab", id: `tab-${key}`,
      "aria-controls": `settings-${key}`, onclick: () => select(name),
      onkeydown(event) {
        const shortcuts = { ArrowLeft: other(name), ArrowRight: other(name), Home: names[0], End: names.at(-1) };
        if (!shortcuts[event.key]) return;
        event.preventDefault();
        select(shortcuts[event.key]);
      },
    }, name);
    tablist.append(tabs[name]);
  }

  const panel = h("section", {
    id: "settingsPanel", class: "settings-panel", role: "dialog",
    "aria-labelledby": "settingsTitle", hidden: true,
  },
    h("div", { class: "settings-head" },
      h("h2", { id: "settingsTitle" }, "Settings"),
      h("button", { class: "btn small ghost", type: "button", "aria-label": "Close settings", onclick: close }, icon("close")),
    ),
    tablist, Object.values(panes),
  );

  const templates = wireTemplates({ panel: panes.Templates, api, post });
  const appearance = wireAppearance({
    panel: panes.Appearance, api, post, onApply,
    onDone() {
      if (!templates.dirty()) close();
    },
  });

  function other(name) {
    return names[0] === name ? names[1] : names[0];
  }

  function select(name) {
    category = name;
    panel.setAttribute("data-category", name.toLowerCase());
    for (const tab of names) {
      const selected = tab === name;
      tabs[tab].setAttribute("aria-selected", String(selected));
      tabs[tab].setAttribute("tabindex", selected ? "0" : "-1");
      if (selected) panes[tab].removeAttribute("hidden");
      else panes[tab].setAttribute("hidden", "");
    }
    if (name === "Templates") templates.open(project);
    else appearance.open();
    tabs[name].focus();
  }

  function isOpen() {
    return !panel.hasAttribute("hidden");
  }

  function close() {
    if (!isOpen() || appearance.isSaving() || templates.isSaving() || !templates.canDiscard()) return;
    templates.reset();
    appearance.cancel();
    panel.setAttribute("hidden", "");
    toggle.setAttribute("aria-expanded", "false");
    toggle.focus();
  }

  toggle.addEventListener("click", () => {
    if (isOpen()) {
      close();
      return;
    }
    project = currentProject();
    panel.removeAttribute("hidden");
    toggle.setAttribute("aria-expanded", "true");
    select(category);
  });
  container.append(panel);

  return {
    isOpen, close,
    projectChanged() {
      if (isOpen() && project !== currentProject()) close();
    },
    async load() {
      await appearance.load();
      toggle.removeAttribute("disabled");
    },
  };
}
