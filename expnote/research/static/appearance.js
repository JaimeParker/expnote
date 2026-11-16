import { h, icon } from "/research/ui.js";

const DEFAULTS = {
  ui_font_size: 15,
  prose_font_size: 17,
  density: "comfortable",
  theme: "system",
  home_layout: "default",
};

const FILE = "research-layout.toml";

export function wireAppearance({ api, post, onApply }) {
  const root = document.documentElement;
  const toggle = document.getElementById("appearanceButton");
  const container = document.getElementById("appearanceControls");
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let defaults = DEFAULTS;
  let saved = DEFAULTS;
  let draft = null;
  let saving = false;

  function applyTheme() {
    const theme = (draft || saved).theme;
    root.setAttribute("data-theme", theme === "system" ? (system.matches ? "dark" : "light") : theme);
  }

  function apply() {
    const settings = draft || saved;
    root.style.setProperty("--ui-font-size", `${settings.ui_font_size}px`);
    root.style.setProperty("--prose-font-size", `${settings.prose_font_size}px`);
    root.setAttribute("data-density", settings.density);
    applyTheme();
    onApply(settings.home_layout);
  }

  function range(name, label, min, max) {
    const id = `appearance-${name}`;
    const output = h("output", { for: id });
    const input = h("input", {
      id, name, type: "range", min, max, step: 1,
      oninput: () => change(name, Number(input.value)),
    });
    return {
      row: h("div", { class: "appearance-field" }, h("label", { for: id }, label), output, input),
      set(value) {
        input.value = String(value);
        output.textContent = `${value}px`;
      },
    };
  }

  function select(name, label, options) {
    const id = `appearance-${name}`;
    const input = h("select", { id, name, onchange: () => change(name, input.value) },
      options.map(([value, text]) => h("option", { value }, text)),
    );
    return {
      row: h("div", { class: "appearance-field" }, h("label", { for: id }, label), input),
      set(value) { input.value = value; },
    };
  }

  const controls = {
    ui_font_size: range("ui_font_size", "Interface font size", 13, 20),
    prose_font_size: range("prose_font_size", "Reading font size", 14, 24),
    density: select("density", "Density", [["comfortable", "Comfortable"], ["compact", "Compact"]]),
    theme: select("theme", "Theme", [["system", "Follow system"], ["light", "Light"], ["dark", "Dark"]]),
    home_layout: select("home_layout", "Home layout", [
      ["default", "Default"], ["task-first", "Task first"], ["single-column", "Single column"],
    ]),
  };
  const warning = h("p", { class: "appearance-warning small", role: "status", hidden: true });
  const failure = h("p", { class: "appearance-warning small", role: "alert", hidden: true });
  const closeButton = h("button", {
    type: "button", class: "btn small ghost", "aria-label": "Close appearance", onclick: cancel,
  }, icon("close"));
  const fieldset = h("fieldset", null,
    Object.values(controls).map(({ row }) => row),
    h("div", { class: "appearance-actions" },
      h("button", { type: "button", class: "btn small ghost", onclick: restoreDefaults }, "Restore defaults"),
      h("button", { type: "button", class: "btn small", onclick: cancel }, "Cancel"),
      h("button", { type: "submit", class: "btn small primary" }, "Save"),
    ),
  );
  const panel = h("section", {
    id: "appearancePanel", class: "appearance-panel", role: "dialog",
    "aria-labelledby": "appearanceTitle", hidden: true,
  },
    h("div", { class: "appearance-head" }, h("h2", { id: "appearanceTitle" }, "Appearance"), closeButton),
    warning,
    h("form", { onsubmit: save }, fieldset, failure),
  );

  function message(node, text) {
    node.textContent = text || "";
    if (text) node.removeAttribute("hidden");
    else node.setAttribute("hidden", "");
  }

  function preview() {
    for (const [name, control] of Object.entries(controls)) control.set(draft[name]);
    apply();
  }

  function change(name, value) {
    draft[name] = value;
    controls[name].set(value);
    apply();
  }

  function restoreDefaults() {
    draft = { ...defaults };
    preview();
  }

  function cancel() {
    if (saving || draft === null) return;
    dismiss();
  }

  function dismiss() {
    draft = null;
    panel.setAttribute("hidden", "");
    toggle.setAttribute("aria-expanded", "false");
    apply();
    toggle.focus();
  }

  async function save(event) {
    event.preventDefault();
    if (saving || draft === null) return;
    saving = true;
    fieldset.setAttribute("disabled", "");
    closeButton.setAttribute("disabled", "");
    panel.setAttribute("aria-busy", "true");
    message(failure, "");
    let success = false;
    try {
      const { data } = await post("/layout", draft);
      saved = data.settings;
      message(warning, "");
      success = true;
    } catch (error) {
      message(failure, error.message || `Could not save ${FILE}.`);
    } finally {
      saving = false;
      fieldset.removeAttribute("disabled");
      closeButton.removeAttribute("disabled");
      panel.removeAttribute("aria-busy");
    }
    if (success) dismiss();
  }

  toggle.addEventListener("click", () => {
    if (draft) {
      cancel();
      return;
    }
    draft = { ...saved };
    message(failure, "");
    preview();
    panel.removeAttribute("hidden");
    toggle.setAttribute("aria-expanded", "true");
    closeButton.focus();
  });
  container.append(panel);
  apply();
  system.addEventListener?.("change", applyTheme);

  return {
    isOpen: () => draft !== null,
    cancel,
    async load() {
      try {
        const { data } = await api("/layout");
        saved = data.settings;
        defaults = data.defaults;
        message(warning, data.warning);
      } catch {
        message(warning, `Could not load ${FILE}. Using the defaults.`);
      }
      apply();
      toggle.removeAttribute("disabled");
    },
  };
}
