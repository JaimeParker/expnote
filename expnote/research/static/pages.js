// Page renderers. Each takes ctx {project, params, arg, state, api} and returns
// Promise<{node, title, keepScroll?, select?, keys?}>. Content from the vault is shown as-is.
// Master-detail list pages (decisions, evidence, TODOs, search) live in lists.js.
import {
  base,
  crumbsTo,
  decisionDetail,
  decisionHref,
  decisionRow,
  evDetail,
  kindPhrase,
  labels,
  projectName,
  runRef,
  todoRow,
} from "/research/detail.js";
import { openDecisionAction, planCloseAction, todoAddForm } from "/research/actions.js";
import { planLanes, lanesLegend } from "/research/lanes.js";
import { LISTS } from "/research/lists.js";
import {
  chip,
  clear,
  commandBlock,
  emptyState,
  h,
  icon,
  kv,
  laneOf,
  link,
  pageHead,
  pair,
  panel,
  planTag,
  plural,
  prose,
  relTime,
  route,
  section,
  short,
  statusChip,
  statusWord,
  store,
  table,
  toneOf,
  dayLabel,
} from "/research/ui.js";

const enc = encodeURIComponent;

// "PLAN-003A：title" -> "title": the id is shown on its own.
const plainTitle = (info) => (info.title || "").replace(new RegExp(`^${info.id}\\s*[：:—–-]*\\s*`), "").trim() || info.title || info.id;

function healthSentence(home) {
  const f = home.findings;
  const parts = [f.fail ? `${plural(f.fail, "failing check")}` : "check clean"];
  if (f.warn) parts.push(plural(f.warn, "warning"));
  const index = home.index;
  if (!index || !index.built) parts.push("index not built");
  else if (index.ageSeconds !== null && index.ageSeconds !== undefined && index.ageSeconds > 3600) parts.push(`index ${relTime(index.builtAt)}`);
  else parts.push("index fresh");
  const sentence = parts.join(", ");
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

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

// ---- PROJECTS INDEX ---------------------------------------------------------------------------

function projectSummary(ctx, project, home) {
  const P = project.id;
  const awaiting = home.decisions.awaiting;
  const statusCounts = Object.entries(home.plans.byStatus).map(([status, n]) => h("a", { class: "status-count", href: route.project(P, { status }) }, statusChip("plan", status), " ", h("b", null, String(n))));
  const runs = home.runs.recent;
  return h(
    "article",
    { class: "panel project-summary" },
    h("header", { class: "panel-head" }, h("h2", null, h("a", { href: route.project(P) }, project.name)), h("a", { class: "health-line", href: route.health(P) }, healthSentence(home))),
    h(
      "div",
      { class: "summary-grid" },
      h(
        "div",
        null,
        h("h3", null, "Plans ", h("span", { class: "count" }, String(home.plans.total))),
        statusCounts.length ? h("div", { class: "status-counts" }, statusCounts) : emptyState("No plans yet."),
        h("h3", null, "Waiting on you ", h("span", { class: "count", "data-tone": awaiting.length ? "warn" : "muted" }, String(awaiting.length))),
        awaiting.length
          ? h("ul", { class: "drows" }, awaiting.slice(0, 5).map((d) => decisionRow(ctx, { ...d, status: "proposed" }, { href: route.decision(P, d.id) })))
          : h("p", { class: "muted ok-line" }, icon("check"), " Nothing waits on you."),
        h("p", { class: "small" }, pair("Agent queue", plural(home.todos.agent, "item")), " ", link(route.project(P), "Open the overview", icon("chevron"))),
      ),
      h(
        "div",
        null,
        h("h3", null, "Recent runs"),
        runs.length
          ? table(["Run", "Topic", "Status"], runs.slice(0, 5).map((r) => [runRef(ctx, r.id), h("span", { class: "clip" }, r.topic), statusChip("run", r.status)]))
          : h("p", { class: "muted small" }, home.runs.workspaces.length ? "No runs in the linked workspaces." : "No workspace is linked to this project."),
      ),
      h("div", null, h("h3", null, "Environment"), flagList(home.doctor.flags)),
    ),
  );
}

async function indexPage(ctx) {
  const { state, api } = ctx;
  const grid = h("div", { class: "project-grid" });
  const node = h("div", { class: "page" }, pageHead({ title: "Research", meta: [pair("Projects", String(state.projects.length))] }), grid);
  if (!state.projects.length) {
    grid.append(
      emptyState("No research project is registered.", "Register one, then reload this page:"),
      commandBlock("expnote project register --vault-root <vault> --project-dir <dir> --images-dir <dir> --profile-note <file>"),
    );
  }
  for (const project of state.projects) {
    const slot = h("div", { class: "project-slot" }, h("div", { class: "panel skeleton-card" }, h("h2", null, project.name), h("div", { class: "skeleton", role: "status" }, h("div", { class: "skeleton-line", "data-w": "1" }), h("div", { class: "skeleton-line", "data-w": "2" }), h("div", { class: "skeleton-line", "data-w": "0" }))));
    grid.append(slot);
    api(`${base(project.id)}/home`)
      .then((body) => slot.replaceChildren(projectSummary(ctx, project, body.data)))
      .catch((error) => {
        slot.replaceChildren(
          h("article", { class: "panel" }, h("h2", null, project.name), h("div", { class: "error-box", role: "alert" }, h("strong", null, error.code), h("p", null, error.message), error.remediation ? h("p", { class: "remediation" }, "Try: ", h("code", null, error.remediation)) : null)),
        );
      });
  }
  return { node, title: "Research" };
}

// ---- PROJECT OVERVIEW -------------------------------------------------------------------------------

const ACTIVE_ORDER = ["awaiting-approval", "running", "approved", "read-out", "draft"];

// Marks a note of a plan's finished cycle (before it was reopened).
function cycleTag(item) {
  return item.earlierCycle ? h("span", { class: "tag-earlier" }, "earlier cycle") : null;
}

// Who approved an exec-plan's rules: the agent (no new judgement rules) or the user.
function approvedTag(item) {
  return item.approvedBy ? h("span", { class: `tag-approved by-${item.approvedBy}` }, `approved by ${item.approvedBy}`) : null;
}

function planRow(ctx, p) {
  const P = ctx.project;
  return h(
    "li",
    { class: "plan-row" },
    h("div", { class: "plan-name" }, planTag(P, p.id), h("span", { class: "plan-title" }, plainTitle(p)), p.openItems ? h("span", { class: "open-count" }, `${p.openItems} open`) : null),
    h("span", { class: "plan-status" }, statusChip("plan", p.status)),
    h("span", { class: "plan-result" }, p.resStatus ? statusChip("res", p.resStatus) : h("span", { class: "muted" }, "none")),
    h("span", { class: "n", "data-label": "EV" }, String(p.evCount)),
    h("span", { class: "n", "data-label": "Decisions" }, String(p.decisionCount)),
  );
}

function plansPanel(ctx, plans, wanted) {
  const P = ctx.project;
  const active = ACTIVE_ORDER.flatMap((status) => plans.filter((p) => p.status === status));
  const finished = plans.filter((p) => !ACTIVE_ORDER.includes(p.status));
  const head = h("div", { class: "plan-row head" }, h("span", null, "Plan"), h("span", null, "Status"), h("span", null, "Result"), h("span", { class: "n" }, "EV"), h("span", { class: "n" }, "Decisions"));
  const note = wanted ? h("p", { class: "small filter-note" }, "Showing ", statusChip("plan", wanted), " plans. ", link(route.project(P), "Show all")) : null;
  return panel(
    "Plans",
    [
      note,
      head,
      active.length ? h("ul", { class: "plan-rows" }, active.map((p) => planRow(ctx, p))) : plans.length ? h("p", { class: "muted small pad" }, "No plan is running or waiting for approval.") : emptyState("No plan matches."),
      finished.length ? section(`Closed or replaced`, h("ul", { class: "plan-rows" }, finished.map((p) => planRow(ctx, p))), { count: finished.length, open: finished.length <= 8, foldKey: "home.finished", id: "finished" }) : null,
    ],
    { count: plans.length, className: "plans-panel" },
  );
}

function feedPanel(ctx, decisions, total) {
  const P = ctx.project;
  const newest = decisions.slice(-15).reverse();
  const days = [];
  for (const d of newest) {
    const last = days[days.length - 1];
    if (last && last.date === d.date) last.rows.push(d);
    else days.push({ date: d.date, rows: [d] });
  }
  return panel(
    "Latest decisions",
    [
      days.map((day) => h("div", { class: "day-group" }, h("h3", { class: "day" }, day.date ? dayLabel(day.date) : "Undated", h("span", { class: "count" }, String(day.rows.length))), h("ul", { class: "drows" }, day.rows.map((d) => decisionRow(ctx, d, { date: false }))))),
      h("p", { class: "small more-link" }, link(route.decisions(P), `All ${total} decisions`, icon("chevron"))),
    ],
    { className: "feed-panel" },
  );
}

function runsPanel(ctx, home) {
  const runs = home.runs.recent;
  const linked = home.runs.workspaces.length > 0;
  return panel(
    "Recent runs",
    runs.length
      ? h(
          "ul",
          { class: "run-rows" },
          runs.map((r) =>
            h(
              "li",
              { class: "run-row" },
              h("div", { class: "run-top" }, runRef(ctx, r.id), statusChip("run", r.status), h("span", { class: "date" }, relTime(r.updatedAt))),
              h("div", { class: "run-topic" }, r.topic),
              r.result ? h("div", { class: "muted small clamp" }, r.result) : null,
            ),
          ),
        )
      : emptyState(linked ? "No run yet." : "No workspace is linked to this project.", linked ? "Runs from the linked workspaces appear here once they start." : "Link a workspace with `expnote project register --workspace`, then reload."),
    { count: runs.length || null, className: "runs-panel" },
  );
}

function waitingPanel(ctx, home, userTodos, manualFile) {
  const awaiting = home.decisions.awaiting;
  const empty = !awaiting.length && !userTodos.length;
  const addForm = todoAddForm(ctx, manualFile);
  return panel(
    "Waiting on you",
    [
      empty
        ? emptyState("Nothing waits on you.", "Decisions you need to approve and TODOs that are yours appear here.")
        : [
            awaiting.length ? [h("h3", null, "Decisions to approve"), h("ul", { class: "drows" }, awaiting.map((d) => decisionRow(ctx, { ...d, status: "proposed" })))] : null,
            userTodos.length ? [h("h3", null, "Your TODOs"), h("ul", { class: "todos" }, userTodos.map((t) => todoRow(ctx, t, { showKind: true, manualSha: manualFile.sha256 })))] : null,
          ],
      addForm,
    ],
    { count: awaiting.length + userTodos.length, tone: empty ? "muted" : "warn", className: "waiting-panel" },
  );
}

function queueGroup(ctx, kind, items) {
  const many = items.length > 5;
  const shown = items.slice(0, 12);
  const body = [h("ul", { class: "todos" }, shown.map((t) => todoRow(ctx, t))), items.length > shown.length ? h("p", { class: "small more-link" }, link(route.todos(ctx.project, { kind }), `All ${items.length} in TODOs`, icon("chevron"))) : link(route.todos(ctx.project, { kind }), "Open in TODOs", icon("chevron"))];
  const open = store.get(`fold.queue.${kind}`, !many);
  const details = h(
    "details",
    { class: "queue-group", open: open ? true : null },
    h("summary", null, h("b", { class: "n" }, String(items.length)), " ", kindPhrase(kind, items.length)),
    h("div", { class: "queue-body" }, body),
  );
  details.addEventListener("toggle", () => store.set(`fold.queue.${kind}`, details.hasAttribute("open")));
  return details;
}

function queuePanel(ctx, home, agentTodos) {
  const groups = new Map();
  for (const t of agentTodos) {
    if (!groups.has(t.kind)) groups.set(t.kind, []);
    groups.get(t.kind).push(t);
  }
  const ordered = [...groups.entries()].sort(([, a], [, b]) => Number(b.some((t) => t.severity !== "info")) - Number(a.some((t) => t.severity !== "info")) || a.length - b.length);
  return panel(
    "Agent queue",
    [
      ordered.length ? ordered.map(([kind, items]) => queueGroup(ctx, kind, items)) : h("p", { class: "muted ok-line" }, icon("check"), " The agents have nothing queued."),
      home.doctor.flags.length ? section("Environment", flagList(home.doctor.flags), { count: home.doctor.flags.length, tone: "warn", open: false, foldKey: "home.flags" }) : null,
    ],
    { count: agentTodos.length, className: "queue-panel" },
  );
}

async function projectPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const wanted = params.get("status");
  const [home, plansBody, decisionsBody, todosBody] = await Promise.all([
    api(`${base(P)}/home`),
    api(`${base(P)}/plans`),
    api(`${base(P)}/decisions`),
    api(`${base(P)}/todos`),
  ]);
  const data = home.data;
  const allPlans = plansBody.data.plans;
  const plans = wanted ? allPlans.filter((p) => p.status === wanted) : allPlans;
  const decisions = decisionsBody.data.decisions;
  const todos = todosBody.data.todos;
  const userTodos = todos.filter((t) => t.owner === "user");
  const agentTodos = todos.filter((t) => t.owner !== "user");
  const node = h(
    "div",
    { class: "page overview" },
    h(
      "div",
      { class: "overview-head" },
      h("h1", null, projectName(ctx)),
      h("p", { class: "health-line" }, h("a", { href: route.health(P) }, healthSentence(data)), "."),
      h("div", { class: "meta-line" }, pair("Plans", String(data.plans.total)), pair("Decisions", String(data.decisions.total)), pair("TODOs", String(data.todos.total))),
      h("div", { class: "actions", "data-slot": "page-actions" }),
    ),
    h(
      "div",
      { class: "overview-grid" },
      h("div", { class: "col-left" }, waitingPanel(ctx, data, userTodos, todosBody.data.manualFile), queuePanel(ctx, data, agentTodos)),
      h(
        "section",
        { class: "panel lanes-panel" },
        h("header", { class: "panel-head" }, h("h2", null, "Decisions by plan"), lanesLegend()),
        planLanes({ project: P, plans: allPlans, decisions, today: data.today }),
      ),
      h("div", { class: "col-mid" }, plansPanel(ctx, plans, wanted), runsPanel(ctx, data)),
      h("div", { class: "col-right" }, feedPanel(ctx, decisions, data.decisions.total)),
    ),
  );
  return { node, title: projectName(ctx) };
}

// ---- PLAN ------------------------------------------------------------------------------------------

const STEPS = ["draft", "approved", "running", "read-out", "confirmed", "closed"];

// The lifecycle as a rail: each reached stage names the decision that moved the plan and its date.
function lifecycleRail(ctx, shown, bundle) {
  const P = ctx.project;
  const info = shown.plan;
  const gates = shown.gates;
  const status = info.status === "awaiting-approval" ? "draft" : info.status;
  if (!gates.known || !STEPS.includes(status)) return null;
  const at = STEPS.indexOf(status);
  const byId = new Map(bundle.decisions.map((d) => [d.id, d]));
  const rulesDecision = bundle.decisions.find((d) => d.role === "rules");
  const resultDecision = bundle.results.map((r) => r.confirmedDecision).find(Boolean);
  const moved = {
    approved: info.approvedDecision,
    running: rulesDecision && rulesDecision.id,
    confirmed: resultDecision,
    closed: info.closedDecision,
  };
  return h(
    "ol",
    { class: "lifecycle", "aria-label": "Plan lifecycle" },
    STEPS.map((step, i) => {
      const decision = moved[step] && byId.get(moved[step]);
      const reached = i <= at;
      const label = step === "draft" && info.status === "awaiting-approval" ? "awaiting approval" : step;
      return h(
        "li",
        { "data-state": i < at ? "done" : i === at ? "current" : "todo", "aria-current": i === at ? "step" : null },
        h("span", { class: "dot" }),
        h("span", { class: "stage" }, label),
        reached && decision ? h("span", { class: "stage-meta" }, link(decisionHref(P, decision), `#${decision.number}`), " ", h("span", { class: "date" }, dayLabel(decision.date))) : null,
        reached && !decision && step === "draft" && shown.frontmatter.created ? h("span", { class: "stage-meta" }, h("span", { class: "date" }, dayLabel(String(shown.frontmatter.created)))) : null,
        reached && !decision && moved[step] ? h("span", { class: "stage-meta" }, link(route.decision(P, moved[step]), moved[step].replace(/^[A-Z0-9]+-DEC-0*/, "#"))) : null,
      );
    }),
  );
}

function nextStep(ctx, gates) {
  const forward = gates.next.filter((n) => !["superseded", "withdrawn"].includes(n.to));
  const side = gates.next.filter((n) => ["superseded", "withdrawn"].includes(n.to));
  const row = (n) =>
    h(
      "div",
      { class: "gate", "data-tone": n.ok ? "ok" : "bad" },
      h("div", { class: "gate-head" }, icon(n.ok ? "check" : "gate"), h("strong", null, `to ${n.to}`), statusWord(n.ok ? "ok" : "bad", n.ok ? "ready" : `blocked by ${n.unmet.length}`)),
      n.unmet.length ? h("ul", null, n.unmet.map((u) => h("li", null, h("code", null, u.code), " ", u.message))) : null,
    );
  return h(
    "div",
    { class: "next-step" },
    h("h2", null, "Next step"),
    gates.currentUnmet.length
      ? h("div", { class: "notice bad" }, icon("alert"), h("div", null, h("strong", null, "This plan is past a gate it does not meet:"), h("ul", null, gates.currentUnmet.map((u) => h("li", null, h("code", null, u.code), " ", u.message)))))
      : null,
    forward.length ? forward.map(row) : h("p", { class: "muted small" }, "No further forward step."),
    side.length ? h("details", { class: "subtle" }, h("summary", null, "Other transitions"), side.map(row)) : null,
  );
}

function figureThumb(P, name) {
  if (!name) return h("span", { class: "thumb empty", "aria-hidden": "true" }, icon("image"));
  return h("img", { class: "thumb", src: `/api/research/projects/${enc(P)}/image/${enc(name)}`, alt: "", loading: "lazy" });
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
    h(
      "div",
      { class: "related-group" },
      h("h4", null, type, " ", h("span", { class: "count" }, String(list.length))),
      h("ul", { class: "related" }, list.map((r) => h("li", null, link(route.note(ctx.project, r.stem), r.title || r.stem), r.status ? h("span", { class: "muted small" }, ` ${r.status}`) : null, h("div", { class: "muted small" }, r.oneLine)))),
    ),
  );
}

// Tab definitions: [key, label, count, tone, render]
function railTabs(ctx, shown, b) {
  const P = ctx.project;
  const display = shown.display;
  const names = labels(display);
  const issues = b.openItems.length + b.deviations.length + b.findings.items.length;
  const evidence = b.evidence.map((e) =>
    h(
      "li",
      { class: e.earlierCycle ? "ev-row earlier-cycle" : "ev-row" },
      figureThumb(P, e.figures[0]),
      h("div", { class: "ev-main" }, link(route.ev(P, e.stem), e.stem), h("div", { class: "d-meta" }, statusChip("ev", e.status), e.labelState !== "na" ? statusChip("label", e.labelState, names) : null, e.figures.length ? h("span", { class: "muted" }, plural(e.figures.length, "figure")) : null, e.appendix ? link(route.note(P, e.appendix.stem), "appendix") : null, cycleTag(e))),
    ),
  );
  return [
    ["decisions", "Decisions", b.decisions.length, null, () => (b.decisions.length ? h("ul", { class: "drows" }, b.decisions.slice().reverse().map((d) => decisionRow(ctx, d, { plans: false, role: true }))) : emptyState("No decision yet."))],
    ["evidence", "Evidence", b.evidence.length, null, () => (evidence.length ? h("ul", { class: "ev-rows" }, evidence) : emptyState("No evidence page."))],
    [
      "results",
      "Results",
      b.results.length,
      null,
      () =>
        b.results.length
          ? h("ul", { class: "plain-rows" }, b.results.map((r) => h("li", { class: r.earlierCycle ? "earlier-cycle" : "" }, h("div", null, link(route.note(P, r.stem), r.id || r.stem)), h("div", { class: "d-meta" }, cycleTag(r), statusChip("res", r.status), r.confirmedDecision ? pair("confirmed by", link(route.decision(P, r.confirmedDecision), r.confirmedDecision.replace(/^[A-Z0-9]+-DEC-0*/, "#"))) : null, r.deviationImpact ? pair("deviation impact", r.deviationImpact) : null))))
          : emptyState("No result card."),
    ],
    [
      "exec",
      "Exec",
      b.execPlans.length,
      null,
      () =>
        b.execPlans.length
          ? h(
              "details",
              { class: "exec-fold" },
              h("summary", null, `Show ${b.execPlans.length} exec-plan${b.execPlans.length === 1 ? "" : "s"} to double-check`),
              h("ul", { class: "plain-rows" }, b.execPlans.map((e) => h("li", { class: e.earlierCycle ? "earlier-cycle" : "" }, h("div", null, link(route.note(P, e.stem), e.id || e.stem)), e.stage ? h("div", { class: "muted small" }, e.stage) : null, h("div", { class: "d-meta" }, cycleTag(e), statusChip("exec", e.status), approvedTag(e), e.frozenOn ? h("span", { class: "date" }, `frozen ${e.frozenOn}`) : e.frozen ? h("span", { class: "muted" }, "frozen") : null, e.rulesShaMatches === false ? statusWord("bad", "rules changed") : null, e.rulesDecision ? link(route.decision(P, e.rulesDecision), e.rulesDecision.replace(/^[A-Z0-9]+-DEC-0*/, "#")) : null)))),
            )
          : emptyState("No exec-plan."),
    ],
    [
      "runs",
      "Runs",
      b.runs.length + b.analysisDocs.length,
      null,
      () => [
        b.runs.length ? h("ul", { class: "plain-rows" }, b.runs.map((r) => h("li", null, h("div", null, runRef(ctx, r.id), " ", r.topic), h("div", { class: "d-meta" }, statusChip("run", r.status), pair("cited by", String(r.citedBy.length))), h("div", { class: "muted small clamp" }, r.result)))) : emptyState("No run is cited by this plan."),
        b.analysisDocs.length ? [h("h3", null, "Analysis docs"), h("ul", { class: "plain-rows" }, b.analysisDocs.map((d) => h("li", null, h("strong", null, d.title), h("div", { class: "muted small" }, d.oneLine), h("div", { class: "small" }, d.linkedRuns.map((r) => [runRef(ctx, r), " "])))))] : null,
      ],
    ],
    ["related", "Related", b.related.length, null, () => relatedList(ctx, b.related)],
    [
      "issues",
      "Issues",
      issues,
      issues ? "warn" : null,
      () => [
        b.openItems.length ? [h("h3", null, "Open items"), h("ul", { class: "todos" }, b.openItems.map((t) => todoRow(ctx, { ...t, plan: null, source: t.kind === "manual" ? "manual" : "derived" }, { showKind: true })))] : null,
        b.deviations.length ? [h("h3", null, "Deviations"), h("ul", { class: "plain-rows" }, b.deviations.map((d) => h("li", null, h("div", null, h("b", null, d.id), " ", h("span", { class: "date" }, d.date), " ", statusChip("deviation", d.status)), h("div", { class: "muted small" }, d.found ? `found ${d.found}. ` : "", d.summary))))] : null,
        b.findings.items.length ? [h("h3", null, "Checks"), h("ul", { class: "findings" }, b.findings.items.map((f) => h("li", null, statusChip("level", f.level), " ", h("code", null, f.code), " ", f.message)))] : null,
        !issues ? h("p", { class: "muted ok-line" }, icon("check"), " Nothing open, no deviation, no finding.") : null,
      ],
    ],
  ];
}

function contextRail(ctx, shown, b, keyState) {
  const tabs = railTabs(ctx, shown, b);
  const saved = store.get("planTab", "decisions");
  keyState.index = Math.max(0, tabs.findIndex(([key]) => key === saved));
  const bodyBox = h("div", { class: "rail-body", role: "tabpanel" });
  const strip = h("div", { class: "tab-strip", role: "tablist", "aria-label": "Plan context" });
  const buttons = tabs.map(([key, label, count, tone], i) =>
    h(
      "button",
      { type: "button", class: "tab", role: "tab", id: `tab-${key}`, onclick: () => activate(i) },
      label,
      " ",
      h("span", { class: "count", "data-tone": tone || "muted" }, String(count)),
    ),
  );
  buttons.forEach((b) => strip.append(b));
  function activate(i) {
    keyState.index = i;
    store.set("planTab", tabs[i][0]);
    buttons.forEach((b, j) => {
      b.setAttribute("aria-selected", j === i ? "true" : "false");
      b.setAttribute("tabindex", j === i ? "0" : "-1");
    });
    bodyBox.setAttribute("aria-labelledby", `tab-${tabs[i][0]}`);
    clear(bodyBox).append(tabs[i][4]());
  }
  keyState.step = (delta) => activate((keyState.index + delta + tabs.length) % tabs.length);
  activate(keyState.index);
  return h("aside", { class: "rail", "aria-label": "Plan status" }, nextStep(ctx, shown.gates), strip, bodyBox);
}

function outlineOf(prosebox, project, plan) {
  const items = [];
  if (!prosebox.querySelectorAll) return items;
  for (const el of prosebox.querySelectorAll("h2, h3")) {
    if (!el.id) continue;
    items.push({ id: el.id, text: el.textContent, level: el.tagName === "H3" ? 3 : 2, el });
  }
  return items;
}

function outlineLinks(items, here) {
  return h(
    "ul",
    { class: "outline" },
    items.map((o) =>
      h("li", { "data-level": o.level }, h("a", { href: here, "data-target": o.id, onclick: (e) => { e.preventDefault(); o.el.scrollIntoView(); } }, o.text)),
    ),
  );
}

async function planPage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const plan = ctx.arg;
  const [shown, bundle] = await Promise.all([api(`${base(P)}/plans/${enc(plan)}`), api(`${base(P)}/plans/${enc(plan)}/bundle`)]);
  const s = shown.data;
  const b = bundle.data;
  const info = s.plan;
  const keyState = { index: 0, step: () => {} };
  const proseBox = prose(s.html, "plan-prose");
  const items = outlineOf(proseBox, P, info.id);
  const here = route.plan(P, info.id);
  const warnings = (bundle.warnings || []).map((w) => h("div", { class: "notice warn" }, icon("alert"), h("div", null, h("code", null, w.code), " ", w.message)));
  const sideOutline = items.length ? h("nav", { class: "outline-side", "aria-label": "Contents" }, h("h2", null, "Contents"), outlineLinks(items, here)) : h("div", { class: "outline-side" });
  const foldOutline = items.length ? h("details", { class: "outline-fold" }, h("summary", null, "Contents ", h("span", { class: "count" }, String(items.length))), outlineLinks(items, here)) : null;
  const node = h(
    "div",
    { class: "page plan-page" },
    h(
      "div",
      { class: "plan-head" },
      h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, link(route.home(), "Research"), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.project(P), projectName(ctx))),
      h("h1", { class: "plan-h1", "data-lane": laneAttr(info.id) }, h("span", { class: "tick", "aria-hidden": "true" }), h("span", { class: "pid" }, info.id), " ", h("span", { class: "ptitle" }, plainTitle(info))),
      h(
        "div",
        { class: "meta-line" },
        h("span", { class: "pair" }, statusChip("plan", info.status)),
        info.owner ? pair("Owner", info.owner) : null,
        info.reopenedOn ? h("span", { class: "pair reopened" }, `Reopened ${info.reopenedOn}`) : null,
        h("span", { class: "muted", title: info.path }, `checksum ${short(info.sha256)}`),
        info.legacy ? h("span", { class: "muted" }, "legacy page") : null,
      ),
      lifecycleRail(ctx, s, b),
      h("div", { class: "actions", "data-slot": "page-actions" }, planCloseAction(ctx, info)),
    ),
    warnings,
    h(
      "div",
      { class: "plan-grid" },
      sideOutline,
      contextRail(ctx, s, b, keyState),
      h("article", { class: "plan-main" }, foldOutline, proseBox),
    ),
  );
  wireOutline(node, items);
  return { node, title: info.id, keys: { "[": () => (keyState.step(-1), true), "]": () => (keyState.step(1), true) } };
}

const laneAttr = (id) => String(laneOf(id));

// Highlight the section being read in the side outline.
function wireOutline(node, items) {
  if (typeof IntersectionObserver !== "function" || !items.length || !node.querySelectorAll) return;
  const links = new Map();
  for (const a of node.querySelectorAll(".outline-side a")) links.set(a.getAttribute("data-target"), a);
  const visible = new Set();
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) (entry.isIntersecting ? visible.add(entry.target.id) : visible.delete(entry.target.id));
      const first = items.find((o) => visible.has(o.id));
      if (!first) return;
      for (const [id, a] of links) (id === first.id ? a.setAttribute("aria-current", "location") : a.removeAttribute("aria-current"));
    },
    { rootMargin: "-72px 0px -65% 0px" },
  );
  for (const o of items) observer.observe(o.el);
}

// ---- DECISION / EVIDENCE (full pages share the preview renderers) --------------------------------------

async function decisionPage(ctx) {
  const P = ctx.project;
  const body = await ctx.api(`${base(P)}/decisions/${enc(ctx.arg)}`);
  const d = body.data.decision;
  const node = h("div", { class: "page full-detail" }, h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, link(route.home(), "Research"), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.project(P), projectName(ctx)), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.decisions(P, { sel: d.id }), "Decisions")), decisionDetail(ctx, body.data));
  const keys = { a: () => openDecisionAction("approve", d.id), r: () => openDecisionAction("reject", d.id) };
  return { node, title: `#${d.number} ${d.title || d.id}`, keys };
}

async function evPage(ctx) {
  const P = ctx.project;
  const body = await ctx.api(`${base(P)}/ev/${enc(ctx.arg)}`);
  const node = h("div", { class: "page full-detail" }, h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, link(route.home(), "Research"), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.project(P), projectName(ctx)), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.evList(P, { sel: body.data.ev.stem }), "Evidence")), evDetail(ctx, body.data));
  return { node, title: body.data.ev.stem };
}

// ---- NOTE ------------------------------------------------------------------------------------------------

async function notePage(ctx) {
  const { api } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/note/${enc(ctx.arg)}`);
  const n = body.data.note;
  const proseBox = prose(body.data.html);
  const items = outlineOf(proseBox, P, n.stem);
  const here = route.note(P, n.stem);
  const node = h(
    "div",
    { class: "page note-page" },
    pageHead({
      crumbs: crumbsTo(ctx),
      title: n.title || n.stem,
      chips: [n.type ? chip(n.type, "muted") : null, n.status ? statusWord("muted", n.status) : null],
      meta: [h("span", { class: "muted" }, n.path), h("span", { class: "muted" }, `checksum ${short(n.sha256)}`)],
    }),
    body.data.entityHref ? h("div", { class: "notice info" }, icon("branch"), h("div", null, "This note has its own page: ", link(body.data.entityHref, "open the structured view"))) : null,
    h(
      "div",
      { class: "note-grid" },
      h("article", { class: "note-main" }, proseBox),
      h(
        "aside",
        { class: "note-aside" },
        items.length ? section("Contents", outlineLinks(items, here), { count: items.length, open: items.length < 16, foldKey: "note.outline" }) : null,
        section("Linked from", body.data.backlinks.length ? h("ul", { class: "related" }, body.data.backlinks.map((b2) => h("li", null, b2.href ? link(b2.href, b2.stem) : b2.stem, b2.type ? h("span", { class: "muted small" }, ` ${b2.type}`) : null))) : emptyState("Nothing links here."), { count: body.data.backlinks.length, open: false }),
        section("Frontmatter", Object.keys(n.frontmatter).length ? kv(Object.entries(n.frontmatter).map(([k, v]) => [k, Array.isArray(v) ? v.join(", ") : String(v)])) : emptyState("No frontmatter."), { open: false }),
      ),
    ),
  );
  return { node, title: n.title || n.stem };
}

// ---- HEALTH --------------------------------------------------------------------------------------------------

async function healthPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const level = params.get("level") || "FAIL,WARN";
  const [check, doctor] = await Promise.all([api(`${base(P)}/check`, { level }), api(`${base(P)}/doctor`)]);
  const c = check.data;
  const rows = [...c.findings, ...c.newCodes].map((f) => [statusChip("level", f.level), h("code", null, f.code), h("span", { class: "clip" }, f.where), f.message]);
  const node = h(
    "div",
    { class: "page health-page" },
    pageHead({
      crumbs: crumbsTo(ctx),
      title: "Health",
      meta: [pair("Failures", statusWord(c.summary.fail ? "bad" : "ok", String(c.summary.fail))), pair("Warnings", statusWord(c.summary.warn ? "warn" : "muted", String(c.summary.warn))), pair("Notes", String(c.summary.info))],
    }),
    h(
      "form",
      { class: "filters inline", onsubmit: (e) => e.preventDefault() },
      h(
        "label",
        { class: "field" },
        h("span", null, "Show"),
        h("select", { onchange: (e) => (window.location.hash = `${route.health(P)}?level=${enc(e.target.value)}`) }, [["FAIL,WARN", "failures and warnings"], ["FAIL", "failures only"], ["FAIL,WARN,INFO", "everything"]].map(([v, t]) => h("option", { value: v, selected: v === level ? true : null }, t))),
      ),
    ),
    h("div", { class: "health-grid" },
      panel("Content checks", table(["Level", "Code", "Where", "Message"], rows, { empty: "No finding at this level." }), { count: rows.length, tone: c.summary.fail ? "bad" : "muted" }),
      panel(
        "Environment and hygiene",
        h(
          "ul",
          { class: "flags all" },
          doctor.data.checks.map((d) =>
            h(
              "li",
              { "data-tone": toneOf("doctor", d.status) },
              h("div", null, statusChip("doctor", d.status), " ", h("strong", null, d.id), " ", d.message),
              d.remediation && d.status !== "ok" ? h("div", { class: "muted small" }, "Try: ", h("code", null, d.remediation)) : null,
              d.items.length ? h("details", { class: "subtle" }, h("summary", null, plural(d.count, "item")), h("ul", null, d.items.map((i) => h("li", null, h("code", null, i))))) : null,
            ),
          ),
        ),
        { count: doctor.data.checks.length },
      ),
    ),
  );
  return { node, title: "Health" };
}

export const PAGES = {
  home: indexPage,
  project: projectPage,
  plan: planPage,
  decision: decisionPage,
  ev: (ctx) => (ctx.arg ? evPage(ctx) : LISTS.ev(ctx)),
  decisions: LISTS.decisions,
  todos: LISTS.todos,
  todo: LISTS.todo,
  search: LISTS.search,
  note: notePage,
  health: healthPage,
};
