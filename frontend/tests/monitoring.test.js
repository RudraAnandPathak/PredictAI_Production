import test from 'node:test';
import assert from 'node:assert/strict';
import {
  driftRows, driftSentence, outcomeProblem, forecastProblem, forecastRows, methodRows, forecastHeadline, buildScenarios, scenarioChange, driftLabel, driftTone, psiText, MAX_SCENARIOS,
} from '../src/features/monitoring/format.js';

test('drift rows list the biggest change first and keep uncompared columns last', () => {
  const rows = driftRows({ columns: { a: { status: 'stable', psi: 0.01, kind: 'numeric' }, b: { status: 'significant', psi: 0.4, kind: 'numeric' }, c: { status: 'insufficient_data' }, d: { status: 'moderate', psi: 0.15 } } });
  assert.deepEqual(rows.map((r) => r.name), ['b', 'd', 'a', 'c']);
  assert.equal(psiText(null), '—');
  assert.equal(psiText(0.12345), '0.123');
});

test('drift sentences never claim more than the server found', () => {
  assert.match(driftSentence({ status: 'stable', drifted_columns: [] }), /No input column changed/);
  assert.match(driftSentence({ status: 'insufficient_data', drifted_columns: [] }), /not enough data/);
  assert.match(driftSentence({ status: 'significant', drifted_columns: ['x1'] }), /1 input column changed/);
  assert.equal(driftLabel('nope'), 'nope');
  assert.equal(driftTone('nope'), 'muted');
});

test('outcomes are checked before sending', () => {
  assert.ok(outcomeProblem('', false));
  assert.ok(outcomeProblem('abc', true));
  assert.equal(outcomeProblem('12.5', true), null);
  assert.equal(outcomeProblem('yes', false), null);
  assert.ok(outcomeProblem('x'.repeat(121), false));
});

test('forecast form validation', () => {
  assert.ok(forecastProblem({ date: '', measure: 'm', agg: 'mean', horizon: 6 }));
  assert.ok(forecastProblem({ date: 'd', measure: '', agg: 'mean', horizon: 6 }));
  assert.equal(forecastProblem({ date: 'd', measure: '', agg: 'count', horizon: 6 }), null);
  assert.ok(forecastProblem({ date: 'd', measure: 'm', agg: 'sum', horizon: 25 }));
  assert.ok(forecastProblem({ date: 'd', measure: 'm', agg: 'sum', horizon: 1.5 }));
});

test('forecast rows keep absent bands empty and methods are ranked by error', () => {
  const rows = forecastRows({ forecast: [{ period: '2025-01', point: 1 }, { period: '2025-02', point: 2, lower80: 1, upper80: 3, lower95: 0, upper95: 4 }] });
  assert.equal(rows[0].low80, null);
  assert.equal(rows[1].high95, 4);
  const m = methodRows({ method: { key: 'b' }, backtest: { methods: { a: { label: 'A', mase: 0.9, mae: 1, folds: 3 }, b: { label: 'B', mase: 0.2, mae: 1, folds: 3 } } } });
  assert.deepEqual(m.map((x) => [x.key, x.chosen]), [['b', true], ['a', false]]);
  assert.match(forecastHeadline({ method: { label: 'X', validated_by_backtest: false } }), /accuracy is unknown/);
  assert.match(forecastHeadline({ method: { label: 'X', validated_by_backtest: true } }), /smallest errors/);
});

test('what-if scenarios drop empty rows, convert numbers and are capped', () => {
  const out = buildScenarios([{ name: ' High ', changes: { x1: '5', plan: 'free', x2: '' } }, { name: '', changes: {} }, { changes: { x1: '-1.5' } }]);
  assert.deepEqual(out, [{ name: 'High', changes: { x1: 5, plan: 'free' } }, { name: undefined, changes: { x1: -1.5 } }]);
  const many = buildScenarios(Array.from({ length: 9 }, () => ({ changes: { a: '1' } })));
  assert.equal(many.length, MAX_SCENARIOS);
  assert.equal(scenarioChange({ change: 2.5 }), '+2.5');
  assert.match(scenarioChange({ probability_change: -0.123, positive_class: 'yes' }), /-12\.3 points for “yes”/);
  assert.match(scenarioChange({ label_changed: true }), /changes/);
});
