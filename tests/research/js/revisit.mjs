// Unit checks for the visibilitychange refresh (static/revisit.js); prints a JSON report.
import { REVISIT_MIN_INTERVAL_MS, createRevisitRefresh } from "../../../expnote/research/static/revisit.js";

const checks = [];
const check = (name, ok) => checks.push({ name, ok: Boolean(ok) });

const field = (tagName, value, defaultValue) => ({ tagName, type: "text", value, defaultValue });
function page({ fields = [], confirmOpen = false } = {}) {
  return {
    querySelector: () => (confirmOpen ? {} : null),
    querySelectorAll: () => fields,
  };
}
function setup(scope) {
  const clock = { t: 100000 };
  const calls = { n: 0 };
  const doc = { visibilityState: "visible" };
  const onChange = createRevisitRefresh({ doc, scope, refresh: () => calls.n++, now: () => clock.t });
  return { clock, calls, doc, onChange };
}

{
  const s = setup(page({ fields: [field("INPUT", "", "")] }));
  s.onChange();
  check("refreshes when visible and clean", s.calls.n === 1);
}
{
  const s = setup(page());
  s.doc.visibilityState = "hidden";
  s.onChange();
  check("no refresh while hidden", s.calls.n === 0);
}
{
  const s = setup(page({ fields: [field("INPUT", "ok", "")] }));
  s.onChange();
  check("skipped when an input is dirty", s.calls.n === 0);
}
{
  const s = setup(page({ fields: [field("TEXTAREA", "draft", "")] }));
  s.onChange();
  check("skipped when a textarea is dirty", s.calls.n === 0);
}
{
  const select = { tagName: "SELECT", options: [{ selected: true, defaultSelected: false }] };
  const s = setup(page({ fields: [select] }));
  s.onChange();
  check("skipped when a select changed", s.calls.n === 0);
}
{
  const box = { tagName: "INPUT", type: "checkbox", checked: true, defaultChecked: false };
  const s = setup(page({ fields: [box] }));
  s.onChange();
  check("skipped when a checkbox changed", s.calls.n === 0);
}
{
  const s = setup(page({ confirmOpen: true }));
  s.onChange();
  check("skipped when a confirm is open", s.calls.n === 0);
}
{
  const s = setup(page());
  s.onChange();
  s.clock.t += REVISIT_MIN_INTERVAL_MS - 1;
  s.onChange();
  check("throttled inside the interval", s.calls.n === 1);
  s.clock.t += 1;
  s.onChange();
  check("refreshes again after the interval", s.calls.n === 2);
}
{
  const dirty = [field("INPUT", "x", "")];
  const s = setup(page({ fields: dirty }));
  s.onChange();
  dirty[0].value = "";
  s.onChange();
  check("a skipped refresh does not start the throttle", s.calls.n === 1);
}
console.log(JSON.stringify({ checks }));
