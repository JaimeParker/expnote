// Renders every GUI page against a running server and reports failures as JSON.
// usage: RESEARCH_STATIC=<dir> node --import ./register.mjs smoke.mjs <baseUrl> <project>
import { installDom, toHtml, walk } from "./dom_shim.mjs";

const [base, project] = process.argv.slice(2);
const dom = installDom();
const realFetch = globalThis.fetch;
globalThis.fetch = (url, options) => realFetch(new URL(url, base), options);
const problems = [];
process.on("unhandledRejection", (e) => problems.push(`unhandledRejection: ${e && e.stack}`));

const api = async (path) => (await (await fetch(`/api/research${path}`)).json());
const P = `/projects/${encodeURIComponent(project)}`;
const enc = encodeURIComponent;

const plans = (await api(`${P}/plans?include_archived=true`)).data.plans.map((p) => p.id);
const decisions = (await api(`${P}/decisions`)).data.decisions;
const evs = (await api(`${P}/ev`)).data.ev.map((e) => e.stem);
const routes = [
  ["home", "#/"],
  ["project", `#/p/${enc(project)}`],
  ["project-filtered", `#/p/${enc(project)}?status=closed`],
  ["decisions", `#/p/${enc(project)}/decisions`],
  ["decisions-layer", `#/p/${enc(project)}/decisions?layer=${enc(decisions[0]?.layer || "")}`],
  ["ev-list", `#/p/${enc(project)}/ev`],
  ["todos", `#/p/${enc(project)}/todos`],
  ["todos-user", `#/p/${enc(project)}/todos?owner=user`],
  ["todos-kind", `#/p/${enc(project)}/todos?kind=ev-unmapped`],
  ["decisions-sel", `#/p/${enc(project)}/decisions?sel=${enc(decisions[decisions.length - 1]?.id || "")}`],
  ["ev-figures", `#/p/${enc(project)}/ev?view=figures`],
  ["ev-sel", `#/p/${enc(project)}/ev?sel=${enc(evs[0] || "")}`],
  ["search", `#/p/${enc(project)}/search?q=${enc(process.env.SEARCH_QUERY || "plan")}`],
  ["search-empty", `#/p/${enc(project)}/search`],
  ["health", `#/p/${enc(project)}/health`],
  ...[...new Set(plans)].map((id) => [`plan ${id}`, `#/p/${enc(project)}/plan/${enc(id)}`]),
  ...decisions.slice(-6).map((d) => [`decision ${d.id}`, `#/p/${enc(project)}/decision/${enc(d.id)}`]),
  ...evs.map((s) => [`ev ${s}`, `#/p/${enc(project)}/ev/${enc(s)}`]),
  ["bad-route", "#/nonsense"],
];

await import("/research/research.js");
const main = dom.ids.main;
const settle = async () => {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 10));
    const busy = main.getAttribute("aria-busy") === "true";
    const loading = main.byClass("skeleton").length > 0 || main.byClass("skeleton-card").length > 0;
    if (!busy && !loading && i > 1) return;
  }
  problems.push("timeout waiting for page");
};
await settle();

const results = [];
for (const [name, hash] of routes) {
  dom.location.hash = hash;
  const start = performance.now();
  dom.fireHashChange();
  await settle();
  const errors = main.byClass("error-box").map((e) => e.textContent);
  let nodes = 0;
  walk(main, () => nodes++);
  if (process.env.DUMP && name === process.env.DUMP) process.stderr.write(toHtml(main));
  const previews = main.byClass("preview-enter").length;
  const h1 = main.byTag("h1")[0];
  results.push({ name, hash, ms: Math.round(performance.now() - start), nodes, previews, h1: h1 ? h1.textContent : null, errors, title: document.title });
}
// ---- write actions: panels render, dry-run diffs show as text, errors read in plain words ----------
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: Boolean(ok), detail: ok ? "" : String(detail).slice(0, 300) });
const html = await (await fetch("/research/")).text();
const token = /name="expnote-token" content="([^"]+)"/.exec(html);
check("page carries the write token", token);
dom.setToken(token ? token[1] : "");
const { nav } = await import("/research/nav.js");
const actions = await import("/research/actions.js");
const buttonLike = (root, text) => root.byTag("button").find((b) => b.textContent.includes(text));
const go = async (hash) => {
  dom.location.hash = hash;
  dom.fireHashChange();
  await settle();
};
const afterFetch = async () => {
  for (let i = 0; i < 200 && main.byClass("skeleton").length; i++) await new Promise((r) => setTimeout(r, 10));
  await new Promise((r) => setTimeout(r, 20));
};

const proposed = decisions.find((d) => d.status === "proposed");
check("a proposed decision exists", proposed);
if (proposed) {
  await go(`#/p/${enc(project)}/decision/${enc(proposed.id)}`);
  const approve = buttonLike(main, "Approve");
  const reject = buttonLike(main, "Reject");
  check("decision page offers Approve and Reject", approve && reject && main.byClass("note-input").length === 1);
  const evil = `<script>alert("x")</script> & <img src=x onerror=1>`;
  main.byClass("note-input")[0].value = evil;
  approve.dispatch("click");
  await afterFetch();
  const panel = main.byClass("confirm-panel")[0];
  check("confirm panel opens before anything is written", panel && panel.textContent.includes(`Approve decision #${proposed.number}`), panel && panel.textContent);
  const diff = main.byClass("diff")[0];
  const diffText = diff ? diff.textContent : "";
  check("dry-run diff shows the status change", diffText.includes("+status: confirmed") && diffText.includes("+confirmed_by: user"), diffText);
  check("dry-run also shows the export file change", main.byClass("diff-path").some((n) => n.textContent.endsWith("decisions.jsonl")));
  check("note text in the diff is a text node, not markup", diffText.includes("<script>") && diff.byClass("add").every((n) => n.raw === null) && main.byTag("script").length === 0 && toHtml(main).includes("&lt;script&gt;"), diffText);
  check("Apply and Cancel are offered", buttonLike(panel, "Apply") && buttonLike(panel, "Cancel"));
  buttonLike(panel, "Cancel").dispatch("click");
  check("Cancel closes the panel", main.byClass("confirm-panel").length === 0);
  reject.dispatch("click");
  await afterFetch();
  check("Reject opens its own confirm", main.byClass("confirm-panel")[0].textContent.includes("Reject decision"));

  await go(`#/p/${enc(project)}/decisions?sel=${enc(proposed.id)}`);
  await afterFetch();
  check("decision preview carries the actions", main.byClass("decision-actions").length === 1);
  const handled = nav.view.keys.a();
  await afterFetch();
  check("key a opens the confirm and does not apply", handled === true && main.byClass("confirm-panel").length === 1);
  const stored = (await api(`${P}/decisions/${enc(proposed.id)}`)).data.decision;
  await go(`#/p/${enc(project)}/decision/${enc(proposed.id)}`);
  await afterFetch();
  const sections = main.byClass("block").map((n) => n.getAttribute("data-section"));
  check("decision page renders the four sections and the history", ["decided", "reason", "options", "lands", "history"].every((k) => sections.includes(k)) && stored.version === 1, sections.join(","));
  check("history lists the proposal", main.byClass("history-list")[0] && main.byClass("history-list")[0].textContent.includes("propose"));
  const decided = decisions.find((d) => d.status === "confirmed");
  if (decided) {
    await go(`#/p/${enc(project)}/decisions?sel=${enc(decided.id)}`);
    await afterFetch();
    check("a confirmed decision has no actions", main.byClass("decision-actions").length === 0 && nav.view.keys.a() === false);
  }
}

const openPlan = (await api(`${P}/plans`)).data.plans.find((p) => p.status === "draft");
check("an open plan exists", openPlan);
if (openPlan) {
  await go(`#/p/${enc(project)}/plan/${enc(openPlan.id)}`);
  await afterFetch();
  const closeButton = buttonLike(main, "Close plan");
  check("plan page offers Close plan", closeButton);
  closeButton.dispatch("click");
  await afterFetch();
  const dialog = main.byClass("confirm-panel")[0];
  const forcedButton = dialog && buttonLike(dialog, "Close without final RES");
  check("unmet gates switch the button to Close without final RES", forcedButton && dialog.textContent.includes("LIFE.TRANSITION"), dialog && dialog.textContent);
  check("the forced button waits for a reason", forcedButton && forcedButton.hasAttribute("disabled"));
  const reason = dialog.byTag("textarea")[0];
  reason.value = "用户决定直接结项 <b>x</b>";
  reason.dispatch("input");
  check("a reason enables it", forcedButton && !forcedButton.hasAttribute("disabled"));
  const closeDiff = main.byClass("diff").map((n) => n.textContent).join("\n");
  check("the dry-run shows forced_close and the new decision", closeDiff.includes("+forced_close: true") && closeDiff.includes("+status: confirmed"), closeDiff);
  buttonLike(dialog, "Cancel").dispatch("click");
  check("Cancel closes the dialog", main.byClass("confirm-panel").length === 0);
}
const closedPlan = (await api(`${P}/plans`)).data.plans.find((p) => p.status === "closed");
if (closedPlan) {
  await go(`#/p/${enc(project)}/plan/${enc(closedPlan.id)}`);
  await afterFetch();
  check("a closed plan has no Close plan button", !buttonLike(main, "Close plan"));
}

const rendered = (await api(`${P}/ev`)).data.ev.find((e) => e.status === "rendered");
check("a rendered evidence page exists", rendered);
if (rendered) {
  await go(`#/p/${enc(project)}/ev/${enc(rendered.stem)}`);
  const inputs = main.byClass("cell-input");
  check("label table is editable", inputs.length >= 1 && main.byClass("label-editor").length === 1);
  const markLabelled = buttonLike(main, "Save and mark labelled");
  check("mark-labelled waits for every cell", markLabelled && markLabelled.hasAttribute("disabled"));
  inputs[0].value = "跌落 <b>严重</b>";
  inputs[0].dispatch("input");
  check("mark-labelled enables once filled", !markLabelled.hasAttribute("disabled"));
  buttonLike(main, "Save labels").dispatch("click");
  await afterFetch();
  const text = (main.byClass("diff")[0] || { textContent: "" }).textContent;
  check("label save previews the cell change", text.includes("跌落 <b>严重</b>") && main.byClass("diff")[0].byTag("b").length === 0, text);
}

await go(`#/p/${enc(project)}/todos`);
await afterFetch();
check("TODO page has the add form and a tick on manual items", buttonLike(main, "Add TODO") && main.byClass("todo-tick").length >= 1);
buttonLike(main, "Add TODO").dispatch("click");
check("add form opens", !main.byClass("todo-add-form")[0].hasAttribute("hidden"));
await go(`#/p/${enc(project)}`);
await afterFetch();
check("overview Waiting panel has Add TODO and ticks", main.byClass("waiting-panel")[0] && buttonLike(main.byClass("waiting-panel")[0], "Add TODO") && main.byClass("waiting-panel")[0].byClass("todo-tick").length >= 1);

const words = (code, extra = {}) => actions.errorPanel({ code, message: `m-${code}`, remediation: `r-${code}`, details: {}, ...extra }, { retry() {} }).textContent;
check("CAS_MISMATCH wording", words("CAS_MISMATCH").includes("This note changed since you opened it. Reload to see the new version."));
check("FILE_BUSY wording", words("FILE_BUSY").includes("The file is open in another program (sync client?). Try again in a moment."));
check("GATE_FAILED shows message, remediation and gates", ["m-GATE_FAILED", "r-GATE_FAILED", "EV.X"].every((t) => words("GATE_FAILED", { details: { gates: [{ code: "EV.X", message: "g" }] } }).includes(t)));
check("USER_ONLY shows message and remediation", words("USER_ONLY").includes("m-USER_ONLY") && words("USER_ONLY").includes("r-USER_ONLY"));
const countdown = actions.errorPanel({ code: "RECENTLY_MODIFIED", message: "x", details: { retryAfterMs: 600 } }, { retry() {} });
const retry = buttonLike(countdown, "Retry");
check("RECENTLY_MODIFIED disables Retry with a countdown", retry.hasAttribute("disabled") && countdown.textContent.includes("Retry in"));
await new Promise((r) => setTimeout(r, 900));
check("Retry enables itself after retryAfterMs", !retry.hasAttribute("disabled"));
const shown = actions.toast("Decision #9 approved", { journal: "/data/journal/x.orig" });
check("toast names the action and the journal path", shown.textContent.includes("Decision #9 approved") && shown.textContent.includes("/data/journal/x.orig"));

for (const c of checks.filter((c) => !c.ok)) problems.push(`check failed: ${c.name} ${c.detail}`);
const failures = results.filter((r) => (r.errors.length && r.name !== "bad-route") || (!r.h1 && r.name !== "bad-route"));
console.log(JSON.stringify({ pages: results.length, failures, problems, checks: checks.length, results }, null, 0));
process.exit(failures.length || problems.length ? 1 : 0);
