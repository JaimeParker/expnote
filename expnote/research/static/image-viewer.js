import { h, icon } from "/research/ui.js";

const localImage = (image) => image?.getAttribute("src")?.startsWith("/api/research/");
const shortcuts = new WeakMap();

export function reviewReader(content, shortcut = null) {
  const reading = h("div", { class: "review-reading" }, content);
  const root = h("div", { class: "review-reader" }, h("div", { class: "review-layout" }, reading));
  if (shortcut) shortcuts.set(root, shortcut);
  return root;
}

function imageAt(target) {
  const link = target.closest?.("a");
  const image = target.closest?.("img") || link?.querySelector("img");
  if (!localImage(image) || image.closest(".image-canvas")) return null;
  // Keep links authored around an image working as links.
  return !link || link.getAttribute("href") === image.getAttribute("src") ? image : null;
}

export function wireImageViewer(scope) {
  let active = null;
  function close(restore = true) {
    if (!active) return false;
    active.close(restore);
    active = null;
    return true;
  }
  scope.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const image = imageAt(event.target);
    if (!image) return;
    event.preventDefault();
    const root = image.closest(".review-reader");
    if (root && active?.root === root) {
      active.show(image);
      return;
    }
    close(false);
    if (root) active = openReview(root, image, close);
    else openImage(image);
  });
  if (typeof MutationObserver === "function") {
    new MutationObserver(() => {
      if (active && !scope.contains(active.root)) close(false);
    }).observe(scope, { childList: true, subtree: true });
  }
  return close;
}

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const lastName = (value) => value.split("/").pop();

function figureOf(image) {
  return {
    src: image.getAttribute("src"),
    alt: image.alt || "Research figure",
    name: image.closest("figure")?.querySelector("figcaption")?.textContent.trim()
      || lastName(image.alt || "Research figure"),
  };
}

function button(label, text, action, glyph = null) {
  return h("button", { type: "button", class: "btn small", "aria-label": label, title: label, onclick: action },
    glyph ? icon(glyph) : text);
}

// A shared canvas for the inline reader and the standalone image dialog.
function imageCanvas() {
  const message = h("p", { class: "image-status", role: "status" });
  const node = h("div", { class: "image-canvas", tabindex: "0", role: "group", "aria-label": "Image canvas" }, message);
  let image, drag;
  let scale = 1;
  let width = 0, height = 0;
  let x = 0.5, y = 0.5;

  function draw() {
    if (!image?.naturalWidth || !node.clientWidth || !node.clientHeight) return;
    const fit = Math.min(1, node.clientWidth / image.naturalWidth, node.clientHeight / image.naturalHeight);
    width = image.naturalWidth * fit * scale;
    height = image.naturalHeight * fit * scale;
    const edgeX = Math.min(0.5, node.clientWidth / (2 * width));
    const edgeY = Math.min(0.5, node.clientHeight / (2 * height));
    x = clamp(x, edgeX, 1 - edgeX);
    y = clamp(y, edgeY, 1 - edgeY);
    image.style.width = width + "px";
    image.style.transform = `translate(${node.clientWidth / 2 - x * width}px, ${node.clientHeight / 2 - y * height}px)`;
    image.hidden = false;
    message.hidden = true;
  }

  function fit() {
    scale = 1;
    x = y = 0.5;
    draw();
  }

  function zoom(factor) {
    scale = clamp(scale * factor, 1, 16);
    draw();
  }

  function pan(dx, dy) {
    if (!width || !height) return;
    x -= dx / width;
    y -= dy / height;
    draw();
  }

  node.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !image?.naturalWidth) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    node.setPointerCapture(event.pointerId);
    node.focus({ preventScroll: true });
  });
  node.addEventListener("pointermove", (event) => {
    if (drag?.id !== event.pointerId) return;
    pan(event.clientX - drag.x, event.clientY - drag.y);
    drag.x = event.clientX;
    drag.y = event.clientY;
  });
  node.addEventListener("lostpointercapture", () => { drag = null; });
  node.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    switch (event.key) {
      case "=": case "+": zoom(1.25); break;
      case "-": zoom(0.8); break;
      case "0": fit(); break;
      case "ArrowLeft": pan(40, 0); break;
      case "ArrowRight": pan(-40, 0); break;
      case "ArrowUp": pan(0, 40); break;
      case "ArrowDown": pan(0, -40); break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
  });

  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(draw) : null;
  observer?.observe(node);
  return {
    node,
    controls: [
      button("Zoom out (−)", "−", () => zoom(0.8)),
      button("Fit image (0)", "Fit", fit),
      button("Zoom in (+)", "+", () => zoom(1.25)),
    ],
    show(figure) {
      image?.remove();
      const next = h("img", { alt: figure.alt, draggable: "false", hidden: true });
      image = next;
      width = height = 0;
      message.textContent = "Loading image…";
      message.hidden = false;
      fit();
      next.addEventListener("load", () => { if (image === next) draw(); }, { once: true });
      next.addEventListener("error", () => {
        if (image === next) message.textContent = "Image unavailable";
      }, { once: true });
      node.append(next);
      next.src = figure.src;
    },
    close() { observer?.disconnect(); },
  };
}

function splitter(layout) {
  const node = h("div", {
    class: "review-divider", role: "separator", tabindex: "0",
    "aria-label": "Image pane width", "aria-orientation": "vertical",
    "aria-valuemin": "35", "aria-valuemax": "65", "aria-valuenow": "50",
  });
  let share = 50;
  function setShare(next) {
    share = clamp(Math.round(next), 35, 65);
    layout.style.setProperty("--image-share", share + "%");
    node.setAttribute("aria-valuenow", share);
  }
  function follow(event) {
    const bounds = layout.getBoundingClientRect();
    setShare(100 * (bounds.right - event.clientX) / bounds.width);
  }
  node.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    node.setPointerCapture(event.pointerId);
  });
  node.addEventListener("pointermove", (event) => {
    if (node.hasPointerCapture(event.pointerId)) follow(event);
  });
  node.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") setShare(share + 4);
    else if (event.key === "ArrowRight") setShare(share - 4);
    else return;
    event.preventDefault();
  });
  return node;
}

function jumpTo(reading, target) {
  for (let node = target; node !== reading; node = node.parentElement) {
    if (node.tagName === "DETAILS") node.open = true;
  }
  reading.scrollTop += target.getBoundingClientRect().top - reading.getBoundingClientRect().top - 8;
  const focus = target.querySelector("summary") || target;
  if (!focus.hasAttribute("tabindex")) focus.setAttribute("tabindex", "-1");
  focus.focus({ preventScroll: true });
}

function readingPosition(reading) {
  const top = reading.getBoundingClientRect().top;
  const blocks = reading.querySelectorAll("h1, h2, h3, h4, p, li, summary, figure, .reading-table, pre");
  return Array.from(blocks).find((block) => {
    const rect = block.getBoundingClientRect();
    return rect.height && rect.bottom > top;
  });
}

function openReview(root, original, dismiss) {
  const layout = root.firstElementChild;
  const reading = layout.firstElementChild;
  const entries = new Map();
  for (const image of reading.querySelectorAll("img")) {
    if (!localImage(image)) continue;
    const figure = figureOf(image);
    if (!entries.has(figure.src)) entries.set(figure.src, figure);
  }
  const figures = [...entries.values()];
  const canvas = imageCanvas();
  let current = -1;
  const previous = button("Previous figure", "‹", () => select(current - 1));
  const next = button("Next figure", "›", () => select(current + 1));
  const options = figures.map((figure, i) => h("option", { value: String(i) }, `${i + 1} / ${figures.length} · ${figure.name}`));
  const picker = h("select", {
    "aria-label": "Figures on this page", onchange: () => select(Number(picker.value)),
  }, options);
  const close = button("Close image (Esc)", null, () => dismiss(), "close");
  const divider = splitter(layout);
  const shortcut = shortcuts.get(root);
  const action = shortcut?.target
    ? button(shortcut.label, shortcut.label, () => jumpTo(reading, shortcut.target))
    : shortcut?.href ? h("a", { class: "btn small", href: shortcut.href }, shortcut.label) : null;
  action?.classList.add("review-shortcut");
  const dock = h("aside", { class: "review-dock", "aria-label": "Figure viewer" },
    h("div", { class: "review-toolbar" },
      h("div", { class: "review-figure-nav" }, figures.length > 1 ? previous : null, picker, figures.length > 1 ? next : null, close),
      h("div", { class: "review-zoom" }, canvas.controls, action)),
    canvas.node);
  let trigger = null;

  function select(index) {
    if (!figures[index] || index === current) return;
    current = index;
    picker.value = String(index);
    picker.title = figures[index].name;
    previous.disabled = index === 0;
    next.disabled = index === figures.length - 1;
    canvas.show(figures[index]);
  }

  function show(image) {
    trigger = image.closest("a") || image;
    select(figures.findIndex((figure) => figure.src === image.getAttribute("src")));
    canvas.node.focus({ preventScroll: true });
  }

  layout.append(divider, dock);
  layout.setAttribute("data-open", "");
  reading.setAttribute("tabindex", "0");
  reading.setAttribute("role", "region");
  reading.setAttribute("aria-label", "Document");
  layout.scrollIntoView({ block: "start", inline: "nearest" });
  reading.scrollTop += original.getBoundingClientRect().top - reading.getBoundingClientRect().top - 12;
  show(original);
  return {
    root,
    show,
    close(restore) {
      const anchor = restore ? readingPosition(reading) : null;
      const focusInViewer = dock.contains(document.activeElement) || divider === document.activeElement;
      canvas.close();
      dock.remove();
      divider.remove();
      layout.removeAttribute("data-open");
      reading.removeAttribute("tabindex");
      reading.removeAttribute("role");
      reading.removeAttribute("aria-label");
      if (focusInViewer && restore) trigger.focus({ preventScroll: true });
      anchor?.scrollIntoView({ block: "start", inline: "nearest" });
    },
  };
}

function openImage(original) {
  const figure = figureOf(original);
  const canvas = imageCanvas();
  const dialog = h("dialog", { class: "image-viewer", "aria-label": figure.alt });
  dialog.append(h("div", { class: "image-viewer-tools" },
    canvas.controls, button("Close image (Esc)", "Close", () => dialog.close())), canvas.node);
  dialog.addEventListener("close", () => {
    canvas.close();
    dialog.remove();
  }, { once: true });
  document.body.append(dialog);
  dialog.showModal();
  canvas.show(figure);
}
