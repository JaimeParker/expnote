import { h } from "/research/ui.js";

export function wireImageViewer(scope) {
  scope.addEventListener("click", (event) => {
    const original = event.target.closest?.("img");
    if (!original || !original.getAttribute("src")?.startsWith("/api/research/")) return;
    event.preventDefault();
    openImage(original);
  });
}

function openImage(original) {
  const image = h("img", { src: original.src, alt: original.alt, draggable: "false" });
  const viewport = h("div", { class: "image-viewer-viewport", tabindex: "0" }, image);
  const dialog = h("dialog", { class: "image-viewer", "aria-label": original.alt || "Research image" });
  let scale = 1;
  let drag = null;

  function size(factor) {
    const oldWidth = image.width || viewport.clientWidth;
    const oldHeight = image.height || viewport.clientHeight;
    const x = (viewport.scrollLeft + viewport.clientWidth / 2) / oldWidth;
    const y = (viewport.scrollTop + viewport.clientHeight / 2) / oldHeight;
    const fit = Math.min(1, viewport.clientWidth / image.naturalWidth,
                         viewport.clientHeight / image.naturalHeight);
    if (!Number.isFinite(fit) || !fit || !image.naturalWidth) return;
    scale = Math.max(1, Math.min(16, factor));
    image.width = Math.round(image.naturalWidth * fit * scale);
    viewport.scrollLeft = x * image.width - viewport.clientWidth / 2;
    viewport.scrollTop = y * image.height - viewport.clientHeight / 2;
  }

  function button(label, action) {
    const control = h("button", { type: "button" }, label);
    control.addEventListener("click", action);
    return control;
  }

  dialog.append(h("div", { class: "image-viewer-tools" },
    button("Zoom out", () => size(scale / 1.25)),
    button("Zoom in", () => size(scale * 1.25)),
    button("Fit", () => size(1)),
    button("Close", () => dialog.close())), viewport);
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY,
             left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener("pointermove", (event) => {
    if (drag?.pointer !== event.pointerId) return;
    viewport.scrollLeft = drag.left + drag.x - event.clientX;
    viewport.scrollTop = drag.top + drag.y - event.clientY;
  });
  viewport.addEventListener("lostpointercapture", () => { drag = null; });
  image.addEventListener("load", () => size(1), { once: true });
  dialog.addEventListener("close", () => dialog.remove(), { once: true });
  document.body.append(dialog);
  dialog.showModal();
  if (image.complete) size(1);
}
