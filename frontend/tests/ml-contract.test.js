// The interface is tested against real server responses (shared/contracts/ml-model-report.*.example.json, trimmed copies of what the API returned),
// not against objects written by hand. The backend test test_predictive_contract.py checks that the API still returns this shape.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  confusionCells, histogramBars, importanceBars, learningGeometry, prGeometry, rankingBars, residualGeometry, rocGeometry, scatterGeometry,
} from '../src/features/predictive/charts.js';
import { explanationView } from '../src/features/explanation/explanation.js';
import { PROBLEM_LABEL, PROBLEM_SHORT, formatScore, headline, modelTitle, problemTypeOf } from '../src/features/predictive/format.js';

const load = (name) => JSON.parse(readFileSync(new URL(`../../shared/contracts/ml-model-report.${name}.example.json`, import.meta.url), 'utf8'));
const CASES = { binary: 'binary_classification', multiclass: 'multiclass_classification', regression: 'regression' };

for (const [name, expected] of Object.entries(CASES)) {
  test(`${name}: the kind of problem is read as a string from the report and from the model`, () => {
    const { report, model } = load(name);
    assert.equal(typeof report.problem, 'object');                      // the report carries it as an object ...
    assert.equal(problemTypeOf(report, model), expected);               // ... and the interface still gets a string
    assert.equal(problemTypeOf(null, model), expected);
    assert.equal(problemTypeOf({ problem: expected }, null), expected);
    assert.equal(problemTypeOf({}, {}), null);
    assert.ok(PROBLEM_LABEL[expected] && PROBLEM_SHORT[expected]);
  });

  test(`${name}: titles, headline and the text the pages print are strings or numbers, never objects`, () => {
    const { report, model } = load(name);
    for (const text of [modelTitle(model), headline(model), report.best.label ?? report.best.algorithm_label, report.best.primary_label, model.algorithm_label, model.name, model.target]) {
      assert.equal(typeof text, 'string');
    }
    assert.ok(Number.isFinite(report.evaluation.n));
    for (const row of report.ranking) { assert.equal(typeof row.label, 'string'); assert.ok(Number.isFinite(row.rank)); }
    for (const w of report.warnings ?? []) assert.ok(typeof w === 'string' || typeof w.message === 'string');
    for (const l of report.limitations ?? []) assert.equal(typeof l, 'string');
    assert.equal(typeof formatScore(model.cv_score), 'string');
  });

  test(`${name}: every explanation in the report can be shown`, () => {
    const { report } = load(name);
    const kinds = Object.keys(report.explanations);
    assert.ok(kinds.length >= 7, kinds.join(','));
    for (const kind of kinds) {
      const view = explanationView(report.explanations[kind]);
      assert.equal(view.available, true, `${kind}: ${view.reason}`);
      assert.equal(view.title, 'Explanation');
      assert.ok(view.summary && typeof view.summary === 'string');
    }
  });

  test(`${name}: the ranking, importance and learning-curve charts can be drawn from what the server sent`, () => {
    const { report } = load(name);
    const bars = rankingBars(report.ranking);
    assert.equal(bars.length, report.ranking.length);
    assert.ok(bars.every((b) => Number.isFinite(b.cv)));
    const importance = importanceBars(report.importance.permutation.features);
    assert.ok(importance.length > 0 && importance.every((b) => Number.isFinite(b.importance) && b.width >= 0 && b.width <= 1));
    const curve = learningGeometry(report.learning_curve, { flip: Boolean(report.best.lower_is_better) });
    assert.ok(curve && curve.trainPath.startsWith('M') && curve.validationPath.startsWith('M'));
  });
}

test('classification: confusion matrix, ROC and precision-recall are drawn from real curves', () => {
  for (const name of ['binary', 'multiclass']) {
    const ev = load(name).report.evaluation;
    const cm = confusionCells(ev.confusion_matrix);
    assert.ok(cm.labels.length >= 2 && cm.cells.length === cm.labels.length ** 2);
    assert.ok(cm.cells.every((c) => Number.isFinite(c.count) && c.share >= 0 && c.share <= 1));
    const roc = rocGeometry(ev.curves.roc);
    assert.ok(roc.series.length >= 1 && roc.series.every((s) => s.d.startsWith('M') && s.auc >= 0 && s.auc <= 1));
    assert.ok(roc.table.rows.length > 0);
    const pr = prGeometry(ev.curves.pr);
    assert.ok(pr.series.length >= 1 && pr.series.every((s) => s.d.startsWith('M')));
  }
  assert.equal(load('multiclass').report.evaluation.curves.roc.length, 3);          // one curve per outcome
});

test('regression: actual-against-predicted, residual plot and residual histogram are drawn from real values', () => {
  const ev = load('regression').report.evaluation;
  const sc = scatterGeometry(ev.scatter);
  assert.ok(sc.points.length > 0 && sc.points.every((p) => Number.isFinite(p.cx) && Number.isFinite(p.cy)));
  assert.ok(sc.domain[0] < sc.domain[1]);
  const res = residualGeometry(ev.scatter);
  assert.ok(res.points.length > 0 && Number.isFinite(res.zeroY));
  const hist = histogramBars(ev.residuals.histogram);
  assert.ok(hist && hist.length === ev.residuals.histogram.counts.length && hist.every((b) => b.height >= 0 && b.height <= 1));
  assert.ok(Number.isFinite(ev.typical_error.within_80) && Number.isFinite(ev.typical_error.within_95));
});

test('the real report keeps the SHAP statement honest: it is reported as not available, with a reason', () => {
  for (const name of Object.keys(CASES)) {
    const shap = load(name).report.importance.shap;
    assert.equal(shap.available, false);
    assert.ok(typeof shap.reason === 'string' && shap.reason.length > 10);
  }
});
