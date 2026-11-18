import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { devApi } from '../src/api/dev.js';
import { NAV_ITEMS, validateNavigation, visibleGroups, accessFor } from '../src/app/navigation.js';
import {
  ACTIVE_JOB, JOB_STATUS_LABEL, accessSummary, blockedReasonText, bytesText, canCancel, canRetry, cleanPath, errorClassText, executionSummary, groupByFolder, isActive, lineageRows, nearLimit, percentOf, secondsText,
} from '../src/features/dev/labels.js';

const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
function recorder() {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json({ data: { ok: true }, meta: { request_id: 'r' } }); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: devApi(client) };
}

test('navigation: Development and Compute are real, Special-only, and the model stays consistent', () => {
  assert.deepEqual(validateNavigation(), []);
  for (const id of ['development', 'compute']) {
    const e = NAV_ITEMS.find((i) => i.id === id);
    assert.equal(e.status, 'existing');
    assert.equal(e.access, 'special');
  }
  assert.equal(accessFor('development', 'special'), 'special');
  const ids = (canOpen) => visibleGroups(canOpen).flatMap((g) => g.items.map((i) => i.id));
  assert.ok(!ids((a) => a === 'public' || a === 'user').includes('development'));
  assert.ok(ids((a) => a !== 'owner').includes('development'));
});

test('every job state has a plain label and the active/cancel/retry rules match the server state machine', () => {
  for (const s of ['queued', 'starting', 'running', 'cancelling', 'succeeded', 'failed', 'timed_out', 'cancelled', 'blocked']) assert.ok(JOB_STATUS_LABEL[s], s);
  assert.deepEqual([...ACTIVE_JOB], ['queued', 'starting', 'running', 'cancelling']);
  assert.ok(isActive('running') && !isActive('succeeded'));
  assert.ok(canCancel('queued') && canCancel('running') && !canCancel('cancelling') && !canCancel('succeeded'));
  assert.ok(canRetry('failed') && canRetry('timed_out') && !canRetry('running') && !canRetry('succeeded'));
});

test('error and blocked reasons are explained in words and unknown ones are shown, not hidden', () => {
  assert.match(errorClassText('memory_limit'), /memory/);
  assert.equal(errorClassText(null), null);
  assert.equal(errorClassText('some_new_class'), 'some new class');
  assert.match(blockedReasonText('entitlement_revoked'), /Special access was removed/);
  assert.equal(blockedReasonText('other_reason'), 'other_reason');
});

test('size, time and percentage helpers never produce NaN', () => {
  assert.equal(bytesText(0), '0 B');
  assert.equal(bytesText(1536), '1.5 KB');
  assert.equal(bytesText(null), '—');
  assert.equal(secondsText(75), '1 min 15 s');
  assert.equal(percentOf(5, 0), 0);
  assert.equal(percentOf('x', 10), 0);
  assert.equal(percentOf(50, 100), 50);
  assert.equal(percentOf(500, 100), 100);
  assert.ok(nearLimit(80, 100) && !nearLimit(79, 100));
});

test('cleanPath rejects traversal, absolute and empty paths; the server still re-checks', () => {
  assert.equal(cleanPath('src/train.py'), 'src/train.py');
  assert.equal(cleanPath('./a.py'), 'a.py');
  for (const bad of ['', '  ', '/etc/passwd', '../x', 'a/../b', 'a//b', null, 'x'.repeat(300)]) assert.equal(cleanPath(bad), null, String(bad));
});

test('files are grouped by folder in a stable order', () => {
  const g = groupByFolder([{ path: 'b/z.py' }, { path: 'a.py' }, { path: 'b/a.py' }]);
  assert.deepEqual(g.map((x) => x.folder), ['', 'b']);
  assert.deepEqual(g[1].files.map((f) => f.path), ['b/a.py', 'b/z.py']);
});

test('the landing page says why it is locked and what running code really means', () => {
  assert.equal(accessSummary({ access: { allowed: false, reason: 'No.' } }).state, 'locked');
  assert.equal(accessSummary({ access: { allowed: false } }).state, 'locked');
  assert.match(accessSummary({ access: { allowed: false } }).text, /Special access/);
  assert.equal(accessSummary({ access: { allowed: true } }).state, 'open');
  assert.equal(accessSummary(null).state, 'unknown');
  assert.equal(executionSummary({ enabled: false }).runs, false);
  assert.match(executionSummary({ enabled: true, isolation: { provider: 'local_trusted', multi_user_safe: false } }).text, /not a sandbox/);
  assert.doesNotMatch(executionSummary({ enabled: true, isolation: { provider: 'docker', multi_user_safe: true } }).text, /not a sandbox/);
});

test('lineage shows a recorded link as recorded and a missing one as missing, never inventing one', () => {
  const rows = lineageRows({ links: [{ stage: 'dataset', present: true, record: { id: 'd1' } }, { stage: 'model', present: false, record: null, note: 'No model linked.' }] });
  assert.deepEqual(rows.map((r) => [r.kind, r.present, r.detail]), [['dataset', true, 'd1'], ['model', false, 'No model linked.']]);
  assert.deepEqual(lineageRows(null), []);
});

test('API wrappers use the right verbs and paths, encode ids, and send the CSRF token on changes', async () => {
  const { calls, api } = recorder();
  await api.overview();
  await api.files.write('p 1', 'a/b.py', 'x', 3);
  await api.files.remove('p1', 'a.py', 'a.py');
  await api.jobs.submit('p1', { kind: 'script', entry: 'a.py' });
  await api.jobs.cancel('p1', 'j1');
  await api.git.push('p1', 'main');
  await api.envs.addPackage('p1', 'e1', 'pandas==2.2.2');
  await api.pipelines.approve('p1', 'r1', 'ok');
  await api.compute.setOverride({ scope: 'user', scope_id: 'u', key: 'job_wall_seconds', value: 100, reason: 'because' });
  const seen = calls.map((c) => `${c.init.method ?? 'GET'} ${c.url}`);
  assert.deepEqual(seen, [
    'GET /api/v1/dev/overview',
    'PUT /api/v1/dev/projects/p%201/files/content',
    'POST /api/v1/dev/projects/p1/files/delete',
    'POST /api/v1/dev/projects/p1/jobs',
    'POST /api/v1/dev/projects/p1/jobs/j1/cancel',
    'POST /api/v1/dev/projects/p1/git/push',
    'POST /api/v1/dev/projects/p1/envs/e1/packages',
    'POST /api/v1/dev/projects/p1/pipeline-runs/r1/approve',
    'PUT /api/v1/dev/owner/compute-overrides',
  ]);
  const body = (i) => JSON.parse(calls[i].init.body);
  assert.deepEqual(body(1), { path: 'a/b.py', content: 'x', expected_version: 3 });
  assert.deepEqual(body(2), { path: 'a.py', confirm: 'a.py' });
  assert.deepEqual(body(6), { package: 'pandas==2.2.2' });
  assert.deepEqual(body(7), { reason: 'ok' });
  for (const c of calls.slice(1)) assert.equal(new Headers(c.init.headers).get('x-csrf-token'), 'csrf-1');
});
