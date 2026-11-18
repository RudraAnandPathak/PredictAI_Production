import assert from 'node:assert/strict';
import { test } from 'node:test';
import { analyticsApi } from '../src/api/analytics.js';
import { createApiClient } from '../src/api/client.js';
import { matchRoute } from '../src/app/router/matchPath.js';
import {
  extent, heatColor, linearScale, mergePeriodSelects, mergeRangeSelects, niceTicks, overlappingIndexes, sortBars, xyRangeSelect,
} from '../src/features/analytics/chartMath.js';
import { buildFilter, describeFilter, formatNumber, formatP, formatPercent } from '../src/features/analytics/format.js';

const json = (body, init = {}) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });
function rig(response = { data: { ok: 1 }, meta: { request_id: 'r' } }) {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json(response); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: analyticsApi(client) };
}

test('analytics calls use the right paths, methods, CSRF header and unwrap data', async () => {
  const { calls, api } = rig();
  assert.deepEqual(await api.schema('a/1'), { ok: 1 });
  assert.equal(calls[0].url, '/api/v1/assets/a%2F1/analytics/schema');
  await api.preset('a1', 'dashboard', { filters: [] });
  assert.equal(calls[1].url, '/api/v1/assets/a1/analytics/presets/dashboard');
  assert.equal(calls[1].init.method, 'POST');
  assert.equal(calls[1].init.headers['X-CSRF-Token'], 'csrf-1');
  await api.rows('r1', { select: { type: 'category', column: 'c', value: 'x' }, offset: 25 });
  assert.deepEqual(JSON.parse(calls[2].init.body), { select: { type: 'category', column: 'c', value: 'x' }, offset: 25, limit: 50, columns: null });
  await api.ask('a1', { question: 'q', use_ai: false });
  assert.equal(calls[3].url, '/api/v1/assets/a1/assistant/ask');
  await api.setInsightState('i1', 'saved');
  assert.deepEqual(JSON.parse(calls[4].init.body), { state: 'saved' });
  await api.insights('a1', { state: 'new', limit: 5 });
  assert.equal(calls[5].url, '/api/v1/assets/a1/insights?state=new&limit=5');
});

test('the analytics module never builds a request from an unencoded id', async () => {
  const { calls, api } = rig();
  await api.getRun('../../etc');
  assert.equal(calls[0].url, '/api/v1/analytics/runs/..%2F..%2Fetc');
});

test('number formatting shows missing values honestly and never invents precision', () => {
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(NaN), '—');
  assert.equal(formatNumber(1234567), '1,234,567');
  assert.equal(formatNumber(0.123456), '0.12');
  assert.equal(formatNumber(0.00001234), '1.23e-5');
  assert.equal(formatP(0.0004), 'p < 0.001');
  assert.equal(formatP(0.0421), 'p = 0.042');
  assert.equal(formatP(null), '—');
  assert.equal(formatPercent(12.345), '12.3%');
});

test('filters are built as typed objects, numbers stay numbers and bad input is refused', () => {
  assert.deepEqual(buildFilter('age', 'numeric', 'ge', '1,000'), { filter: { column: 'age', op: 'ge', value: 1000 } });
  assert.match(buildFilter('age', 'numeric', 'ge', 'abc').error, /not a number/);
  assert.match(buildFilter('age', 'numeric', 'ge', '').error, /Enter a value/);
  assert.deepEqual(buildFilter('c', 'categorical', 'in', ' a, b ,,c'), { filter: { column: 'c', op: 'in', value: ['a', 'b', 'c'] } });
  assert.deepEqual(buildFilter('d', 'datetime', 'between', '2025-01-01', '2025-02-01'), { filter: { column: 'd', op: 'between', value: ['2025-01-01', '2025-02-01'] } });
  assert.deepEqual(buildFilter('c', 'categorical', 'is_null'), { filter: { column: 'c', op: 'is_null' } });
  assert.match(buildFilter('', 'numeric', 'eq', '1').error, /Choose a column/);
  assert.match(buildFilter('c', 'categorical', 'eq', 'x'.repeat(201)).error, /too long/);
  assert.match(describeFilter({ column: 'age', op: 'ge', value: 18 }), /age/);
});

test('scales, ticks and extents', () => {
  assert.deepEqual(extent([3, null, 1, NaN, 9]), [1, 9]);
  assert.deepEqual(extent([]), [0, 1]);
  const s = linearScale([0, 10], [100, 200]);
  assert.equal(s(5), 150);
  assert.equal(s.invert(150), 5);
  assert.deepEqual(niceTicks(0, 100, 5), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(niceTicks(5, 5), [5]);
});

test('sorting bars never changes the values', () => {
  const bars = [{ label: 'b', value: 2 }, { label: 'a', value: 5 }, { label: 'c', value: null }];
  assert.deepEqual(sortBars(bars, 'value_desc').map((b) => b.label), ['a', 'b', 'c']);
  assert.deepEqual(sortBars(bars, 'value_asc').map((b) => b.label), ['b', 'a', 'c']);
  assert.deepEqual(sortBars(bars, 'label').map((b) => b.label), ['a', 'b', 'c']);
  assert.deepEqual(bars.map((b) => b.label), ['b', 'a', 'c']);
});

test('a dragged rectangle becomes a server select descriptor using the data scales', () => {
  const sx = linearScale([0, 100], [0, 500]); const sy = linearScale([0, 10], [400, 0]);
  const sel = xyRangeSelect({ x_column: 'x', y_column: 'y' }, { x0: 100, x1: 300, y0: 100, y1: 200 }, sx, sy);
  assert.deepEqual(sel, { type: 'xy_range', x_column: 'x', y_column: 'y', x_lo: 20, x_hi: 60, y_lo: 5, y_hi: 7.5 });
});

test('bins and periods merge into a single range', () => {
  assert.deepEqual(overlappingIndexes([{ x0: 0, x1: 10 }, { x0: 10, x1: 20 }, { x0: 20, x1: 30 }], 12, 25), [1, 2]);
  assert.deepEqual(mergeRangeSelects([{ type: 'range', column: 'v', lo: 0, hi: 5 }, { type: 'range', column: 'v', lo: 5, hi: 10, hi_inclusive: true }]), { type: 'range', column: 'v', lo: 0, hi: 10, hi_inclusive: true });
  assert.equal(mergeRangeSelects([{ type: 'category' }]), null);
  assert.deepEqual(mergePeriodSelects([{ type: 'period', column: 'd', start: '2025-01-01', end: '2025-01-31' }, { type: 'period', column: 'd', start: '2025-02-01', end: '2025-02-28' }]), { type: 'period', column: 'd', start: '2025-01-01', end: '2025-02-28' });
});

test('heat colours only use the scale the server sent, and gaps stay transparent', () => {
  assert.equal(heatColor(null, { min: 0, max: 1 }), 'transparent');
  assert.match(heatColor(1, { min: 0, max: 1 }), /85%/);
  assert.match(heatColor(-1, { min: -1, max: 1, diverging: true }), /var\(--warm\)/);
});

test('the analytics routes resolve to their pages', () => {
  const routes = [{ path: '/analytics' }, { path: '/analytics/:assetId' }];
  assert.equal(matchRoute(routes, '/analytics').route.path, '/analytics');
  assert.deepEqual(matchRoute(routes, '/analytics/abc').params, { assetId: 'abc' });
  assert.equal(matchRoute(routes, '/analytics/a/b'), null);
});
