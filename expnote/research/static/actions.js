// The GUI write actions: approve/reject a proposed decision, close a plan, adopt a draft as a plan, close a deviation, save EV labels, review a result, add and tick TODOs.
// Every change is previewed first (the server's dry-run diff) and applied only from an explicit click.
// Text from the vault or the user only ever goes through text nodes here.
import { nav } from "/research/nav.js";
import { append, clear, copyButton, h, icon, skeleton } from "/research/ui.js";

const enc = encodeURIComponent;
const projectPath = (p) => `/projects/${enc(p)}`;

// Re-render the current page from fresh data (registered by research.js).
export const refresh = () => (nav.refresh ? nav.refresh() : Promise.resolve());

// ---- toast ------------------------------------------------------------------------------------

let toastHost = null;
const TOAST_MS = 12000;

function host() {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  return toastHost;
}

function dismiss(node) {
  try {
    host().removeChild(node);
  } catch {
    /* already gone */
  }
}

// A short confirmation. `journal` is the pre-image path the audit trail points at.
export function toast(message, { journal = null, tone = "ok", action = null } = {}) {
  const close = h("button", { type: "button", class: "btn small ghost", "aria-label": "Dismiss", onclick: () => dismiss(node) }, icon("close"));
  const node = h(
    "div",
    { class: "toast", "data-tone": tone, role: tone === "bad" ? "alert" : null },
    h("div", { class: "toast-msg" }, icon(tone === "bad" ? "alert" : "check"), h("span", null, message), close),
    journal ? h("div", { class: "toast-audit" }, h("span", { class: "muted small" }, "Audit trail "), h("code", null, journal), copyButton(journal, "Copy path")) : null,
    action ? h("button", { type: "button", class: "btn small", onclick: action.run }, action.label) : null,
  );
  host().append(node);
  setTimeout(() => dismiss(node), TOAST_MS);
  return node;
}

// ---- errors in plain words ---------------------------------------------------------------------

export function describeError(error) {
  const details = error.details || {};
  switch (error.code) {
    case "CAS_MISMATCH":
      return { message: "This note changed since you opened it. Reload to see the new version.", reload: true };
    case "CONFLICT":
      return { message: "This decision changed since you opened it. Reload to see the new version.", reload: true };
    case "FORBIDDEN_TOKEN":
      return { message: "The page is out of date. Reload it and try again.", reload: true };
    case "RECENTLY_MODIFIED":
      return { message: "The note was saved a moment ago and may still be open in an editor.", retryAfterMs: Number(details.retryAfterMs) || 2000, retry: true };
    case "FILE_BUSY":
      return { message: "The file is open in another program (sync client?). Try again in a moment.", retry: true };
    case "USER_ONLY":
    case "GATE_FAILED":
      return { message: error.message, remediation: error.remediation, gates: details.gates || [] };
    case "NETWORK":
      return { message: error.message, remediation: error.remediation, retry: true };
    default:
      return { message: error.message || "The change was not made.", remediation: error.remediation, gates: details.gates || [] };
  }
}

// An error block with the right way out: Retry (after a countdown when the server says so) or Reload.
export function errorPanel(error, { retry } = {}) {
  const info = describeError(error);
  const box = h("div", { class: "write-error", role: "alert" }, h("div", { class: "error-title" }, icon("alert"), h("strong", null, error.code || "Error")), h("p", null, info.message));
  if (info.remediation) box.append(h("p", { class: "remediation" }, "Try: ", h("code", null, info.remediation)));
  if (info.gates && info.gates.length) box.append(h("ul", { class: "gate-list" }, info.gates.map((g) => h("li", null, h("code", null, g.code || ""), " ", g.message || ""))));
  if (info.reload) {
    box.append(h("button", { type: "button", class: "btn", onclick: () => window.location.reload() }, icon("refresh"), "Reload"));
  } else if (info.retry && retry) {
    const button = h("button", { type: "button", class: "btn", onclick: retry }, icon("refresh"), "Retry");
    box.append(button);
    if (info.retryAfterMs) countdown(box, button, info.retryAfterMs);
  }
  return box;
}

// Retry stays disabled until `ms` have passed; a visible count says how long is left.
function countdown(box, button, ms) {
  const end = Date.now() + ms;
  const label = h("span", { class: "countdown muted small", role: "timer" });
  button.setAttribute("disabled", "");
  box.append(label);
  const tick = () => {
    const left = end - Date.now();
    if (left <= 0) {
      clear(label).append("You can retry now.");
      button.removeAttribute("disabled");
      return true;
    }
    clear(label).append(`Retry in ${Math.ceil(left / 1000)} s`);
    return false;
  };
  if (tick()) return;
  const timer = setInterval(() => {
    if (tick()) clearInterval(timer);
  }, 250);
}

// ---- the diff -----------------------------------------------------------------------------------

function diffBlock(change) {
  if (change.op === "rename") {
    return h("div", { class: "diff-file" }, h("p", { class: "small" }, "Moves ", h("code", null, String(change.address || "").replace(/^from:/, "")), " to ", h("code", { class: "diff-path" }, change.path)));
  }
  const lines = String(change.unifiedDiff || "").split("\n");
  const shown = lines.filter((line) => !line.startsWith("--- ") && !line.startsWith("+++ "));
  const pre = h(
    "pre",
    { class: "diff", tabindex: "0", "aria-label": `Changes to ${change.path}` },
    shown.map((line) => h("span", { class: line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "ctx" }, `${line}\n`)),
  );
  return h("div", { class: "diff-file" }, h("code", { class: "diff-path" }, change.path), pre, change.diffTruncated ? h("p", { class: "muted small" }, "The diff is cut short here; the whole change is applied.") : null);
}

// ---- confirm panel: dry-run diff, then Apply / Cancel ----------------------------------------------

// `send(dryRun)` posts the action. `success(data)` names the result for the toast. `details(data)` may add nodes under the diffs.
export function openConfirm(slot, { title, send, success, details = null, onApplied = refresh, onClose = () => {} }) {
  const body = h("div", { class: "confirm-body" });
  const applyButton = h("button", { type: "button", class: "btn primary", disabled: true }, "Apply");
  const cancelButton = h("button", { type: "button", class: "btn" }, "Cancel");
  const panel = h("section", { class: "confirm-panel", role: "group", "aria-label": title }, h("h3", null, title), body, h("div", { class: "confirm-buttons" }, applyButton, cancelButton));
  let busy = false;

  function close() {
    clear(slot);
    onClose();
  }
  const setBusy = (value) => {
    busy = value;
    for (const button of [applyButton, cancelButton]) (value ? button.setAttribute("disabled", "") : button.removeAttribute("disabled"));
  };

  async function preview() {
    setBusy(true);
    applyButton.setAttribute("disabled", "");
    clear(body).append(skeleton(2));
    try {
      const envelope = await send(true);
      const changes = envelope.data.changes || [];
      clear(body);
      if (!changes.length) {
        body.append(h("p", { class: "muted" }, "Nothing would change: the note already says this."));
        cancelButton.textContent = "Close";
      } else {
        append(body, changes.map(diffBlock));
        if (details) append(body, [details(envelope.data)]);
        applyButton.removeAttribute("disabled");
      }
      for (const w of envelope.warnings || []) body.append(h("p", { class: "muted small" }, `${w.code}: ${w.message}`));
    } catch (error) {
      clear(body).append(errorPanel(error, { retry: preview }));
    }
    cancelButton.removeAttribute("disabled");
    busy = false;
  }

  async function apply() {
    if (busy) return;
    setBusy(true);
    try {
      const envelope = await send(false);
      const first = (envelope.data.changes || [])[0];
      const journal = first ? first.journal || first.path : null;
      close();
      toast(success(envelope.data), { journal });
      await onApplied(envelope);
    } catch (error) {
      clear(body).append(errorPanel(error, { retry: apply }));
      cancelButton.removeAttribute("disabled");
      busy = false;
    }
  }

  applyButton.addEventListener("click", apply);
  cancelButton.addEventListener("click", close);
  // Escape closes the panel, not the preview pane or whatever the page binds it to.
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  });
  clear(slot).append(panel);
  preview().then(() => {
    if (!applyButton.hasAttribute("disabled") && applyButton.focus) applyButton.focus();
  });
  return { panel, close, preview };
}

// ---- decisions ------------------------------------------------------------------------------------

const controllers = new Map();

const VERBS = {
  approve: { label: "Approve", past: "approved" },
  reject: { label: "Reject", past: "rejected" },
};

// Approve / Reject for a proposed decision of the store. Returns null when there is nothing to do.
export function decisionActions(ctx, d) {
  if (d.status !== "proposed" || !d.version || d.source !== "db") return null;
  const P = ctx.project;
  const note = h("input", { type: "text", class: "note-input", placeholder: "Note (optional)", "aria-label": "Note, optional", maxlength: "300", autocomplete: "off" });
  const confirmSlot = h("div", { class: "confirm-slot" });
  const buttons = {};
  const open = (verb) => {
    const text = (note.value ?? note.getAttribute("value") ?? "").trim();
    const { label, past } = VERBS[verb];
    openConfirm(confirmSlot, {
      title: `${label} decision #${d.number}`,
      send: (dryRun) => ctx.post(`${projectPath(P)}/decisions/${enc(d.id)}/${verb}`, { version: d.version, note: text || undefined, dryRun }),
      success: () => `Decision #${d.number} ${past}`,
    });
  };
  buttons.approve = h("button", { type: "button", class: "btn primary", onclick: () => open("approve") }, icon("check"), "Approve");
  buttons.reject = h("button", { type: "button", class: "btn", onclick: () => open("reject") }, icon("cross"), "Reject");
  const node = h("div", { class: "decision-actions" }, h("div", { class: "act-bar" }, note, buttons.approve, buttons.reject), confirmSlot);
  controllers.set(d.id, { node, open });
  return node;
}

// Keyboard: `a` / `r` on the selected decision open the confirm panel; they never apply.
export function openDecisionAction(verb, id) {
  const controller = id ? controllers.get(id) : null;
  if (!controller || controller.node.isConnected === false) return false;
  controller.open(verb);
  return true;
}

// ---- close a plan -----------------------------------------------------------------------------------

const OPEN_PLAN_STATES = new Set(["draft", "approved", "running", "read-out", "confirmed"]);
const DEFAULT_CLOSE_REASON = "Closed in the GUI.";

// The "Close plan" button and its dialog. The dialog first asks the server what closing would do (a dry run):
// when every gate passes it shows the change; when a gate fails it lists them, asks for a reason and offers
// "Close without final RES", which the server records as a forced close (check keeps the warnings).
export function planCloseAction(ctx, plan) {
  if (!OPEN_PLAN_STATES.has(plan.status) || !plan.sha256) return null;
  const slot = h("div", { class: "confirm-slot" });
  const url = `${projectPath(ctx.project)}/plans/${enc(plan.id)}/close`;
  const button = h("button", { type: "button", class: "btn", onclick: () => openPlanClose(slot, ctx, plan, url) }, icon("check"), "Close plan");
  return h("div", { class: "plan-close" }, button, slot);
}

function openPlanClose(slot, ctx, plan, url) {
  const reason = h("textarea", { class: "reason-input", rows: "3", "aria-label": "Reason for closing", placeholder: "Why is this plan being closed? (kept as the decision's reason)", maxlength: "1000" });
  const body = h("div", { class: "confirm-body" });
  const applyButton = h("button", { type: "button", class: "btn primary", disabled: true }, "Close plan");
  const cancelButton = h("button", { type: "button", class: "btn" }, "Cancel");
  const panel = h("section", { class: "confirm-panel", role: "group", "aria-label": `Close ${plan.id}` }, h("h3", null, `Close ${plan.id}`), body, h("label", { class: "reason-label" }, "Reason", reason), h("div", { class: "confirm-buttons" }, applyButton, cancelButton));
  let forced = false;
  let busy = false;
  const typed = () => (reason.value ?? reason.getAttribute("value") ?? "").trim();
  const payload = (dryRun) => ({
    expectedSha256: plan.sha256,
    force: forced,
    reason: typed() || (forced ? (dryRun ? "(your reason)" : "") : DEFAULT_CLOSE_REASON),
    dryRun,
  });
  const syncApply = () => {
    if (busy) return;
    if (forced && !typed()) applyButton.setAttribute("disabled", "");
    else applyButton.removeAttribute("disabled");
  };
  function close() {
    clear(slot);
  }
  const setBusy = (value) => {
    busy = value;
    for (const b of [applyButton, cancelButton]) (value ? b.setAttribute("disabled", "") : b.removeAttribute("disabled"));
    if (!value) syncApply();
  };

  async function preview() {
    setBusy(true);
    clear(body).append(skeleton(2));
    try {
      const envelope = await ctx.post(url, payload(true));
      clear(body).append(h("p", { class: "ok-line" }, icon("check"), " Every gate for closing passes."));
      append(body, (envelope.data.changes || []).map(diffBlock));
      applyButton.textContent = "Close plan";
    } catch (error) {
      if (error.code !== "GATE_FAILED") {
        clear(body).append(errorPanel(error, { retry: preview }));
        setBusy(false);
        return;
      }
      forced = true;
      clear(body).append(
        h("div", { class: "notice warn" }, icon("alert"), h("div", null, h("strong", null, "Closing now skips these gates:"), h("ul", { class: "gate-list" }, (error.details.gates || []).map((g) => h("li", null, h("code", null, g.code || ""), " ", g.message || ""))))),
        h("p", { class: "muted small" }, "Write why you are closing it anyway. It is recorded as your decision and check keeps showing the missing pieces as warnings."),
      );
      applyButton.textContent = "Close without final RES";
      try {
        const envelope = await ctx.post(url, payload(true));
        append(body, (envelope.data.changes || []).map(diffBlock));
      } catch (second) {
        body.append(errorPanel(second, { retry: preview }));
      }
    }
    setBusy(false);
  }

  async function apply() {
    if (busy) return;
    setBusy(true);
    try {
      const envelope = await ctx.post(url, payload(false));
      close();
      toast(`${plan.id} closed${forced ? " (gates skipped)" : ""}`, { journal: ((envelope.data.changes || [])[0] || {}).path });
      await refresh();
    } catch (error) {
      body.append(errorPanel(error, { retry: apply }));
      setBusy(false);
    }
  }

  applyButton.addEventListener("click", apply);
  cancelButton.addEventListener("click", close);
  reason.addEventListener("input", syncApply);
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  });
  clear(slot).append(panel);
  return preview();
}

// ---- evidence labels ----------------------------------------------------------------------------------

const cellValue = (input) => (input.value ?? input.getAttribute("value") ?? "").trim();

// An editable label table for a rendered page: only the columns the profile says the user fills.
export function evLabelEditor(ctx, data) {
  const e = data.ev;
  const table = e.labelTable;
  if (e.status !== "rendered" || !table || !e.sha256 || !table.rows.length) return null;
  const P = ctx.project;
  const editable = new Set(table.valueColumns);
  const inputs = [];
  const rows = table.rows.map((row) => {
    const cells = [h("th", { scope: "row" }, row.key)];
    row.values.forEach((text, i) => {
      const column = i + 1;
      if (!editable.has(column)) {
        cells.push(h("td", null, text));
        return;
      }
      const input = h("input", { type: "text", class: "cell-input", value: text, "aria-label": `${table.header[column]} for ${row.key}`, autocomplete: "off", maxlength: "300" });
      inputs.push({ input, key: row.key, columnIndex: column, column: table.header[column], original: text.trim() });
      cells.push(h("td", null, input));
    });
    return h("tr", null, cells);
  });
  const remarkInput = data.write && data.write.remark ? h("input", { type: "text", class: "note-input", placeholder: "Remark (optional)", "aria-label": "Remark, optional", maxlength: "300", autocomplete: "off" }) : null;
  const confirmSlot = h("div", { class: "confirm-slot" });
  const hint = h("p", { class: "muted small", "aria-live": "polite" });
  const allFilled = () => inputs.filter((c) => c.columnIndex === table.valueColumns[0]).every((c) => cellValue(c.input));
  const commitButton = h("button", { type: "button", class: "btn", onclick: () => save(true) }, icon("check"), "Save and mark labelled");
  const saveButton = h("button", { type: "button", class: "btn primary", onclick: () => save(false) }, "Save labels");

  function syncCommit() {
    if (allFilled()) commitButton.removeAttribute("disabled");
    else commitButton.setAttribute("disabled", "");
  }
  for (const c of inputs) c.input.addEventListener("input", syncCommit);
  syncCommit();

  function payloadRows(remark) {
    const byRow = new Map();
    for (const c of inputs) {
      const now = cellValue(c.input);
      if (now === c.original) continue;
      if (!byRow.has(c.key)) byRow.set(c.key, {});
      byRow.get(c.key)[c.column] = now;
    }
    const changed = [...byRow.entries()].map(([key, values]) => ({ key, values }));
    if (changed.length || !remark) return changed;
    // A remark rides along with a row; re-send the first row as it is.
    const first = inputs[0];
    return first ? [{ key: first.key, values: Object.fromEntries(inputs.filter((c) => c.key === first.key).map((c) => [c.column, cellValue(c.input)])) }] : [];
  }

  function save(commit) {
    const remark = remarkInput ? (remarkInput.value ?? remarkInput.getAttribute("value") ?? "").trim() : "";
    const sendRows = payloadRows(remark);
    if (!sendRows.length && !commit) {
      clear(hint).append("No label changed.");
      return;
    }
    clear(hint);
    openConfirm(confirmSlot, {
      title: commit ? `Save labels and mark ${e.stem} labelled` : `Save labels of ${e.stem}`,
      send: (dryRun) => ctx.post(`${projectPath(P)}/ev/${enc(e.stem)}/labels`, { expectedSha256: e.sha256, rows: sendRows, remark: remark || undefined, commit, dryRun }),
      success: (result) => (result.committed ? "Labels saved, page marked labelled" : "Labels saved"),
    });
  }

  const buttons = [saveButton, commitButton];
  return h(
    "div",
    { class: "label-editor" },
    h("div", { class: "table-wrap" }, h("table", { class: "label-table" }, h("thead", null, h("tr", null, table.header.map((c) => h("th", { scope: "col" }, c)))), h("tbody", null, rows))),
    remarkInput,
    h("div", { class: "act-bar" }, buttons),
    hint,
    confirmSlot,
  );
}

// ---- result review -------------------------------------------------------------------------------------

const IMPACTS = ["none", "weak", "moderate", "affects"];

// The user's review of a first-read result: check the proposed deviation impact, then pass it or return it with remarks.
export function resReviewPanel(ctx, n) {
  const fm = n.frontmatter || {};
  if (n.type !== "result" || fm.status !== "first-read" || !n.sha256) return null;
  const P = ctx.project;
  const ident = fm.id || n.stem;
  const proposed = IMPACTS.includes(fm.deviation_impact) ? fm.deviation_impact : "";
  const select = h("select", { class: "impact-select", "aria-label": "Deviation impact" }, [proposed ? null : h("option", { value: "", selected: true }, "Choose..."), IMPACTS.map((v) => h("option", { value: v, selected: v === proposed ? true : null }, v))]);
  const remarks = h("textarea", { class: "note-input remarks-input", rows: "4", placeholder: "Remarks (required to return the result)", "aria-label": "Review remarks", maxlength: "2000" });
  const confirmSlot = h("div", { class: "confirm-slot" });
  const hint = h("p", { class: "muted small", "aria-live": "polite" });

  function review(verdict) {
    const impact = select.value ?? proposed;
    const text = (remarks.value ?? "").trim();
    if (verdict === "return" && !text) {
      clear(hint).append("Say what to revise before returning the result.");
      return;
    }
    if (verdict === "pass" && !impact) {
      clear(hint).append("Choose the deviation impact before passing the result.");
      return;
    }
    clear(hint);
    openConfirm(confirmSlot, {
      title: verdict === "pass" ? `Pass the review of ${ident}` : `Return ${ident} for revision`,
      send: (dryRun) => ctx.post(`${projectPath(P)}/res/${enc(ident)}/review`, { expectedSha256: n.sha256, verdict, deviationImpact: impact || undefined, remarks: text || undefined, dryRun }),
      success: () => (verdict === "pass" ? "Result confirmed" : "Result returned to the agent"),
    });
  }

  return h(
    "section",
    { class: "res-review", "aria-label": "Review this result" },
    h("h2", null, "Review this result"),
    h("p", { class: "muted small" }, "Read the result, check the deviation impact the agent proposed, then pass it or return it with remarks."),
    h("label", { class: "reason-label" }, "Deviation impact", select),
    h("label", { class: "reason-label" }, "Remarks", remarks),
    h("div", { class: "act-bar" }, h("button", { type: "button", class: "btn primary", onclick: () => review("pass") }, icon("check"), "Pass review"), h("button", { type: "button", class: "btn", onclick: () => review("return") }, "Return for revision")),
    hint,
    confirmSlot,
  );
}

// ---- manual TODOs ------------------------------------------------------------------------------------------

// A button that opens the add form. `manualFile` is the TODO payload's `{path, sha256}`.
export function todoAddForm(ctx, manualFile) {
  if (!manualFile || !manualFile.path) return null;
  const P = ctx.project;
  const text = h("input", { type: "text", name: "text", class: "todo-text", placeholder: "What needs doing?", "aria-label": "TODO text", maxlength: "300", autocomplete: "off", required: true });
  const plan = h("select", { name: "plan", "aria-label": "Plan, optional" }, h("option", { value: "" }, "No plan"));
  const owner = h("select", { name: "owner", "aria-label": "Owner" }, h("option", { value: "user" }, "Mine"), h("option", { value: "agent" }, "Agent"));
  const feedback = h("div", { class: "form-feedback" });
  const form = h("form", { class: "todo-add-form", hidden: true }, h("label", { class: "field" }, h("span", null, "TODO"), text), h("label", { class: "field" }, h("span", null, "Plan"), plan), h("label", { class: "field" }, h("span", null, "Owner"), owner));
  const submit = h("button", { type: "submit", class: "btn primary" }, "Add");
  const cancel = h("button", { type: "button", class: "btn" }, "Cancel");
  form.append(h("div", { class: "act-bar" }, submit, cancel), feedback);
  let loaded = false;

  async function loadPlans() {
    if (loaded) return;
    loaded = true;
    try {
      const body = await ctx.api(`${projectPath(P)}/plans`);
      for (const p of body.data.plans) plan.append(h("option", { value: p.id }, p.id));
    } catch {
      /* the plan stays optional text-free; adding works without it */
    }
  }
  const toggle = (show) => {
    if (show) {
      form.removeAttribute("hidden");
      loadPlans();
      if (text.focus) text.focus();
    } else {
      form.setAttribute("hidden", "");
      clear(feedback);
    }
  };
  const openButton = h("button", { type: "button", class: "btn small", onclick: () => toggle(true) }, icon("list"), "Add TODO");
  cancel.addEventListener("click", () => toggle(false));

  async function add(event) {
    event.preventDefault();
    const value = (text.value ?? "").trim();
    if (!value) {
      clear(feedback).append(h("p", { class: "muted small" }, "Write what needs doing first."));
      return;
    }
    submit.setAttribute("disabled", "");
    clear(feedback);
    try {
      const envelope = await ctx.post(`${projectPath(P)}/todos`, { text: value, plan: plan.value || undefined, owner: owner.value || "user", expectedSha256: manualFile.sha256 || null });
      const first = (envelope.data.changes || [])[0];
      toggle(false);
      text.value = "";
      toast(`TODO ${envelope.data.id || ""} added`.replace("  ", " "), { journal: first ? first.journal || first.path : null });
      await refresh();
    } catch (error) {
      clear(feedback).append(errorPanel(error, { retry: () => add(event) }));
    }
    submit.removeAttribute("disabled");
  }
  form.addEventListener("submit", add);
  return h("div", { class: "todo-add" }, openButton, form);
}

// Mark a manual TODO done (or open again). Throws the API error; the caller says what went wrong.
async function setTodoDone(ctx, t, manualSha, done) {
  const envelope = await ctx.post(`${projectPath(ctx.project)}/todos/${enc(t.id)}/done`, { done, expectedSha256: manualSha });
  const first = (envelope.data.changes || [])[0];
  toast(done ? "TODO ticked" : "TODO re-opened", { journal: first ? first.journal || first.path : null });
  await refresh();
}

function toastWriteError(error) {
  const info = describeError(error);
  toast(info.message, { tone: "bad", action: info.reload ? { label: "Reload", run: () => window.location.reload() } : null });
}

// A checkbox for a manual TODO line. A failed write puts the box back and says why.
export function todoTick(ctx, t, manualSha) {
  if (t.source !== "manual" || !manualSha) return null;
  const box = h("input", { type: "checkbox", class: "todo-tick", "aria-label": `Mark done: ${t.message}`, checked: t.state === "done" ? true : null });
  box.addEventListener("click", (event) => event.stopPropagation());
  box.addEventListener("change", async () => {
    const done = Boolean(box.checked);
    box.setAttribute("disabled", "");
    try {
      await setTodoDone(ctx, t, manualSha, done);
    } catch (error) {
      box.checked = !done;
      toastWriteError(error);
    }
    box.removeAttribute("disabled");
  });
  return box;
}

// The same action as a button for the detail pane: "Mark done", or "Open again" for a ticked item.
export function todoDoneButton(ctx, t, manualSha) {
  if (t.source !== "manual" || !manualSha) return null;
  const done = t.state !== "done";
  const button = h("button", { type: "button", class: done ? "btn primary" : "btn" }, icon("check"), done ? "Mark done" : "Open again");
  button.addEventListener("click", async () => {
    button.setAttribute("disabled", "");
    try {
      await setTodoDone(ctx, t, manualSha, done);
    } catch (error) {
      toastWriteError(error);
      button.removeAttribute("disabled");
    }
  });
  return button;
}

// ---- close a deviation ---------------------------------------------------------------------------------------

const oneLine = (text) => text.replace(/\s*\n\s*/g, " ").trim();

// "Close deviation": a textarea holding the current disposition, then the server's dry-run diff and Apply.
// `deviation` is the deviation payload of `GET .../deviations/{id}`; there is nothing to do once it is closed.
export function deviationCloseAction(ctx, deviation) {
  if (deviation.status === "closed" || !deviation.registerSha256) return null;
  const slot = h("div", { class: "confirm-slot" });
  const url = `${projectPath(ctx.project)}/deviations/${enc(deviation.id)}/close`;
  const open = () => {
    const disposition = h("textarea", { class: "reason-input", rows: "3", "aria-label": "Disposition", maxlength: "1000" }, deviation.disposition || "");
    disposition.value = deviation.disposition || "";
    const review = h("button", { type: "button", class: "btn primary" }, "Review change");
    const cancel = h("button", { type: "button", class: "btn" }, "Cancel");
    const confirmSlot = h("div", { class: "confirm-slot" });
    const typed = () => oneLine(disposition.value ?? disposition.textContent ?? "");
    const lock = (locked) => {
      for (const control of [disposition, review]) (locked ? control.setAttribute("disabled", "") : control.removeAttribute("disabled"));
    };
    review.addEventListener("click", () => {
      lock(true);
      openConfirm(confirmSlot, {
        title: `Close deviation ${deviation.id}`,
        send: (dryRun) => ctx.post(url, { expectedSha256: deviation.registerSha256, disposition: typed(), dryRun }),
        success: () => `Deviation ${deviation.id} closed`,
        onClose: () => {
          lock(false);
          if (disposition.focus) disposition.focus();
        },
      });
    });
    cancel.addEventListener("click", () => clear(slot));
    const panel = h("section", { class: "confirm-panel", role: "group", "aria-label": `Close deviation ${deviation.id}` }, h("h3", null, `Close deviation ${deviation.id}`), h("label", { class: "reason-label" }, "Disposition (how it was settled)", disposition), h("div", { class: "confirm-buttons" }, review, cancel), confirmSlot);
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        clear(slot);
      }
    });
    clear(slot).append(panel);
    if (disposition.focus) disposition.focus();
  };
  return h("div", { class: "deviation-close" }, h("button", { type: "button", class: "btn primary", onclick: open }, icon("check"), "Close deviation"), slot);
}

// ---- adopt a draft as a plan -------------------------------------------------------------------------------------

// What the server says an adoption would do besides the diffs: the move, the links that follow, the template sections the draft lacks.
function adoptSummary(data) {
  if (data.alreadyStandard && !data.moved) return h("p", { class: "muted" }, `${data.path} already is the plan ${data.id}.`);
  const missing = data.missingSections || [];
  return h(
    "div",
    { class: "adopt-summary" },
    data.linkEdits ? h("p", { class: "small" }, `${data.linkEdits} link${data.linkEdits === 1 ? "" : "s"} in ${data.linkFiles} note${data.linkFiles === 1 ? "" : "s"} will follow the new name.`) : null,
    h("p", { class: "small" }, "The text of the note is not changed."),
    missing.length ? h("div", { class: "block" }, h("h3", null, "Sections the plan template has and your draft lacks"), h("ul", { class: "gate-list" }, missing.map((name) => h("li", null, name)))) : h("p", { class: "ok-line" }, icon("check"), " The draft has every section of the plan template."),
  );
}

// "Adopt as plan" for a `plan-draft-unadopted` TODO: id and title, then the server's dry run, then Apply.
export function planAdoptAction(ctx, t) {
  if (t.kind !== "plan-draft-unadopted" || !t.path || !t.sha256) return null;
  const slot = h("div", { class: "confirm-slot" });
  const url = `${projectPath(ctx.project)}/plans/adopt`;
  const open = () => {
    const planId = h("input", { type: "text", name: "id", value: t.suggestedId || "", "aria-label": "Plan id", placeholder: "Plan id", maxlength: "80", autocomplete: "off" });
    const title = h("input", { type: "text", name: "title", value: t.suggestedTitle || "", "aria-label": "Plan title", maxlength: "200", autocomplete: "off" });
    const review = h("button", { type: "button", class: "btn primary" }, "Review change");
    const cancel = h("button", { type: "button", class: "btn" }, "Cancel");
    const confirmSlot = h("div", { class: "confirm-slot" });
    const typed = (input) => (input.value ?? input.getAttribute("value") ?? "").trim();
    const lock = (locked) => {
      for (const control of [planId, title, review]) (locked ? control.setAttribute("disabled", "") : control.removeAttribute("disabled"));
    };
    review.addEventListener("click", () => {
      lock(true);
      openConfirm(confirmSlot, {
        title: `Adopt ${t.stem} as a plan`,
        send: (dryRun) => ctx.post(url, { note: t.path, id: typed(planId), title: typed(title) || undefined, expectedSha256: t.sha256, dryRun }),
        success: (result) => `${result.id} adopted`,
        details: adoptSummary,
        onClose: () => {
          lock(false);
          if (planId.focus) planId.focus();
        },
      });
    });
    cancel.addEventListener("click", () => clear(slot));
    const panel = h(
      "section",
      { class: "confirm-panel", role: "group", "aria-label": `Adopt ${t.stem} as a plan` },
      h("h3", null, `Adopt ${t.stem} as a plan`),
      h("label", { class: "field" }, h("span", null, "Plan id"), planId),
      h("label", { class: "field" }, h("span", null, "Title"), title),
      h("div", { class: "confirm-buttons" }, review, cancel),
      confirmSlot,
    );
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        clear(slot);
      }
    });
    clear(slot).append(panel);
    if (planId.focus) planId.focus();
  };
  return h("div", { class: "plan-adopt" }, h("button", { type: "button", class: "btn primary", onclick: open }, icon("check"), "Adopt as plan"), slot);
}
