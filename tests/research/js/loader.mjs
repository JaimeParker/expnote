// Maps the GUI's absolute "/research/x.js" imports onto the static directory.
import { pathToFileURL } from "node:url";
import { join } from "node:path";

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("/research/")) {
    const file = join(process.env.RESEARCH_STATIC, specifier.slice("/research/".length));
    return { url: pathToFileURL(file).href, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
