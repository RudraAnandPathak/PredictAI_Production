import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCEPT_ATTRIBUTE, answerNote, answerSegments, checkFile, embeddingNote, formatBytes, hasActive, limitLines, looksLikeReference, nearLimit, policyChangeBody, readyCount, sourceWarnings, statusInfo, workerWaiting, accessNote, citationWhere } from '../src/modules/research_intelligence/labels.js';
import { researchIntelligenceApi } from '../src/modules/research_intelligence/api.js';

test('queued sources explain the worker; failed ones show the server message', () => {
  assert.match(statusInfo({ status: 'queued' }).hint, /worker/);
  assert.equal(statusInfo({ status: 'failed', error_message: 'Scanned PDF without text.' }).hint, 'Scanned PDF without text.');
  assert.equal(statusInfo({ status: 'processing', stage: 'embedding' }).detail, 'embedding');
  assert.equal(statusInfo({ status: 'weird' }).label, 'weird');
});

test('the built-in index is never called semantic', () => {
  const base = { status: 'ready', chunk_count: 4 };
  assert.match(embeddingNote({ ...base, embedding_kind: 'lexical' }), /shared words only/);
  assert.doesNotMatch(embeddingNote({ ...base, embedding_kind: 'lexical' }), /semantic/i);
  assert.match(embeddingNote({ ...base, embedding_kind: 'semantic', embed_model: 'm1' }), /Meaning-based matching \(m1\)/);
  assert.match(embeddingNote({ ...base, embed_status: 'failed' }), /keyword search still works/);
  assert.equal(embeddingNote({ status: 'queued' }), null);
});

test('access and safety notes', () => {
  assert.match(accessNote({ access: 'abstract_only' }), /abstract/);
  assert.match(accessNote({ access: 'metadata_only' }), /nothing to search/);
  assert.equal(accessNote({ access: 'full_text' }), null);
  const w = sourceWarnings({ injection_flags: 2, malware: 'not_scanned' });
  assert.equal(w.length, 2);
  assert.match(w[0], /never followed/);
  assert.match(sourceWarnings({ malware: 'infected' })[0], /flagged/);
});

test('file checks use the server limits and supported types', () => {
  assert.equal(checkFile({ name: 'a.pdf', size: 10 }, 5).ok, true);
  assert.equal(checkFile({ name: 'a.exe', size: 10 }, 5).ok, false);
  assert.equal(checkFile({ name: 'a.pdf', size: 0 }, 5).ok, false);
  assert.match(checkFile({ name: 'a.pdf', size: 6 * 1024 * 1024 }, 5).reason, /5 MB/);
  assert.equal(checkFile({ name: 'noext', size: 3 }, 5).ok, false);
  assert.match(ACCEPT_ATTRIBUTE, /\.docx/);
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(-1), '—');
});

test('references: urls, DOIs and arXiv ids only', () => {
  for (const ok of ['https://example.org/a', '10.1000/xyz123', 'doi:10.1000/xyz', 'arXiv:2401.01234', '2401.01234v2', 'https://doi.org/10.1000/xyz']) assert.equal(looksLikeReference(ok), true, ok);
  for (const bad of ['', 'hello world', 'ftp://x.org/a', 'javascript:alert(1)', 'https://a b']) assert.equal(looksLikeReference(bad), false, bad);
});

test('limits show the real numbers and warn near the cap', () => {
  const lines = limitLines({ workspaces_used: 1, max_workspaces: 3, max_sources_per_workspace: 20, max_upload_mb: 25, imports_today: 2, imports_per_day: 30, ai_requests_today: 5, ai_requests_per_day: 50 });
  assert.equal(lines.length, 5);
  assert.match(lines[0], /1 of 3/);
  assert.deepEqual(limitLines(null), []);
  assert.equal(nearLimit(8, 10), true);
  assert.equal(nearLimit(7, 10), false);
  assert.equal(nearLimit(1, 0), false);
});

test('answer notes say exactly how an answer was produced', () => {
  const model = answerNote({ mode: 'model', provider: 'ollama', model: 'qwen', external: false, removed_unsupported: 2, retrieval: { passages_used: 4, sources_searched: 2, flagged_passages: 1 } });
  assert.match(model, /AI model/); assert.match(model, /ollama · qwen/); assert.match(model, /2 sentence\(s\) without support/); assert.match(model, /4 passage\(s\) from 2 source\(s\)/); assert.match(model, /instruction-like/);
  assert.doesNotMatch(model, /external provider/);
  assert.match(answerNote({ mode: 'model', provider: 'x', external: true, retrieval: {} }), /external provider/);
  assert.match(answerNote({ mode: 'extractive', retrieval: {} }), /no AI model wrote this/);
  assert.match(answerNote({ mode: 'insufficient_evidence', retrieval: {} }), /Not enough evidence/);
  assert.equal(answerNote(null), null);
});

test('only citations that exist become links', () => {
  const cites = [{ label: 1, source_title: 'Paper', page: 3 }, { label: 2, source_title: 'Other', locator: 'sheet A' }];
  const segs = answerSegments('Claim one [1]. Claim two [2]. Stray [9] stays text.', cites);
  assert.equal(segs.filter((s) => s.kind === 'cite').length, 2);
  assert.equal(segs.map((s) => (s.kind === 'cite' ? `[${s.label}]` : s.text)).join(''), 'Claim one [1]. Claim two [2]. Stray [9] stays text.');
  assert.deepEqual(answerSegments('', []), []);
  assert.equal(citationWhere(cites[0]), 'Paper, page 3');
  assert.equal(citationWhere(cites[1]), 'Other, sheet A');
});

test('policy changes: external needs confirm, local does not', () => {
  assert.deepEqual(policyChangeBody('external_allowed'), { ai_data_policy: 'external_allowed', confirm: true });
  assert.deepEqual(policyChangeBody('local_only'), { ai_data_policy: 'local_only', confirm: false });
});

test('source list helpers', () => {
  const s = [{ status: 'queued' }, { status: 'ready', included: true, chunk_count: 3 }, { status: 'ready', included: false, chunk_count: 3 }, { status: 'ready', included: true, chunk_count: 0 }];
  assert.equal(workerWaiting(s), true); assert.equal(hasActive(s), true); assert.equal(readyCount(s), 1);
  assert.equal(hasActive([{ status: 'ready' }]), false);
});

test('api wrapper builds the right requests', async () => {
  const calls = [];
  const client = {
    request: async (path, o = {}) => { calls.push([o.method ?? 'GET', path, o.body]); return { data: { ok: true } }; },
    upload: async (path, o) => { calls.push(['UPLOAD', path, o.body.name]); return { data: { ok: true } }; },
  };
  const api = researchIntelligenceApi(client);
  await api.workspaces.create('N', '');
  await api.sources.upload('w 1', { name: 'a b.pdf' });
  await api.sources.addReference('w1', '10.1/x');
  await api.workspaces.setPolicy('w1', 'external_allowed', true);
  await api.ask('w1', 'Q?', null);
  await api.jobs.retry('j1');
  assert.deepEqual(calls[0], ['POST', '/research-intelligence/workspaces', { name: 'N', description: null }]);
  assert.deepEqual(calls[1], ['UPLOAD', '/research-intelligence/workspaces/w%201/sources/upload?filename=a%20b.pdf', 'a b.pdf']);
  assert.deepEqual(calls[2][2], { reference: '10.1/x' });
  assert.deepEqual(calls[3][2], { ai_data_policy: 'external_allowed', confirm: true });
  assert.deepEqual(calls[4][2], { question: 'Q?', conversation_id: null, source_ids: null });
  assert.equal(calls[5][1], '/research-intelligence/jobs/j1/retry');
});

test('phase 10 completion api wrappers build the right requests', async () => {
  const calls = [];
  const client = {
    request: async (path, o = {}) => { calls.push([o.method ?? 'GET', path, o.body]); return { data: { ok: true } }; },
    download: async (path, o) => { calls.push(['DOWNLOAD', path, o.method]); return { blob: {}, filename: 'x.pdf' }; },
  };
  const api = researchIntelligenceApi(client);
  await api.ask('w1', 'Q?', 'c1', ['s1', 's2']);
  await api.discovery.search({ query: 'sleep' });
  await api.discovery.results('s 1', 'page=2');
  await api.discovery.save('s1', 'r1');
  await api.discovery.compare(['a', 'b']);
  await api.discovery.importTo('w1', 'l1', true);
  await api.analyses.start('w1', { source_ids: ['a', 'b'] });
  await api.studio.link('w1', 'f1', 'f2', 'supports');
  await api.studio.setGroupItems('w1', 'g1', ['f1']);
  await api.studio.generate('w1', 'hypotheses', 'a1');
  await api.reports.create('w1', { analysis_id: 'a1', report_type: 'findings' });
  await api.reports.restore('w1', 'r1', 'v1');
  const out = await api.reports.export('w1', 'r 1', 'pdf');
  const by = (m, p) => calls.find((c) => c[0] === m && c[1] === p);
  assert.deepEqual(calls[0][2], { question: 'Q?', conversation_id: 'c1', source_ids: ['s1', 's2'] });
  assert.ok(by('POST', '/research-intelligence/discovery/search'));
  assert.ok(by('GET', '/research-intelligence/discovery/searches/s%201?page=2'));
  assert.deepEqual(by('POST', '/research-intelligence/discovery/library')[2], { search_id: 's1', rid: 'r1' });
  assert.deepEqual(by('POST', '/research-intelligence/discovery/compare')[2], { ids: ['a', 'b'] });
  assert.deepEqual(by('POST', '/research-intelligence/workspaces/w1/discovery/import')[2], { library_id: 'l1', full_text: true });
  assert.ok(by('POST', '/research-intelligence/workspaces/w1/analyses'));
  assert.deepEqual(by('POST', '/research-intelligence/workspaces/w1/studio/links')[2], { from_id: 'f1', to_id: 'f2', relation: 'supports', note: null });
  assert.deepEqual(by('PUT', '/research-intelligence/workspaces/w1/studio/groups/g1/items')[2], { finding_ids: ['f1'] });
  assert.deepEqual(by('POST', '/research-intelligence/workspaces/w1/studio/generate')[2], { kind: 'hypotheses', analysis_id: 'a1', source_ids: null });
  assert.ok(by('POST', '/research-intelligence/workspaces/w1/reports'));
  assert.ok(by('POST', '/research-intelligence/workspaces/w1/reports/r1/versions/v1/restore'));
  assert.deepEqual(calls.at(-1), ['DOWNLOAD', '/research-intelligence/workspaces/w1/reports/r%201/export?format=pdf', 'GET']);
  assert.equal(out.filename, 'x.pdf');
});
