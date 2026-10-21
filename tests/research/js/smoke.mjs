// Renders every GUI page against a running server and reports failures as JSON.
// usage: RESEARCH_STATIC=<dir> node --import ./register.mjs smoke.mjs <baseUrl> <project>
import { installDom, toHtml, walk } from "./dom_shim.mjs";

const [base, project] = process.argv.slice(2);
const dom = installDom();
const realFetch = globalThis.fetch;
globalThis.fetch = (url, options) => realFetch(new URL(url, base), options);
const problems = [];
process.on("unhandledRejection", (e) => problems.push(`unhandledRejection: ${e && e.stack}`));

const api = async (path) => (await (await fetch(`/api/research${path}`)).json());
const P = `/projects/${encodeURIComponent(project)}`;
const enc = encodeURIComponent;

const plans = (await api(`${P}/plans?include_archived=true`)).data.plans.map((p) => p.id);
const decisions = (await api(`${P}/decisions`)).data.decisions;
const evs = (await api(`${P}/ev`)).data.ev.map((e) => e.stem);
const routes = [
  ["home", "#/"],
  ["project", `#/p/${enc(project)}`],
  ["project-filtered", `#/p/${enc(project)}?status=closed`],
  ["decisions", `#/p/${enc(project)}/decisions`],
  ["decisions-layer", `#/p/${enc(project)}/decisions?layer=${enc(decisions[0]?.layer || "")}`],
  ["ev-list", `#/p/${enc(project)}/ev`],
  ["todos", `#/p/${enc(project)}/todos`],
  ["todos-user", `#/p/${enc(project)}/todos?owner=user`],
  ["todos-kind", `#/p/${enc(project)}/todos?kind=ev-unmapped`],
  ["decisions-sel", `#/p/${enc(project)}/decisions?sel=${enc(decisions[decisions.length - 1]?.id || "")}`],
  ["ev-figures", `#/p/${enc(project)}/ev?view=figures`],
  ["ev-sel", `#/p/${enc(project)}/ev?sel=${enc(evs[0] || "")}`],
  ["search", `#/p/${enc(project)}/search?q=${enc(process.env.SEARCH_QUERY || "plan")}`],
  ["search-empty", `#/p/${enc(project)}/search`],
  ["health", `#/p/${enc(project)}/health`],
  ...[...new Set(plans)].map((id) => [`plan ${id}`, `#/p/${enc(project)}/plan/${enc(id)}`]),
  ...decisions.slice(-6).map((d) => [`decision ${d.id}`, `#/p/${enc(project)}/decision/${enc(d.id)}`]),
  ...evs.map((s) => [`ev ${s}`, `#/p/${enc(project)}/ev/${enc(s)}`]),
  ["bad-route", "#/nonsense"],
];

await import("/research/research.js");
const main = dom.ids.main;
const settle = async () => {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 10));
    const busy = main.getAttribute("aria-busy") === "true";
    const loading = main.byClass("skeleton").length > 0 || main.byClass("skeleton-card").length > 0;
    if (!busy && !loading && i > 1) return;
  }
  problems.push("timeout waiting for page");
};
await settle();

const results = [];
for (const [name, hash] of routes) {
  dom.location.hash = hash;
  const start = performance.now();
  dom.fireHashChange();
  await settle();
  const errors = main.byClass("error-box").map((e) => e.textContent);
  let nodes = 0;
  walk(main, () => nodes++);
  if (process.env.DUMP && name === process.env.DUMP) process.stderr.write(toHtml(main));
  const previews = main.byClass("preview-enter").length;
  const h1 = main.byTag("h1")[0];
  results.push({ name, hash, ms: Math.round(performance.now() - start), nodes, previews, h1: h1 ? h1.textContent : null, errors, title: document.title });
}
const failures = results.filter((r) => (r.errors.length && r.name !== "bad-route") || (!r.h1 && r.name !== "bad-route"));
console.log(JSON.stringify({ pages: results.length, failures, problems, results }, null, 0));
process.exit(failures.length || problems.length ? 1 : 0);
