import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from '../src/modules/research_intelligence/logic.js';

test('navigation has the eight research destinations and marks the longest match', () => {
  assert.equal(L.RESEARCH_NAV.length, 8);
  assert.deepEqual(L.RESEARCH_NAV.map((n) => n.label), ['Research Home', 'My Workspaces', 'Discover Research', 'Source Library', 'Research Assistant', 'Research Studio', 'Research Reports', 'Research Settings']);
  assert.equal(L.activeNav('/research-intelligence'), 'home');
  assert.equal(L.activeNav('/research-intelligence/discover'), 'discover');
  assert.equal(L.activeNav('/research-intelligence/reports/'), 'reports');
  assert.equal(L.activeNav('/research-intelligence/ws1/reports/r1'), null);
  assert.equal(L.activeNav('/elsewhere'), null);
});

test('search body drops empty fields and reports bad input', () => {
  assert.deepEqual(L.buildSearchBody({ query: '  ' }, null).errors, ['Enter keywords, a title, an author or a DOI.']);
  const ok = L.buildSearchBody({ query: ' sleep ', year_from: '2015', year_to: '2020', open_access: true, type: '', providers: ['openalex'] }, 'w1');
  assert.deepEqual(ok.errors, []);
  assert.deepEqual(ok.body, { query: 'sleep', year_from: 2015, year_to: 2020, open_access: true, providers: ['openalex'], workspace_id: 'w1' });
  assert.match(L.buildSearchBody({ query: 'x', year_from: '2022', year_to: '2010' }).errors[0], /first year/);
  assert.equal(L.buildSearchBody({ query: 'x', year_from: 'abc' }).body.year_from, undefined);
});

test('results query only carries chosen filters', () => {
  assert.equal(L.resultsQuery({ page: 2 }), 'page=2&page_size=20');
  const q = new URLSearchParams(L.resultsQuery({ sort: 'date', open_access: true, within: 'sleep', type: '' }));
  assert.equal(q.get('open_access'), 'true'); assert.equal(q.get('sort'), 'date'); assert.equal(q.get('within'), 'sleep'); assert.equal(q.has('type'), false);
});

test('result wording never invents what a provider did not report', () => {
  assert.equal(L.authorLine({ authors: [] }), 'Authors not reported');
  assert.equal(L.authorLine({ authors: ['A', 'B', 'C', 'D', 'E', 'F'] }), 'A, B, C, D and 2 more');
  assert.match(L.accessLabel({ oa_url: null }), /No open full text/);
  assert.match(L.accessLabel({ oa_url: 'https://x' }), /provider/);
  assert.equal(L.citationCountLabel({ citation_count: null }), 'Citation count not reported');
  assert.equal(L.citationCountLabel({ citation_count: 0, citation_source: 'openalex' }), '0 citations (openalex)');
  assert.equal(L.providerState('rate_limited'), 'Waiting (rate limit)');
});

test('compare selection is limited to 2-6 papers', () => {
  assert.equal(L.canCompare(['a']), false); assert.equal(L.canCompare(['a', 'b']), true); assert.equal(L.canCompare([1, 2, 3, 4, 5, 6, 7]), false);
  assert.deepEqual(L.toggleId(['a'], 'b', 6), ['a', 'b']); assert.deepEqual(L.toggleId(['a', 'b'], 'a'), ['b']);
  assert.deepEqual(L.toggleId(['a', 'b'], 'c', 2), ['a', 'b']);
});

test('analysis body needs two ready sources', () => {
  assert.equal(L.analysisBody({ sourceIds: ['a'] }, ['a', 'b']).errors.length, 1);
  const r = L.analysisBody({ title: ' T ', question: '', sourceIds: ['a', 'b', 'zz'], useModel: 1 }, ['a', 'b']);
  assert.deepEqual(r.body, { source_ids: ['a', 'b'], use_model: true, title: 'T' });
  assert.equal(L.analysisActive({ status: 'running' }), true); assert.equal(L.analysisActive({ status: 'succeeded' }), false);
});

test('outline helpers respect depth and size limits', () => {
  let r = L.addOutlineItem([], '  ');
  assert.match(r.error, /heading/);
  r = L.addOutlineItem([], 'A'); assert.equal(r.items.length, 1);
  r = L.addOutlineItem(r.items, 'B', 0); assert.equal(r.items[0].children[0].heading, 'B');
  assert.equal(L.outlineDepth(r.items), 2); assert.equal(L.countNodes(r.items), 2);
  const deep = [{ heading: 'a', children: [{ heading: 'b', children: [{ heading: 'c', children: [] }] }] }];
  assert.equal(L.outlineDepth(deep), 3);
  assert.equal(L.addOutlineItem(deep, 'x', 0).items[0].children.length, 2);
  assert.deepEqual(L.moveOutlineItem([1, 2, 3], 0, 1), [2, 1, 3]); assert.deepEqual(L.moveOutlineItem([1, 2], 0, -1), [1, 2]);
  assert.deepEqual(L.removeOutlineItem([1, 2, 3], 1), [1, 3]);
});

test('relations and generators are labelled', () => {
  assert.equal(L.relationLabel('contradicts'), 'Contradicts');
  assert.equal(L.canLink('a', 'a'), false); assert.equal(L.canLink('a', 'b'), true);
  assert.equal(L.generatorLabel('hypotheses'), 'Hypotheses to test');
  assert.match(L.GENERATORS.find((g) => g[0] === 'hypotheses')[2], /not validated/);
});

const REPORT = { sections: [
  { id: 's1', heading: 'Findings', blocks: [{ id: 'b1', type: 'text', origin: 'analysis', text: 'Hello', cite: ['1'] }, { id: 'b2', type: 'quote', origin: 'source', text: 'Q', cite: ['1'] }, { id: 'b3', type: 'bullets', origin: 'analysis', items: ['x'], cite: [] }] },
  { id: 's2', heading: 'Gaps', blocks: [{ id: 'b4', type: 'table', origin: 'analysis', columns: ['a'], rows: [['1']] }] },
], sources: [{ label: 1, title: 'T' }] };

test('quotes and tables are locked, text and bullets are editable', () => {
  const [t, q, b] = REPORT.sections[0].blocks;
  assert.equal(L.isLocked(q), true); assert.equal(L.isLocked(REPORT.sections[1].blocks[0]), true);
  assert.equal(L.isEditable(t), true); assert.equal(L.isEditable(b), true); assert.equal(L.isEditable(q), false);
});

test('edit helpers are immutable and never touch quotes', () => {
  const a = L.setBlockText(REPORT.sections, 's1', 'b1', 'Changed');
  assert.equal(a[0].blocks[0].text, 'Changed'); assert.equal(REPORT.sections[0].blocks[0].text, 'Hello');
  assert.equal(L.setBlockText(REPORT.sections, 's1', 'b2', 'Forged')[0].blocks[1].text, 'Q');
  assert.deepEqual(L.setBlockBullets(REPORT.sections, 's1', 'b3', ' one \n\n two ')[0].blocks[2].items, ['one', 'two']);
  assert.equal(L.removeBlock(REPORT.sections, 's1', 'b2')[0].blocks.length, 2);
  assert.equal(L.removeSection(REPORT.sections, 's2').length, 1);
  assert.deepEqual(L.moveSection(REPORT.sections, 's2', -1).map((s) => s.id), ['s2', 's1']);
  assert.equal(L.moveSection(REPORT.sections, 's1', -1), REPORT.sections);
});

test('new blocks and sections are cleaned before saving', () => {
  const withNew = L.addTextBlock(L.addSection(REPORT.sections, '  '), 's1');
  assert.equal(withNew[2].heading, 'New section');
  const cleaned = L.cleanForSave(withNew);
  assert.equal(cleaned.length, 2);
  assert.equal(cleaned[0].blocks.length, 3);
  assert.equal(L.sectionsChanged(REPORT.sections, cleaned), false);
});

test('origin counts, citations and wording', () => {
  assert.deepEqual(L.reportCounts(REPORT), { source: 1, analysis: 3, author: 0 });
  assert.equal(L.citeText(['1', '3']), '[1], [3]'); assert.equal(L.citeText([]), '');
  assert.equal(L.sourceByLabel(REPORT, '1').title, 'T'); assert.equal(L.sourceByLabel(REPORT, 9), null);
  assert.match(L.originLabel('source'), /Quoted/); assert.match(L.REPORT_STATE_HINT, /not been peer reviewed/);
  assert.equal(L.downloadName('download', 'report.pdf'), 'report.pdf'); assert.equal(L.downloadName('a.pdf', 'x'), 'a.pdf');
  assert.deepEqual(L.EXPORT_FORMATS.map((f) => f[0]), ['pdf', 'docx', 'pptx', 'md']);
});

import { readFileSync } from 'node:fs';
import { matchRoute } from '../src/app/router/matchPath.js';

test('fixed research paths are declared before the workspace route and resolve to themselves', () => {
  const src = readFileSync(new URL('../src/app/routes.jsx', import.meta.url), 'utf8');
  const paths = [...src.matchAll(/path: '(\/research-intelligence[^']*)'/g)].map((m) => m[1]);
  const routes = paths.map((path) => ({ path }));
  assert.ok(paths.indexOf('/research-intelligence/:workspaceId') > paths.indexOf('/research-intelligence/settings'));
  for (const n of L.RESEARCH_NAV.filter((x) => x.key !== 'home')) assert.equal(matchRoute(routes, n.to).route.path, n.to, n.to);
  assert.equal(matchRoute(routes, '/research-intelligence/w1/studio').route.path, '/research-intelligence/:workspaceId/studio');
  assert.equal(matchRoute(routes, '/research-intelligence/w1/reports/r9').params.reportId, 'r9');
  assert.equal(matchRoute(routes, '/research-intelligence/w1').route.path, '/research-intelligence/:workspaceId');
});
