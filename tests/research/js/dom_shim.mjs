// A tiny DOM, just enough to run the research GUI modules under Node (no browser here).
const listeners = new WeakMap();

export class Node_ {
  constructor(type) {
    this.nodeType = type;
    this.childNodes = [];
    this.parent = null;
  }
  get firstChild() { return this.childNodes[0] || null; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  appendChild(child) { return this._add(child); }
  _add(child) {
    if (child.parent) child.parent.removeChild(child);
    child.parent = this;
    this.childNodes.push(child);
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parent = null;
    return child;
  }
  replaceWith(...nodes) {
    const parent = this.parent;
    if (!parent) return;
    const i = parent.childNodes.indexOf(this);
    this.parent = null;
    parent.childNodes.splice(i, 1, ...nodes);
    nodes.forEach((n) => { n.parent = parent; });
  }
}

export class Text_ extends Node_ {
  constructor(text) { super(3); this.data = String(text); }
  get textContent() { return this.data; }
}

export class Element_ extends Node_ {
  constructor(tag) {
    super(1);
    this.tagName = tag.toUpperCase();
    this.attrs = {};
    this.raw = null;
    this.hidden = false;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  append(...nodes) { nodes.forEach((n) => this._add(typeof n === "string" ? new Text_(n) : n)); }
  replaceChildren(...nodes) {
    this.childNodes.forEach((c) => { c.parent = null; });
    this.childNodes = [];
    this.raw = null;
    this.append(...nodes);
  }
  set innerHTML(html) { this.replaceChildren(); this.raw = String(html); }
  get innerHTML() { return this.raw ?? ""; }
  set textContent(text) { this.replaceChildren(new Text_(text)); }
  get textContent() { return this.raw ?? this.childNodes.map((c) => c.textContent).join(""); }
  addEventListener(type, fn) {
    const map = listeners.get(this) || {};
    (map[type] ||= []).push(fn);
    listeners.set(this, map);
  }
  dispatch(type, event = {}) { ((listeners.get(this) || {})[type] || []).forEach((fn) => fn({ target: this, preventDefault() {}, ...event })); }
  focus() {}
  select() {}
  blur() {}
  query(predicate, out = []) {
    for (const child of this.children) {
      if (predicate(child)) out.push(child);
      child.query(predicate, out);
    }
    return out;
  }
  byClass(name) { return this.query((e) => (e.attrs.class || "").split(/\s+/).includes(name)); }
  byTag(tag) { return this.query((e) => e.tagName === tag.toUpperCase()); }
}

export function installDom() {
  const ids = {};
  for (const id of ["main", "topnav", "projectSwitch", "searchInput", "searchForm", "runsLink", "topbar", "helpButton"]) {
    ids[id] = new Element_(id === "searchInput" ? "input" : "div");
  }
  const windowListeners = {};
  const location = { hash: "" };
  const body = new Element_("body");
  globalThis.document = {
    createElement: (tag) => new Element_(tag),
    createElementNS: (_ns, tag) => new Element_(tag),
    createTextNode: (t) => new Text_(t),
    getElementById: (id) => ids[id],
    addEventListener() {},
    body,
    title: "",
  };
  globalThis.window = {
    location,
    addEventListener: (type, fn) => { (windowListeners[type] ||= []).push(fn); },
    scrollTo() {},
    // Wide screen: master-detail previews render, so the smoke run covers them.
    matchMedia: () => ({ matches: true }),
  };
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: async () => {} } }, configurable: true });
  globalThis.FormData = class { constructor(form) { this.form = form; } entries() { return []; } };
  return {
    ids,
    location,
    fireHashChange: () => (windowListeners.hashchange || []).forEach((fn) => fn()),
  };
}

export function walk(node, visit) {
  visit(node);
  (node.childNodes || []).forEach((c) => walk(c, visit));
}

const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function toHtml(node, depth = 0) {
  const pad = "  ".repeat(depth);
  if (node.nodeType === 3) return `${pad}${esc(node.data)}\n`;
  const attrs = Object.entries(node.attrs).map(([k, v]) => ` ${k}="${esc(v).replace(/"/g, "&quot;")}"`).join("");
  const tag = node.tagName.toLowerCase();
  const inner = node.raw !== null && node.raw !== "" ? `${pad}  <!-- server html -->${node.raw.slice(0, 80)}...\n` : node.childNodes.map((c) => toHtml(c, depth + 1)).join("");
  return `${pad}<${tag}${attrs}>\n${inner}${pad}</${tag}>\n`;
}
