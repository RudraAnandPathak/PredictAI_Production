import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { workspaceApi } from '../src/api/workspace.js';
import { columnsBefore, defaultStep, describeVersion, fromOperation, OP_ORDER, OPS, stepProblem, toOperation } from '../src/features/workspace/cleaning.js';

test('every operation has a label, help text and a default step', () => {
  for (const op of OP_ORDER) {
    assert.ok(OPS[op].label && OPS[op].help, op);
    assert.equal(defaultStep(op).op, op);
  }
  assert.equal(OP_ORDER.length, 9);
});

test('columns reflect earlier renames and removals', () => {
  const base = ['a', 'b', 'c'];
  const steps = [{ op: 'rename_column', column: 'a', new_name: ' alpha ' }, { op: 'drop_columns', columns: ['b'] }, { op: 'trim_whitespace', columns: [] }];
  assert.deepEqual(columnsBefore(steps, base, 0), ['a', 'b', 'c']);
  assert.deepEqual(columnsBefore(steps, base, 1), ['alpha', 'b', 'c']);
  assert.deepEqual(columnsBefore(steps, base, 2), ['alpha', 'c']);
  assert.deepEqual(columnsBefore([{ op: 'rename_column', column: 'a', new_name: '' }], base, 1), base);
});

test('step validation mirrors the server rules', () => {
  const cols = ['a', 'b'];
  assert.equal(stepProblem({ ...defaultStep('fill_missing'), column: '' }, cols), 'Choose a column.');
  assert.equal(stepProblem({ ...defaultStep('fill_missing'), column: 'a' }, cols), '');
  assert.match(stepProblem({ ...defaultStep('fill_missing'), column: 'a', strategy: 'constant', value: '' }, cols), /value/);
  assert.match(stepProblem({ ...defaultStep('rename_column'), column: 'a', new_name: ' ' }, cols), /new name/);
  assert.match(stepProblem({ ...defaultStep('filter_rows'), column: 'a', filter: 'gt', value: 'x' }, cols), /number/);
  assert.equal(stepProblem({ ...defaultStep('filter_rows'), column: 'a', filter: 'is_empty' }, cols), '');
  assert.match(stepProblem(defaultStep('drop_columns'), cols), /at least one/);
  assert.equal(stepProblem(defaultStep('drop_duplicates'), cols), '');
  assert.equal(stepProblem({ ...defaultStep('convert_type'), column: 'zzz' }, cols), 'Choose a column.');
});

test('payloads contain only the fields each step uses', () => {
  assert.deepEqual(toOperation(defaultStep('drop_duplicates')), { op: 'drop_duplicates' });
  assert.deepEqual(toOperation({ ...defaultStep('drop_duplicates'), columns: ['a'] }), { op: 'drop_duplicates', columns: ['a'] });
  assert.deepEqual(toOperation({ ...defaultStep('fill_missing'), column: 'a', strategy: 'median', value: 'ignored' }), { op: 'fill_missing', column: 'a', strategy: 'median' });
  assert.deepEqual(toOperation({ ...defaultStep('fill_missing'), column: 'a', strategy: 'constant', value: '0' }), { op: 'fill_missing', column: 'a', strategy: 'constant', value: '0' });
  assert.deepEqual(toOperation({ ...defaultStep('convert_type'), column: 'a', to: 'date', date_format: 'dmy' }), { op: 'convert_type', column: 'a', to: 'date', on_invalid: 'empty', date_format: 'dmy' });
  assert.deepEqual(toOperation({ ...defaultStep('convert_type'), column: 'a', to: 'float' }), { op: 'convert_type', column: 'a', to: 'float', on_invalid: 'empty' });
  assert.deepEqual(toOperation({ ...defaultStep('rename_column'), column: 'a', new_name: ' x ' }), { op: 'rename_column', column: 'a', new_name: 'x' });
  assert.deepEqual(toOperation({ ...defaultStep('filter_rows'), column: 'a', filter: 'not_empty', value: 'zz' }), { op: 'filter_rows', column: 'a', filter: 'not_empty', action: 'keep' });
  assert.deepEqual(toOperation({ ...defaultStep('drop_missing'), how: 'all' }), { op: 'drop_missing', how: 'all' });
});

test('operations from suggestions and history round-trip into editable steps', () => {
  const op = { op: 'fill_missing', column: 'score', strategy: 'median' };
  assert.deepEqual(toOperation(fromOperation(op)), op);
  const trim = { op: 'trim_whitespace', columns: ['name'] };
  assert.deepEqual(toOperation(fromOperation(trim)), trim);
});

test('versions are described in plain words', () => {
  assert.equal(describeVersion({ number: 1, kind: 'original' }), 'Version 1 — the original upload');
  assert.equal(describeVersion({ number: 3, kind: 'derived' }), 'Version 3 — cleaned');
});

test('cleaning calls use the right paths, methods and explicit confirmation', async () => {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push([url, init.method ?? 'GET', init.body ?? null]); return new Response(JSON.stringify({ data: {}, meta: { request_id: 'r' } }), { status: 200, headers: { 'content-type': 'application/json' } }); } });
  client.configure({ getCsrfToken: () => 'c' });
  const api = workspaceApi(client);
  await api.assets.cleaningSuggestions('a1');
  await api.assets.previewCleaning('a1', [{ op: 'drop_duplicates' }]);
  await api.assets.confirmCleaning('t/1');
  await api.assets.discardCleaning('t1');
  await api.assets.history('a1');
  await api.assets.rollback('a1', 'v2');
  assert.deepEqual(calls.map((c) => [c[0], c[1]]), [
    ['/api/v1/assets/a1/cleaning/suggestions', 'GET'], ['/api/v1/assets/a1/cleaning/preview', 'POST'], ['/api/v1/cleaning/t%2F1/confirm', 'POST'],
    ['/api/v1/cleaning/t1/discard', 'POST'], ['/api/v1/assets/a1/history', 'GET'], ['/api/v1/assets/a1/rollback', 'POST']]);
  assert.deepEqual(JSON.parse(calls[1][2]), { operations: [{ op: 'drop_duplicates' }] });
  assert.deepEqual(JSON.parse(calls[2][2]), { confirm: true });
  assert.deepEqual(JSON.parse(calls[5][2]), { version_id: 'v2', confirm: true });
});
