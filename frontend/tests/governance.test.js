import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { workspaceApi } from '../src/api/workspace.js';
import { checkRetention, dateInputValue, earliestRetention, footprintLines } from '../src/features/workspace/governance.js';
import { formatBytes } from '../src/features/workspace/format.js';

const NOW = new Date('2026-11-12T10:00:00Z');

test('retention dates mirror the server rules', () => {
  assert.equal(earliestRetention(NOW), '2026-11-13');
  assert.equal(checkRetention('', NOW), null);
  assert.match(checkRetention('2026-11-12', NOW), /future/);
  assert.equal(checkRetention('2026-11-13', NOW), null);
  assert.match(checkRetention('2037-01-01', NOW), /ten years/);
  assert.match(checkRetention('next week', NOW), /valid date/);
  assert.equal(dateInputValue('2027-06-30T23:59:59.000000Z'), '2027-06-30');
  assert.equal(dateInputValue(null), '');
});

test('footprint lines name what a deletion removes', () => {
  const lines = footprintLines({ items: [{ kind: 'original', objects: 1, bytes: 2048 }, { kind: 'profiles', objects: 2, bytes: 10 }] }, formatBytes);
  assert.deepEqual(lines.map((l) => l.label), ['Original file', 'Data profiles']);
  assert.equal(lines[0].text, '1 stored item, 2.0 KB');
  assert.equal(lines[1].text, '2 stored items, 10 B');
  assert.deepEqual(footprintLines(null, formatBytes), []);
});

test('governance wrappers call the documented endpoints', async () => {
  const calls = [];
  const fake = { request: async (path, options = {}) => { calls.push([path, options.method ?? 'GET', options.body]); return { data: {} }; }, url: (p) => p };
  const api = workspaceApi(fake);
  await api.assets.setRetention('a1', '2027-01-01');
  await api.assets.setRetention('a1', '');
  await api.assets.footprint('a1');
  await api.inventory({ horizon_days: 7 });
  assert.deepEqual(calls[0], ['/assets/a1/retention', 'PATCH', { retain_until: '2027-01-01' }]);
  assert.deepEqual(calls[1][2], { retain_until: null });
  assert.equal(calls[2][0], '/assets/a1/footprint');
  assert.equal(calls[3][0], '/workspace/inventory?horizon_days=7');
  assert.ok(createApiClient);
});
