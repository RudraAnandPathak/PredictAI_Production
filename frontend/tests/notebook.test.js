import test from 'node:test';
import assert from 'node:assert/strict';
import { blocks, citeTarget, inlineParts, modeNote, readySourceCount, studioProblem, warnings } from '../src/modules/notebook/logic.js';

test('citation marks and bold become parts, and HTML stays text', () => {
  assert.deepEqual(inlineParts('Began in **1789** [S1] and ended <b>x</b> [S2].'), [
    { type: 'text', value: 'Began in ' }, { type: 'bold', value: '1789' }, { type: 'text', value: ' ' }, { type: 'cite', value: 'S1' }, { type: 'text', value: ' and ended <b>x</b> ' }, { type: 'cite', value: 'S2' }, { type: 'text', value: '.' }]);
  assert.deepEqual(inlineParts(''), []);
});

test('markdown becomes headings, items and paragraphs', () => {
  const b = blocks('## Key ideas\n- One [S1]\n1. Two\n\nPlain line\n_No AI answered._');
  assert.deepEqual(b.map((x) => x.type), ['heading', 'item', 'item', 'paragraph', 'paragraph']);
  assert.equal(b[0].level, 3);
  assert.equal(b[4].note, true);
  assert.equal(b[1].parts[1].type, 'cite');
});

test('a citation opens the stored passage, an unknown one opens nothing', () => {
  const out = { citations: { S1: { chunk_id: 'c1', source_id: 's1', source_title: 'History', locator: 'p.2' } } };
  assert.deepEqual(citeTarget(out, 'S1'), { chunk_id: 'c1', source_id: 's1', source_title: 'History', locator: 'p.2' });
  assert.equal(citeTarget(out, 'S9'), null);
  assert.equal(citeTarget(null, 'S1'), null);
});

test('the mode note says who wrote it and warnings carry the server reasons', () => {
  assert.match(modeNote({ mode: 'model', model: 'gemini-x' }), /Written by gemini-x/);
  assert.match(modeNote({ mode: 'extractive' }), /no AI model answered/);
  assert.deepEqual(warnings({ mode: 'model', meta: { uncited_lines: 2, warning: 'w' } }), ['w', '2 line(s) state something without a source mark. Treat those as unchecked.']);
  assert.deepEqual(warnings({ mode: 'extractive', meta: { reasons: ['gemini: daily request limit reached'] } }), ['gemini: daily request limit reached']);
  assert.deepEqual(warnings({ mode: 'model', meta: {} }), []);
});

test('Studio input is checked before sending', () => {
  assert.equal(studioProblem({ kind: '', readySources: 2 }), 'Choose what to make.');
  assert.match(studioProblem({ kind: 'faq', readySources: 0 }), /Add a source/);
  assert.match(studioProblem({ kind: 'faq', instructions: 'x'.repeat(601), readySources: 1 }), /600/);
  assert.equal(studioProblem({ kind: 'faq', instructions: 'short', readySources: 1 }), null);
  assert.equal(readySourceCount([{ status: 'ready', included: 1 }, { status: 'ready', included: 0 }, { status: 'processing', included: 1 }]), 1);
});

import { deepProblem, importLines, mindMapRows, nextCard, pickVoices, quizScore, speechLines } from '../src/modules/notebook/logic.js';

test('quiz score counts right answers and unanswered questions as wrong', () => {
  const qs = [{ answer: 0 }, { answer: 2 }, { answer: 1 }];
  assert.deepEqual(quizScore(qs, { 0: 0, 1: 1 }), { right: 1, total: 3, answered: 2, percent: 33 });
  assert.deepEqual(quizScore([], {}), { right: 0, total: 0, answered: 0, percent: 0 });
});

test('flashcards wrap around in both directions', () => {
  assert.equal(nextCard(2, 1, 3), 0);
  assert.equal(nextCard(0, -1, 3), 2);
  assert.equal(nextCard(0, 1, 0), 0);
});

test('mind map rows indent by depth and folding hides children', () => {
  const map = { root: 'R', children: [{ label: 'A', sources: ['S1'], children: [{ label: 'A1', children: [] }] }, { label: 'B', children: [] }] };
  assert.deepEqual(mindMapRows(map).map((r) => [r.label, r.depth, r.path]), [['A', 1, '0'], ['A1', 2, '0.0'], ['B', 1, '1']]);
  assert.deepEqual(mindMapRows(map, new Set(['0'])).map((r) => r.label), ['A', 'B']);
  assert.deepEqual(mindMapRows(null), []);
});

test('two hosts get two voices when there are two, and marks are not read aloud', () => {
  const v = pickVoices([{ name: 'x', lang: 'fr-FR' }, { name: 'a', lang: 'en-GB' }, { name: 'b', lang: 'en-US' }]);
  assert.deepEqual([v.A.name, v.B.name, v.samePitchShift], ['a', 'b', false]);
  assert.equal(pickVoices([{ name: 'only', lang: 'en-IN' }]).samePitchShift, true);
  assert.equal(pickVoices([]).A, null);
  assert.deepEqual(speechLines({ lines: [{ speaker: 'B', text: 'It began in 1789 [S1].' }, { speaker: 'Z', text: 'Hi' }, { speaker: 'A', text: ' [S2] ' }] }),
    [{ speaker: 'B', text: 'It began in 1789 .' }, { speaker: 'A', text: 'Hi' }]);
});

test('deep research input and the import summary', () => {
  assert.match(deepProblem({ question: ' ', maxSources: 3 }), /Ask a question/);
  assert.match(deepProblem({ question: 'x'.repeat(401), maxSources: 3 }), /400/);
  assert.match(deepProblem({ question: 'ok', maxSources: 9 }), /between 1 and 8/);
  assert.equal(deepProblem({ question: 'ok', maxSources: '5' }), null);
  assert.deepEqual(importLines({ imported: [{}], failed: [{ title: 'T', reason: 'damaged' }], remaining: 2 }),
    ['1 report(s) copied into notebooks.', 'Not copied: T (damaged).', '2 still to copy; run the import again.']);
});
