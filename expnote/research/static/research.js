// expnote research GUI: shell, hash router and API client. Pages live in pages.js.
import { clear, errorBox, h, route, skeleton } from "/research/ui.js";
import { PAGES } from "/research/pages.js";

const API = "/api/research";

export class ApiError extends Error {
  constructor(error, status) {
    super(error.message || "request failed");
    this.code = error.code || "ERROR";
    this.remediation = error.remediation || null;
    this.status = status;
  }
}

export async function api(path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
  }
  const url = `${API}${path}${query.toString() ? `?${query}` : ""}`;
  let response;
  try {
    response = await fetch(url, { headers: { Accept: "application/json" } });
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

export const state = { projects: [], features: {}, project: null };

const main = document.getElementById("main");
const topnav = document.getElementById("topnav");
const switchBox = document.getElementById("projectSwitch");
const searchInput = document.getElementById("searchInput");
let navigation = 0;

// ---- routing ------------------------------------------------------------------------------

export function parseHash(hash) {
  const [pathPart, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const parts = pathPart.split("/").filter(Boolean).map((p) => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p;
    }
  });
  return { parts, params: new URLSearchParams(query) };
}

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

async function show() {
  // `#section` inside a rendered note is an in-page anchor, not a route.
  const hash = window.location.hash;
  if (hash && !hash.startsWith("#/")) {
    scrollToAnchor(hash);
    return;
  }
  const token = ++navigation;
  const parsed = parseHash(window.location.hash);
  const match = resolveRoute(parsed);
  state.project = match && match.ctx.project ? match.ctx.project : state.project;
  updateChrome(parsed);
  if (!match) {
    clear(main).append(errorBox({ code: "NOT_FOUND", message: "There is no such page.", remediation: "Use the navigation above." }));
    document.title = "Not found · expnote research";
    return;
  }
  clear(main).append(skeleton(6));
  main.setAttribute("aria-busy", "true");
  try {
    const view = await match.page({ ...match.ctx, state, api });
    if (token !== navigation) return;
    clear(main).append(view.node);
    document.title = `${view.title} · expnote research`;
    if (!view.keepScroll) window.scrollTo(0, 0);
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
  ["TODOs", (p) => route.todos(p), ["todos"]],
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
  for (const [label, href, kinds] of SECTIONS) {
    const active = kinds.length ? kinds.includes(current) : current === "" || current === "plan";
    topnav.append(h("a", { href: href(project), "aria-current": active ? "page" : null }, label));
  }
}

function wireSearch() {
  document.getElementById("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const project = state.project || (state.projects[0] && state.projects[0].id);
    const text = searchInput.value.trim();
    if (project) window.location.hash = route.search(project, { q: text });
  });
  document.addEventListener("keydown", (event) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target.tagName || "").toUpperCase());
    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if (event.key === "Escape" && event.target === searchInput) {
      searchInput.blur();
    }
  });
}

async function start() {
  wireSearch();
  window.addEventListener("hashchange", show);
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
