// The corrective research features (combined report, follow-ups, per-provider wording, search tools) tested against real server responses
// (shared/contracts/phase6c.*.example.json). backend/tests/test_phase6_corrective_contract.py checks the API still returns these shapes, so a change on either side fails a test.
// The examples come from the backend's in-process fake web (documented API shapes), not from the live Wikipedia, arXiv or SearXNG.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { researchApi } from '../src/api/research.js';
import { DIAGNOSIS_LABEL, SECTION_SCOPE_LABEL, SOURCE_CLASS_LABEL } from '../src/features/research/labels.js';
import { needsApproval, outboundSummary, planLines, sentByProvider } from '../src/features/research/plan.js';
import {
  FOLLOW_UP_MAX, answerLines, buildFollowUpBody, comparisonRows, datasetSummary, evidenceTags, externalGroups, followUpLine, hasSeparation, reportOf, reportSections,
} from '../src/features/research/report.js';
import { QUERY_MAX, buildSearchBody, diagnosisRows, openBody, searchableProviders, unavailableProviders } from '../src/features/research/searchtools.js';
import { ORIGIN_LABEL, STATEMENT_LABEL, CONFIDENCE_LABEL } from '../src/features/research/labels.js';

const dir = new URL('../../shared/contracts/', import.meta.url);
const load = (name) => JSON.parse(readFileSync(new URL(`phase6c.${name}.example.json`, dir), 'utf8'));

test('the corrective example files are present and parse', () => {
  const names = readdirSync(dir).filter((n) => n.startsWith('phase6c.') && n.endsWith('.example.json'));
  assert.ok(names.length >= 10, `found ${names.length}`);
  for (const n of names) JSON.parse(readFileSync(new URL(n, dir), 'utf8'));
});

test('the outbound list is drawn from the objects the server sends, grouped by provider, never as "[object Object]"', () => {
  const s = load('session-web');
  const out = outboundSummary(s);
  assert.equal(out.leaves, true);
  assert.ok(out.items.length >= 2);
  for (const q of out.queries) assert.equal(typeof q, 'string');
  assert.ok(!JSON.stringify(out).includes('[object Object]'));
  const groups = sentByProvider(out);
  assert.deepEqual(groups.map((g) => g.provider).sort(), ['arxiv', 'searxng', 'wikipedia'].filter((p) => groups.some((g) => g.provider === p)).sort());
  assert.ok(groups.find((g) => g.provider === 'wikipedia').queries.length >= 2);          // each provider receives its own wording, with a role
  for (const g of groups) for (const q of g.queries) assert.ok(q.query.length > 0);
  // what is shown is exactly what the server will send: every variant of the response appears once
  const sent = new Set(groups.flatMap((g) => g.queries.map((q) => `${g.provider}|${q.query}`)));
  for (const item of s.web.will_send) for (const v of item.variants) assert.ok(sent.has(`${v.provider}|${v.query}`));
  assert.equal(needsApproval(s), true);
  assert.ok(planLines(s.plan).some((l) => /calculated here/.test(l)));                      // dataset figures are said to be calculated, not written by a model
});

test('the outbound summary still accepts plain strings and says nothing leaves when web is off or there is nothing to send', () => {
  assert.equal(outboundSummary({ web: { allowed_now: true, will_send: ['river flood'], note: 'n' }, plan: { web: { providers: ['wikipedia'] } } }).queries[0], 'river flood');
  assert.equal(outboundSummary({ web: { allowed_now: false, will_send: [{ query: 'x', providers: ['wikipedia'], removed: [], variants: [] }] } }).leaves, false);
  assert.equal(outboundSummary({ web: { allowed_now: true, will_send: [] } }).leaves, false);
  assert.equal(outboundSummary(null).leaves, false);
  assert.deepEqual(sentByProvider(outboundSummary(null)), []);
  // an entry with providers but no variants is still shown under each provider
  const g = sentByProvider(outboundSummary({ web: { allowed_now: true, will_send: [{ query: 'a b', providers: ['wikipedia', 'searxng'], removed: [], variants: [] }] } }));
  assert.deepEqual(g.map((x) => x.provider), ['wikipedia', 'searxng']);
});

test('the combined report can be drawn from a real result: every part of it has the fields the screen reads', () => {
  const { bundle } = load('bundle-combined');
  const report = reportOf(bundle);
  assert.ok(report);
  const answer = answerLines(report);
  assert.ok(answer.length >= 1);
  for (const a of answer) {
    assert.ok(a.text && a.section);
    assert.ok(ORIGIN_LABEL[a.origin], a.origin);
    assert.ok(STATEMENT_LABEL[a.kind], a.kind);
    assert.ok(CONFIDENCE_LABEL[a.confidence], a.confidence);
    assert.ok(a.label);
    assert.ok(a.evidence.length >= 1);
  }
  const sections = reportSections(report);
  assert.ok(sections.length >= 3);                                                           // the example keeps the first three parts of the plan (lists in contract examples are trimmed)
  assert.ok(sections.some((s) => s.scope === 'dataset'));
  for (const s of sections) assert.ok(SECTION_SCOPE_LABEL[s.scope], s.scope);
  for (const s of sections) assert.equal(typeof s.answered, 'boolean');
  const dataset = datasetSummary(report);
  assert.ok(dataset && dataset.calculationCount >= 1);
  assert.ok(dataset.datasets[0].name && dataset.datasets[0].runs[0].runId);
  assert.equal(typeof dataset.datasets[0].runs[0].rows, 'number');
  const ext = externalGroups(report, bundle);
  assert.equal(ext.used, true);
  assert.ok(ext.groups.length >= 1);
  for (const g of ext.groups) { assert.ok(SOURCE_CLASS_LABEL[g.cls], g.cls); assert.ok(g.label); }
  assert.ok(hasSeparation(report));
  assert.ok(Array.isArray(report.uncertainty) && report.uncertainty.length >= 1);
  assert.ok(report.uncertainty.some((u) => /not proof|do not show/.test(u)));              // an association is never presented as a cause
});

test('the comparison keeps calculated and outside text apart and states that it matches words, not meaning', () => {
  const { bundle } = load('bundle-combined');
  const rows = comparisonRows(reportOf(bundle));
  assert.ok(rows.length >= 1);
  for (const r of rows) {
    assert.ok(r.calculated.text && r.calculated.evidence);
    assert.ok(r.external.length >= 1);
    for (const x of r.external) { assert.ok(x.label); assert.ok(x.text); assert.notEqual(x.text, r.calculated.text); }
    assert.match(r.note, /by words, not by meaning/);
  }
});

test('a result made before reports existed has no report and the screen still works from its conclusions', () => {
  const old = load('bundle-combined').bundle;
  const stripped = { ...old };
  delete stripped.report;
  assert.equal(reportOf(stripped), null);
  assert.deepEqual(answerLines(null), []);
  assert.deepEqual(reportSections(undefined), []);
  assert.equal(datasetSummary({ dataset: { present: false } }), null);
  assert.deepEqual(externalGroups(null, old).groups, []);
  assert.deepEqual(comparisonRows(null), []);
  assert.equal(hasSeparation(null), false);
});

test('sources and citations carry their kind: Wikipedia, web page and calculation are told apart', () => {
  const { bundle } = load('bundle-combined');
  const labels = new Set(bundle.sources.map((s) => s.source_label));
  assert.ok([...labels].every((l) => typeof l === 'string' && l.length > 0));
  assert.ok([...labels].some((l) => /^Wikipedia/.test(l)));
  const wiki = load('citation-wikipedia');
  assert.equal(wiki.open.type, 'external');
  assert.match(wiki.open.url, /^https:\/\/en\.wikipedia\.org\//);
  assert.match(wiki.open.note, /may have changed/);
  assert.ok(wiki.verification.verified);
  assert.ok(wiki.context && wiki.context.quote);
  const calc = load('citation-calculation');
  assert.equal(calc.open.type, 'analysis_run');
  assert.ok(calc.calculation.run_id && calc.calculation.how);
  assert.equal(calc.context, null);                                                          // a calculated sentence has no page around it; the dialog shows the sentence itself
  assert.ok(calc.verification.verified);
});

test('a follow-up session says what it builds on and is not approved until the person approves it', () => {
  const f = load('follow-up');
  assert.ok(f.parent && f.parent.question);
  assert.match(followUpLine(f), /Follow-up to/);
  assert.equal(followUpLine({}), '');
  assert.equal(f.plan.subtopics.length, 1);                                                  // the question is answered as asked, not split into a fixed outline
  assert.equal(f.plan.subtopics[0].title, 'Answer');
  assert.equal(needsApproval(f), false);                                                     // web off in this follow-up: nothing to approve, nothing leaves
});

test('follow-up question: validated before anything is sent, and the web switch is only sent when the person set it', () => {
  assert.deepEqual(Object.keys(buildFollowUpBody({ question: '' }).errors), ['question']);
  assert.deepEqual(Object.keys(buildFollowUpBody({ question: 'ab' }).errors), ['question']);
  assert.ok(buildFollowUpBody({ question: 'x'.repeat(FOLLOW_UP_MAX + 1) }).errors.question);
  assert.deepEqual(buildFollowUpBody({ question: '  What results does it report?  ' }).body, { question: 'What results does it report?', options: {} });
  assert.deepEqual(buildFollowUpBody({ question: 'What next?', web: true }).body.options, { web: true });
  assert.deepEqual(buildFollowUpBody({ question: 'What next?', web: false }).body.options, { web: false });
});

test('search tools: the form is checked, hits are opened with the right reference, and the test results are plain', () => {
  assert.ok(buildSearchBody({ provider: 'wikipedia', query: '' }).errors.query);
  assert.ok(buildSearchBody({ provider: '', query: 'business class' }).errors.provider);
  assert.ok(buildSearchBody({ provider: 'arxiv', query: 'x'.repeat(QUERY_MAX + 1) }).errors.query);
  assert.deepEqual(buildSearchBody({ provider: 'arxiv', query: '  attention  ' }).body, { provider: 'arxiv', query: 'attention', limit: 8 });
  const found = load('providers-search');
  assert.ok(found.hits.length >= 1);
  const hit = found.hits[0];
  assert.match(hit.source_label, /^Wikipedia/);
  const body = openBody('wikipedia', hit);
  assert.equal(body.provider, 'wikipedia');
  assert.ok(body.ref.pageid || body.ref.title);
  assert.deepEqual(openBody('arxiv', { extra: { arxiv_id: '2101.00001' } }), { provider: 'arxiv', ref: { arxiv_id: '2101.00001' } });
  assert.equal(openBody('arxiv', { title: 'no id' }), null);
  assert.deepEqual(openBody('searxng', { url: 'https://aviation.test/a' }), { provider: 'searxng', ref: { url: 'https://aviation.test/a' } });
  assert.equal(openBody('searxng', null), null);
  const rows = diagnosisRows(load('providers-diagnose'));
  assert.ok(rows.length >= 3);
  for (const r of rows) { assert.ok(DIAGNOSIS_LABEL[r.status], r.status); assert.ok(r.message); }
  assert.deepEqual(rows.map((r) => r.provider), ['wikipedia', 'arxiv', 'searxng']);
});

test('opened items: Wikipedia and arXiv responses have the fields the reader shows, and a preprint is never presented as peer reviewed', () => {
  const wiki = load('providers-open-wikipedia');
  assert.match(wiki.label, /^Wikipedia/);
  assert.ok(wiki.attribution && wiki.permalink && wiki.license);
  assert.ok(wiki.sections.length >= 1 && wiki.sections.every((s) => typeof s.text === 'string'));
  const paper = load('providers-open-arxiv');
  assert.match(paper.label, /not peer reviewed/);
  assert.equal(paper.full_text, true);
  assert.ok(paper.pdf_url);
  assert.ok(paper.sections.length >= 1 && paper.sections.every((s) => typeof s.text === 'string'));
});

test('which providers can be searched, and why the others cannot, come from the capabilities the server sent', () => {
  const caps = load('capabilities');
  assert.ok(caps.search_providers.length >= 3);
  assert.equal(caps.follow_ups, true);
  assert.ok(caps.dataset_research.max_datasets >= 1 && caps.dataset_research.questions.length >= 3);
  assert.ok(/no number is written by a language model/i.test(caps.dataset_research.note));
  const usable = searchableProviders(caps);
  const off = unavailableProviders(caps);
  assert.equal(usable.length + off.length, caps.search_providers.length);
  for (const o of off) assert.ok(o.reason);
  const none = unavailableProviders({ search_providers: [{ key: 'arxiv', label: 'arXiv', available: true, allowed: false }, { key: 'searxng', label: 'Web', available: false, allowed: true, reason: 'No SearXNG address is set.' }] });
  assert.match(none[0].reason, /Switched off/);
  assert.equal(none[1].reason, 'No SearXNG address is set.');
  assert.deepEqual(searchableProviders(null), []);
});

test('the API client reaches the new endpoints with the right method and body', async () => {
  const calls = [];
  const client = { request: (path, options = {}) => { calls.push({ path, ...options }); return Promise.resolve({ data: { ok: true } }); } };
  const api = researchApi(client);
  await api.followUp('s 1', { question: 'q', options: {} });
  await api.diagnoseProviders('p1');
  await api.diagnoseProviders('p1', 'arxiv');
  await api.providerSearch('p1', { provider: 'wikipedia', query: 'x', limit: 8 });
  await api.providerOpen('p1', { provider: 'arxiv', ref: { arxiv_id: '2101.00001' } });
  assert.equal(calls[0].path, '/research/sessions/s%201/follow-up');
  assert.equal(calls[0].method, 'POST');
  assert.deepEqual(calls[0].body, { question: 'q', options: {} });
  assert.equal(calls[1].path, '/projects/p1/research/providers/diagnose');
  assert.deepEqual(calls[1].body, {});
  assert.deepEqual(calls[2].body, { provider: 'arxiv' });
  assert.equal(calls[3].path, '/projects/p1/research/providers/search');
  assert.equal(calls[4].path, '/projects/p1/research/providers/open');
  for (const c of calls) assert.equal(c.method, 'POST');
});

test('badges under a report line are shown once each: a calculated sentence does not carry four that all say "calculated"', () => {
  const calc = evidenceTags({ origin: 'calculated', kind: 'computed', confidence: 'calculated', label: 'Calculated from your data' });
  assert.deepEqual(calc.map((t) => t.label), ['Calculated by PredictAI', 'Calculated from your data']);
  const wiki = evidenceTags({ origin: 'external_research', kind: 'stated', confidence: 'single_source', label: 'Wikipedia (encyclopedia: a tertiary source, edited by volunteers)' });
  assert.equal(wiki.length, 4);                                                              // different words are all kept
  assert.match(wiki[3].label, /^Wikipedia/);
  assert.deepEqual(evidenceTags({}), []);
  // every real line in the example gets at least its origin
  for (const a of answerLines(reportOf(load('bundle-combined').bundle))) assert.ok(evidenceTags(a).length >= 1);
});
