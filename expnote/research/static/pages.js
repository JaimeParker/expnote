// Page renderers. Each takes ctx {project, params, arg, state, api} and returns
// Promise<{node, title, keepScroll?}>. Content from the vault is shown as-is.
import {
  chip,
  commandBlock,
  emptyState,
  h,
  highlight,
  icon,
  imageUrl,
  kv,
  link,
  pageHead,
  plural,
  prose,
  qs,
  relTime,
  route,
  section,
  short,
  statusChip,
  table,
  toneOf,
} from "/research/ui.js";

const enc = encodeURIComponent;
const base = (p) => `/projects/${enc(p)}`;
const labels = (display) => (display && display.label_cn) || {};

function projectName(ctx) {
  const found = ctx.state.projects.find((p) => p.id === ctx.project);
  return found ? found.name : ctx.project;
}

function runRef(ctx, id) {
  return ctx.state.features.runApp
    ? h("a", { href: `/#/run/${enc(id)}`, class: "run-id" }, h("code", null, id))
    : h("code", { class: "run-id" }, id);
}

const ROLE_TONES = { approval: "info", rules: "violet", closing: "ok", result: "violet", "label-auth": "warn", other: "muted" };

function planLinks(ctx, ids) {
  return (ids || []).map((id, i) => [i ? ", " : "", link(route.plan(ctx.project, id), id)]);
}

// ---- decision rows (shared by plan, project and timeline pages) ----------------------------

function decisionRow(ctx, d) {
  const P = ctx.project;
  const notes = [];
  if (d.supersededBy) notes.push(h("span", { class: "supersession" }, "superseded by ", link(route.decision(P, d.supersededBy), d.supersededBy)));
  if (d.supersedes && d.supersedes.length) {
    notes.push(h("span", { class: "supersession" }, "supersedes ", d.supersedes.map((id, i) => [i ? ", " : "", link(route.decision(P, id), id)])));
  }
  return h(
    "li",
    { class: "decision", "data-status": d.status || "" },
    h("span", { class: "d-num" }, `#${d.number}`),
    h(
      "div",
      { class: "d-main" },
      h("a", { class: "d-title", href: route.decision(P, d.id) }, d.title || d.id),
      h(
        "div",
        { class: "d-meta" },
        h("span", { class: "muted" }, d.date),
        d.layer ? chip(d.layer, "muted") : null,
        d.role ? chip(d.role, ROLE_TONES[d.role] || "muted") : null,
        statusChip("decision", d.status),
        d.legacy ? chip("legacy", "muted") : null,
        d.plans && d.plans.length && !d.hidePlans ? h("span", { class: "plans" }, planLinks(ctx, d.plans)) : null,
        notes,
      ),
      h("div", { class: "row-actions", "data-slot": "decision-actions" }),
    ),
  );
}

// ---- HOME -----------------------------------------------------------------------------------

function flagList(flags) {
  if (!flags.length) return h("p", { class: "muted small ok-line" }, icon("check"), " No environment or hygiene warnings.");
  return h(
    "ul",
    { class: "flags" },
    flags.map((f) =>
      h(
        "li",
        { "data-tone": f.status === "fail" ? "bad" : "warn" },
        h("div", null, statusChip("doctor", f.status), " ", h("strong", null, f.id), " ", f.message),
        f.remediation ? h("div", { class: "muted small" }, "Try: ", h("code", null, f.remediation)) : null,
      ),
    ),
  );
}

function indexChip(index) {
  if (!index || !index.built) return chip("index not built", "warn");
  const age = index.ageSeconds;
  const text = age === null || age === undefined ? "index built" : `index ${relTime(index.builtAt)}`;
  return chip(text, "muted", { title: `${index.files} files, ${index.notes} notes, ${index.linksUnresolved} unresolved links` });
}

function homeCard(ctx, project, home) {
  const P = project.id;
  const plans = home.plans;
  const stateChips = Object.entries(plans.byStatus).map(([status, n]) =>
    h("a", { class: "state-chip", href: route.project(P, { status }) }, statusChip("plan", status), h("b", null, n)),
  );
  const planList = plans.groups
    .flatMap((g) => g.plans)
    .slice(0, 8)
    .map((p) =>
      h(
        "li",
        null,
        h("a", { href: route.plan(P, p.id), class: "plan-id" }, p.id),
        h("span", { class: "plan-title" }, p.title),
        statusChip("plan", p.status),
        p.openItems ? chip(`${p.openItems} open`, "warn") : null,
      ),
    );
  const awaiting = home.decisions.awaiting;
  const todos = home.todos;
  const runs = home.runs.recent;
  const findings = home.findings;
  return h(
    "article",
    { class: "card project-card" },
    h(
      "header",
      { class: "project-card-head" },
      h("h2", null, h("a", { href: route.project(P) }, project.name)),
      h(
        "div",
        { class: "chips" },
        findings.fail ? chip(`${findings.fail} FAIL`, "bad") : chip("check clean", "ok"),
        findings.warn ? chip(`${findings.warn} WARN`, "warn") : null,
        indexChip(home.index),
      ),
    ),
    h("h3", null, "Plans ", chip(plans.total, "muted", { class: "chip count" })),
    stateChips.length ? h("div", { class: "state-chips" }, stateChips) : null,
    planList.length ? h("ul", { class: "plan-list" }, planList) : emptyState("No plans yet."),
    plans.total > 8 ? h("p", { class: "small" }, link(route.project(P), `All ${plans.total} plans`, icon("chevron"))) : null,
    h("h3", null, "Waiting on you ", chip(awaiting.length, awaiting.length ? "warn" : "muted", { class: "chip count" })),
    awaiting.length
      ? h("ul", { class: "decisions compact" }, awaiting.slice(0, 5).map((d) => decisionRow(ctx, { ...d, status: "proposed", hidePlans: false })))
      : h("p", { class: "muted small ok-line" }, icon("check"), " No decision waits for you."),
    h("h3", null, "TODOs ", chip(todos.total, "muted", { class: "chip count" }), h("span", { class: "muted small" }, ` ${todos.user} yours · ${todos.agent} agent`)),
    todos.items.length
      ? h("ul", { class: "todo-compact" }, todos.items.slice(0, 6).map((t) => todoRow(ctx, t, true)))
      : emptyState("Nothing to do."),
    todos.total > 6 ? h("p", { class: "small" }, link(route.todos(P), "All TODOs", icon("chevron"))) : null,
    h("h3", null, "Recent runs"),
    runs.length
      ? table(
          ["Run", "Topic", "Status", "Result"],
          runs.slice(0, 5).map((r) => [runRef(ctx, r.id), r.topic, statusChip("run", r.status), h("span", { class: "clip" }, r.result)]),
        )
      : h("p", { class: "muted small" }, home.runs.workspaces.length ? "No runs in the linked workspaces." : "No workspace is linked to this project."),
    h("h3", null, "Health"),
    flagList(home.doctor.flags),
  );
}

async function homePage(ctx) {
  const { state, api } = ctx;
  const grid = h("div", { class: "project-grid" });
  const node = h("div", { class: "page" }, pageHead({ title: "Research", meta: [plural(state.projects.length, "project")] }), grid);
  if (!state.projects.length) {
    grid.append(
      emptyState("No research project is registered.", "Register one, then reload this page:"),
      commandBlock("expnote project register --vault-root <vault> --project-dir <dir> --images-dir <dir> --profile-note <file>"),
    );
  }
  for (const project of state.projects) {
    const slot = h("div", { class: "project-slot" }, h("div", { class: "card skeleton-card" }, h("h2", null, project.name), h("div", { class: "skeleton", role: "status" }, h("div", { class: "skeleton-line", "data-w": "1" }), h("div", { class: "skeleton-line", "data-w": "2" }), h("div", { class: "skeleton-line", "data-w": "0" }))));
    grid.append(slot);
    api(`${base(project.id)}/home`)
      .then((body) => slot.replaceChildren(homeCard(ctx, project, body.data)))
      .catch((error) => {
        slot.replaceChildren(
          h("article", { class: "card" }, h("h2", null, project.name), h("div", { class: "error-box", role: "alert" }, h("strong", null, error.code), h("p", null, error.message), error.remediation ? h("p", { class: "remediation" }, "Try: ", h("code", null, error.remediation)) : null)),
        );
      });
  }
  return { node, title: "Research" };
}

// ---- TODO rows ---------------------------------------------------------------------------------

function todoTarget(ctx, t) {
  const P = ctx.project;
  switch (t.kind) {
    case "decision-proposed":
    case "decision-review-due":
      return route.decision(P, t.ref);
    case "ev-unlabelled":
    case "ev-unmapped":
      return route.ev(P, t.ref);
    case "plan-awaiting-approval":
    case "plan-gate-blocked":
    case "legacy-no-final-res":
      return route.plan(P, t.ref);
    default:
      return t.plan ? route.plan(P, t.plan) : null;
  }
}

function todoRow(ctx, t, compact = false) {
  const target = todoTarget(ctx, t);
  return h(
    "li",
    { class: "todo", "data-owner": t.owner, "data-severity": t.severity },
    h("span", { class: "who", title: t.owner === "user" ? "Waiting on you" : "Agent work" }, icon(t.owner === "user" ? "user" : "bot")),
    h(
      "div",
      { class: "todo-main" },
      target ? h("a", { href: target, class: "todo-msg" }, t.message) : h("span", { class: "todo-msg" }, t.message),
      h(
        "div",
        { class: "d-meta" },
        chip(t.kind, "muted"),
        t.source === "manual" ? chip("manual", "violet") : null,
        t.severity && t.source !== "manual" ? chip(t.severity, toneOf("severity", t.severity)) : null,
        t.state === "done" ? chip("done", "ok") : null,
        t.due ? chip(`due ${t.due}`, "warn") : null,
        t.plan && !compact ? link(route.plan(ctx.project, t.plan), t.plan) : null,
      ),
      !compact && t.action ? h("p", { class: "muted small" }, t.action) : null,
      !compact && t.command ? commandBlock(t.command) : null,
    ),
  );
}

// ---- PROJECT -------------------------------------------------------------------------------------

async function projectPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const wanted = params.get("status");
  const [home, plansBody, decisionsBody] = await Promise.all([
    api(`${base(P)}/home`),
    api(`${base(P)}/plans`, { status: wanted }),
    api(`${base(P)}/decisions`),
  ]);
  const h0 = home.data;
  const plans = plansBody.data.plans;
  const recent = decisionsBody.data.decisions.slice(-8).reverse();
  const planRows = plans.map((p) => [
    h("div", null, link(route.plan(P, p.id), h("strong", null, p.id)), h("div", { class: "muted small clip" }, p.title)),
    statusChip("plan", p.status),
    p.resStatus ? statusChip("res", p.resStatus) : h("span", { class: "muted" }, "none"),
    String(p.evCount),
    String(p.decisionCount),
    p.openItems ? chip(p.openItems, "warn") : h("span", { class: "muted" }, "0"),
    p.legacy ? chip("legacy", "muted") : null,
  ]);
  const filterNote = wanted
    ? h("p", { class: "small" }, "Showing ", statusChip("plan", wanted), " plans. ", link(route.project(P), "Show all"))
    : null;
  const deviations = h0.deviations.open;
  const node = h(
    "div",
    { class: "page" },
    pageHead({
      crumbs: [{ href: route.home(), label: "Research" }],
      title: projectName(ctx),
      chips: [
        h0.findings.fail ? chip(`${h0.findings.fail} FAIL`, "bad") : chip("check clean", "ok"),
        h0.findings.warn ? chip(`${h0.findings.warn} WARN`, "warn") : null,
        indexChip(h0.index),
      ],
      meta: [h("span", null, `${h0.plans.total} plans · ${h0.decisions.total} decisions · ${h0.todos.total} TODOs`)],
    }),
    h("div", { class: "grid-2" },
      h("div", { class: "stack" },
        section("Plans", [filterNote, table(["Plan", "Status", "Result", "EV", "Decisions", "Open", ""], planRows, { empty: "No plans match.", className: "plans-table" })], { count: plans.length, id: "plans" }),
        section("Latest decisions", [
          h("ul", { class: "decisions" }, recent.map((d) => decisionRow(ctx, d))),
          h("p", { class: "small" }, link(route.decisions(P), "Open the decision timeline", icon("chevron"))),
        ], { count: h0.decisions.total }),
      ),
      h("div", { class: "stack" },
        section("Waiting on you", h0.decisions.awaiting.length
          ? h("ul", { class: "decisions" }, h0.decisions.awaiting.map((d) => decisionRow(ctx, { ...d, status: "proposed" })))
          : h("p", { class: "muted ok-line" }, icon("check"), " No decision waits for you."), { count: h0.decisions.awaiting.length, tone: h0.decisions.awaiting.length ? "warn" : "muted" }),
        section("TODOs", [
          h("ul", { class: "todo-compact" }, h0.todos.items.map((t) => todoRow(ctx, t, true))),
          h("p", { class: "small" }, link(route.todos(P), `All ${h0.todos.total} TODOs`, icon("chevron"))),
        ], { count: h0.todos.total }),
        section("Open deviations", deviations.length
          ? table(["ID", "Date", "Found", "Impact", "Summary"], deviations.map((d) => [d.id, d.date, d.found || "", d.impact || "", h("span", { class: "clip" }, d.summary)]))
          : h("p", { class: "muted ok-line" }, icon("check"), " No open deviation."), { count: deviations.length, tone: deviations.length ? "warn" : "muted" }),
        section("Environment", flagList(h0.doctor.flags), { count: h0.doctor.flags.length, tone: h0.doctor.flags.length ? "warn" : "muted", open: h0.doctor.flags.length > 0 }),
      ),
    ),
  );
  return { node, title: projectName(ctx) };
}

// ---- PLAN ------------------------------------------------------------------------------------------

const STEPS = ["draft", "approved", "running", "read-out", "confirmed", "closed"];

function stepper(gates, status) {
  if (!gates.known || !STEPS.includes(status)) return null;
  const at = STEPS.indexOf(status);
  return h(
    "ol",
    { class: "stepper", "aria-label": "Plan lifecycle" },
    STEPS.map((step, i) => h("li", { "data-state": i < at ? "done" : i === at ? "current" : "todo", "aria-current": i === at ? "step" : null }, h("span", { class: "dot" }), h("span", null, step))),
  );
}

function gatesCard(ctx, gates) {
  const forward = gates.next.filter((n) => !["superseded", "withdrawn"].includes(n.to));
  const side = gates.next.filter((n) => ["superseded", "withdrawn"].includes(n.to));
  const row = (n) =>
    h(
      "div",
      { class: "gate", "data-tone": n.ok ? "ok" : "bad" },
      h("div", { class: "gate-head" }, icon(n.ok ? "check" : "gate"), h("strong", null, `→ ${n.to}`), chip(n.ok ? "ready" : `blocked by ${n.unmet.length}`, n.ok ? "ok" : "bad")),
      n.unmet.length ? h("ul", null, n.unmet.map((u) => h("li", null, h("code", null, u.code), " ", u.message))) : null,
    );
  return [
    gates.currentUnmet.length
      ? h("div", { class: "notice bad" }, icon("alert"), h("div", null, h("strong", null, "This plan is past a gate it does not meet:"), h("ul", null, gates.currentUnmet.map((u) => h("li", null, h("code", null, u.code), " ", u.message)))))
      : null,
    forward.length ? forward.map(row) : h("p", { class: "muted small" }, "No further forward step."),
    side.length ? h("details", { class: "subtle" }, h("summary", null, "Other transitions"), side.map(row)) : null,
  ];
}

function execTable(ctx, rows) {
  return table(
    ["Exec-plan", "Status", "Frozen", "Rules hash", "Rules decision"],
    rows.map((e) => [
      h("div", null, h("strong", null, e.id || e.stem), e.stage ? h("div", { class: "muted small" }, e.stage) : null, link(route.note(ctx.project, e.stem), icon("doc"))),
      statusChip("exec", e.status),
      e.frozen ? chip("frozen", "info") : chip("not frozen", "muted"),
      e.rulesShaMatches === true ? chip("matches", "ok") : e.rulesShaMatches === false ? chip("changed", "bad") : chip("n/a", "muted"),
      e.rulesDecision ? link(route.decision(ctx.project, e.rulesDecision), e.rulesDecision) : "",
    ]),
    { empty: "No exec-plan." },
  );
}

function evTable(ctx, rows, display) {
  const names = labels(display);
  return table(
    ["Evidence", "Status", "Labels", "Appendix", "Figures"],
    rows.map((e) => [
      h("div", null, link(route.ev(ctx.project, e.stem), e.stem), e.legacy ? chip("legacy", "muted") : null),
      statusChip("ev", e.status),
      statusChip("label", e.labelState, names),
      e.appendix ? link(route.note(ctx.project, e.appendix.stem), icon("doc"), " appendix") : h("span", { class: "muted" }, "none"),
      e.figures.length ? chip(e.figures.length, "muted") : "",
    ]),
    { empty: "No evidence page." },
  );
}

function relatedList(ctx, rows) {
  if (!rows.length) return emptyState("No related note.");
  const groups = new Map();
  for (const r of rows) {
    const key = r.type || "note";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return [...groups.entries()].map(([type, list]) =>
    h("div", { class: "related-group" }, h("h4", null, chip(type, "muted"), " ", plural(list.length, "note")),
      h("ul", { class: "related" }, list.map((r) => h("li", null, link(route.note(ctx.project, r.stem), r.title || r.stem), r.status ? chip(r.status, "muted") : null, h("div", { class: "muted small" }, r.oneLine))))),
  );
}

async function planPage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const plan = ctx.arg;
  const [shown, bundle] = await Promise.all([api(`${base(P)}/plans/${enc(plan)}`), api(`${base(P)}/plans/${enc(plan)}/bundle`)]);
  const s = shown.data;
  const b = bundle.data;
  const display = s.display;
  const info = s.plan;
  const findings = b.findings;
  const asideCards = [
    section("Next step", gatesCard(ctx, s.gates), { id: "gates" }),
    section("Decisions", b.decisions.length ? h("ul", { class: "decisions compact" }, b.decisions.slice().reverse().map((d) => decisionRow(ctx, { ...d, hidePlans: true }))) : emptyState("No decision yet."), { count: b.decisions.length, id: "decisions" }),
    section("Exec-plans", execTable(ctx, b.execPlans), { count: b.execPlans.length, id: "exec" }),
    section("Evidence", evTable(ctx, b.evidence, display), { count: b.evidence.length, id: "evidence" }),
    section("Results", table(["Result", "Status", "Decision", "Deviation impact"], b.results.map((r) => [h("div", null, h("strong", null, r.id || r.stem), link(route.note(P, r.stem), icon("doc"))), statusChip("res", r.status), r.confirmedDecision ? link(route.decision(P, r.confirmedDecision), r.confirmedDecision) : h("span", { class: "muted" }, "none"), r.deviationImpact || ""]), { empty: "No result card." }), { count: b.results.length, id: "results" }),
    section("Deviations", table(["ID", "Date", "Status", "Found", "Summary"], b.deviations.map((d) => [d.id, d.date, statusChip("deviation", d.status), d.found || "", h("span", { class: "clip" }, d.summary)]), { empty: "No deviation recorded." }), { count: b.deviations.length, id: "deviations", open: b.deviations.length > 0 }),
    section("Open items", b.openItems.length ? h("ul", { class: "todo-compact" }, b.openItems.map((t) => todoRow(ctx, { ...t, plan: info.id, source: t.kind === "manual" ? "manual" : "derived" }))) : h("p", { class: "muted ok-line" }, icon("check"), " Nothing open."), { count: b.openItems.length, id: "open", tone: b.openItems.length ? "warn" : "muted" }),
    section("Related notes", relatedList(ctx, b.related), { count: b.related.length, id: "related", open: false }),
    section("Analysis docs", b.analysisDocs.length ? h("ul", { class: "related" }, b.analysisDocs.map((d) => h("li", null, h("strong", null, d.title), chip(d.workspace, "muted"), h("div", { class: "muted small" }, d.oneLine), h("div", { class: "small" }, d.linkedRuns.map((r) => [runRef(ctx, r), " "]))))) : emptyState("No linked analysis doc."), { count: b.analysisDocs.length, id: "docs", open: false }),
    section("Linked runs", table(["Run", "Topic", "Status", "Result", "Cited by"], b.runs.map((r) => [runRef(ctx, r.id), r.topic, statusChip("run", r.status), h("span", { class: "clip" }, r.result), String(r.citedBy.length)]), { empty: "No run is cited by this plan." }), { count: b.runs.length, id: "runs", open: false }),
    section("Checks", findings.items.length ? h("ul", { class: "findings" }, findings.items.map((f) => h("li", null, statusChip("level", f.level), " ", h("code", null, f.code), " ", f.message))) : h("p", { class: "muted ok-line" }, icon("check"), " No FAIL or WARN for this plan."), { count: `${findings.fail} FAIL · ${findings.warn} WARN`, id: "checks", tone: findings.fail ? "bad" : findings.warn ? "warn" : "ok", open: findings.items.length > 0 }),
  ];
  const warnings = (bundle.warnings || []).map((w) => h("div", { class: "notice warn" }, icon("alert"), h("div", null, h("code", null, w.code), " ", w.message)));
  const node = h(
    "div",
    { class: "page" },
    pageHead({
      crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }],
      title: info.title && info.title.startsWith(info.id) ? info.title : `${info.id}${info.title && info.title !== info.id ? " · " + info.title : ""}`,
      chips: [statusChip("plan", info.status), info.legacy ? chip("legacy", "muted") : null],
      meta: [
        info.owner ? h("span", null, "owner ", h("b", null, info.owner)) : null,
        info.approvedDecision ? h("span", null, "approved by ", link(route.decision(P, info.approvedDecision), info.approvedDecision)) : null,
        info.closedDecision ? h("span", null, "closed by ", link(route.decision(P, info.closedDecision), info.closedDecision)) : null,
        h("span", { class: "muted", title: info.path }, `sha ${short(info.sha256)}`),
      ],
    }),
    stepper(s.gates, info.status),
    warnings,
    h("div", { class: "plan-layout" },
      h("article", { class: "plan-body card" }, h("div", { class: "card-body" }, prose(s.html, "plan-prose"))),
      h("aside", { class: "plan-aside", "aria-label": "Plan status" }, asideCards),
    ),
  );
  return { node, title: info.id };
}

// ---- DECISIONS ---------------------------------------------------------------------------------------

function filterSelect(name, label, options, current, onchange) {
  return h("label", { class: "field" }, h("span", null, label),
    h("select", { name, onchange }, h("option", { value: "" }, "any"), options.map((o) => h("option", { value: o, selected: o === current ? true : null }, o))));
}

async function decisionsPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const current = { plan: params.get("plan") || "", layer: params.get("layer") || "", status: params.get("status") || "" };
  const body = await api(`${base(P)}/decisions`, current);
  const rows = body.data.decisions.slice().reverse();
  const statuses = [...new Set(rows.map((d) => d.status).filter(Boolean))].sort();
  const go = (key) => (e) => {
    window.location.hash = route.decisions(P, { ...current, [key]: e.target.value });
  };
  const list = h("div", { class: "timeline" });
  const count = h("span", { class: "muted small" });
  const render = (needle) => {
    const text = needle.trim().toLowerCase();
    const shown = rows.filter((d) => !text || `${d.id} ${d.title} ${d.date}`.toLowerCase().includes(text));
    count.textContent = `${shown.length} of ${rows.length}`;
    list.replaceChildren();
    if (!shown.length) {
      list.append(emptyState("No decision matches these filters."));
      return;
    }
    let month = "";
    let ul = null;
    for (const d of shown) {
      const m = (d.date || "").slice(0, 7) || "undated";
      if (m !== month) {
        month = m;
        list.append(h("h3", { class: "month" }, m));
        ul = h("ul", { class: "decisions" });
        list.append(ul);
      }
      ul.append(decisionRow(ctx, d));
    }
  };
  const search = h("input", { type: "search", placeholder: "Filter by title or id", "aria-label": "Filter decisions", oninput: (e) => render(e.target.value) });
  render("");
  const node = h("div", { class: "page" },
    pageHead({ crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }], title: "Decisions", meta: [h("span", null, "Newest first. The layer filter replaces the old research view.")] }),
    h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
      filterSelect("plan", "Plan", body.data.plans, current.plan, go("plan")),
      filterSelect("layer", "Layer", body.data.layers, current.layer, go("layer")),
      filterSelect("status", "Status", statuses.length ? statuses : ["proposed", "confirmed", "superseded", "rejected"], current.status, go("status")),
      h("label", { class: "field grow" }, h("span", null, "Find"), search),
      count,
    ),
    list,
  );
  return { node, title: "Decisions", keepScroll: true };
}

async function decisionPage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/decisions/${enc(ctx.arg)}`);
  const d = body.data.decision;
  const refs = body.data.refsIn;
  const byType = new Map();
  for (const r of refs) {
    const key = r.type || "note";
    if (!byType.has(key)) byType.set(key, new Map());
    byType.get(key).set(r.stem, r);
  }
  const fm = d.frontmatter || {};
  const node = h("div", { class: "page narrow" },
    pageHead({
      crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }, { href: route.decisions(P), label: "Decisions" }],
      title: `#${d.number} · ${d.title || d.id}`,
      chips: [statusChip("decision", d.status), d.layer ? chip(d.layer, "muted") : null, d.legacy ? chip("legacy", "muted") : null],
      meta: [h("span", null, d.id), h("span", null, d.date), d.confirmedBy ? h("span", null, "confirmed by ", h("b", null, d.confirmedBy)) : null, d.sha256 ? h("span", { class: "muted" }, `sha ${short(d.sha256)}`) : null],
    }),
    h("div", { class: "card" }, h("div", { class: "card-body" },
      kv([
        ["Plans", d.plans.length ? d.plans.map((id, i) => [i ? ", " : "", link(route.plan(P, id), id)]) : null],
        ["Supersedes", d.supersedes.length ? d.supersedes.map((id, i) => [i ? ", " : "", link(route.decision(P, id), id)]) : null],
        ["Superseded by", d.supersededBy ? link(route.decision(P, d.supersededBy), d.supersededBy) : null],
        ["Raw status", d.statusRaw && d.statusRaw !== d.status ? d.statusRaw : null],
        ["Source", d.path],
        ["Review by", fm.review_by || null],
      ]),
      h("div", { class: "row-actions", "data-slot": "decision-actions" }),
    )),
    d.html ? h("div", { class: "card" }, h("div", { class: "card-body" }, prose(d.html))) : null,
    d.decisionTextHtml ? section("Decision", prose(d.decisionTextHtml)) : null,
    d.landsTextHtml ? section("Lands in", prose(d.landsTextHtml)) : null,
    section("Cited by", refs.length
      ? [...byType.entries()].map(([type, map]) => h("div", { class: "related-group" }, h("h4", null, chip(type, "muted")),
          h("ul", { class: "related" }, [...map.values()].map((r) => h("li", null, r.href ? h("a", { href: r.href }, r.stem) : r.stem, chip(r.form, "muted"))))))
      : emptyState("No note cites this decision."), { count: refs.length, open: refs.length > 0 && refs.length < 25 }),
  );
  return { node, title: `#${d.number} ${d.title || d.id}` };
}

// ---- EVIDENCE ---------------------------------------------------------------------------------------------

async function evListPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const labelState = params.get("label_state") || "";
  const body = await api(`${base(P)}/ev`, { label_state: labelState });
  const names = labels(body.data.display);
  const rows = body.data.ev.map((e) => [
    link(route.ev(P, e.stem), e.stem),
    e.plan ? link(route.plan(P, e.plan), e.plan) : h("span", { class: "muted" }, "none"),
    statusChip("ev", e.status),
    statusChip("label", e.labelState, names),
    String(e.figures.length),
  ]);
  const states = ["filled", "partial", "empty", "missing", "na"];
  const node = h("div", { class: "page" },
    pageHead({ crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }], title: "Evidence" }),
    h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
      h("label", { class: "field" }, h("span", null, "Label state"),
        h("select", { onchange: (e) => (window.location.hash = route.evList(P, { label_state: e.target.value })) },
          h("option", { value: "" }, "any"), states.map((s) => h("option", { value: s, selected: s === labelState ? true : null }, names[s] || s))))),
    table(["Evidence", "Plan", "Status", "Labels", "Figures"], rows, { empty: "No evidence page matches." }),
  );
  return { node, title: "Evidence" };
}

function dataTable(t, { filledColumn } = {}) {
  if (!t) return null;
  return h("div", { class: "table-wrap" }, h("table", null,
    h("thead", null, h("tr", null, t.header.map((c) => h("th", { scope: "col" }, c)))),
    h("tbody", null, t.rows.map((row) => h("tr", { "data-filled": filledColumn !== undefined ? String(Boolean((row[filledColumn] || "").trim())) : null }, row.map((cell) => h("td", null, cell)))))));
}

async function evPage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/ev/${enc(ctx.arg)}`);
  const e = body.data.ev;
  const names = labels(body.data.display);
  const label = e.labelTable;
  const figures = e.figures.map((name) => {
    const img = h("img", { src: imageUrl(P, name), alt: name, loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(chip("image missing", "warn")));
    return h("figure", null, h("a", { href: imageUrl(P, name), target: "_blank", rel: "noopener" }, img), h("figcaption", null, name));
  });
  const node = h("div", { class: "page" },
    pageHead({
      crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }, { href: route.evList(P), label: "Evidence" }],
      title: e.stem,
      chips: [statusChip("ev", e.status), statusChip("label", e.labelState, names), e.legacy ? chip("legacy", "muted") : null],
      meta: [
        e.plan ? h("span", null, "plan ", link(route.plan(P, e.plan), e.plan)) : null,
        e.execPlan ? h("span", null, "exec ", link(route.note(P, e.execPlan), e.execPlan)) : null,
        e.appendix ? h("span", null, link(route.note(P, e.appendix.stem), icon("doc"), " appendix")) : null,
        e.labelledBy ? h("span", null, "labelled by ", h("b", null, e.labelledBy)) : null,
        h("span", { class: "muted" }, `sha ${short(e.sha256)}`),
      ],
    }),
    section("Figures", figures.length ? h("div", { class: "figures" }, figures) : emptyState("No figure listed."), { count: figures.length, id: "figures" }),
    section("Endpoints", e.endpointTable ? dataTable(e.endpointTable) : emptyState("No endpoint table on this page."), { id: "endpoints" }),
    section("Labels", [
      label ? dataTable({ header: label.header, rows: label.rows.map((r) => [r.key, ...r.values]) }, { filledColumn: (label.valueColumns[0] || 1) }) : emptyState(e.labelState === "missing" ? "This page has no label section." : "The label table is empty."),
      e.remark ? h("div", { class: "remark" }, h("h4", null, "Remark"), h("pre", null, e.remark)) : null,
      h("div", { class: "row-actions", "data-slot": "ev-label-actions" }),
      h("p", { class: "muted small" }, "Read-only view. Labels are edited in Obsidian for now."),
    ], { id: "labels", tone: toneOf("label", e.labelState), count: names[e.labelState] || e.labelState }),
    e.runs.length ? section("Runs", h("p", null, e.runs.map((r) => [runRef(ctx, r), " "])), { count: e.runs.length, open: false }) : null,
    section("Page text", prose(e.html), { open: false, id: "text" }),
  );
  return { node, title: e.stem };
}

// ---- TODOS --------------------------------------------------------------------------------------------------

async function todosPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const current = { owner: params.get("owner") || "", source: params.get("source") || "all", include_done: params.get("include_done") || "" };
  const body = await api(`${base(P)}/todos`, current);
  const items = body.data.todos;
  const mine = items.filter((t) => t.owner === "user");
  const theirs = items.filter((t) => t.owner !== "user");
  const go = (key) => (e) => (window.location.hash = route.todos(P, { ...current, [key]: e.target.value }));
  const group = (title, list, who) =>
    section(title, list.length ? h("ul", { class: "todos" }, list.map((t) => todoRow(ctx, t))) : h("p", { class: "muted ok-line" }, icon("check"), who ? " Nothing waits for you." : " Nothing for the agent."), { count: list.length, tone: list.length && who ? "warn" : "muted" });
  const file = body.data.manualFile;
  const node = h("div", { class: "page" },
    pageHead({ crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }], title: "TODOs", meta: [h("span", null, "Derived items are computed from the notes; manual items come from the TODO note."), file.path ? h("span", { class: "muted", title: file.path }, `sha ${short(file.sha256)}`) : null] }),
    h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
      filterSelect("owner", "Owner", ["user", "agent"], current.owner, go("owner")),
      filterSelect("source", "Source", ["all", "derived", "manual"], current.source, go("source")),
      h("label", { class: "field check" }, h("input", { type: "checkbox", checked: current.include_done ? true : null, onchange: (e) => (window.location.hash = route.todos(P, { ...current, include_done: e.target.checked ? "true" : "" })) }), h("span", null, "Show ticked manual items"))),
    h("div", { class: "row-actions", "data-slot": "todo-add" }),
    group("Waiting on you", mine, true),
    group("Agent work", theirs, false),
  );
  return { node, title: "TODOs", keepScroll: true };
}

// ---- SEARCH ------------------------------------------------------------------------------------------------------

const MODE_NOTES = { fts: "full text", like: "substring match (short query)", name: "file names", none: "filters only" };

async function searchPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const current = { q: params.get("q") || "", type: params.get("type") || "", plan: params.get("plan") || "", status: params.get("status") || "" };
  const results = h("div", { class: "results", "aria-live": "polite" });
  const summary = h("p", { class: "muted small" });
  const more = h("button", { type: "button", class: "btn", hidden: true }, "Load more");
  const terms = current.q.split(/\s+/);
  let cursor = null;
  const load = async (append) => {
    const hasQuery = current.q || current.type || current.plan || current.status;
    if (!hasQuery) {
      results.replaceChildren(emptyState("Type a query, or pick a filter.", "Short queries (1-2 characters) use substring matching; longer ones use full text."));
      return;
    }
    const body = await api(`${base(P)}/search`, { ...current, cursor: append ? cursor : null });
    const hits = body.data.hits;
    if (!append) results.replaceChildren();
    if (!hits.length && !append) results.append(emptyState("No note matches.", "Try fewer words, or remove a filter."));
    for (const hit of hits) {
      results.append(h("article", { class: "hit" },
        h("div", { class: "hit-head" },
          hit.href ? h("a", { href: hit.href, class: "hit-title" }, hit.stem) : h("span", { class: "hit-title" }, hit.stem),
          hit.type ? chip(hit.type, "muted") : null,
          hit.status ? chip(hit.status, "muted") : null,
          hit.entityId ? h("code", null, hit.entityId) : null),
        hit.text ? h("p", { class: "snippet" }, highlight(hit.text, terms)) : null,
        h("div", { class: "muted small" }, `${hit.path}${hit.line ? ":" + hit.line : ""}`)));
    }
    cursor = body.meta.nextCursor;
    more.hidden = !cursor;
    summary.textContent = `${body.meta.total} result${body.meta.total === 1 ? "" : "s"} · ${MODE_NOTES[body.data.matchMode] || body.data.matchMode}`;
  };
  more.addEventListener("click", () => load(true).catch(() => {}));
  const form = h("form", { class: "filters search-form", onsubmit: (e) => {
    e.preventDefault();
    const data = new FormData(e.target);
    window.location.hash = route.search(P, Object.fromEntries(data.entries()));
  } },
    h("label", { class: "field grow" }, h("span", null, "Query"), h("input", { name: "q", type: "search", value: current.q, autocomplete: "off", "aria-label": "Search query" })),
    h("label", { class: "field" }, h("span", null, "Type"), h("input", { name: "type", value: current.type, list: "note-types", placeholder: "any" })),
    h("datalist", { id: "note-types" }, ["plan", "exec-plan", "evidence", "evidence-appendix", "result", "decision", "theory", "execution-log"].map((t) => h("option", { value: t }))),
    h("label", { class: "field" }, h("span", null, "Plan"), h("input", { name: "plan", value: current.plan, placeholder: "any" })),
    h("label", { class: "field" }, h("span", null, "Status"), h("input", { name: "status", value: current.status, placeholder: "any" })),
    h("button", { class: "btn primary", type: "submit" }, icon("search"), "Search"));
  await load(false);
  const node = h("div", { class: "page" },
    pageHead({ crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }], title: "Search" }),
    form, summary, results, h("div", { class: "more" }, more));
  return { node, title: current.q ? `Search: ${current.q}` : "Search" };
}

// ---- NOTE --------------------------------------------------------------------------------------------------------------

async function notePage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/note/${enc(ctx.arg)}`);
  const n = body.data.note;
  const outline = n.outline.filter((o) => o.level > 1);
  const node = h("div", { class: "page" },
    pageHead({
      crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }],
      title: n.title || n.stem,
      chips: [n.type ? chip(n.type, "muted") : null, n.status ? chip(n.status, "muted") : null],
      meta: [h("span", { class: "muted" }, n.path), h("span", { class: "muted" }, `sha ${short(n.sha256)}`)],
    }),
    body.data.entityHref ? h("div", { class: "notice info" }, icon("branch"), h("div", null, "This note has its own page: ", link(body.data.entityHref, "open the structured view"))) : null,
    h("div", { class: "note-layout" },
      h("article", { class: "card" }, h("div", { class: "card-body" }, prose(body.data.html))),
      h("aside", { class: "note-aside" },
        outline.length ? section("Outline", h("ul", { class: "outline" }, outline.map((o) => h("li", { "data-level": o.level }, o.text))), { count: outline.length, open: outline.length < 14 }) : null,
        section("Linked from", body.data.backlinks.length ? h("ul", { class: "related" }, body.data.backlinks.map((b) => h("li", null, b.href ? link(b.href, b.stem) : b.stem, b.type ? chip(b.type, "muted") : null))) : emptyState("Nothing links here."), { count: body.data.backlinks.length, open: false }),
        section("Frontmatter", Object.keys(n.frontmatter).length ? kv(Object.entries(n.frontmatter).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)])) : emptyState("No frontmatter."), { open: false }),
      ),
    ),
  );
  return { node, title: n.title || n.stem };
}

// ---- HEALTH --------------------------------------------------------------------------------------------------------------

async function healthPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const level = params.get("level") || "FAIL,WARN";
  const [check, doctor] = await Promise.all([api(`${base(P)}/check`, { level }), api(`${base(P)}/doctor`)]);
  const c = check.data;
  const rows = [...c.findings, ...c.newCodes].map((f) => [statusChip("level", f.level), h("code", null, f.code), h("span", { class: "clip" }, f.where), f.message]);
  const node = h("div", { class: "page" },
    pageHead({ crumbs: [{ href: route.home(), label: "Research" }, { href: route.project(P), label: projectName(ctx) }], title: "Health", chips: [chip(`${c.summary.fail} FAIL`, c.summary.fail ? "bad" : "ok"), chip(`${c.summary.warn} WARN`, c.summary.warn ? "warn" : "muted"), chip(`${c.summary.info} INFO`, "muted")] }),
    h("form", { class: "filters", onsubmit: (e) => e.preventDefault() },
      h("label", { class: "field" }, h("span", null, "Show"),
        h("select", { onchange: (e) => (window.location.hash = `${route.health(P)}${qs({ level: e.target.value })}`) },
          [["FAIL,WARN", "FAIL and WARN"], ["FAIL", "FAIL only"], ["FAIL,WARN,INFO", "everything"]].map(([v, t]) => h("option", { value: v, selected: v === level ? true : null }, t))))),
    section("Content checks", table(["Level", "Code", "Where", "Message"], rows, { empty: "No finding at this level." }), { count: rows.length, tone: c.summary.fail ? "bad" : "muted" }),
    section("Environment and hygiene", h("ul", { class: "flags all" }, doctor.data.checks.map((d) => h("li", { "data-tone": toneOf("doctor", d.status) },
      h("div", null, statusChip("doctor", d.status), " ", h("strong", null, d.id), " ", d.message),
      d.remediation && d.status !== "ok" ? h("div", { class: "muted small" }, "Try: ", h("code", null, d.remediation)) : null,
      d.items.length ? h("details", { class: "subtle" }, h("summary", null, plural(d.count, "item")), h("ul", null, d.items.map((i) => h("li", null, h("code", null, i))))) : null))),
      { count: doctor.data.checks.length }),
  );
  return { node, title: "Health" };
}

export const PAGES = {
  home: homePage,
  project: projectPage,
  plan: planPage,
  decisions: decisionsPage,
  decision: decisionPage,
  ev: (ctx) => (ctx.arg ? evPage(ctx) : evListPage(ctx)),
  todos: todosPage,
  search: searchPage,
  note: notePage,
  health: healthPage,
};
