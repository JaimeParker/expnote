// Master-detail list pages: decisions, evidence, TODOs and search.
// At >= 1100px the selected item opens in a preview pane (?sel=<id>); below that, rows open full pages.
import { openDecisionAction, todoAddForm } from "/research/actions.js";
import {
  base,
  decisionDetail,
  decisionRow,
  evDetail,
  kindPhrase,
  labels,
  notePreview,
  planPreview,
  projectName,
  todoDetail,
  todoExtras,
  todoRow,
  todoTarget,
} from "/research/detail.js";
import { facet, masterDetail } from "/research/masterdetail.js";
import { parseHash, writeHash } from "/research/nav.js";
import {
  clear,
  emptyState,
  h,
  highlight,
  icon,
  imageUrl,
  isMaster,
  link,
  monthLabel,
  planSwatch,
  planTag,
  plural,
  route,
  statusChip,
  statusWord,
  store,
} from "/research/ui.js";

const enc = encodeURIComponent;

function textFilter(label, value, oninput) {
  return h("label", { class: "field find" }, h("span", { class: "sr-only" }, label), h("input", { type: "search", value, placeholder: label, autocomplete: "off", spellcheck: "false", oninput: (e) => oninput(e.target.value) }));
}

const countBy = (rows, keyOf) => {
  const counts = new Map();
  for (const row of rows) {
    const key = keyOf(row);
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  return counts;
};

// ---- DECISIONS ----------------------------------------------------------------------------------

async function decisionsPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/decisions`);
  const all = body.data.decisions.slice().reverse();
  const filters = { plan: params.get("plan") || "", layer: params.get("layer") || "", status: params.get("status") || "", q: params.get("q") || "" };
  const hashFor = (id) => route.decisions(P, { ...filters, sel: id });
  const md = masterDetail({
    hashFor,
    fullHref: (id) => route.decision(P, id),
    load: async (id) => ({ kind: "Decision", node: decisionDetail(ctx, (await api(`${base(P)}/decisions/${enc(id)}`)).data, { heading: "h2" }) }),
    hint: [h("p", null, "Select a decision to read it here."), h("p", { class: "muted small" }, "j and k move, Enter opens the page, Esc closes.")],
  });
  const matches = (d, f) =>
    (!f.plan || (d.plans || []).includes(f.plan)) &&
    (!f.layer || d.layer === f.layer) &&
    (!f.status || d.status === f.status) &&
    (!f.q || `${d.id} #${d.number} ${d.title} ${d.date}`.toLowerCase().includes(f.q.toLowerCase()));
  const shownRows = () => all.filter((d) => matches(d, filters));
  const facetItems = (key, values, label) => values.map((v) => ({ value: v, node: label ? label(v) : v, count: all.filter((d) => matches(d, { ...filters, [key]: v })).length }));
  const statuses = [...new Set(all.map((d) => d.status).filter(Boolean))].sort();
  const filterBox = h("div", { class: "facets" });
  const heading = h("div", { class: "md-title" });
  const list = h("div", { class: "md-list", role: "list", "aria-label": "Decisions" });
  const setFilter = (key) => (value) => {
    filters[key] = value;
    writeHash(hashFor(md.current()));
    draw();
  };
  function draw() {
    const rows = shownRows();
    clear(heading).append(h("h1", null, "Decisions"), h("span", { class: "count" }, rows.length === all.length ? String(all.length) : `${rows.length} of ${all.length}`));
    clear(filterBox).append(
      textFilter("Find by title or number", filters.q, (value) => {
        filters.q = value;
        writeHash(hashFor(md.current()));
        drawList();
        heading.lastChild.textContent = shownRows().length === all.length ? String(all.length) : `${shownRows().length} of ${all.length}`;
      }),
      facet("Plan", facetItems("plan", body.data.plans, (id) => planSwatch(id)), filters.plan, setFilter("plan")),
      body.data.layers.length > 1 ? facet("Layer", facetItems("layer", body.data.layers), filters.layer, setFilter("layer")) : null,
      statuses.length > 1 ? facet("Status", facetItems("status", statuses), filters.status, setFilter("status")) : null,
    );
    drawList();
  }
  function drawList() {
    const rows = shownRows();
    md.reset();
    md.setOrder(rows.map((d) => d.id));
    clear(list);
    if (!rows.length) {
      list.append(emptyState("No decision matches these filters.", "Clear a filter on the left."));
      return;
    }
    let month = null;
    let ul = null;
    for (const d of rows) {
      const m = (d.date || "").slice(0, 7);
      if (m !== month) {
        month = m;
        list.append(h("h2", { class: "month" }, monthLabel(m)));
        ul = h("ul", { class: "drows" });
        list.append(ul);
      }
      const row = decisionRow(ctx, d, { layer: !filters.layer && body.data.layers.length > 1, href: route.decision(P, d.id) });
      md.bind(d.id, row);
      ul.append(row);
    }
  }
  draw();
  const node = h("div", { class: "page md-page" }, h("div", { class: "md-grid" }, h("div", { class: "md-side" }, heading, filterBox), list, md.node));
  md.start(params.get("sel"));
  const keys = { ...md.keys, a: () => openDecisionAction("approve", md.current()), r: () => openDecisionAction("reject", md.current()) };
  return { node, title: "Decisions", select: (id) => md.select(id, { write: false }), keys };
}

// ---- EVIDENCE -----------------------------------------------------------------------------------

async function evListPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const body = await api(`${base(P)}/ev`);
  const names = labels(body.data.display);
  const all = body.data.ev;
  const filters = { label_state: params.get("label_state") || "", plan: params.get("plan") || "", q: params.get("q") || "" };
  let view = params.get("view") || store.get("evView", "list");
  const hashFor = (id) => route.evList(P, { ...filters, view: view === "list" ? "" : view, sel: id });
  const md = masterDetail({
    hashFor,
    fullHref: (id) => route.ev(P, id),
    load: async (id) => ({ kind: "Evidence", node: evDetail(ctx, (await api(`${base(P)}/ev/${enc(id)}`)).data, { heading: "h2" }) }),
    hint: [h("p", null, "Select an evidence page to read it here."), h("p", { class: "muted small" }, "j and k move, Enter opens the page, Esc closes.")],
  });
  const matches = (e, f) => (!f.label_state || e.labelState === f.label_state) && (!f.plan || (f.plan === "none" ? !e.plan : e.plan === f.plan)) && (!f.q || e.stem.toLowerCase().includes(f.q.toLowerCase()));
  const shownRows = () => all.filter((e) => matches(e, filters));
  const planCounts = (f) => countBy(all.filter((e) => matches(e, { ...f, plan: "" })), (e) => e.plan || "none");
  const heading = h("div", { class: "md-title" });
  const filterBox = h("div", { class: "facets" });
  const list = h("div", { class: "md-list" });
  const toggle = h("div", { class: "segmented", role: "group", "aria-label": "View" });
  let pageNode = null;
  const setView = (next) => {
    view = next;
    if (pageNode) pageNode.setAttribute("data-mode", next);
    store.set("evView", next);
    writeHash(hashFor(md.current()));
    draw();
  };
  const setFilter = (key) => (value) => {
    filters[key] = value;
    writeHash(hashFor(md.current()));
    draw();
  };
  function groups(rows) {
    const map = new Map();
    for (const e of rows) {
      const key = e.plan || "";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(e);
    }
    return [...map.entries()].sort(([a], [b]) => (a === "") - (b === "") || a.localeCompare(b));
  }
  function uniform(rows, keyOf) {
    const values = new Set(rows.map(keyOf));
    return values.size === 1 ? [...values][0] : undefined;
  }
  function listRow(e, cols) {
    const row = h(
      "li",
      { class: "ev-li" },
      h("a", { class: "row-link ev-stem", href: route.ev(P, e.stem) }, e.stem),
      cols.status ? h("span", { class: "cell" }, statusChip("ev", e.status)) : null,
      cols.label ? h("span", { class: "cell" }, statusChip("label", e.labelState, names)) : null,
      cols.figures ? h("span", { class: "cell n" }, e.figures.length ? `${e.figures.length} fig` : "") : null,
    );
    md.bind(e.stem, row);
    return row;
  }
  function tile(e) {
    const first = e.figures[0];
    const row = h(
      "li",
      { class: "tile" },
      h("a", { class: "row-link", href: route.ev(P, e.stem) }, first ? h("img", { src: imageUrl(P, first), alt: "", loading: "lazy" }) : h("span", { class: "thumb empty", "aria-hidden": "true" }, icon("image")), h("span", { class: "cap" }, e.stem)),
    );
    md.bind(e.stem, row);
    return row;
  }
  function drawList() {
    const rows = shownRows();
    md.reset();
    const grouped = groups(rows);
    const figuresFirst = (items) => (view === "figures" ? [...items.filter((e) => e.figures.length), ...items.filter((e) => !e.figures.length)] : items);
    md.setOrder(grouped.flatMap(([, g]) => figuresFirst(g).map((e) => e.stem)));
    clear(list);
    if (!rows.length) {
      list.append(emptyState("No evidence page matches.", "Clear a filter on the left."));
      return;
    }
    for (const [plan, items] of grouped) {
      const sameStatus = uniform(items, (e) => e.status);
      const sameLabel = uniform(items, (e) => e.labelState);
      const cols = { status: sameStatus === undefined, label: sameLabel === undefined, figures: view === "list" && uniform(items, (e) => e.figures.length) === undefined };
      const notes = [
        items.length > 1 && sameStatus !== undefined ? h("span", { class: "uniform" }, "all ", statusChip("ev", sameStatus)) : null,
        items.length > 1 && sameLabel !== undefined ? h("span", { class: "uniform" }, "labels ", statusChip("label", sameLabel, names)) : null,
      ];
      list.append(
        h(
          "section",
          { class: "ev-group" },
          h("header", { class: "group-head" }, plan ? planTag(P, plan) : h("span", { class: "no-plan" }, "No plan"), h("span", { class: "count" }, plural(items.length, "page")), notes),
          view === "figures"
            ? [
                items.some((e) => e.figures.length) ? h("ul", { class: "tiles" }, items.filter((e) => e.figures.length).map(tile)) : null,
                items.some((e) => !e.figures.length) ? h("ul", { class: "ev-lis no-figure" }, items.filter((e) => !e.figures.length).map((e) => listRow(e, { status: true, label: true, figures: false }))) : null,
              ]
            : h("ul", { class: "ev-lis" }, items.map((e) => listRow(e, cols))),
        ),
      );
    }
  }
  function draw() {
    const rows = shownRows();
    clear(heading).append(h("h1", null, "Evidence"), h("span", { class: "count" }, rows.length === all.length ? String(all.length) : `${rows.length} of ${all.length}`));
    clear(toggle).append(
      h("button", { type: "button", "aria-pressed": String(view === "list"), onclick: () => setView("list") }, icon("list"), "List"),
      h("button", { type: "button", "aria-pressed": String(view === "figures"), onclick: () => setView("figures") }, icon("grid"), "Figures"),
    );
    const counts = planCounts(filters);
    const planItems = [...counts.entries()].filter(([id]) => id !== "none").sort(([a], [b]) => a.localeCompare(b)).map(([id, n]) => ({ value: id, node: planSwatch(id), count: n }));
    if (counts.has("none")) planItems.push({ value: "none", label: "No plan", count: counts.get("none") });
    const stateItems = ["filled", "partial", "empty", "missing", "na"].map((s) => ({ value: s, label: names[s] || s, count: all.filter((e) => matches(e, { ...filters, label_state: s })).length })).filter((i) => i.count || i.value === filters.label_state);
    clear(filterBox).append(
      toggle,
      textFilter("Find by name", filters.q, (value) => {
        filters.q = value;
        writeHash(hashFor(md.current()));
        drawList();
      }),
      facet("Plan", planItems, filters.plan, setFilter("plan")),
      facet("Labels", stateItems, filters.label_state, setFilter("label_state")),
    );
    drawList();
  }
  draw();
  const node = h("div", { class: "page md-page", "data-view": "evidence", "data-mode": view }, h("div", { class: "md-grid" }, h("div", { class: "md-side" }, heading, filterBox), list, md.node));
  pageNode = node;
  md.start(params.get("sel"));
  return { node, title: "Evidence", select: (id) => md.select(id, { write: false }), keys: md.keys };
}

// ---- TODOS --------------------------------------------------------------------------------------

async function todosPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const includeDone = params.get("include_done") || "";
  const body = await api(`${base(P)}/todos`, { include_done: includeDone });
  const all = body.data.todos;
  const filters = { owner: params.get("owner") || "", source: params.get("source") || "", kind: params.get("kind") || "" };
  const hashFor = (id) => route.todos(P, { ...filters, include_done: includeDone, sel: id });
  const byId = new Map(all.map((t) => [t.id, t]));
  const md = masterDetail({
    hashFor,
    fullHref: (id) => (byId.get(id) && todoTarget(ctx, byId.get(id))) || route.todo(P, id),
    load: async (id) => {
      if (!byId.has(id)) throw Object.assign(new Error("This item is no longer listed."), { code: "NOT_FOUND" });
      const t = byId.get(id);
      return { kind: "TODO", node: todoDetail(ctx, t, { extras: await todoExtras(ctx, t), manualSha: body.data.manualFile.sha256, heading: "h2" }) };
    },
    hint: [h("p", null, "Select a TODO to see what it needs."), h("p", { class: "muted small" }, "j and k move, Enter opens the item, Esc closes.")],
  });
  const matches = (t, f) => (!f.owner || (f.owner === "user") === (t.owner === "user")) && (!f.source || t.source === f.source) && (!f.kind || t.kind === f.kind);
  const shownRows = () => all.filter((t) => matches(t, filters));
  const heading = h("div", { class: "md-title" });
  const filterBox = h("div", { class: "facets" });
  const list = h("div", { class: "md-list" });
  const manualSha = body.data.manualFile.sha256;
  const addForm = todoAddForm(ctx, body.data.manualFile);
  const setFilter = (key) => (value) => {
    filters[key] = value;
    writeHash(hashFor(md.current()));
    draw();
  };
  function groupBlock(kind, items) {
    const many = items.length > 5;
    const key = `todos.expanded.${kind}`;
    const expanded = !many || store.get(key, false) || filters.kind === kind;
    const head = h("header", { class: "group-head" }, h("h2", null, h("b", { class: "n" }, String(items.length)), " ", kindPhrase(kind, items.length)));
    const block = h("section", { class: "todo-group" }, head);
    if (!expanded) {
      block.append(
        h(
          "div",
          { class: "summary-row" },
          h("span", { class: "muted" }, "Collapsed: the same kind of item repeated."),
          h("button", { type: "button", class: "btn small", onclick: () => { store.set(key, true); drawList(); } }, `Show all ${items.length}`),
        ),
      );
      return block;
    }
    if (many) head.append(h("button", { type: "button", class: "btn small ghost", onclick: () => { store.set(key, false); drawList(); } }, "Collapse"));
    const ul = h("ul", { class: "todos" });
    for (const t of items) {
      // Wide screens select the row in the preview pane; narrow ones open the item's own page.
      const row = todoRow(ctx, t, { manualSha, href: isMaster() ? hashFor(t.id) : todoTarget(ctx, t) || route.todo(P, t.id) });
      md.bind(t.id, row);
      ul.append(row);
    }
    block.append(ul);
    return block;
  }
  function drawList() {
    const rows = shownRows();
    md.reset();
    clear(list).append(h("div", { class: "row-actions", "data-slot": "todo-add" }, addForm));
    if (!rows.length) {
      list.append(emptyState("Nothing here.", all.length ? "Clear a filter on the left." : "Derived items are computed from the notes; manual items come from the TODO note."));
      md.setOrder([]);
      return;
    }
    const groups = new Map();
    for (const t of rows) {
      if (!groups.has(t.kind)) groups.set(t.kind, []);
      groups.get(t.kind).push(t);
    }
    const ordered = [...groups.entries()].sort(([, a], [, b]) => Number(b.some((t) => t.owner === "user")) - Number(a.some((t) => t.owner === "user")) || a.length - b.length);
    const visible = [];
    for (const [kind, items] of ordered) {
      const block = groupBlock(kind, items);
      list.append(block);
      visible.push(...items.filter((t) => md.has(t.id)));
    }
    md.setOrder(visible.map((t) => t.id));
  }
  function draw() {
    const rows = shownRows();
    clear(heading).append(h("h1", null, "TODOs"), h("span", { class: "count" }, rows.length === all.length ? String(all.length) : `${rows.length} of ${all.length}`));
    const kinds = [...countBy(all.filter((t) => matches(t, { ...filters, kind: "" })), (t) => t.kind).entries()];
    clear(filterBox).append(
      facet("Owner", [["user", "Yours"], ["agent", "Agents"]].map(([v, l]) => ({ value: v, label: l, count: all.filter((t) => matches(t, { ...filters, owner: v })).length })), filters.owner, setFilter("owner")),
      facet("Kind", kinds.map(([k, n]) => ({ value: k, label: kindPhrase(k, 2), count: n })), filters.kind, setFilter("kind")),
      h("label", { class: "field check" }, h("input", { type: "checkbox", checked: includeDone ? true : null, onchange: (e) => (window.location.hash = route.todos(P, { ...filters, include_done: e.target.checked ? "true" : "" })) }), h("span", null, "Show ticked manual items")),
      body.data.manualFile.path ? h("p", { class: "muted small" }, "Manual items come from ", h("span", { title: body.data.manualFile.path }, "the TODO note"), ".") : null,
    );
    drawList();
  }
  draw();
  const node = h("div", { class: "page md-page" }, h("div", { class: "md-grid" }, h("div", { class: "md-side" }, heading, filterBox), list, md.node));
  // After a write the selected item may be gone: fall back to the first row instead of an error.
  md.start(byId.has(params.get("sel")) ? params.get("sel") : null);
  return { node, title: "TODOs", select: (id) => md.select(byId.has(id) ? id : null, { write: false }), keys: md.keys };
}

// One TODO as a full page: where the rows of a narrow screen lead, and the target of "Open page" for items with no page of their own.
async function todoPage(ctx) {
  const P = ctx.project;
  const body = await ctx.api(`${base(P)}/todos`, { include_done: "true" });
  const t = body.data.todos.find((item) => item.id === ctx.arg);
  const crumbs = h("nav", { class: "crumbs", "aria-label": "Breadcrumb" }, link(route.home(), "Research"), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.project(P), projectName(ctx)), h("span", { class: "sep", "aria-hidden": "true" }, "/"), link(route.todos(P, t ? { sel: t.id } : {}), "TODOs"));
  if (!t) return { node: h("div", { class: "page full-detail" }, crumbs, emptyState("This TODO is gone: it was done or its cause is resolved.", link(route.todos(P), "Back to the TODOs"))), title: "TODO" };
  const detail = todoDetail(ctx, t, { extras: await todoExtras(ctx, t), manualSha: body.data.manualFile.sha256, heading: "h1" });
  return { node: h("div", { class: "page full-detail" }, crumbs, detail), title: "TODO" };
}

// ---- SEARCH -------------------------------------------------------------------------------------

const MODE_NOTES = { fts: "full text", like: "substring match (short query)", name: "file names", none: "filters only" };

// A hit's own route decides what the preview shows.
async function previewByHref(ctx, href, hit) {
  const { api } = ctx;
  const P = ctx.project;
  if (!href) return { kind: "Image", node: h("article", { class: "detail" }, h("h2", { class: "detail-title" }, hit.stem), h("img", { class: "preview-image", src: imageUrl(P, hit.stem), alt: hit.stem })) };
  const { parts } = parseHash(href);
  const kind = parts[2];
  const arg = parts.slice(3).join("/");
  if (kind === "decision") return { kind: "Decision", node: decisionDetail(ctx, (await api(`${base(P)}/decisions/${enc(arg)}`)).data, { heading: "h2" }) };
  if (kind === "ev") return { kind: "Evidence", node: evDetail(ctx, (await api(`${base(P)}/ev/${enc(arg)}`)).data, { heading: "h2" }) };
  if (kind === "plan") return { kind: "Plan", node: planPreview(ctx, (await api(`${base(P)}/plans/${enc(arg)}`)).data) };
  const note = (await api(`${base(P)}/note/${enc(arg || hit.stem)}`)).data;
  return { kind: "Note", node: notePreview(ctx, { ...note.note, html: note.html }, { href }) };
}

async function searchPage(ctx) {
  const { api, params } = ctx;
  const P = ctx.project;
  const current = { q: params.get("q") || "", type: params.get("type") || "", plan: params.get("plan") || "", status: params.get("status") || "" };
  const hits = new Map();
  const hashFor = (id) => route.search(P, { ...current, sel: id });
  const md = masterDetail({
    hashFor,
    fullHref: (id) => hits.get(id)?.href || route.search(P, current),
    load: async (id) => {
      const hit = hits.get(id) || { stem: id.split("|")[0] };
      return previewByHref(ctx, hit.href || (id.startsWith("#/") ? id.split("|")[0] : null), hit);
    },
    hint: [h("p", null, current.q ? "Select a result to read the note here." : "Results open here as you select them."), h("p", { class: "muted small" }, "j and k move, Enter opens the page, Esc closes.")],
  });
  const results = h("div", { class: "results", "aria-live": "polite" });
  const summary = h("p", { class: "muted small result-summary" });
  const more = h("button", { type: "button", class: "btn", hidden: true }, "Load more");
  const terms = current.q.split(/\s+/);
  let cursor = null;
  const order = [];
  const load = async (append) => {
    const hasQuery = current.q || current.type || current.plan || current.status;
    if (!hasQuery) {
      results.replaceChildren(emptyState("Type a query, or pick a filter.", "Short queries (1-2 characters) use substring matching; longer ones use full text."));
      return;
    }
    const found = await api(`${base(P)}/search`, { ...current, cursor: append ? cursor : null });
    if (!append) {
      results.replaceChildren();
      md.reset();
      order.length = 0;
      hits.clear();
    }
    if (!found.data.hits.length && !append) results.append(emptyState("No note matches.", "Try fewer words, or remove a filter."));
    for (const hit of found.data.hits) {
      const id = `${hit.href || hit.path}|${hit.line || 0}`;
      hits.set(id, hit);
      order.push(id);
      const row = h(
        "article",
        { class: "hit" },
        h("div", { class: "hit-head" }, hit.href ? h("a", { href: hit.href, class: "row-link hit-title" }, hit.stem) : h("span", { class: "row-link hit-title" }, hit.stem), hit.type ? h("span", { class: "muted small" }, hit.type) : null, hit.status ? statusWord("muted", hit.status) : null, hit.entityId ? h("code", null, hit.entityId) : null),
        hit.text ? h("p", { class: "snippet" }, highlight(hit.text, terms)) : null,
        h("div", { class: "muted small path" }, `${hit.path}${hit.line ? ":" + hit.line : ""}`),
      );
      md.bind(id, row);
      results.append(row);
    }
    md.setOrder(order.slice());
    cursor = found.meta.nextCursor;
    more.hidden = !cursor;
    summary.textContent = `${plural(found.meta.total, "result")}, ${MODE_NOTES[found.data.matchMode] || found.data.matchMode}`;
  };
  more.addEventListener("click", () => load(true).catch(() => {}));
  const field = (name, label, props = {}) => h("label", { class: "field" }, h("span", null, label), h("input", { name, value: current[name], autocomplete: "off", ...props }));
  const form = h(
    "form",
    {
      class: "search-form",
      onsubmit: (e) => {
        e.preventDefault();
        const data = new FormData(e.target);
        window.location.hash = route.search(P, Object.fromEntries(data.entries()));
      },
    },
    h("label", { class: "field" }, h("span", null, "Query"), h("input", { name: "q", type: "search", value: current.q, autocomplete: "off", "aria-label": "Search query" })),
    field("type", "Type", { list: "note-types", placeholder: "any" }),
    h("datalist", { id: "note-types" }, ["plan", "exec-plan", "evidence", "evidence-appendix", "result", "decision", "theory", "execution-log"].map((t) => h("option", { value: t }))),
    field("plan", "Plan", { placeholder: "any" }),
    field("status", "Status", { placeholder: "any" }),
    h("button", { class: "btn primary", type: "submit" }, icon("search"), "Search"),
  );
  await load(false);
  const node = h(
    "div",
    { class: "page md-page" },
    h("div", { class: "md-grid" }, h("div", { class: "md-side" }, h("div", { class: "md-title" }, h("h1", null, "Search")), form, summary), h("div", { class: "md-list" }, results, h("div", { class: "more" }, more)), md.node),
  );
  md.start(params.get("sel"), { auto: Boolean(current.q) });
  return { node, title: current.q ? `Search: ${current.q}` : "Search", select: (id) => md.select(id, { write: false }), keys: md.keys };
}

export const LISTS = { decisions: decisionsPage, ev: evListPage, todos: todosPage, todo: todoPage, search: searchPage };
