// Refresh the current page when a tab becomes visible again, without ever
// discarding what the user is in the middle of.
export const REVISIT_MIN_INTERVAL_MS = 5000;

const FIELDS = "input, textarea, select";
const OPEN_CONFIRM = ".confirm-panel, .confirm-slot > *";

function isDirty(field) {
  const tag = String(field.tagName || "").toUpperCase();
  if (tag === "SELECT") return Array.from(field.options || []).some((o) => o.selected !== o.defaultSelected);
  const type = String(field.type || "").toLowerCase();
  if (type === "checkbox" || type === "radio") return field.checked !== field.defaultChecked;
  return (field.value || "") !== (field.defaultValue || "");
}

export function hasWorkInProgress(scope) {
  if (scope.querySelector(OPEN_CONFIRM)) return true;
  return Array.from(scope.querySelectorAll(FIELDS)).some(isDirty);
}

// Returns the visibilitychange handler. `scope` is the page content element (not the top bar's search box).
export function createRevisitRefresh({ doc, scope, refresh, now = () => Date.now() }) {
  let last = -Infinity;
  return function onVisibilityChange() {
    if (doc.visibilityState !== "visible") return false;
    if (now() - last < REVISIT_MIN_INTERVAL_MS) return false;
    if (hasWorkInProgress(scope)) return false;
    last = now();
    refresh();
    return true;
  };
}
