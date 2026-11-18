// Document search screen: the request it builds, the words it uses for a hit, and the calls it makes. The server ranks and cites; none of that is tested here.
import assert from 'node:assert/strict';
import test from 'node:test';
import { researchApi } from '../src/api/research.js';
import { SEARCH_MODES, buildSearchBody, documentRows, hitPlace, hitWhy } from '../src/features/research/documents.js';

test('a search needs a question of a sensible length and a known mode', () => {
  assert.deepEqual(Object.keys(buildSearchBody({ query: ' ', mode: 'hybrid' }).errors), ['query']);
  assert.ok(buildSearchBody({ query: 'x'.repeat(2001), mode: 'hybrid' }).errors.query);
  assert.ok(buildSearchBody({ query: 'levee', mode: 'magic' }).errors.mode);
  assert.deepEqual(SEARCH_MODES.map((m) => m.key), ['hybrid', 'keyword', 'semantic']);
});

test('the body names only the documents that were ticked and bounds the number of results', () => {
  assert.deepEqual(buildSearchBody({ query: ' levee ', mode: 'keyword', assetIds: [], k: 8 }).body, { query: 'levee', mode: 'keyword', k: 8 });
  assert.deepEqual(buildSearchBody({ query: 'levee', mode: 'hybrid', assetIds: ['a1'], k: 500 }).body, { query: 'levee', mode: 'hybrid', k: 50, asset_ids: ['a1'] });
  assert.equal(buildSearchBody({ query: 'levee', mode: 'hybrid', k: 'many' }).body.k, 8);
});

test('a place is described in words, and estimated pages say so', () => {
  assert.equal(hitPlace({ page_start: 3, page_end: 3, page_estimated: false }), 'page 3');
  assert.equal(hitPlace({ page_start: 3, page_end: 4, page_estimated: true, section: 'Floods' }), 'pages 3–4 (estimated) · Floods');
  assert.equal(hitPlace({}), 'place not recorded');
});

test('the reason for a result names the methods and the matched words, or says it was found by meaning', () => {
  assert.match(hitWhy({ methods: { keyword: { rank: 1 }, semantic: { rank: 2 } }, matched_terms: ['river'] }), /keyword rank 1 and semantic rank 2\. Matched words: river\./);
  assert.match(hitWhy({ methods: { semantic: { rank: 1 } }, matched_terms: [] }), /found by meaning/);
});

test('documents that are not ready cannot be ticked, and every state is explained', () => {
  const rows = documentRows({ documents: [
    { asset_id: 'a', name: 'A', extraction_ready: false, indexed: false },
    { asset_id: 'b', name: 'B', extraction_ready: true, indexed: true, stale: false, chunks: 12 },
    { asset_id: 'c', name: 'C', extraction_ready: true, indexed: true, stale: true, chunks: 3 },
    { asset_id: 'd', name: 'D', extraction_ready: true, indexed: false },
  ] });
  assert.deepEqual(rows.map((r) => r.searchable), [false, true, true, true]);
  assert.match(rows[0].state, /not extracted/); assert.match(rows[1].state, /12 passages/); assert.match(rows[2].state, /out of date/); assert.match(rows[3].state, /first time/);
  assert.deepEqual(documentRows(undefined), []);
});

test('the API wrappers call the retrieval routes of the project', async () => {
  const calls = [];
  const client = { request: async (path, opts) => { calls.push([path, opts?.method ?? 'GET', opts?.body]); return { data: { ok: true } }; } };
  const api = researchApi(client);
  await api.documentStatus('p 1'); await api.searchDocuments('p1', { query: 'q', mode: 'hybrid', k: 8 });
  assert.deepEqual(calls[0], ['/projects/p%201/documents/index', 'GET', undefined]);
  assert.deepEqual(calls[1], ['/projects/p1/documents/search', 'POST', { query: 'q', mode: 'hybrid', k: 8 }]);
});
