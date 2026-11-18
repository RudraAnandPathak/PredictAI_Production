// Pure geometry for the charts: scales, ticks, paths and the mapping from a screen selection back to a server `select` descriptor.
// No statistics are computed here. Heights, positions and descriptors come from numbers the server already returned.

export function extent(values) {
  let lo = Infinity; let hi = -Infinity;
  for (const v of values) {
    if (typeof v !== 'number' || !Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return lo === Infinity ? [0, 1] : [lo, hi];
}

export function linearScale([d0, d1], [r0, r1]) {
  const span = d1 - d0 || 1;
  const f = (v) => r0 + ((v - d0) / span) * (r1 - r0);
  f.invert = (px) => d0 + ((px - r0) / (r1 - r0 || 1)) * span;
  f.domain = [d0, d1];
  return f;
}

/** "Nice" axis ticks (1, 2, 5 × 10^n) covering [lo, hi] with about `count` steps. */
export function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / Math.max(1, count);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag;
  const start = Math.ceil(lo / step) * step;
  const out = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

export function paddedDomain(lo, hi, pad = 0.05, floorAtZero = false) {
  const span = hi - lo || Math.abs(hi) || 1;
  const a = floorAtZero && lo >= 0 ? 0 : lo - span * pad;
  return [a, hi + span * pad];
}

export function linePath(points, x, y) {
  let d = '';
  let pen = false;
  for (const p of points) {
    if (p.y === null || p.y === undefined || !Number.isFinite(p.y)) { pen = false; continue; }   // a gap stays a gap
    d += `${pen ? 'L' : 'M'}${x(p.x).toFixed(2)},${y(p.y).toFixed(2)}`;
    pen = true;
  }
  return d;
}

export function nearest(points, px, pxOf) {
  let best = null; let bestD = Infinity;
  points.forEach((p, i) => {
    const d = Math.abs(pxOf(p) - px);
    if (d < bestD) { bestD = d; best = i; }
  });
  return best;
}

export function sortBars(bars, mode) {
  const copy = [...bars];
  if (mode === 'value_desc') copy.sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity));
  else if (mode === 'value_asc') copy.sort((a, b) => (a.value ?? Infinity) - (b.value ?? Infinity));
  else if (mode === 'label') copy.sort((a, b) => String(a.label).localeCompare(String(b.label)));
  return copy;
}

export function rectOf(a, b) {
  return { x0: Math.min(a.x, b.x), x1: Math.max(a.x, b.x), y0: Math.min(a.y, b.y), y1: Math.max(a.y, b.y) };
}

/** A scatter rectangle (pixels) -> the server's xy_range descriptor, using the data scales' inverses. */
export function xyRangeSelect(brush, rect, sx, sy) {
  const xs = [sx.invert(rect.x0), sx.invert(rect.x1)].sort((a, b) => a - b);
  const ys = [sy.invert(rect.y0), sy.invert(rect.y1)].sort((a, b) => a - b);
  return { type: 'xy_range', x_column: brush.x_column, y_column: brush.y_column, x_lo: xs[0], x_hi: xs[1], y_lo: ys[0], y_hi: ys[1] };
}

/** Bars (histogram bins or time periods) whose pixel span overlaps a dragged x-range -> the first and last picked element. */
export function overlappingIndexes(spans, x0, x1) {
  const hit = [];
  spans.forEach((s, i) => { if (s.x1 >= x0 && s.x0 <= x1) hit.push(i); });
  return hit;
}

/** Combine the select descriptors of several adjacent histogram bins into one range descriptor. */
export function mergeRangeSelects(selects) {
  const ranges = selects.filter((s) => s?.type === 'range');
  if (!ranges.length) return null;
  const first = ranges[0]; const last = ranges[ranges.length - 1];
  return { type: 'range', column: first.column, lo: first.lo, hi: last.hi, hi_inclusive: Boolean(last.hi_inclusive) };
}

export function mergePeriodSelects(selects) {
  const periods = selects.filter((s) => s?.type === 'period');
  if (!periods.length) return null;
  return { type: 'period', column: periods[0].column, start: periods[0].start, end: periods[periods.length - 1].end };
}

/** Heatmap colour: returns a CSS colour from a value, using only the scale the server sent. */
export function heatColor(value, scale) {
  if (value === null || value === undefined || !Number.isFinite(value)) return 'transparent';
  const { min = 0, max = 1, diverging = false } = scale || {};
  if (diverging) {
    const m = Math.max(Math.abs(min), Math.abs(max)) || 1;
    const t = Math.min(1, Math.abs(value) / m);
    return value >= 0 ? `color-mix(in srgb, var(--accent) ${Math.round(t * 85)}%, transparent)` : `color-mix(in srgb, var(--warm) ${Math.round(t * 85)}%, transparent)`;
  }
  const t = max > min ? Math.min(1, Math.max(0, (value - min) / (max - min))) : 0;
  return `color-mix(in srgb, var(--accent) ${Math.round(t * 85)}%, transparent)`;
}

/** Serialise an <svg> element to a standalone file (so a chart can be exported as an image). */
export function svgToString(svg) {
  const clone = svg.cloneNode(true);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}`;
}
