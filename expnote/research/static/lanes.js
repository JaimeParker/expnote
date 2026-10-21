// Plan lanes: one horizontal lane per plan, one dot per decision on a date axis.
// Everything is positioned with SVG attributes (x, y, cx, width); colour comes from CSS via data-lane.
import { clear, h, isMaster, laneOf, route, s, toneOf } from "/research/ui.js";

const DAY = 86_400_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LABEL_W = 132;
const STATUS_W = 128;
const PAD = 14;
const AXIS_H = 30;
const MIN_W = 720;
const HIT_R = 12;

const parse = (iso) => {
  const t = Date.parse(`${(iso || "").slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(t) ? null : t;
};

function buildLanes(plans, decisions) {
  const lanes = plans.map((plan) => ({
    plan,
    items: decisions.filter((d) => (d.plans || []).includes(plan.id) && parse(d.date) !== null),
  }));
  const first = (lane) => (lane.items.length ? Math.min(...lane.items.map((d) => parse(d.date))) : Infinity);
  return lanes.sort((a, b) => first(a) - first(b) || a.plan.id.localeCompare(b.plan.id));
}

function monthStarts(t0, t1) {
  const out = [];
  const cursor = new Date(t0);
  cursor.setUTCDate(1);
  cursor.setUTCHours(0, 0, 0, 0);
  for (let t = cursor.getTime(); t <= t1; ) {
    if (t >= t0) out.push(t);
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    t = cursor.getTime();
  }
  return out;
}

function weekStarts(t0, t1) {
  const out = [];
  const first = new Date(t0);
  const toMonday = (8 - first.getUTCDay()) % 7;
  for (let t = t0 + toMonday * DAY; t <= t1; t += 7 * DAY) out.push(t);
  return out;
}

export function planLanes({ project, plans, decisions, today }) {
  const lanes = buildLanes(plans, decisions);
  const dated = decisions.map((d) => parse(d.date)).filter((t) => t !== null);
  const holder = h("div", { class: "lanes-scroll" });
  if (!lanes.length || !dated.length) return h("div", { class: "empty" }, h("p", null, "No decision is linked to a plan yet."));
  const t0 = Math.min(...dated) - 2 * DAY;
  const t1 = Math.max(parse(today) || 0, ...dated) + 3 * DAY;

  function draw(width) {
    const big = typeof window.innerWidth === "number" && window.innerWidth >= 1800;
    const DOT_R = big ? 5.5 : 4.5;
    const LANE_H = big ? 38 : 34;
    const W = Math.max(width, MIN_W);
    const x0 = LABEL_W;
    const x1 = W - STATUS_W;
    const x = (t) => x0 + ((t - t0) / (t1 - t0)) * (x1 - x0);
    const H = AXIS_H + lanes.length * LANE_H + 6;
    const wide = isMaster();
    const grid = [];
    for (const t of weekStarts(t0, t1)) grid.push(s("line", { class: "tick-week", x1: x(t), x2: x(t), y1: AXIS_H - 4, y2: H }));
    const months = monthStarts(t0, t1);
    months.forEach((t, i) => {
      const d = new Date(t);
      const label = d.getUTCMonth() === 0 || i === 0 ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : MONTHS[d.getUTCMonth()];
      grid.push(s("line", { class: "tick-month", x1: x(t), x2: x(t), y1: 2, y2: H }), s("text", { class: "axis-label", x: x(t) + 5, y: 14 }, label));
    });
    const startDay = new Date(t0 + 2 * DAY);
    if (!months.length || x(months[0]) - x0 > 70) grid.push(s("text", { class: "axis-label", x: x0 + 2, y: 14 }, `${MONTHS[startDay.getUTCMonth()]} ${startDay.getUTCDate()}`));
    const todayT = parse(today);
    if (todayT !== null) grid.push(s("line", { class: "today", x1: x(todayT), x2: x(todayT), y1: AXIS_H - 4, y2: H }), s("text", { class: "axis-label today-label", x: x(todayT), y: AXIS_H - 8, "text-anchor": "end" }, "today"));

    const rows = lanes.map(({ plan, items }, index) => {
      const y0 = AXIS_H + index * LANE_H;
      const cy = y0 + LANE_H / 2;
      const xs = items.map((d) => x(parse(d.date)));
      const rowEnds = [-Infinity, -Infinity];
      const dots = items
        .slice()
        .sort((a, b) => parse(a.date) - parse(b.date) || a.number - b.number)
        .map((d) => {
          const px = x(parse(d.date));
          let row = rowEnds[0] <= rowEnds[1] ? 0 : 1;
          if (px - rowEnds[row] < DOT_R * 2.4 && px - rowEnds[1 - row] >= DOT_R * 2.4) row = 1 - row;
          rowEnds[row] = px;
          const py = cy + (row ? DOT_R + 0.5 : -DOT_R - 0.5);
          const awaiting = d.status === "proposed";
          const faded = d.status === "superseded" || d.status === "rejected";
          const note = `#${d.number} ${d.title || d.id} (${d.date}${d.status && d.status !== "confirmed" ? `, ${d.status}` : ""})`;
          return s(
            "a",
            { class: "dot-link", href: wide ? route.decisions(project, { sel: d.id }) : route.decision(project, d.id), "data-state": awaiting ? "awaiting" : faded ? "faded" : "done" },
            s("title", null, note),
            s("circle", { class: "hitbox", cx: px, cy: py, r: HIT_R }),
            s("circle", { class: "dot", cx: px, cy: py, r: DOT_R }),
          );
        });
      return s(
        "g",
        { class: "lane", "data-lane": laneOf(plan.id) },
        index % 2 ? s("rect", { class: "band", x: 0, y: y0, width: W, height: LANE_H }) : null,
        s("rect", { class: "lane-tick", x: 0, y: y0 + 8, width: 3, height: LANE_H - 16 }),
        s("a", { href: route.plan(project, plan.id) }, s("text", { class: "lane-label", x: PAD, y: cy + 5 }, plan.id), s("title", null, plan.title || plan.id)),
        xs.length ? s("line", { class: "lane-bar", x1: Math.min(...xs), x2: Math.max(...xs), y1: cy, y2: cy }) : null,
        dots,
        s("text", { class: "lane-status", "data-tone": toneOf("plan", plan.status), x: W - PAD, y: cy + 5, "text-anchor": "end" }, plan.status),
      );
    });

    clear(holder).append(s("svg", { class: "lanes", width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "group", "aria-label": "Decisions by plan, by date" }, grid, rows));
  }

  let drawn = 0;
  let measured = false;
  draw(1200);
  drawn = 1200;
  if (typeof ResizeObserver === "function") {
    new ResizeObserver((entries) => {
      const width = Math.floor(entries[0].contentRect.width);
      if (width <= 0 || (measured && Math.abs(width - drawn) <= 2)) return;
      drawn = width;
      draw(width);
      // A narrow screen scrolls inside the lanes: open at the right end, where the latest decisions are.
      if (!measured) holder.scrollLeft = holder.scrollWidth;
      measured = true;
    }).observe(holder);
  }
  return holder;
}

export function lanesLegend() {
  const mark = (state) => s("svg", { class: "legend-dot", width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": "true" }, s("circle", { class: "dot", "data-state": state, cx: 7, cy: 7, r: 4.5 }));
  return h(
    "div",
    { class: "lanes-legend" },
    h("span", null, mark("done"), "decision"),
    h("span", null, mark("awaiting"), "waits for your approval"),
    h("span", null, mark("faded"), "superseded"),
  );
}
