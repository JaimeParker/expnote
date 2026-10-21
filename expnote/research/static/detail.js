// Renderers shared by full pages and master-detail previews: decisions, evidence, TODOs, notes.
import {
  chip,
  commandBlock,
  emptyState,
  h,
  icon,
  imageUrl,
  isMaster,
  kv,
  link,
  pair,
  planTag,
  planTags,
  prose,
  route,
  section,
  short,
  statusChip,
  statusWord,
  toneOf,
} from "/research/ui.js";

const enc = encodeURIComponent;
export const base = (p) => `/projects/${enc(p)}`;
export const labels = (display) => (display && display.label_cn) || {};

export function projectName(ctx) {
  const found = ctx.state.projects.find((p) => p.id === ctx.project);
  return found ? found.name : ctx.project;
}

export function runRef(ctx, id) {
  return ctx.state.features.runApp
    ? h("a", { href: `/#/run/${enc(id)}`, class: "run-id" }, h("code", null, id))
    : h("code", { class: "run-id" }, id);
}

export const crumbsTo = (ctx, ...extra) => [{ href: route.home(), label: "Research" }, { href: route.project(ctx.project), label: projectName(ctx) }, ...extra];

// ---- decisions -------------------------------------------------------------------------------

function decisionLinks(P, ids) {
  return (ids || []).map((id, i) => [i ? ", " : "", link(route.decision(P, id), id.replace(/^[A-Z0-9]+-DEC-0*/, "#"))]);
}

// One decision as a flat row: number, title, then a quiet meta line.
// Status shows only when it is not the usual "confirmed"; the layer only where `layer` is set.
export function decisionHref(P, d) {
  return isMaster() ? route.decisions(P, { sel: d.id }) : route.decision(P, d.id);
}

export function decisionRow(ctx, d, { date = true, layer = false, plans = true, role = false, href } = {}) {
  const P = ctx.project;
  const notes = [];
  if (d.supersededBy) notes.push(h("span", { class: "muted" }, "superseded by ", decisionLinks(P, [d.supersededBy])));
  if (d.supersedes && d.supersedes.length) notes.push(h("span", { class: "muted" }, "supersedes ", decisionLinks(P, d.supersedes)));
  return h(
    "li",
    { class: "drow", "data-status": d.status || "" },
    h("span", { class: "num" }, `#${d.number}`),
    h(
      "div",
      { class: "d-main" },
      h("a", { class: "row-link d-title", href: href || decisionHref(P, d) }, d.title || d.id),
      h(
        "div",
        { class: "d-meta" },
        date && d.date ? h("span", { class: "date" }, d.date) : null,
        plans && d.plans && d.plans.length ? planTags(P, d.plans) : null,
        d.status && d.status !== "confirmed" ? statusChip("decision", d.status) : null,
        d.legacy ? h("span", { class: "muted" }, "legacy") : null,
        layer && d.layer ? h("span", { class: "muted" }, d.layer) : null,
        role && d.role && d.role !== "other" ? h("span", { class: "muted" }, d.role) : null,
        notes,
      ),
    ),
  );
}

const groupBy = (rows, keyOf) => {
  const groups = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return groups;
};

export function decisionDetail(ctx, data, { heading = "h1" } = {}) {
  const P = ctx.project;
  const d = data.decision;
  const refs = data.refsIn;
  const fm = d.frontmatter || {};
  const cited = refs.length
    ? [...groupBy(refs, (r) => r.type || "note").entries()].map(([type, list]) =>
        h(
          "div",
          { class: "related-group" },
          h("h4", null, type, " ", h("span", { class: "count" }, String(list.length))),
          h("ul", { class: "related" }, [...new Map(list.map((r) => [r.stem, r])).values()].map((r) => h("li", null, r.href ? h("a", { href: r.href }, r.stem) : r.stem, r.form !== "hub" && r.form ? h("span", { class: "muted small" }, ` ${r.form}`) : null))),
        ),
      )
    : emptyState("No note cites this decision.");
  const textBlocks = d.decisionTextHtml
    ? [h("div", { class: "block" }, prose(d.decisionTextHtml)), d.landsTextHtml ? h("div", { class: "block" }, h("h3", null, "Lands in"), prose(d.landsTextHtml)) : null]
    : [d.html ? h("div", { class: "block" }, prose(d.html)) : emptyState("This decision has no text.")];
  return h(
    "article",
    { class: "detail" },
    h(
      "header",
      { class: "detail-head" },
      h("div", { class: "detail-ident" }, h("span", { class: "num big" }, `#${d.number}`), h("span", { class: "muted" }, d.id), d.date ? h("span", { class: "date" }, d.date) : null, d.layer ? h("span", { class: "muted" }, d.layer) : null, d.status !== "confirmed" ? statusChip("decision", d.status) : null, d.legacy ? h("span", { class: "muted" }, "legacy") : null),
      h(heading, { class: "detail-title" }, d.title || d.id),
    ),
    h(
      "div",
      { class: "detail-grid" },
      h("div", { class: "detail-main" }, textBlocks),
      h(
        "div",
        { class: "detail-side" },
        kv([
          ["Plans", d.plans.length ? planTags(P, d.plans) : null],
          ["Supersedes", d.supersedes.length ? decisionLinks(P, d.supersedes) : null],
          ["Superseded by", d.supersededBy ? decisionLinks(P, [d.supersededBy]) : null],
          ["Confirmed by", d.confirmedBy],
          ["Raw status", d.statusRaw && d.statusRaw !== d.status ? d.statusRaw : null],
          ["Review by", fm.review_by || null],
          ["Source", d.path],
          ["Checksum", d.sha256 ? short(d.sha256) : null],
        ]),
        h("div", { class: "row-actions", "data-slot": "decision-actions" }),
        h("h3", null, "Cited by ", h("span", { class: "count" }, String(refs.length))),
        cited,
      ),
    ),
  );
}

// ---- evidence ----------------------------------------------------------------------------------

// `htmlRows[r][c]` is the server-sanitised inline HTML of a cell (links, code, bold, line breaks);
// the raw text stays as the fallback.
export function dataTable(t, { filledColumn, htmlRows } = {}) {
  if (!t) return null;
  const cell = (text, html) => {
    const td = h("td", null);
    if (html) td.innerHTML = html;
    else td.textContent = text;
    return td;
  };
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      null,
      h("thead", null, h("tr", null, t.header.map((c) => h("th", { scope: "col" }, c)))),
      h("tbody", null, t.rows.map((row, r) => h("tr", { "data-filled": filledColumn !== undefined ? String(Boolean((row[filledColumn] || "").trim())) : null }, row.map((text, c) => cell(text, htmlRows && htmlRows[r] && htmlRows[r][c]))))),
    ),
  );
}

export function figureTile(P, name, { caption = true } = {}) {
  const img = h("img", { src: imageUrl(P, name), alt: name, loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(chip("image missing", "warn")));
  return h("figure", null, h("a", { href: imageUrl(P, name), target: "_blank", rel: "noopener" }, img), caption ? h("figcaption", null, name) : null);
}

export function evDetail(ctx, data, { heading = "h1" } = {}) {
  const P = ctx.project;
  const e = data.ev;
  const names = labels(data.display);
  const label = e.labelTable;
  const fm = e.frontmatter || {};
  const figures = e.figures.map((name) => figureTile(P, name));
  return h(
    "article",
    { class: "detail" },
    h(
      "header",
      { class: "detail-head" },
      h("div", { class: "detail-ident" }, statusChip("ev", e.status), statusChip("label", e.labelState, names), fm.date ? h("span", { class: "date" }, fm.date) : null, e.legacy ? h("span", { class: "muted" }, "legacy") : null),
      h(heading, { class: "detail-title" }, e.stem),
      h(
        "div",
        { class: "meta-line" },
        e.plan ? pair("plan", planTag(P, e.plan)) : null,
        e.execPlan ? pair("exec", link(route.note(P, e.execPlan), e.execPlan)) : null,
        e.appendix ? h("span", null, link(route.note(P, e.appendix.stem), icon("doc"), " appendix")) : null,
        e.labelledBy ? pair("labelled by", e.labelledBy) : null,
        h("span", { class: "muted" }, `checksum ${short(e.sha256)}`),
      ),
    ),
    section("Figures", figures.length ? h("div", { class: "figures" }, figures) : emptyState("No figure listed."), { count: figures.length, id: "figures" }),
    section("Endpoints", e.endpointTable ? dataTable(e.endpointTable, { htmlRows: e.endpointTable.rowsHtml }) : emptyState("No endpoint table on this page."), { id: "endpoints", open: Boolean(e.endpointTable) }),
    section(
      "Labels",
      [
        label ? dataTable({ header: label.header, rows: label.rows.map((r) => [r.key, ...r.values]) }, { filledColumn: label.valueColumns[0] || 1, htmlRows: label.rows.map((r) => [r.keyHtml, ...(r.valuesHtml || [])]) }) : emptyState(e.labelState === "missing" ? "This page has no label section." : "The label table is empty."),
        e.remark ? h("div", { class: "remark" }, h("h4", null, "Remark"), h("pre", null, e.remark)) : null,
        h("div", { class: "row-actions", "data-slot": "ev-label-actions" }),
        h("p", { class: "muted small" }, "Read-only view. Labels are edited in Obsidian for now."),
      ],
      { id: "labels", tone: toneOf("label", e.labelState), count: names[e.labelState] || e.labelState },
    ),
    e.runs.length ? section("Runs", h("p", null, e.runs.map((r) => [runRef(ctx, r), " "])), { count: e.runs.length, open: false }) : null,
    section("Page text", prose(e.html), { open: false, id: "text" }),
  );
}

// ---- TODOs -------------------------------------------------------------------------------------

export function todoTarget(ctx, t) {
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

// What a group of TODOs of one kind means, in the singular and the plural.
const KIND_TEXT = {
  "ev-unmapped": ["evidence page has no plan", "evidence pages have no plan"],
  "ev-unlabelled": ["evidence page waits for labels", "evidence pages wait for labels"],
  "deviation-open": ["open deviation", "open deviations"],
  "decision-proposed": ["decision waits for approval", "decisions wait for approval"],
  "decision-review-due": ["decision is due for review", "decisions are due for review"],
  "decision-confirmed-not-applied": ["confirmed decision is not applied yet", "confirmed decisions are not applied yet"],
  "plan-awaiting-approval": ["plan awaits approval", "plans await approval"],
  "plan-gate-blocked": ["plan is blocked at a gate", "plans are blocked at a gate"],
  "legacy-no-final-res": ["legacy plan has no final result", "legacy plans have no final result"],
  "exec-rules-unconfirmed": ["exec-plan has unconfirmed rules", "exec-plans have unconfirmed rules"],
  "res-awaiting-confirmation": ["result awaits confirmation", "results await confirmation"],
  "stale-running-runs": ["run looks stale", "runs look stale"],
  "sync-conflict-file": ["sync conflict file", "sync conflict files"],
  manual: ["manual item", "manual items"],
};

export function kindPhrase(kind, count) {
  const text = KIND_TEXT[kind];
  return text ? text[count === 1 ? 0 : 1] : kind;
}

export function todoRow(ctx, t, { showKind = false } = {}) {
  const target = todoTarget(ctx, t);
  return h(
    "li",
    { class: "todo", "data-owner": t.owner, "data-severity": t.severity },
    h("span", { class: "who", title: t.owner === "user" ? "Waiting on you" : "Agent work" }, icon(t.owner === "user" ? "user" : "bot")),
    h(
      "div",
      { class: "todo-main" },
      target ? h("a", { href: target, class: "row-link todo-msg" }, t.message) : h("span", { class: "row-link todo-msg" }, t.message),
      h(
        "div",
        { class: "d-meta" },
        showKind ? h("span", { class: "muted" }, t.kind) : null,
        t.plan ? planTag(ctx.project, t.plan) : null,
        t.severity && t.severity !== "info" && t.source !== "manual" ? statusWord(toneOf("severity", t.severity), t.severity) : null,
        t.state === "done" ? statusWord("ok", "done") : null,
        t.due ? statusWord("warn", `due ${t.due}`) : null,
        t.source === "manual" ? h("span", { class: "muted" }, "manual") : null,
      ),
    ),
  );
}

export function todoDetail(ctx, t, { heading = "h1" } = {}) {
  const target = todoTarget(ctx, t);
  return h(
    "article",
    { class: "detail" },
    h(
      "header",
      { class: "detail-head" },
      h("div", { class: "detail-ident" }, icon(t.owner === "user" ? "user" : "bot"), h("span", null, t.owner === "user" ? "Waiting on you" : "Agent work"), t.severity ? statusWord(toneOf("severity", t.severity), t.severity) : null, t.due ? statusWord("warn", `due ${t.due}`) : null),
      h(heading, { class: "detail-title" }, t.message),
    ),
    kv([
      ["Kind", kindPhrase(t.kind, 1)],
      ["Source", t.source],
      ["Reference", t.ref],
      ["Plan", t.plan ? planTag(ctx.project, t.plan) : null],
      ["Next action", t.action],
    ]),
    target ? h("p", null, link(target, "Go to the item", icon("chevron"))) : null,
    t.command ? commandBlock(t.command) : null,
  );
}

// ---- notes and plans in a preview ---------------------------------------------------------------

export function notePreview(ctx, n, { href } = {}) {
  return h(
    "article",
    { class: "detail" },
    h("header", { class: "detail-head" }, h("div", { class: "detail-ident" }, n.type ? h("span", null, n.type) : null, n.status ? statusWord("muted", n.status) : null), h("h2", { class: "detail-title" }, n.title || n.stem), h("div", { class: "meta-line" }, h("span", { class: "muted" }, n.path))),
    h("div", { class: "block" }, prose(n.html)),
    href ? h("p", null, link(href, "Open the full page", icon("chevron"))) : null,
  );
}

export function planPreview(ctx, data) {
  const info = data.plan;
  return h(
    "article",
    { class: "detail" },
    h(
      "header",
      { class: "detail-head" },
      h("div", { class: "detail-ident" }, planTag(ctx.project, info.id), statusChip("plan", info.status), info.legacy ? h("span", { class: "muted" }, "legacy") : null),
      h("h2", { class: "detail-title" }, info.title),
      h("div", { class: "meta-line" }, info.owner ? pair("owner", info.owner) : null),
    ),
    h("div", { class: "block" }, prose(data.html, "plan-prose")),
  );
}
