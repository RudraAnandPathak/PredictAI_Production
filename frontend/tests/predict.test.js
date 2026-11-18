import test from 'node:test';
import assert from 'node:assert/strict';
import {
  aiLines, backtestLine, bonusText, chapterRows, geminiSetupProblem, optionLines, predictBody, predictProblem, priorityLabel, scoreText, setupBody, usageLine, verdictLabel, verdictTone, AI_MODES, PREDICTION_NOTE,
} from '../src/features/phase7/predict.js';

test('priority wording never says probability or certainty', () => {
  for (const p of ['high', 'medium', 'low', 'never_asked']) assert.doesNotMatch(priorityLabel(p), /probab|certain|guarantee|will be asked/i);
  assert.equal(priorityLabel('nope'), 'nope');
  assert.match(PREDICTION_NOTE, /not the real paper/);
  assert.equal(verdictTone('not_clearly_better_than_chance'), 'warn');
  assert.equal(verdictLabel('not_tested'), 'Could not be tested');
});

test('chapter rows keep the server order and show missing figures as a dash', () => {
  const rows = chapterRows({ chapters: [{ chapter: 'A', rank: 1, score: 80, history_score: 70, web_bonus: 10, priority: 'high', why: ['x'], flags: [{ code: 'POSSIBLY_REMOVED' }] }, { chapter: 'B', rank: 2, score: 0, priority: 'never_asked' }] });
  assert.deepEqual(rows.map((r) => r.chapter), ['A', 'B']);
  assert.equal(rows[1].web, 0);
  assert.equal(scoreText(80), '80.0');
  assert.equal(scoreText(null), '—');
  assert.equal(bonusText(10), '+10.0');
  assert.equal(bonusText(0), '—');
  assert.deepEqual(chapterRows(null), []);
});

test('the test of the ranking is shown in the server words', () => {
  assert.match(backtestLine({ message: 'Tested on 4 held-out paper(s): …' }), /^Tested on 4/);
  assert.equal(backtestLine(null), 'The ranking was not tested.');
});

test('AI lines say whether the AI was used and why not', () => {
  assert.deepEqual(aiLines(null), []);
  const none = aiLines({ used_ai: false, reasons: ['gemini: the daily request limit is used up'], rejected: [] });
  assert.equal(none[0], 'No AI was used for this paper.');
  assert.match(none[1], /daily request limit/);
  const used = aiLines({ used_ai: true, accepted: 8, model: 'm', requests: 1, rejected: [{ slot: 3, reason: 'bad' }], reasons: [] });
  assert.match(used[0], /8 question\(s\) written by m in 1 request/);
  assert.equal(used[1], 'Slot 3: bad');
  assert.equal(AI_MODES.length, 3);
});

test('prediction input is checked before sending and sent in the server shape', () => {
  assert.match(predictProblem({ yearFrom: '20', yearTo: '', maxArticles: 5 }), /four-digit/);
  assert.match(predictProblem({ yearFrom: '2024', yearTo: '2020', maxArticles: 5 }), /cannot be after/);
  assert.match(predictProblem({ yearFrom: '', yearTo: '', maxArticles: 9 }), /between 1 and 8/);
  assert.equal(predictProblem({ yearFrom: '2020', yearTo: '2024', maxArticles: 5, examName: 'Grade 10 Biology' }), null);
  assert.deepEqual(predictBody({ yearFrom: '2020', yearTo: '', useWeb: true, maxArticles: '3', examName: ' Grade 10 ' }), { year_from: 2020, use_web: true, max_articles: 3, exam_name: 'Grade 10' });
  assert.deepEqual(predictBody({ yearFrom: '', yearTo: '', useWeb: false, maxArticles: 5, examName: '' }), { use_web: false, max_articles: 5 });
});

test('option lines explain what will and will not be used', () => {
  const lines = optionLines({ web: { allowed: false, reasons: ['You have not turned on web research.'] }, ai: { ready: false, note: 'No AI provider is set up.' } });
  assert.match(lines[0], /Web articles are off: You have not turned on web research/);
  assert.match(lines[1], /AI will not be used/);
  assert.match(optionLines({ web: { allowed: true }, ai: { ready: true } })[1], /ready to write/);
});

test('usage is reported with the server figures and the quota zone', () => {
  const line = usageLine({ requests_today: 3, daily_request_limit: 15, requests_last_minute: 1, requests_per_minute: 4, resets_at: '2026-11-17T08:00:00.000Z', reset_zone: 'America/Los_Angeles' });
  assert.match(line, /Used 3 of 15 request\(s\) today; 1 in the last minute \(limit 4\)/);
  assert.match(line, /America\/Los_Angeles/);
  assert.equal(usageLine(null), 'No usage yet.');
});

test('Gemini setup needs a model, sane limits and the data-use acknowledgement', () => {
  assert.match(geminiSetupProblem({ model: '' }), /Name the model/);
  assert.match(geminiSetupProblem({ model: 'a b' }), /no spaces/);
  assert.match(geminiSetupProblem({ model: 'm', daily: '0' }), /whole number/);
  assert.match(geminiSetupProblem({ model: 'm', daily: '15', perMinute: 'x' }), /Per-minute/);
  assert.equal(geminiSetupProblem({ model: 'm', daily: '15', perMinute: '4' }), null);
  assert.deepEqual(setupBody({ model: ' m ', daily: '15', perMinute: '' }), { model: 'm', enable: true, daily_request_limit: 15 });
});
