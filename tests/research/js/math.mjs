// renderMath degrades gracefully when KaTeX can't be loaded; prints a JSON report.
// usage: RESEARCH_STATIC=<dir without katex/> node --import ./register.mjs math.mjs
import { renderMath } from "../../../expnote/research/static/math.js";

const checks = [];
const check = (name, ok) => checks.push({ name, ok: Boolean(ok) });
const problems = [];
process.on("unhandledRejection", (e) => problems.push(String(e)));

const el = (tex, display) => ({
  textContent: tex,
  dataset: { display },
  classList: { add() {} },
});
const root = (els) => ({ querySelectorAll: () => els });

await renderMath(root([]));
check("no .math elements: returns without touching anything", true);

const math = [el("x^2", "0"), el("y", "1")];
await renderMath(root(math));
check("failed import leaves inline source", math[0].textContent === "x^2");
check("failed import leaves display source", math[1].textContent === "y");
check("elements marked done", math.every((m) => m.dataset.mathDone === "1"));
await renderMath(root(math));
check("re-call is a no-op", math[0].textContent === "x^2");
await renderMath(null);
check("null root tolerated", true);
check("no unhandled rejections", problems.length === 0);

console.log(JSON.stringify({ checks }));
