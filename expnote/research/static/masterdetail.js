// Master-detail: a list on the left, the selected item's full detail in a preview pane on the right.
// Selecting writes `?sel=<id>` so reload and back keep it; below 1100px rows navigate to full pages.
import { clear, errorBox, h, icon, isMaster, skeleton } from "/research/ui.js";
import { writeHash } from "/research/nav.js";

// `load(id)` -> Promise<{kind, node}>; `fullHref(id)` is the full page; `hashFor(id|null)` is this page's own hash.
export function masterDetail({ load, fullHref, hashFor, hint }) {
  const rows = new Map();
  let order = [];
  let current = null;
  let token = 0;

  const kindLabel = h("span", { class: "preview-kind" });
  const openLink = h("a", { class: "open-full", href: "#/" }, "Open page", icon("external"));
  const closeButton = h("button", { type: "button", class: "btn small ghost", "aria-label": "Close preview (Esc)", title: "Close (Esc)", onclick: () => select(null) }, icon("close"));
  const body = h("div", { class: "preview-body" });
  const node = h("aside", { class: "preview", "aria-label": "Preview", "data-empty": "true" }, h("header", { class: "preview-head" }, kindLabel, openLink, closeButton), body);

  function showHint() {
    node.setAttribute("data-empty", "true");
    kindLabel.textContent = "";
    clear(body).append(h("div", { class: "preview-hint" }, hint));
  }
  showHint();

  function mark() {
    for (const [id, el] of rows) {
      if (id === current) el.setAttribute("aria-current", "true");
      else el.removeAttribute("aria-current");
    }
  }

  async function select(id, { write = true, push = false } = {}) {
    if (id === null || id === undefined || id === "") {
      current = null;
      token++;
      mark();
      showHint();
      if (write) writeHash(hashFor(null));
      return;
    }
    current = id;
    mark();
    const row = rows.get(id);
    if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
    if (write) writeHash(hashFor(id), { push });
    if (!isMaster()) return;
    const mine = ++token;
    node.setAttribute("data-empty", "false");
    openLink.setAttribute("href", fullHref(id));
    clear(body).append(skeleton(6));
    try {
      const loaded = await load(id);
      if (mine !== token) return;
      kindLabel.textContent = loaded.kind || "";
      clear(body).append(h("div", { class: "preview-enter" }, loaded.node));
      body.scrollTop = 0;
    } catch (error) {
      if (mine !== token) return;
      clear(body).append(errorBox(error));
    }
  }

  function move(delta) {
    if (!order.length) return false;
    const at = order.indexOf(current);
    const next = at < 0 ? (delta > 0 ? 0 : order.length - 1) : Math.max(0, Math.min(order.length - 1, at + delta));
    select(order[next]);
    return true;
  }

  // Wire one list row: a click selects it (wide screens); inner links such as plan tags still navigate.
  function bind(id, row) {
    rows.set(id, row);
    row.setAttribute("data-id", id);
    if (id === current) row.setAttribute("aria-current", "true");
    // Enter on a focused row link selects that row; once it is selected, Enter falls through to "open the page".
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || id === current || !isMaster()) return;
      const onLink = event.target.closest && event.target.closest(".row-link");
      if (!onLink) return;
      event.preventDefault();
      event.stopPropagation();
      select(id, { push: true });
    });
    row.addEventListener("click", (event) => {
      if (!isMaster() || event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
      const inner = event.target.closest ? event.target.closest("a, button, input, label, select, textarea") : null;
      if (inner && !inner.classList.contains("row-link")) return;
      event.preventDefault();
      select(id, { push: true });
    });
  }

  return {
    node,
    bind,
    select,
    reset: () => {
      rows.clear();
      order = [];
    },
    setOrder: (ids) => {
      order = ids;
    },
    // First paint: honour `sel`, otherwise open the first row on a wide screen.
    start(sel, { auto = true } = {}) {
      if (sel) select(sel, { write: false });
      else if (auto && isMaster() && order.length) select(order[0], { write: true });
    },
    current: () => current,
    has: (id) => rows.has(id),
    keys: {
      j: () => move(1),
      k: () => move(-1),
      Enter: () => {
        if (current === null) return false;
        window.location.hash = fullHref(current);
        return true;
      },
      Escape: () => {
        if (current === null) return false;
        select(null);
        return true;
      },
    },
  };
}

// A group of mutually exclusive filter buttons ("any" plus the options), each with a count.
export function facet(title, items, current, onPick) {
  return h(
    "div",
    { class: "facet" },
    h("h3", null, title),
    h(
      "ul",
      null,
      items.map((item) =>
        h(
          "li",
          null,
          h(
            "button",
            { type: "button", class: "facet-btn", "aria-pressed": item.value === current ? "true" : "false", onclick: () => onPick(item.value === current ? "" : item.value) },
            h("span", { class: "facet-label" }, item.node || item.label),
            item.count !== undefined ? h("span", { class: "count" }, String(item.count)) : null,
          ),
        ),
      ),
    ),
  );
}
