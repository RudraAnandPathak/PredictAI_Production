// Pure geometry for the modelling charts. Every position comes from numbers the server already returned (curves, counts, scores); nothing is
// re-estimated or smoothed here, and a missing value stays a gap. The same data is always available as a table beside the chart.
import { extent, linePath, linearScale, niceTicks, paddedDomain } from '../analytics/chartMath.js';

export const PLOT = Object.freeze({ w: 720, h: 380, l: 64, r: 20, t: 16, b: 48 });
export const AREA = Object.freeze({ x0: PLOT.l, x1: PLOT.w - PLOT.r, y0: PLOT.t, y1: PLOT.h - PLOT.b });

const pairs = (xs = [], ys = []) => xs.map((x, i) => ({ x, y: ys[i] })).filter((p) => typeof p.x === 'number' && typeof p.y === 'number');

/** ROC curves: one path per class on a fixed 0-1 square, plus the "no skill" diagonal. */
export function rocGeometry(curves = [], area = AREA) {
  const x = linearScale([0, 1], [area.x0, area.x1]);
  const y = linearScale([0, 1], [area.y1, area.y0]);
  return {
    x, y, ticks: [0, 0.25, 0.5, 0.75, 1],
    diagonal: { x1: x(0), y1: y(0), x2: x(1), y2: y(1) },
    series: curves.map((c) => ({ label: String(c.class), auc: c.auc, d: linePath(pairs(c.fpr, c.tpr), x, y), points: pairs(c.fpr, c.tpr).length })),
    table: { columns: ['Class', 'False positive rate', 'True positive rate'], rows: curves.flatMap((c) => pairs(c.fpr, c.tpr).map((p) => [String(c.class), p.x, p.y])) },
  };
}

/** Precision-recall curves; the dashed line is the share of real cases (what a model with no skill would score). */
export function prGeometry(curves = [], area = AREA) {
  const x = linearScale([0, 1], [area.x0, area.x1]);
  const y = linearScale([0, 1], [area.y1, area.y0]);
  return {
    x, y, ticks: [0, 0.25, 0.5, 0.75, 1],
    series: curves.map((c) => ({ label: String(c.class), ap: c.average_precision, prevalence: c.prevalence, d: linePath(pairs(c.recall, c.precision), x, y),
                                 baselineY: typeof c.prevalence === 'number' ? y(c.prevalence) : null })),
    table: { columns: ['Class', 'Recall', 'Precision'], rows: curves.flatMap((c) => pairs(c.recall, c.precision).map((p) => [String(c.class), p.x, p.y])) },
  };
}

/** Confusion matrix cells with a 0-1 shade taken from the server's row-normalised shares (so shading means "share of the true class"). */
export function confusionCells(cm) {
  if (!cm || !Array.isArray(cm.labels) || !Array.isArray(cm.counts)) return null;
  const n = cm.labels.length;
  const cells = [];
  for (let r = 0; r < n; r += 1) {
    const rowTotal = (cm.counts[r] ?? []).reduce((a, b) => a + (typeof b === 'number' ? b : 0), 0);
    for (let c = 0; c < n; c += 1) {
      const count = cm.counts[r]?.[c];
      const share = cm.row_normalised?.[r]?.[c];
      cells.push({ row: r, col: c, actual: String(cm.labels[r]), predicted: String(cm.labels[c]), count, share: typeof share === 'number' ? share : null, correct: r === c, rowTotal });
    }
  }
  return { labels: cm.labels.map(String), cells, size: n };
}

/** Horizontal importance bars. Widths are relative to the largest importance shown; a negative importance (shuffling helped) is drawn at zero and flagged. */
export function importanceBars(features = [], { top = 12 } = {}) {
  const rows = [...features].filter((f) => typeof f.importance === 'number').sort((a, b) => b.importance - a.importance).slice(0, top);
  const max = Math.max(...rows.map((r) => Math.max(r.importance, 0)), 0) || 1;
  return rows.map((f) => ({ feature: String(f.feature), importance: f.importance, std: typeof f.std === 'number' ? f.std : null, share: typeof f.share === 'number' ? f.share : null,
                            reliable: f.reliable === undefined ? null : Boolean(f.reliable), width: Math.max(0, f.importance) / max, negative: f.importance < 0 }));
}

/** Histogram bars from the server's edges/counts (n+1 edges, n counts). Returns null when the two do not line up. */
export function histogramBars(hist) {
  if (!hist || !Array.isArray(hist.edges) || !Array.isArray(hist.counts) || hist.edges.length !== hist.counts.length + 1) return null;
  const max = Math.max(...hist.counts, 1);
  return hist.counts.map((count, i) => ({ lo: hist.edges[i], hi: hist.edges[i + 1], count, height: count / max }));
}

/** Actual vs predicted: one shared domain so the "perfect prediction" diagonal is a true 45 degree line. */
export function scatterGeometry(scatter, area = AREA) {
  if (!scatter || !Array.isArray(scatter.actual) || scatter.actual.length !== scatter.predicted?.length) return null;
  const [lo, hi] = extent([...scatter.actual, ...scatter.predicted]);
  const domain = paddedDomain(lo, hi, 0.05);
  const x = linearScale(domain, [area.x0, area.x1]);
  const y = linearScale(domain, [area.y1, area.y0]);
  const points = scatter.actual.map((a, i) => ({ actual: a, predicted: scatter.predicted[i], residual: scatter.residual?.[i], cx: x(a), cy: y(scatter.predicted[i]) }));
  return { x, y, domain, ticks: niceTicks(domain[0], domain[1], 6), points, diagonal: { x1: x(domain[0]), y1: y(domain[0]), x2: x(domain[1]), y2: y(domain[1]) },
           shown: scatter.points ?? points.length, of: scatter.of ?? points.length,
           table: { columns: ['Actual', 'Predicted', 'Residual (actual - predicted)'], rows: points.map((p) => [p.actual, p.predicted, p.residual ?? null]) } };
}

/** Residuals against predicted values, centred on zero. */
export function residualGeometry(scatter, area = AREA) {
  if (!scatter || !Array.isArray(scatter.predicted) || scatter.predicted.length !== scatter.residual?.length) return null;
  const [px0, px1] = extent(scatter.predicted);
  const [r0, r1] = extent(scatter.residual);
  const reach = Math.max(Math.abs(r0), Math.abs(r1)) || 1;
  const x = linearScale(paddedDomain(px0, px1, 0.05), [area.x0, area.x1]);
  const y = linearScale([-reach * 1.08, reach * 1.08], [area.y1, area.y0]);
  return { x, y, xticks: niceTicks(x.domain[0], x.domain[1], 6), yticks: niceTicks(-reach, reach, 6), zeroY: y(0),
           points: scatter.predicted.map((p, i) => ({ predicted: p, residual: scatter.residual[i], cx: x(p), cy: y(scatter.residual[i]) })) };
}

/** Learning curve: training and validation score against the number of training rows, with the validation spread as a band.
 *  Error metrics arrive negated (so that higher is always better inside the engine); `flip` shows them as ordinary positive errors. */
export function learningGeometry(curve, { flip = false } = {}, area = AREA) {
  const sign = flip ? -1 : 1;
  const pts = (curve?.points ?? []).filter((p) => typeof p.train_rows === 'number' && typeof p.train_score === 'number' && typeof p.validation_score === 'number')
    .map((p) => ({ train_rows: p.train_rows, train_score: sign * p.train_score, validation_score: sign * p.validation_score, validation_std: p.validation_std ?? 0 }));
  if (!curve?.available || pts.length < 2) return null;
  const x = linearScale([pts[0].train_rows, pts[pts.length - 1].train_rows], [area.x0, area.x1]);
  const [lo, hi] = extent(pts.flatMap((p) => [p.train_score, p.validation_score, p.validation_score - p.validation_std, p.validation_score + p.validation_std]));
  const y = linearScale(paddedDomain(lo, hi, 0.08), [area.y1, area.y0]);
  const train = pts.map((p) => ({ x: p.train_rows, y: p.train_score }));
  const valid = pts.map((p) => ({ x: p.train_rows, y: p.validation_score }));
  const upper = pts.map((p) => `${x(p.train_rows).toFixed(2)},${y(p.validation_score + p.validation_std).toFixed(2)}`);
  const lower = [...pts].reverse().map((p) => `${x(p.train_rows).toFixed(2)},${y(p.validation_score - p.validation_std).toFixed(2)}`);
  return { x, y, xticks: pts.map((p) => p.train_rows), yticks: niceTicks(y.domain[0], y.domain[1], 5), trainPath: linePath(train, x, y), validationPath: linePath(valid, x, y),
           band: `M${upper.join('L')}L${lower.join('L')}Z`, points: pts,
           table: { columns: ['Training rows', 'Training score', 'Validation score', 'Validation spread'], rows: pts.map((p) => [p.train_rows, p.train_score, p.validation_score, p.validation_std]) } };
}

/** Partial dependence. Numeric inputs draw as lines over the grid; categorical inputs as points per category (a line between categories would imply an order). */
export function pdpGeometry(pdp, area = AREA) {
  if (!pdp?.available || !Array.isArray(pdp.grid) || !pdp.grid.length) return null;
  const numeric = pdp.grid.every((g) => typeof g === 'number');
  const names = Object.keys(pdp.series ?? {});
  const all = names.flatMap((k) => pdp.series[k]).concat(pdp.low ?? [], pdp.high ?? []);
  const [lo, hi] = extent(all);
  const y = linearScale(paddedDomain(lo, hi, 0.1), [area.y1, area.y0]);
  let x;
  if (numeric) { const [a, b] = extent(pdp.grid); x = linearScale([a, b], [area.x0, area.x1]); }
  else { const step = pdp.grid.length > 1 ? (area.x1 - area.x0) / pdp.grid.length : 0; x = (i) => area.x0 + step * (i + 0.5); x.domain = [0, pdp.grid.length - 1]; }
  const at = (i) => (numeric ? x(pdp.grid[i]) : x(i));
  const series = names.map((name) => ({ name, points: pdp.series[name].map((v, i) => ({ cx: at(i), cy: y(v), value: v, at: pdp.grid[i] })),
                                         d: numeric ? linePath(pdp.series[name].map((v, i) => ({ x: pdp.grid[i], y: v })), x, y) : '' }));
  const band = numeric && pdp.low && pdp.high && names.length === 1
    ? `M${pdp.grid.map((g, i) => `${x(g).toFixed(2)},${y(pdp.high[i]).toFixed(2)}`).join('L')}L${[...pdp.grid].reverse().map((g, i, arr) => `${x(g).toFixed(2)},${y(pdp.low[arr.length - 1 - i]).toFixed(2)}`).join('L')}Z` : null;
  return { numeric, x, y, yticks: niceTicks(y.domain[0], y.domain[1], 5), xticks: numeric ? niceTicks(...extent(pdp.grid), 6) : pdp.grid.map((g, i) => ({ i, label: String(g) })), series, band,
           table: { columns: [pdp.feature, ...names, ...(names.length === 1 ? ['Low (10th pct)', 'High (90th pct)'] : [])],
                    rows: pdp.grid.map((g, i) => [g, ...names.map((n) => pdp.series[n][i]), ...(names.length === 1 ? [pdp.low?.[i] ?? null, pdp.high?.[i] ?? null] : [])]) } };
}

/** Candidate ranking bars: cross-validation score with its spread, test score marked. Domain starts at zero for scores that cannot go negative so lengths stay honest. */
export function rankingBars(ranking = []) {
  const rows = ranking.filter((r) => typeof r.cv_primary_shown === 'number');
  const max = Math.max(...rows.flatMap((r) => [r.cv_primary_shown + (r.cv_std ?? 0), r.test_primary_shown ?? 0]), 0) || 1;
  return rows.map((r) => ({ id: r.id, label: r.label, rank: r.rank, cv: r.cv_primary_shown, std: r.cv_std ?? 0, test: r.test_primary_shown ?? null, overfitting: r.overfitting,
                            cvWidth: r.cv_primary_shown / max, stdWidth: (r.cv_std ?? 0) / max, testAt: r.test_primary_shown == null ? null : r.test_primary_shown / max, fitSeconds: r.fit_s, bytes: r.model_bytes }));
}

/** Prediction distribution: a bar per class (or a histogram for numbers) from a batch summary. */
export function distributionBars(summary) {
  const counts = summary?.prediction_counts;
  if (!Array.isArray(counts) || !counts.length) return null;
  const max = Math.max(...counts.map((c) => c.rows), 1);
  return counts.map((c) => ({ label: String(c.class), rows: c.rows, share: c.share, width: c.rows / max }));
}

/** Probability bars for one prediction. */
export function probabilityBars(probabilities = []) {
  return probabilities.filter((p) => typeof p.probability === 'number').map((p) => ({ label: String(p.class), probability: p.probability, width: Math.max(0, Math.min(1, p.probability)) }));
}

/** Local effects, signed: positive pushes the focus outcome up. Widths are relative to the largest effect. */
export function effectBars(effects = []) {
  const rows = effects.filter((e) => typeof e.effect === 'number');
  const max = Math.max(...rows.map((e) => Math.abs(e.effect)), 0) || 1;
  return rows.map((e) => ({ feature: String(e.feature), value: e.value, effect: e.effect, direction: e.effect >= 0 ? 'raises' : 'lowers', width: Math.abs(e.effect) / max }));
}
