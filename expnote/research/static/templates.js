import { clear, errorBox, h, prose } from "/research/ui.js";

const TITLES = {
  plan: "PLAN",
  exec: "Execution PLAN",
  ev: "EV · Evidence",
  appendix: "Evidence appendix",
  res: "RES · Result",
};
const SCOPES = { project: "Project", global: "Global" };

export function wireTemplates({ panel, api, post }) {
  let path = null;
  let catalog = null;
  let selected = null;
  let scope = null;
  let editor = null;
  let saving = false;
  let loadId = 0;

  function entries() {
    return [...catalog.templates, ...catalog.references];
  }

  function current() {
    return entries().find((row) => row.id === selected);
  }

  function dirty() {
    return Boolean(editor && editor.value !== current().files[scope].text);
  }

  function canDiscard() {
    return !dirty() || window.confirm("Discard your unsaved template changes?");
  }

  function reset() {
    loadId += 1;
    path = catalog = selected = scope = editor = null;
    clear(panel);
  }

  async function load(requestPath) {
    path = requestPath;
    const request = ++loadId;
    editor = null;
    clear(panel).append(h("p", { role: "status", class: "muted" }, "Loading templates…"));
    try {
      const { data } = await api(requestPath);
      if (request !== loadId) return;
      catalog = data;
      selected = entries().some((row) => row.id === selected) ? selected : catalog.templates[0].id;
      scope = scope || current().source || "project";
      render();
    } catch (error) {
      if (request !== loadId) return;
      clear(panel).append(errorBox(error, () => load(requestPath)));
    }
  }

  function open(project) {
    if (path) return;
    if (project) load(`/projects/${encodeURIComponent(project)}/templates`);
    else clear(panel).append(h("p", { class: "muted" }, "Select a project to manage templates."));
  }

  function picker(id, label, options, value, change) {
    const select = h("select", {
      id, onchange() {
        if (!saving) change(select.value);
      },
    }, options);
    select.value = value;
    return {
      control: select,
      field: h("div", { class: "template-field" }, h("label", { for: id }, label), select),
    };
  }

  function scopeOptions(projectDisabled = false) {
    return Object.entries(SCOPES).map(([value, label]) => h("option", {
      value, disabled: value === "project" && projectDisabled,
    }, label));
  }

  function render({ draft, status } = {}) {
    const row = current();
    const file = row.files[scope];
    const generated = Boolean(row.source);
    const message = h("p", { class: "template-message small", role: "status", hidden: !status }, status);
    const failure = h("p", { class: "appearance-warning small", role: "alert", hidden: true });
    const preview = h("section", { class: "template-preview", "aria-label": "Rendered example", hidden: true });
    const reload = h("button", {
      class: "btn small", type: "button", hidden: true,
      onclick: () => { if (canDiscard()) load(path); },
    }, "Reload saved");
    const documentPicker = picker("template-document", "Document", [
      h("optgroup", { label: "Document templates" },
        catalog.templates.map((item) => h("option", { value: item.id }, TITLES[item.id]))),
      catalog.references.length ? h("optgroup", { label: "Project references" },
        catalog.references.map((item) => h("option", { value: item.id }, item.id))) : null,
    ], selected, (value) => {
      if (!canDiscard()) {
        documentPicker.control.value = selected;
        return;
      }
      selected = value;
      scope = current().source || "project";
      render();
      panel.querySelector?.("#template-document")?.focus();
    });
    const scopePicker = generated && picker(
      "template-edit-scope", "Edit template", scopeOptions(), scope, (value) => {
        if (!canDiscard()) {
          scopePicker.control.value = scope;
          return;
        }
        scope = value;
        render();
        panel.querySelector?.("#template-edit-scope")?.focus();
      },
    );
    const used = generated && picker(
      "template-source", "Use for new documents", scopeOptions(!row.files.project.saved), row.source,
      (value) => operate("select", value),
    );

    editor = h("textarea", { id: "template-markdown", rows: 15, spellcheck: "false", oninput: changed });
    editor.value = draft ?? file.text;
    const save = h("button", { class: "btn small primary", type: "submit" }, "Save template");
    const restore = h("button", {
      class: "btn small", type: "button", disabled: !file.canRestore,
      onclick: () => { if (canDiscard()) operate("restore"); },
    }, "Restore previous");
    const previewButton = h("button", {
      class: "btn small", type: "button", onclick: () => operate("preview"),
    }, "Preview example");

    const editorLabel = generated ? `${SCOPES[scope]} Markdown` : "Reference Markdown";
    const fields = h("fieldset", null,
      h("div", { class: "template-fields" }, documentPicker.field, scopePicker && scopePicker.field, used && used.field),
      generated && row.effectiveSource !== row.source
        ? h("p", { class: "appearance-warning small", role: "status" }, "Project template missing. Using Global.")
        : null,
      h("p", { class: "template-path small muted" },
        !file.saved && generated ? (scope === "global" ? "Built-in starting text · " : "Start from global · ") : null,
        file.path,
      ),
      generated ? h("details", { class: "template-variables small" },
        h("summary", null, "Template variables"),
        row.variables.map((name) => h("code", null, `{{${name}}}`)),
      ) : null,
      h("div", { class: "template-workspace" },
        h("div", { class: "template-field template-editor" }, h("label", { for: "template-markdown" }, editorLabel), editor),
        preview,
      ),
      h("div", { class: "template-actions" }, previewButton, restore, reload, save),
    );
    clear(panel).append(
      h("p", { class: "template-project small muted" }, catalog.projectName),
      h("form", { class: "template-form", onsubmit(event) { event.preventDefault(); operate("save"); } }, fields, failure, message),
    );
    setSaveState();

    function setSaveState() {
      if (dirty() || !file.saved) save.removeAttribute("disabled");
      else save.setAttribute("disabled", "");
    }

    function changed() {
      setSaveState();
      message.setAttribute("hidden", "");
      preview.setAttribute("hidden", "");
    }

    async function operate(action, targetScope = scope) {
      if (saving || (action === "save" && save.hasAttribute("disabled"))) return;
      saving = true;
      fields.setAttribute("disabled", "");
      panel.setAttribute("aria-busy", "true");
      failure.setAttribute("hidden", "");
      message.setAttribute("hidden", "");
      reload.setAttribute("hidden", "");
      try {
        const { data } = await post(path, {
          action, id: selected, scope: targetScope,
          expectedVersion: action === "select" ? row.selectionVersion : file.version,
          text: ["save", "preview"].includes(action) ? editor.value : undefined,
        });
        if (action === "preview") {
          clear(preview).append(
            h("h3", null, "Rendered example"), prose(data.html),
            h("details", null, h("summary", null, "Generated Markdown"), h("pre", null, data.text)),
          );
          preview.removeAttribute("hidden");
        } else {
          const input = action === "select" && dirty() ? editor.value : undefined;
          catalog = data;
          render({
            draft: input,
            status: action === "select" ? `Using the ${SCOPES[targetScope].toLowerCase()} template.`
              : action === "restore" ? "Previous version restored." : "Template saved.",
          });
          panel.querySelector?.("#template-markdown")?.focus();
        }
      } catch (error) {
        if (used) used.control.value = row.source;
        failure.textContent = error.message;
        failure.removeAttribute("hidden");
        if (error.code === "CAS_MISMATCH") reload.removeAttribute("hidden");
      } finally {
        saving = false;
        panel.removeAttribute("aria-busy");
        fields.removeAttribute("disabled");
      }
    }
  }

  return { open, reset, canDiscard, dirty, isSaving: () => saving };
}
