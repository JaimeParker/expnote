// Lazy KaTeX rendering for server-emitted `.math[data-display]` elements (textContent = raw TeX).
// katex.render builds DOM and sets styles through CSSOM, which the CSP allows; never use innerHTML.
const KATEX_JS = "/research/katex/katex.mjs";
const KATEX_CSS = "/research/katex/katex.min.css";

let katexPromise = null;

function loadKatex() {
  if (!katexPromise) {
    katexPromise = (async () => {
      const mod = await import(KATEX_JS);
      try {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = KATEX_CSS;
        document.head.appendChild(link);
      } catch {
        // stylesheet is cosmetic; rendering still works without it
      }
      return mod.default || mod;
    })().catch(() => null);
  }
  return katexPromise;
}

export async function renderMath(root) {
  try {
    if (!root || !root.querySelectorAll) return;
    const pending = [...root.querySelectorAll(".math[data-display]")].filter((el) => !el.dataset.mathDone);
    if (!pending.length) return;
    for (const el of pending) el.dataset.mathDone = "pending";
    const katex = await loadKatex();
    for (const el of pending) {
      if (el.dataset.mathDone !== "pending") continue;
      const tex = el.textContent;
      el.dataset.mathDone = "1";
      if (!katex) continue; // sources stay visible as plain text
      const display = el.dataset.display === "1";
      try {
        katex.render(tex, el, {
          displayMode: display,
          throwOnError: true,
          strict: "ignore",
          trust: false,
          maxSize: 50,
          output: "htmlAndMathml",
        });
      } catch (err) {
        const mark = display ? "$$" : "$";
        el.textContent = `${mark}${tex}${mark}`;
        el.classList.add("math-error");
        el.title = String((err && err.message) || err);
      }
    }
  } catch {
    // fire-and-forget: never surface rendering problems to callers
  }
}
