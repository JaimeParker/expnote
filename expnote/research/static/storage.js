import { clear, h } from "/research/ui.js";

function details(title, rows, format = String) {
  if (!rows?.length) return null;
  return h("details", null,
    h("summary", null, title, ` (${rows.length})`),
    h("ul", { class: "storage-paths" }, rows.map(row => h("li", null, format(row)))),
  );
}

export function storageControl(ctx) {
  const path = `/projects/${encodeURIComponent(ctx.project)}/storage`;
  const section = h("section", { class: "panel storage-panel", hidden: true, "aria-label": "Document storage" });
  const status = h("p", { class: "muted", role: "status" });
  const toggle = h("button", { class: "btn small", type: "button", "aria-expanded": "false" }, "Storage");

  function showPanel(open) {
    section.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  }

  function fields(data) {
    const choice = (label, value) => {
      const select = h("select", null,
        h("option", { value: "vault" }, "Obsidian Vault"),
        h("option", { value: "research" }, "Research"),
      );
      select.value = value;
      return { select, label: h("label", null, label, select) };
    };
    const ev = choice("EV and appendices", data.evidenceLocation);
    const res = choice("RES", data.resultsLocation);
    const review = h("div", { class: "storage-preview" });
    const preview = h("button", { type: "submit", class: "btn small" }, "Preview migration");
    const form = h("form", { class: "storage-fields" }, ev.label, res.label, preview);

    async function request(options, apply) {
      preview.disabled = ev.select.disabled = res.select.disabled = true;
      clear(review);
      status.textContent = apply ? "Applying migration…" : "Preparing preview…";
      try {
        const result = (await ctx.post(path, { ...options, dryRun: !apply })).data;
        status.textContent = result.applied ? "Storage updated." : "";
        if (result.applied) {
          fields(result);
          status.textContent = "Storage updated.";
          return;
        }
        if (!result.planHash) {
          status.textContent = "The selected locations are already in use.";
          return;
        }
        const movement = ({ source, target }) => `${source} → ${target}`;
        const confirm = h("button", { type: "button", class: "btn small primary", disabled: Boolean(result.conflicts.length) },
          "Apply migration");
        confirm.addEventListener("click", () => request({ ...options, expectedPlanHash: result.planHash }, true));
        review.append(
          ...[
            details("Documents to move", result.moves, movement),
            details("Images to copy", result.copyImages, movement),
            details("Image copies to archive", result.archiveImages),
            details("Notes with updated links", result.rewrite),
            details("Images already unavailable", result.missingImages),
            details("Conflicts", result.conflicts),
          ].filter(Boolean),
          confirm,
        );
        if (result.conflicts.length) status.textContent = "Resolve the conflicts and preview again.";
      } catch (error) {
        status.textContent = error.message || "Storage could not be updated.";
      } finally {
        preview.disabled = ev.select.disabled = res.select.disabled = false;
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      request({ ev: ev.select.value, res: res.select.value }, false);
    });
    for (const select of [ev.select, res.select]) {
      select.addEventListener("change", () => {
        clear(review);
        status.textContent = "";
      });
    }
    clear(section).append(
      h("h2", null, "Document storage"),
      form,
      h("p", { class: "small", title: data.absEvidenceAppendixDir }, `EV: ${data.absEvidenceDir}`),
      h("p", { class: "small" }, `RES: ${data.absResultsDir}`),
      status,
      review,
    );
  }

  toggle.addEventListener("click", async () => {
    if (!section.hidden) {
      showPanel(false);
      return;
    }
    showPanel(true);
    clear(section).append(status);
    status.textContent = "Loading storage…";
    try {
      const { data } = await ctx.api(path);
      if (data.interrupted) {
        status.textContent = `Interrupted migration. Run expnote project restore-storage ${ctx.project} --apply.`;
      } else if (data.layout === "split") {
        status.textContent = "";
        fields(data);
      } else {
        status.textContent = `This project uses the Vault layout. Run expnote project migrate-layout ${ctx.project} to preview the split layout.`;
      }
    } catch (error) {
      status.textContent = error.message || "Storage could not be loaded.";
    }
  });

  return h("div", { class: "storage-control" }, toggle, section);
}
