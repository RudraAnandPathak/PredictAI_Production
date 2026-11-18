import assert from 'node:assert/strict';
import { test } from 'node:test';
import { conversationsApi } from '../src/api/conversations.js';
import { createApiClient } from '../src/api/client.js';
import { matchRoute } from '../src/app/router/matchPath.js';
import {
  MAX_QUESTION, answerNote, canStart, dayGroup, groupByDay, kindLabel, previewOf, questionProblem, stepsOf,
} from '../src/features/conversations/format.js';

const json = (body, init = {}) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });
function rig() {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json({ data: { ok: 1 }, meta: { request_id: 'r' } }); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: conversationsApi(client) };
}

test('conversation calls use the right paths and methods, encode ids, send CSRF, and unwrap data', async () => {
  const { calls, api } = rig();
  assert.deepEqual(await api.list({ q: 'a b', kind: 'data', limit: 100 }), { ok: 1 });
  assert.match(calls[0].url, /^\/api\/v1\/conversations\?/);
  assert.match(calls[0].url, /q=a(\+|%20)b/);
  await api.targets();
  await api.create('research', 'w/1', 'My title');
  assert.equal(calls[2].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[2].init.body), { kind: 'research', target_id: 'w/1', title: 'My title' });
  assert.equal(calls[2].init.headers['X-CSRF-Token'] ?? calls[2].init.headers.get?.('X-CSRF-Token'), 'csrf-1');
  await api.create('data', 'a1');
  assert.deepEqual(JSON.parse(calls[3].init.body), { kind: 'data', target_id: 'a1' });          // no empty title is sent
  await api.send('data', 'x/y', 'hello?');
  assert.equal(calls[4].url, '/api/v1/conversations/data/x%2Fy/messages');
  assert.deepEqual(JSON.parse(calls[4].init.body), { text: 'hello?' });
  await api.rename('data', 'c1', 'New');
  assert.equal(calls[5].init.method, 'PATCH');
  await api.remove('research', 'c2');
  assert.equal(calls[6].init.method, 'DELETE');
  assert.equal(calls[6].url, '/api/v1/conversations/research/c2');
});

test('the conversation routes match, with the kind and id as parameters', () => {
  const m = matchRoute([{ path: '/conversations/:kind/:conversationId' }], '/conversations/research/abc-123');
  assert.deepEqual(m.params, { kind: 'research', conversationId: 'abc-123' });
  assert.equal(matchRoute([{ path: '/conversations/:kind/:conversationId' }], '/conversations'), null);
});

test('previews are one line and never longer than asked', () => {
  assert.equal(previewOf('  a\n\n b\tc  '), 'a b c');
  const long = previewOf('x'.repeat(300), 50);
  assert.equal(long.length, 50);
  assert.ok(long.endsWith('…'));
  assert.equal(previewOf(null), '');
});

test('list entries are grouped by local day, newest first inside each group, empty groups omitted', () => {
  const now = new Date(2026, 10, 16, 12, 0, 0);
  const at = (d, h = 9) => new Date(2026, 10, d, h).toISOString();
  assert.equal(dayGroup(at(16, 1), now), 'Today');
  assert.equal(dayGroup(at(15, 23), now), 'Yesterday');
  assert.equal(dayGroup(at(11), now), 'This week');
  assert.equal(dayGroup(at(2), now), 'Earlier');
  assert.equal(dayGroup('nonsense', now), 'Earlier');
  const items = [{ id: 'a', updated_at: at(16, 10) }, { id: 'b', updated_at: at(16, 8) }, { id: 'c', updated_at: at(1) }];
  assert.deepEqual(groupByDay(items, now).map((g) => [g.label, g.items.map((i) => i.id)]), [['Today', ['a', 'b']], ['Earlier', ['c']]]);
  assert.deepEqual(groupByDay([], now), []);
  assert.deepEqual(groupByDay(undefined, now), []);
});

test('a question must be non-empty and within the limit, with a plain reason otherwise', () => {
  assert.equal(questionProblem('   '), 'Type a question.');
  assert.equal(questionProblem('What is the mean?'), null);
  assert.match(questionProblem('x'.repeat(MAX_QUESTION + 1)), /under 2400 characters/);
  assert.equal(questionProblem('x'.repeat(MAX_QUESTION)), null);
});

test('answer notes say how an answer was produced and never invent one', () => {
  assert.match(answerNote({ answer: { mode: 'extractive' } }), /No language model was used/);
  assert.match(answerNote({ answer: { mode: 'model', provider: 'ollama', removed_unsupported: 2 } }), /ollama.*removed.*\(2\)/);
  assert.match(answerNote({ answer: { mode: 'insufficient_evidence' } }), /nothing is asserted/);
  assert.equal(answerNote({ answer: { mode: 'mystery' } }), null);
  assert.equal(answerNote({}), null);
});

test('calculation steps are shown only when the server stored text steps', () => {
  assert.deepEqual(stepsOf({ details: { steps: ['Averaged spend', '', 5, 'Checked the figure'] } }), ['Averaged spend', 'Checked the figure']);
  assert.deepEqual(stepsOf({ details: { steps: 'nope' } }), []);
  assert.deepEqual(stepsOf({}), []);
});

test('starting needs a known kind and a chosen target; labels are honest', () => {
  assert.equal(canStart('data', 'a1'), true);
  assert.equal(canStart('research', ''), false);
  assert.equal(canStart('other', 'a1'), false);
  assert.equal(kindLabel('data'), 'Dataset');
  assert.equal(kindLabel('zzz'), 'Conversation');
});
