import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { phase7Api } from '../src/api/phase7.js';
import { NAV_ITEMS, validateNavigation } from '../src/app/navigation.js';
import {
  CONFIRM_MOVES, LIFECYCLE_LABEL, LIFECYCLE_NEXT, LIFECYCLE_NOTE, SECTION_STATE_LABEL, bytesText, needsConfirm, nextStates, ops, parseEmails, quotaText, readinessProblems, stateText,
} from '../src/features/phase7/labels.js';

const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
function recorder() {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json({ data: { ok: true }, meta: { request_id: 'r' } }); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: phase7Api(client) };
}

test('navigation: the exam and reports placeholders are replaced, nothing is duplicated, and the model is consistent', () => {
  assert.deepEqual(validateNavigation(), []);
  for (const id of ['exam', 'reports', 'models', 'dashboard', 'schedules', 'workspaces', 'intelligence']) {
    const entry = NAV_ITEMS.find((i) => i.id === id);
    assert.ok(entry, id);
    assert.equal(entry.status, 'existing', id);
    assert.equal(entry.phase, 7, id);
  }
  assert.equal(NAV_ITEMS.filter((i) => i.path === '/reports').length, 1);
  assert.equal(NAV_ITEMS.filter((i) => i.path === '/exam').length, 1);
  assert.equal(NAV_ITEMS.find((i) => i.id === 'models').audience, 'standard');
});

test('lifecycle: the interface never claims a model is deployed', () => {
  assert.match(LIFECYCLE_NOTE, /not mean the model is deployed/i);
  for (const label of Object.values(LIFECYCLE_LABEL)) assert.doesNotMatch(label, /deploy|live|production/i);
  assert.deepEqual(nextStates('draft').includes('active'), false, 'a draft cannot jump to active');
  assert.ok(nextStates('staging').includes('active'));
  assert.deepEqual(nextStates('nonsense'), []);
  assert.ok(needsConfirm('staging', 'active') && needsConfirm('active', 'archived') && needsConfirm('active', 'staging'));
  assert.ok(!needsConfirm('evaluated', 'archived') && !needsConfirm('registered', 'evaluated'));
  for (const [from, to] of CONFIRM_MOVES) assert.ok(LIFECYCLE_NEXT[from].includes(to), `${from} -> ${to} must be an allowed move`);
});

test('monitoring states: unavailable is never presented as healthy', () => {
  assert.equal(stateText('unavailable'), SECTION_STATE_LABEL.unavailable);
  assert.doesNotMatch(SECTION_STATE_LABEL.unavailable, /working|ok|healthy/i);
  assert.equal(stateText('something-new'), 'Unknown');
});

test('parseEmails keeps valid, distinct addresses and reports the rest', () => {
  assert.deepEqual(parseEmails('A@x.com, a@x.com; b@y.org nope'), { ok: ['a@x.com', 'b@y.org'], bad: ['nope'] });
  assert.deepEqual(parseEmails(''), { ok: [], bad: [] });
});

test('quota and size text', () => {
  assert.equal(quotaText(2, 3), '2 of 3 documents');
  assert.match(quotaText(9, null), /no limit with Special access/);
  assert.equal(bytesText(0), '0 B');
  assert.equal(bytesText(1536), '1.5 KB');
  assert.equal(bytesText(-1), '—');
  assert.equal(bytesText(NaN), '—');
});

test('report edit operations match the server contract and readiness shows the server’s own reasons', () => {
  assert.deepEqual(ops.title('T'), { op: 'set_title', text: 'T' });
  assert.deepEqual(ops.editClaim('b1', 2, 'x'), { op: 'edit_claim', block_id: 'b1', claim: 2, text: 'x' });
  assert.deepEqual(ops.citationStyle('ieee'), { op: 'set_settings', settings: { citation_style: 'ieee' } });
  assert.deepEqual(readinessProblems({ reasons: ['One', '', 5, 'Two'] }), ['One', 'Two']);
  assert.deepEqual(readinessProblems(null), []);
});

test('registry transitions send the confirmation flag; nothing is confirmed implicitly', async () => {
  const { calls, api } = recorder();
  await api.registry.transition('v/1', 'active');
  assert.equal(calls[0].url, '/api/v1/registry/versions/v%2F1/transition');
  assert.deepEqual(JSON.parse(calls[0].init.body), { to: 'active', confirm: false, reason: null });
  await api.registry.transition('v1', 'active', { confirm: true, reason: 'go' });
  assert.deepEqual(JSON.parse(calls[1].init.body), { to: 'active', confirm: true, reason: 'go' });
  await api.registry.rollback('m1', 'v0', { confirm: true });
  assert.deepEqual(JSON.parse(calls[2].init.body), { to_version_id: 'v0', confirm: true, reason: null });
});

test('report calls: optimistic edit, export, versions and CSRF', async () => {
  const { calls, api } = recorder();
  await api.reports.edit('r1', 3, [ops.title('New')], 'note');
  assert.equal(calls[0].init.method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[0].init.body), { base_version: 3, ops: [{ op: 'set_title', text: 'New' }], note: 'note' });
  assert.equal(calls[0].init.headers['X-CSRF-Token'], 'csrf-1');
  await api.reports.createExport('r1', { format: 'pdf', version: 3, style: 'apa' });
  assert.equal(calls[1].url, '/api/v1/reports/r1/exports');
  await api.reports.diff('r1', 1, 2);
  assert.equal(calls[2].url, '/api/v1/reports/r1/diff?a=1&b=2');
  await api.reports.create('p1', { source_kind: 'blank' });
  assert.equal(calls[3].url, '/api/v1/projects/p1/reports');
});

test('exam, intelligence, workspace, schedule and owner calls use the documented routes', async () => {
  const { calls, api } = recorder();
  await api.exam.approve('a1', { confirm: true, acknowledgeWarnings: true });
  assert.deepEqual(JSON.parse(calls[0].init.body), { confirm: true, acknowledge_warnings: true });
  await api.exam.approve('a1');
  assert.deepEqual(JSON.parse(calls[1].init.body), { confirm: false, acknowledge_warnings: false });
  await api.intelligence.rootCause('as1', { outcome: 'churn' });
  assert.equal(calls[2].url, '/api/v1/assets/as1/intelligence/root-cause');
  await api.workspaces.share('w1', { resource_type: 'report', resource_id: 'r1', permission: 'view' });
  assert.equal(calls[3].url, '/api/v1/workspaces/w1/shares');
  await api.workspaces.revoke('g1');
  assert.deepEqual([calls[4].url, calls[4].init.method], ['/api/v1/shares/g1', 'DELETE']);
  await api.schedules.action('s1', 'pause');
  assert.deepEqual(JSON.parse(calls[5].init.body), { action: 'pause' });
  await api.ownerOps.setMaintenance(true, 'back soon', true);
  assert.deepEqual([calls[6].url, calls[6].init.method, JSON.parse(calls[6].init.body)], ['/api/v1/owner/maintenance', 'PUT', { active: true, notice: 'back soon', confirm: true }]);
  await api.ownerOps.setQuota('standard_max_documents', 5);
  assert.deepEqual(JSON.parse(calls[7].init.body), { value: 5 });
  await api.dashboard();
  assert.equal(calls[8].url, '/api/v1/dashboard');
});
