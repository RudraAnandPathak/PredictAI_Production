import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSessionBody, clarificationOf, skippedProviders, understandingLine } from '../src/features/research/plan.js';
import { explanationOf, providerRows, rejectedRows, PROVIDER_STATUS_LABEL } from '../src/features/research/report.js';

const base = { question: 'How was France under Louis XVI?', level: 'topic' };

test('the request carries the arXiv mode and the AI-planning choice only when they differ from the defaults', () => {
  const plain = buildSessionBody(base).body.options;
  assert.equal(plain.arxiv_mode, undefined);
  assert.equal(plain.ai_plan, undefined);
  const chosen = buildSessionBody({ ...base, arxivMode: 'always', aiPlan: true }).body.options;
  assert.equal(chosen.arxiv_mode, 'always');
  assert.equal(chosen.ai_plan, true);
  assert.equal(buildSessionBody({ ...base, arxivMode: 'bogus' }).body.options.arxiv_mode, undefined);
});

test('a clarification block is shown only when the server needs one', () => {
  assert.equal(clarificationOf({ plan: { clarification: null } }), null);
  const c = clarificationOf({ plan: { clarification: { needed: true, reason: 'no_document', message: 'Which paper?', options: [{ label: 'Name the paper', question: 'Explain the methodology used in arXiv:2101.00001' }] } } });
  assert.equal(c.reason, 'no_document');
  assert.equal(c.options[0].question, 'Explain the methodology used in arXiv:2101.00001');
});

test('skipped providers keep their reason, and the understanding line names the subject', () => {
  const s = { plan: { web: { skipped_providers: [{ provider: 'arxiv', reason: 'This question is about history.' }] }, understanding: { entities: [{ name: 'Louis XVI', role: 'subject' }, { name: 'France', role: 'context' }], task: 'overview', domain: 'history' } } };
  assert.deepEqual(skippedProviders(s), [{ provider: 'arxiv', reason: 'This question is about history.' }]);
  assert.equal(understandingLine(s), 'About: Louis XVI · Kind of question: overview · Field: history');
  assert.deepEqual(skippedProviders({}), []);
});

test('the explanation keeps every statement with where it comes from, and is null for an older result', () => {
  assert.equal(explanationOf({}), null);
  const x = explanationOf({ explanation: { schema: 'predictai.explanation/1', subject: ['Louis XVI'], task: 'overview', how_to_read: 'x', direct_answer: [{ part: 'Finances', text: 'The crown was in debt.', support: 'one_source', evidence: ['E4'], cites: [{ evidence: 'E4', source: 'S1', where: 'french rev.pdf, p. 4' }] }], side_by_side: null, connections: [], timing: null, unknown: ['arxiv was not searched'] } });
  assert.equal(x.direct[0].cites[0].where, 'french rev.pdf, p. 4');
  assert.deepEqual(x.sideBySide, []);
  assert.deepEqual(x.unknown, ['arxiv was not searched']);
});

test('a provider that failed is shown as unavailable, never as a success', () => {
  const rows = providerRows({ providers: [{ provider: 'searxng', status: 'failed', note: 'unreachable', results: 0, errors: ['It could not be reached.'] }, { provider: 'wikipedia', status: 'ok', results: 6, read: 1, rejected: 5 }] });
  assert.equal(PROVIDER_STATUS_LABEL[rows[0].status], 'Unavailable');
  assert.equal(PROVIDER_STATUS_LABEL[rows[1].status], 'Worked');
  assert.equal(rejectedRows({ rejected: [{ title: 'Louis XVIII', provider: 'wikipedia', reason: 'a different person' }] })[0].reason, 'a different person');
});
