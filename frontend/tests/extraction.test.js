import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { workspaceApi } from '../src/api/workspace.js';
import { matchRoute } from '../src/app/router/matchPath.js';
import { basisInfo, formatNumber, formatPct, histogramHeights, sourceLine } from '../src/features/workspace/profile.js';
import { columnQuality, confidenceTone, describePage, pageWindow, typeLabel } from '../src/features/workspace/extraction.js';

test('pageWindow clamps offsets and reports prev/next', () => {
  assert.deepEqual(pageWindow(0, 50, 120), { offset: 0, hasPrev: false, hasNext: true, from: 1, to: 50 });
  assert.deepEqual(pageWindow(100, 50, 120), { offset: 100, hasPrev: true, hasNext: false, from: 101, to: 120 });
  assert.deepEqual(pageWindow(900, 50, 120), { offset: 119, hasPrev: true, hasNext: false, from: 120, to: 120 });
  assert.deepEqual(pageWindow(0, 50, 0), { offset: 0, hasPrev: false, hasNext: false, from: 0, to: 0 });
  assert.deepEqual(pageWindow(-5, 10, null), { offset: 0, hasPrev: false, hasNext: false, from: 0, to: 0 });
});

test('OCR pages are described with their confidence, other pages without', () => {
  assert.equal(describePage({ method: 'ocr', ocr_confidence: 91.4 }), 'Read with OCR (91% confidence)');
  assert.equal(describePage({ method: 'text', ocr_confidence: null }), 'Text layer');
  assert.equal(describePage({ method: 'none' }), 'No text found');
  assert.equal(confidenceTone(90), 'badge--ok');
  assert.equal(confidenceTone(70), 'badge--warn');
  assert.equal(confidenceTone(30), 'badge--danger');
  assert.equal(confidenceTone(null), null);
});

test('type labels are plain language and unknown types pass through', () => {
  assert.equal(typeLabel('integer'), 'Whole number');
  assert.equal(typeLabel('mixed'), 'Mixed');
  assert.equal(typeLabel('weird'), 'weird');
});

test('column quality is computed from exact counts', () => {
  assert.deepEqual(columnQuality({ missing: 25 }, 100), { filled: 75, filledPct: 75 });
  assert.deepEqual(columnQuality({ missing: 500 }, 100), { filled: 0, filledPct: 0 });
  assert.equal(columnQuality({ missing: 0 }, null), null);
});

test('the asset page route captures both ids and does not swallow the project route', () => {
  const routes = [{ path: '/projects/:projectId' }, { path: '/projects/:projectId/assets/:assetId' }];
  assert.deepEqual(matchRoute(routes, '/projects/p1/assets/a%202').params, { projectId: 'p1', assetId: 'a 2' });
  assert.deepEqual(matchRoute(routes, '/projects/p1').params, { projectId: 'p1' });
  assert.equal(matchRoute(routes, '/projects/p1/assets'), null);
});

test('extraction calls hit the right paths with encoded ids and query strings', async () => {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url) => { calls.push(url); return new Response(JSON.stringify({ data: { ok: 1 }, meta: { request_id: 'r' } }), { status: 200, headers: { 'content-type': 'application/json' } }); } });
  const api = workspaceApi(client);
  await api.assets.preview('a/b', { offset: 50, limit: 50 });
  await api.assets.pages('a1', { start: 2, limit: 1 });
  await api.assets.structure('a1');
  await api.assets.extraction('a1');
  assert.deepEqual(calls, ['/api/v1/assets/a%2Fb/preview?offset=50&limit=50', '/api/v1/assets/a1/pages?start=2&limit=1', '/api/v1/assets/a1/structure', '/api/v1/assets/a1/extraction']);
});

test('profile labels: facts, heuristics and estimates stay distinguishable', () => {
  assert.equal(basisInfo('fact').label, 'Exact');
  assert.equal(basisInfo('heuristic').label, 'Rule of thumb');
  assert.equal(basisInfo('estimate').label, 'Estimate');
  assert.notEqual(basisInfo('fact').badge, basisInfo('heuristic').badge);
  assert.equal(basisInfo('weird').label, 'weird');
});

test('number formatting is compact, stable and null-safe', () => {
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(1234), '1,234');
  assert.equal(formatNumber(0.123456), '0.1235');
  assert.equal(formatNumber(12.3456), '12.35');
  assert.equal(formatPct(0), '0%');
  assert.equal(formatPct(33.333), '33%');
  assert.equal(formatPct(3.333), '3.3%');
  assert.equal(formatPct(null), '—');
});

test('histogram heights scale to the tallest bin and keep non-empty bins visible', () => {
  assert.deepEqual(histogramHeights([{ count: 100 }, { count: 50 }, { count: 1 }, { count: 0 }]), [100, 50, 4, 0]);
  assert.deepEqual(histogramHeights([{ count: 0 }, { count: 0 }]), [0, 0]);
});

test('source line names the version and a checksum prefix', () => {
  assert.equal(sourceLine({ profile: { source: { version_number: 2, checksum_sha256: 'abcdef0123456789' } } }), 'version 2 · SHA-256 abcdef012345…');
  assert.equal(sourceLine(null), '');
});

test('profile calls: GET and POST with refresh', async () => {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push([url, init.method ?? 'GET']); return new Response(JSON.stringify({ data: {}, meta: { request_id: 'r' } }), { status: 200, headers: { 'content-type': 'application/json' } }); } });
  client.configure({ getCsrfToken: () => 'c' });
  const api = workspaceApi(client);
  await api.assets.profile('a1');
  await api.assets.createProfile('a1', { refresh: 'true' });
  assert.deepEqual(calls, [['/api/v1/assets/a1/profile', 'GET'], ['/api/v1/assets/a1/profile?refresh=true', 'POST']]);
});
