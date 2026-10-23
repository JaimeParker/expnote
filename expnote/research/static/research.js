// expnote research GUI: shell, hash router and API client. Pages live in pages.js.
import { baseOf, nav, parseHash } from "/research/nav.js";
import { PAGES } from "/research/pages.js";
import { createRevisitRefresh } from "/research/revisit.js";
import { clear, errorBox, h, icon, route, setLaneOrder, skeleton } from "/research/ui.js";

const API = "/api/research";

export class ApiError extends Error {
  constructor(error, status) {
    super(error.message || "request failed");
    this.code = error.code || "ERROR";
    this.remediation = error.remediation || null;
    this.details = error.details || {};
    this.retryable = Boolean(error.retryable);
    this.status = status;
  }
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { ...options, headers: { Accept: "application/json", ...(options.headers || {}) } });
  } catch (cause) {
    throw new ApiError({ code: "NETWORK", message: "The server did not answer.", remediation: "Is `expnote web` still running?" }, 0);
  }
  let body = null;
  try {
    body = await response.json();
  } catch {
    throw new ApiError({ code: `HTTP_${response.status}`, message: `The server answered ${response.status} without JSON.` }, response.status);
  }
  if (!body.ok) throw new ApiError(body.error || {}, response.status);
  return body;
}

export async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
  }
  return request(`${API}${path}${query.toString() ? `?${query}` : ""}`);
}

// The per-process write token is templated into the page as a <meta>; read it when a write happens.
function writeToken() {
  const meta = typeof document.querySelector === "function" ? document.querySelector('meta[name="expnote-token"]') : null;
  return (meta && meta.getAttribute("content")) || "";
}

// A GUI write (design 7.2). The body carries `expectedSha256`; `dryRun: true` only plans.
export async function post(path, body) {
  return request(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Expnote-Token": writeToken() },
    body: JSON.stringify(body),
  });
}

export const state = { projects: [], features: {}, project: null };

const main = document.getElementById("main");
const topnav = document.getElementById("topnav");
const switchBox = document.getElementById("projectSwitch");
const searchInput = document.getElementById("searchInput");
let navigation = 0;

// ---- routing ------------------------------------------------------------------------------

export { parseHash };

function resolveRoute({ parts, params }) {
  if (!parts.length) return { page: PAGES.home, ctx: { params } };
  if (parts[0] !== "p" || !parts[1]) return null;
  const project = parts[1];
  const kind = parts[2];
  const arg = parts.slice(3).join("/");
  const ctx = { project, params, arg };
  if (!kind) return { page: PAGES.project, ctx };
  const page = PAGES[kind];
  return page ? { page, ctx } : null;
}

function scrollToAnchor(hash) {
  const id = decodeURIComponent(hash.slice(1));
  const target = document.getElementById(id);
  if (target && target.scrollIntoView) target.scrollIntoView();
}

// After a write: rebuild the current page from fresh data without a loading flash.
async function refreshPage() {
  nav.baseKey = null;
  await show({ silent: true });
}
nav.refresh = refreshPage;

const laneLoaded = new Set();

// Plan colours follow the plan list order, so fetch it once per project.
async function ensureLanes(project) {
  if (!project || laneLoaded.has(project)) return;
  try {
    const body = await api(`/projects/${encodeURIComponent(project)}/plans`, { include_archived: "true" });
    setLaneOrder(body.data.plans.map((p) => p.id));
    laneLoaded.add(project);
  } catch {
    /* colours fall back to a hash of the plan id */
  }
}

async function show({ silent = false } = {}) {
  // `#section` inside a rendered note is an in-page anchor, not a route.
  const hash = window.location.hash;
  if (hash && !hash.startsWith("#/")) {
    scrollToAnchor(hash);
    return;
  }
  const parsed = parseHash(window.location.hash);
  // Only `?sel=` changed on a master-detail page: move the selection, keep the page.
  if (nav.view && nav.view.select && baseOf(hash) === nav.baseKey) {
    nav.view.select(parsed.params.get("sel") || null);
    return;
  }
  const token = ++navigation;
  if (!silent) nav.view = null;
  const match = resolveRoute(parsed);
  state.project = match && match.ctx.project ? match.ctx.project : state.project;
  updateChrome(parsed);
  if (!match) {
    clear(main).append(errorBox({ code: "NOT_FOUND", message: "There is no such page.", remediation: "Use the navigation above." }));
    document.title = "Not found · expnote research";
    return;
  }
  // A refresh after a write keeps what is on screen until the fresh page is ready.
  const scrolledTo = silent ? window.scrollY || 0 : 0;
  if (!silent) clear(main).append(skeleton(6));
  main.setAttribute("aria-busy", "true");
  try {
    await ensureLanes(match.ctx.project);
    const view = await match.page({ ...match.ctx, state, api, post });
    if (token !== navigation) return;
    clear(main).append(view.node);
    nav.view = view;
    nav.baseKey = baseOf(window.location.hash);
    document.title = `${view.title} · expnote research`;
    if (silent) window.scrollTo(0, scrolledTo);
    else if (!view.keepScroll) window.scrollTo(0, 0);
  } catch (error) {
    if (token !== navigation) return;
    clear(main).append(errorBox(error, () => show()));
    document.title = "Error · expnote research";
  } finally {
    if (token === navigation) main.removeAttribute("aria-busy");
  }
}

// ---- chrome: project switcher, section links, search ----------------------------------------

function renderSwitcher() {
  clear(switchBox).append(h("span", { class: "sr-only" }, "Project"));
  if (!state.projects.length) return;
  if (state.projects.length === 1) {
    const only = state.projects[0];
    switchBox.append(h("a", { class: "project-name", href: route.project(only.id) }, only.name));
    return;
  }
  const select = h(
    "select",
    { "aria-label": "Project", onchange: (e) => (window.location.hash = route.project(e.target.value)) },
    state.projects.map((p) => h("option", { value: p.id, selected: p.id === state.project ? true : null }, p.vaultOk ? p.name : `${p.name} (offline)`)),
  );
  switchBox.append(select);
}

const SECTIONS = [
  ["Overview", (p) => route.project(p), []],
  ["Decisions", (p) => route.decisions(p), ["decisions", "decision"]],
  ["Evidence", (p) => route.evList(p), ["ev"]],
  ["TODOs", (p) => route.todos(p), ["todos", "todo"]],
  ["Search", (p) => route.search(p), ["search"]],
  ["Health", (p) => route.health(p), ["health"]],
];

function updateChrome({ parts }) {
  const project = parts[0] === "p" ? parts[1] : null;
  renderSwitcher();
  clear(topnav);
  if (!project) {
    topnav.setAttribute("hidden", "");
    return;
  }
  topnav.removeAttribute("hidden");
  const current = parts[2] || "";
  let activeLink = null;
  for (const [label, href, kinds] of SECTIONS) {
    const active = kinds.length ? kinds.includes(current) : current === "" || current === "plan";
    const anchor = h("a", { href: href(project), "aria-current": active ? "page" : null }, label);
    if (active) activeLink = anchor;
    topnav.append(anchor);
  }
  // On a narrow screen the tabs scroll: keep the current one in view and fade the cut edge.
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      if (activeLink) topnav.scrollLeft = Math.max(0, (activeLink.offsetLeft || 0) - 16);
      markTabOverflow();
    });
  }
}

function markTabOverflow() {
  const more = topnav.scrollWidth - topnav.clientWidth - topnav.scrollLeft > 4;
  if (more) topnav.setAttribute("data-more", "");
  else topnav.removeAttribute("data-more");
}

const KEYS = [
  ["/", "Search notes"],
  ["j  k", "Next and previous item in a list"],
  ["Enter", "Open the selected item as a page"],
  ["Esc", "Close the preview, or leave the search box"],
  ["[  ]", "Previous and next tab on a plan"],
  ["a  r", "Approve or reject the selected proposed decision (a preview comes first)"],
  ["?", "Show or hide this help"],
];

function buildHelp() {
  const close = h("button", { type: "button", class: "btn small ghost", "aria-label": "Close help", onclick: () => toggleHelp(false) }, icon("close"));
  const box = h(
    "div",
    { class: "help", role: "dialog", "aria-label": "Keyboard shortcuts", hidden: true },
    h("div", { class: "help-head" }, h("h2", null, "Keyboard"), close),
    h("dl", null, KEYS.map(([keys, what]) => [h("dt", null, keys.split("  ").map((k) => h("kbd", null, k))), h("dd", null, what)])),
    h("p", { class: "muted small" }, "Keys do nothing while you type in a field. Every change is previewed and needs your click on Apply."),
  );
  document.body.append(box);
  return { box, close };
}

let help = null;
function toggleHelp(open) {
  help = help || buildHelp();
  const show = open === undefined ? help.box.hasAttribute("hidden") : open;
  if (show) {
    help.box.removeAttribute("hidden");
    help.close.focus();
  } else {
    help.box.setAttribute("hidden", "");
  }
}

function wireKeys() {
  topnav.addEventListener("scroll", markTabOverflow);
  window.addEventListener("resize", markTabOverflow);
  document.getElementById("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const project = state.project || (state.projects[0] && state.projects[0].id);
    const text = searchInput.value.trim();
    if (project) window.location.hash = route.search(project, { q: text });
  });
  const helpButton = document.getElementById("helpButton");
  if (helpButton) helpButton.addEventListener("click", () => toggleHelp());
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target.tagName || "").toUpperCase());
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === "Escape") {
      if (help && !help.box.hasAttribute("hidden")) toggleHelp(false);
      else if (event.target === searchInput) {
        if (searchInput.value) searchInput.value = "";
        else searchInput.blur();
      }
      else if (!typing && nav.view && nav.view.keys && nav.view.keys.Escape && nav.view.keys.Escape()) event.preventDefault();
      return;
    }
    if (typing) return;
    if (event.key === "/") {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if (event.key === "?") {
      event.preventDefault();
      toggleHelp();
    } else if (nav.view && nav.view.keys && nav.view.keys[event.key]) {
      // Enter on a link or button keeps its own meaning, except on a list row link.
      const onControl = event.key === "Enter" && event.target.closest && event.target.closest("a, button, summary") && !event.target.closest(".row-link");
      if (onControl) return;
      if (nav.view.keys[event.key]()) event.preventDefault();
    }
  });
}

async function start() {
  wireKeys();
  window.addEventListener("hashchange", () => show());
  // A tab left open goes stale; refresh it on return unless the user has work in progress.
  document.addEventListener("visibilitychange", createRevisitRefresh({ doc: document, scope: main, refresh: refreshPage }));
  try {
    const body = await api("/projects");
    state.projects = body.data.projects;
    state.features = body.data.features || {};
    const runs = document.getElementById("runsLink");
    if (state.features.runApp) runs.removeAttribute("hidden");
    const active = state.projects.find((p) => p.active) || state.projects[0];
    state.project = state.project || (active && active.id) || null;
  } catch (error) {
    clear(main).append(errorBox(error, () => window.location.reload()));
    return;
  }
  if (!window.location.hash) window.location.hash = "#/";
  else await show();
}

start();
