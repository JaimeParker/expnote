// Hash routing state shared by the shell and the pages.
// A selection (`?sel=`) inside a master-detail page is changed in place: no re-render, no lost scroll.

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

// The route without `sel`: two hashes with the same base show the same page.
export function baseOf(hash) {
  const { parts, params } = parseHash(hash);
  params.delete("sel");
  const query = params.toString();
  return parts.join("/") + (query ? `?${query}` : "");
}

export const nav = { baseKey: null, view: null, refresh: null };

// Rewrite the address bar without rendering again. `push` adds a history entry.
export function writeHash(hash, { push = false } = {}) {
  nav.baseKey = baseOf(hash);
  const history = window.history;
  if (history && history.pushState && history.replaceState) {
    (push ? history.pushState : history.replaceState).call(history, null, "", hash);
  } else {
    window.location.hash = hash;
  }
}
