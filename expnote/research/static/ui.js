// Small DOM toolkit for the research GUI: element builder, status words, plan tags, panels, states.
// Text always goes through text nodes; only server-sanitised Markdown uses innerHTML.
// Layout is positioned by classes and SVG attributes only (the CSP forbids inline styles).

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value);
    } else if (key === "class") {
      el.setAttribute("class", value);
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(typeof child === "object" ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";

// SVG element builder: positions come from attributes (x, y, cx, width), never from inline styling.
export function s(tag, props, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

// ---- icons (stroke paths, 24px grid) ------------------------------------------------

const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
  doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  cross: '<path d="M6 6l12 12M18 6L6 18"/>',
  alert: '<path d="M12 3L2 21h20z"/><path d="M12 10v5M12 18v.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 7v10M18 11c0 4-6 3-12 6"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 012-2h8"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  gate: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-9 8"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
  bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13v2M15 13v2"/>',
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 115 0c0 1.8-2.5 2-2.5 4M12 17v.5"/>',
  grid: '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
  refresh: '<path d="M20 11a8 8 0 10-2 6"/><path d="M20 4v7h-7"/>',
};

export function icon(name, label) {
  const span = h("span", { class: "icon", "aria-hidden": label ? null : "true", role: label ? "img" : null, "aria-label": label || null });
  span.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">${ICONS[name] || ""}</svg>`;
  return span;
}

// ---- routes -------------------------------------------------------------------------

const enc = encodeURIComponent;
export const route = {
  home: () => "#/",
  project: (p, query) => `#/p/${enc(p)}${qs(query)}`,
  plan: (p, id) => `#/p/${enc(p)}/plan/${enc(id)}`,
  decisions: (p, query) => `#/p/${enc(p)}/decisions${qs(query)}`,
  decision: (p, id) => `#/p/${enc(p)}/decision/${enc(id)}`,
  evList: (p, query) => `#/p/${enc(p)}/ev${qs(query)}`,
  ev: (p, stem) => `#/p/${enc(p)}/ev/${enc(stem)}`,
  todos: (p, query) => `#/p/${enc(p)}/todos${qs(query)}`,
  search: (p, query) => `#/p/${enc(p)}/search${qs(query)}`,
  note: (p, stem) => `#/p/${enc(p)}/note/${enc(stem)}`,
  health: (p) => `#/p/${enc(p)}/health`,
};

export function qs(query) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query || {})) {
    if (value !== null && value !== undefined && value !== "") params.set(key, value);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function imageUrl(projectId, name) {
  return `/api/research/projects/${enc(projectId)}/image/${enc(name)}`;
}

// ---- per-viewer conveniences (never required for correctness) --------------------------

export const store = {
  get(key, fallback = null) {
    try {
      const raw = window.localStorage.getItem(`expnote.research.${key}`);
      return raw === null || raw === undefined ? fallback : JSON.parse(raw);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(`expnote.research.${key}`, JSON.stringify(value));
    } catch {
      /* private window or blocked storage: the page works without it */
    }
  },
};

// Master-detail needs room for a preview pane; below this width rows navigate to full pages.
export function isMaster() {
  return typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 1100px)").matches : true;
}

// ---- tones: one colour per meaning, shared by every status vocabulary ------------------

const TONES = {
  plan: { draft: "muted", "awaiting-approval": "warn", approved: "info", running: "info", "read-out": "violet", confirmed: "ok", closed: "ok", superseded: "muted", withdrawn: "muted" },
  decision: { proposed: "warn", confirmed: "ok", superseded: "muted", rejected: "bad" },
  exec: { draft: "muted", frozen: "info", running: "info", done: "ok", aborted: "bad" },
  ev: { draft: "muted", rendered: "warn", labelled: "ok", "label-na": "muted" },
  res: { draft: "muted", "first-read": "warn", confirmed: "ok", superseded: "muted", withdrawn: "muted" },
  label: { filled: "ok", partial: "warn", empty: "bad", missing: "muted", na: "muted" },
  run: { finished: "ok", completed: "ok", done: "ok", running: "info", failed: "bad", error: "bad", killed: "bad" },
  doctor: { ok: "ok", info: "info", warn: "warn", fail: "bad" },
  level: { FAIL: "bad", WARN: "warn", INFO: "info" },
  severity: { action: "bad", warn: "warn", info: "info" },
  deviation: { open: "warn", closed: "ok" },
  owner: { user: "violet", agent: "info" },
  gate: { ok: "ok", blocked: "bad" },
};

export function toneOf(kind, value) {
  return (TONES[kind] && TONES[kind][value]) || "muted";
}

// A small bordered tag. Use it only when the tag carries information the row does not already show.
export function chip(text, tone = "muted", extra = {}) {
  if (text === null || text === undefined || text === "") return null;
  return h("span", { class: "chip", "data-tone": tone, ...extra }, String(text));
}

// A status as a coloured dot plus the word itself.
export function statusWord(tone, text, extra = {}) {
  return h("span", { class: "status", "data-tone": tone, ...extra }, String(text));
}

export function statusChip(kind, value, display) {
  if (value === null || value === undefined || value === "") return statusWord("muted", "none");
  const label = (display && display[value]) || (kind === "level" ? String(value).toLowerCase() : value);
  return statusWord(toneOf(kind, value), label, { title: label === value ? null : value });
}

// ---- plan lanes: a stable colour per plan, used wherever a plan id appears ---------------------

export const LANE_COUNT = 8;
const laneOrder = new Map();

export function setLaneOrder(planIds) {
  laneOrder.clear();
  planIds.forEach((id, i) => laneOrder.set(id, i % LANE_COUNT));
}

export function laneOf(planId) {
  if (laneOrder.has(planId)) return laneOrder.get(planId);
  let sum = 0;
  for (const ch of String(planId)) sum = (sum * 31 + ch.charCodeAt(0)) % 997;
  return sum % LANE_COUNT;
}

export function planTag(project, planId, { href = route.plan(project, planId) } = {}) {
  return h("a", { class: "plan-tag", href, "data-lane": laneOf(planId), title: planId }, h("span", { class: "tick", "aria-hidden": "true" }), planId);
}

// The same tag without a link, for use inside buttons.
export function planSwatch(planId) {
  return h("span", { class: "plan-tag", "data-lane": laneOf(planId) }, h("span", { class: "tick", "aria-hidden": "true" }), planId);
}

export function planTags(project, ids) {
  return (ids || []).map((id) => planTag(project, id));
}

// ---- layout pieces ----------------------------------------------------------------------

export function link(href, ...children) {
  return h("a", { href }, ...children);
}

// A label with its value; replaces "A · B · C" strings.
export function pair(label, ...value) {
  return h("span", { class: "pair" }, h("span", { class: "k" }, label), " ", h("b", { class: "v" }, ...value));
}

// A collapsible block. `foldKey` remembers the open/closed choice for this viewer.
export function section(title, body, options = {}) {
  const { count, id, open = true, tone, aside, foldKey } = options;
  const remembered = foldKey ? store.get(`fold.${foldKey}`, null) : null;
  const isOpen = remembered === null ? open : remembered;
  const head = h(
    "summary",
    { class: "fold-head" },
    h("h2", null, title),
    count !== undefined && count !== null ? h("span", { class: "count", "data-tone": tone || "muted" }, String(count)) : null,
    aside || null,
  );
  const details = h("details", { class: "fold", id: id || null, open: isOpen ? true : null }, head, h("div", { class: "fold-body" }, body));
  if (foldKey) details.addEventListener("toggle", () => store.set(`fold.${foldKey}`, details.hasAttribute("open")));
  return details;
}

// A titled block that does not fold.
export function panel(title, body, { count, tone, className = "", aside } = {}) {
  return h(
    "section",
    { class: `panel ${className}`.trim() },
    h("header", { class: "panel-head" }, h("h2", null, title), count !== undefined && count !== null ? h("span", { class: "count", "data-tone": tone || "muted" }, String(count)) : null, aside || null),
    h("div", { class: "panel-body" }, body),
  );
}

export function pageHead({ crumbs = [], title, chips = [], meta = [], actions }) {
  return h(
    "div",
    { class: "page-head" },
    crumbs.length
      ? h(
          "nav",
          { class: "crumbs", "aria-label": "Breadcrumb" },
          crumbs.map((c, i) => [i ? h("span", { class: "sep", "aria-hidden": "true" }, "/") : null, c.href ? link(c.href, c.label) : h("span", null, c.label)]),
        )
      : null,
    h("div", { class: "title-row" }, h("h1", null, title), h("div", { class: "chips" }, chips)),
    meta.length ? h("div", { class: "meta-line" }, meta.filter(Boolean)) : null,
    h("div", { class: "actions", "data-slot": "page-actions" }, actions || null),
  );
}

export function kv(pairs) {
  return h(
    "dl",
    { class: "kv" },
    pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [h("dt", null, k), h("dd", null, v)]),
  );
}

export function table(headers, rows, options = {}) {
  const { empty = "Nothing here yet.", className = "" } = options;
  if (!rows.length) return emptyState(empty);
  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: className },
      h("thead", null, h("tr", null, headers.map((t) => h("th", { scope: "col" }, t)))),
      h("tbody", null, rows.map((cells) => h("tr", null, cells.map((c) => h("td", null, c))))),
    ),
  );
}

export function emptyState(text, hint) {
  return h("div", { class: "empty" }, h("p", null, text), hint ? h("p", { class: "muted small" }, hint) : null);
}

export function skeleton(lines = 4) {
  const rows = [];
  for (let i = 0; i < lines; i++) rows.push(h("div", { class: "skeleton-line", "data-w": String((i * 37) % 4) }));
  return h("div", { class: "skeleton", role: "status", "aria-label": "Loading" }, rows);
}

export function errorBox(error, retry) {
  const detail = error.remediation ? h("p", { class: "remediation" }, "Try: ", h("code", null, error.remediation)) : null;
  return h(
    "div",
    { class: "error-box", role: "alert" },
    h("div", { class: "error-title" }, icon("alert"), h("strong", null, error.code || "Error")),
    h("p", null, error.message || "Something went wrong."),
    detail,
    retry ? h("button", { type: "button", class: "btn", onclick: retry }, icon("refresh"), "Retry") : null,
  );
}

export function prose(html, className = "") {
  const box = h("div", { class: `prose ${className}`.trim() });
  box.innerHTML = html || "";
  return box;
}

export function copyButton(text, label = "Copy") {
  const button = h("button", { type: "button", class: "btn small ghost", title: label, "aria-label": label }, icon("copy"));
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      button.setAttribute("data-done", "true");
      setTimeout(() => button.removeAttribute("data-done"), 1200);
    } catch {
      /* clipboard unavailable: the command stays selectable */
    }
  });
  return button;
}

export function commandBlock(text) {
  return h("div", { class: "command" }, h("code", null, text), copyButton(text, "Copy command"));
}

// ---- small formatters ---------------------------------------------------------------------

export const short = (sha) => (sha ? sha.slice(0, 8) : "");
export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-10-21" -> "Oct 21"; anything else is returned untouched.
export function dayLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}` : iso || "";
}

export function monthLabel(iso) {
  const m = /^(\d{4})-(\d{2})/.exec(iso || "");
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "Undated";
}

export function relTime(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  const steps = [[60, "s"], [3600, "m"], [86400, "h"], [Infinity, "d"]];
  const divisors = [1, 60, 3600, 86400];
  for (let i = 0; i < steps.length; i++) {
    if (Math.abs(seconds) < steps[i][0]) {
      const value = Math.max(0, Math.round(seconds / divisors[i]));
      return seconds < 5 ? "just now" : `${value}${steps[i][1]} ago`;
    }
  }
  return iso;
}

export function highlight(text, terms) {
  const clean = terms.map((t) => t.trim()).filter(Boolean);
  if (!clean.length) return [text];
  const escaped = clean.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  return text.split(pattern).map((part, i) => (i % 2 ? h("mark", null, part) : part));
}
