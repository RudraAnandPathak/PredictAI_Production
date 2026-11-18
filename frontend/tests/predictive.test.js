import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { predictiveApi } from '../src/api/predictive.js';
import { matchRoute } from '../src/app/router/matchPath.js';
import { NAV_ITEMS, validateNavigation } from '../src/app/navigation.js';
import { EXPLANATION_TITLE, explanationOf, explanationView } from '../src/features/explanation/explanation.js';
import {
  AREA, confusionCells, distributionBars, effectBars, histogramBars, importanceBars, learningGeometry, pdpGeometry, prGeometry, probabilityBars,
  rankingBars, residualGeometry, rocGeometry, scatterGeometry,
} from '../src/features/predictive/charts.js';
import {
  MAX_SEED, algorithmsFor, buildConfig, checkForm, defaultForm, describeConfig, formFromConfig, hasErrors, kindOfProblem, metricsFor,
} from '../src/features/predictive/config.js';
import { formatMs, formatScore, formatSeconds, formatShare, headline, modelTitle, phaseLabel } from '../src/features/predictive/format.js';
import { checkEntered, fieldsFromSchema, sortedIssues, typicalValues, valuesPayload } from '../src/features/predictive/inputs.js';
import { canCancel, isFinal, nextPollDelayMs, pollJob, progressPercent } from '../src/features/predictive/jobs.js';
import { footprintLines } from '../src/features/workspace/governance.js';

const json = (body, init = {}) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });
function rig(response = { data: { ok: 1 }, meta: { request_id: 'r' } }) {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json(response); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: predictiveApi(client), client };
}

/* ------------------------------------------------------------------ API layer */
test('every predictive call uses the right path and method, sends the CSRF header on writes, and unwraps data', async () => {
  const { calls, api } = rig();
  assert.deepEqual(await api.catalog(), { ok: 1 });
  assert.equal(calls[0].url, '/api/v1/ml/catalog');
  await api.schema('a/1');
  assert.equal(calls[1].url, '/api/v1/assets/a%2F1/ml/schema');
  await api.assess('a1', { target: 'y' }, 'v1');
  assert.equal(calls[2].url, '/api/v1/assets/a1/ml/assess');
  assert.equal(calls[2].init.method, 'POST');
  assert.equal(calls[2].init.headers['X-CSRF-Token'], 'csrf-1');
  assert.deepEqual(JSON.parse(calls[2].init.body), { config: { target: 'y' }, version_id: 'v1' });
  await api.startTraining('a1', { target: 'y' }, { versionId: 'v1', label: 'first' });
  assert.equal(calls[3].url, '/api/v1/assets/a1/ml/jobs');
  assert.deepEqual(JSON.parse(calls[3].init.body), { config: { target: 'y' }, version_id: 'v1', label: 'first' });
  await api.startTraining('a1', { target: 'y' });
  assert.deepEqual(JSON.parse(calls[4].init.body), { config: { target: 'y' }, version_id: null, label: null });
  await api.jobs('a1', { limit: 10 });
  assert.equal(calls[5].url, '/api/v1/assets/a1/ml/jobs?limit=10');
  await api.job('j1');
  assert.equal(calls[6].url, '/api/v1/ml/jobs/j1');
  await api.cancelJob('j1');
  assert.equal(calls[7].url, '/api/v1/ml/jobs/j1/cancel');
  assert.equal(calls[7].init.method, 'POST');
  await api.models({ asset_id: 'a1' });
  assert.equal(calls[8].url, '/api/v1/ml/models?asset_id=a1');
  await api.renameModel('m1', 'Better name');
  assert.equal(calls[9].init.method, 'PATCH');
  assert.deepEqual(JSON.parse(calls[9].init.body), { name: 'Better name' });
  await api.deleteModel('m1');
  assert.equal(calls[10].init.method, 'DELETE');
  assert.equal(calls[10].init.headers['X-CSRF-Token'], 'csrf-1');
  await api.report('m1');
  assert.equal(calls[11].url, '/api/v1/ml/models/m1/report');
  await api.partialDependence('m1', 'x1');
  assert.equal(calls[12].url, '/api/v1/ml/models/m1/partial-dependence');
  assert.deepEqual(JSON.parse(calls[12].init.body), { feature: 'x1' });
  await api.compare(['m1', 'm2']);
  assert.deepEqual(JSON.parse(calls[13].init.body), { ids: ['m1', 'm2'] });
  await api.predict('m1', { x1: 1 });
  assert.equal(calls[14].url, '/api/v1/ml/models/m1/predict');
  assert.deepEqual(JSON.parse(calls[14].init.body), { values: { x1: 1 }, explain: true, save: true });
  await api.predict('m1', { x1: 1 }, { explain: false, save: false });
  assert.deepEqual(JSON.parse(calls[15].init.body), { values: { x1: 1 }, explain: false, save: false });
  await api.predictBatch('m1', 'd1');
  assert.equal(calls[16].url, '/api/v1/ml/models/m1/predict-batch');
  assert.deepEqual(JSON.parse(calls[16].init.body), { dataset_asset_id: 'd1', version_id: null });
  await api.predictions('m1', { kind: 'batch', limit: 5 });
  assert.equal(calls[17].url, '/api/v1/ml/models/m1/predictions?kind=batch&limit=5');
  await api.prediction('p1');
  assert.equal(calls[18].url, '/api/v1/ml/predictions/p1');
});

test('no predictive call builds a URL from an unencoded id', async () => {
  const { calls, api } = rig();
  await api.model('../../admin');
  await api.job('a/b?c=d');
  await api.prediction('x#y');
  assert.equal(calls[0].url, '/api/v1/ml/models/..%2F..%2Fadmin');
  assert.equal(calls[1].url, '/api/v1/ml/jobs/a%2Fb%3Fc%3Dd');
  assert.equal(calls[2].url, '/api/v1/ml/predictions/x%23y');
});

test('downloading a saved prediction is a plain GET with no body, no content type and no CSRF token; POST downloads still carry them', async () => {
  const seen = [];
  const client = createApiClient({
    baseUrl: '/api/v1',
    fetchImpl: async (url, init) => { seen.push({ url, init }); return new Response('row,prediction\n1,yes\n', { status: 200, headers: { 'content-disposition': 'attachment; filename="predictions-abc.csv"' } }); },
  });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  const api = predictiveApi(client);
  const { blob, filename } = await api.downloadPrediction('p1');
  assert.equal(filename, 'predictions-abc.csv');
  assert.equal(await blob.text(), 'row,prediction\n1,yes\n');
  assert.equal(seen[0].url, '/api/v1/ml/predictions/p1/download');
  assert.equal(seen[0].init.method, 'GET');
  assert.equal(seen[0].init.body, undefined);
  assert.equal(seen[0].init.headers['X-CSRF-Token'], undefined);
  assert.equal(seen[0].init.headers['Content-Type'], undefined);
  await client.download('/exports', { body: { a: 1 } });
  assert.equal(seen[1].init.method, 'POST');
  assert.equal(seen[1].init.headers['X-CSRF-Token'], 'csrf-1');
  assert.deepEqual(JSON.parse(seen[1].init.body), { a: 1 });
});

/* ------------------------------------------------------------------ training form */
test('the default form sends only the target; every other setting is left to the server defaults', () => {
  assert.deepEqual(buildConfig({ ...defaultForm(), target: 'bought' }), { target: 'bought' });
});

test('only settings that differ from the defaults are sent, in the server\'s vocabulary', () => {
  const form = {
    ...defaultForm(), target: 'price', problemType: 'regression', primaryMetric: 'r2', exclude: ['id'], roles: { zip: 'categorical', note: 'default' },
    preprocessing: { ...defaultForm().preprocessing, scaling: 'robust', drop_duplicates: false }, algorithms: ['ridge', 'random_forest'],
    testSize: 0.3, cvStrategy: 'kfold', cvFolds: 3, hpoMethod: 'random', hpoIter: 8, hpoTopK: 1, hpoFolds: 2, seed: 7, timeBudget: 45,
  };
  assert.deepEqual(buildConfig(form), {
    target: 'price', problem_type: 'regression', primary_metric: 'r2',
    features: { exclude: ['id'], overrides: { zip: { role: 'categorical' } } },
    preprocessing: { scaling: 'robust', drop_duplicates: false }, algorithms: ['ridge', 'random_forest'],
    validation: { test_size: 0.3, cv_strategy: 'kfold', cv_folds: 3 },
    hpo: { method: 'random', n_iter: 8, top_k: 1, cv_folds: 2 }, seed: 7, time_budget_s: 45,
  });
});

test('a configuration the server proposes round-trips through the form unchanged', () => {
  const proposed = {
    target: 'bought', problem_type: 'binary_classification', positive_label: 'yes', features: { exclude: ['leaky'], overrides: { plan: { role: 'categorical' } } },
    preprocessing: { numeric_impute: 'mean', outliers: 'clip_iqr' }, validation: { test_size: 0.25, cv_folds: 4, cv_strategy: 'stratified_kfold' },
    hpo: { method: 'grid', n_iter: 12, top_k: 2, cv_folds: 3 }, class_weight: 'balanced', rare_classes: 'drop', seed: 99,
  };
  assert.deepEqual(buildConfig(formFromConfig(proposed)), proposed);
  assert.deepEqual(buildConfig(formFromConfig({})), { target: '' });
});

test('form checks give plain messages and mirror the server limits', () => {
  const schema = { columns: [{ name: 'y', role: 'numeric', can_be_target: true }, { name: 'when', role: 'datetime', can_be_target: false }, { name: 'cat', role: 'categorical', can_be_target: true }] };
  const limits = { max_cv_folds: 5, max_cv_repeats: 2, max_hpo_trials: 30, max_hpo_algorithms: 2, time_budget_s: 120 };
  const ok = { ...defaultForm(), target: 'y' };
  assert.deepEqual(checkForm(ok, { schema, limits }), {});
  assert.match(checkForm(defaultForm(), { schema, limits }).target, /Choose the column/);
  assert.match(checkForm({ ...ok, target: 'nope' }, { schema, limits }).target, /exists/);
  assert.match(checkForm({ ...ok, target: 'when' }, { schema, limits }).target, /date column/);
  assert.match(checkForm({ ...ok, target: 'cat', problemType: 'regression' }, { schema, limits }).problemType, /holds text/);
  assert.ok(checkForm({ ...ok, exclude: ['y'] }, { schema, limits }).exclude);
  assert.ok(checkForm({ ...ok, testSize: 0.9 }, { schema, limits }).testSize);
  assert.ok(checkForm({ ...ok, testSize: 0.01 }, { schema, limits }).testSize);
  assert.deepEqual(checkForm({ ...ok, testSize: 0 }, { schema, limits }), {});
  assert.ok(checkForm({ ...ok, cvFolds: 1 }, { schema, limits }).cvFolds);
  assert.ok(checkForm({ ...ok, cvFolds: 6 }, { schema, limits }).cvFolds);
  assert.ok(checkForm({ ...ok, cvFolds: 2.5 }, { schema, limits }).cvFolds);
  assert.ok(checkForm({ ...ok, cvStrategy: 'time_series', shuffle: true, timeColumn: 'when' }, { schema, limits }).shuffle);
  assert.ok(checkForm({ ...ok, cvStrategy: 'time_series', shuffle: false }, { schema, limits }).timeColumn);
  assert.deepEqual(checkForm({ ...ok, cvStrategy: 'time_series', shuffle: false, timeColumn: 'when' }, { schema, limits }), {});
  assert.ok(checkForm({ ...ok, hpoMethod: 'grid', hpoIter: 31 }, { schema, limits }).hpoIter);
  assert.ok(checkForm({ ...ok, hpoMethod: 'grid', hpoTopK: 3 }, { schema, limits }).hpoTopK);
  assert.equal(checkForm({ ...ok, hpoIter: 9999 }, { schema, limits }).hpoIter, undefined);   // not checked while tuning is off
  assert.ok(checkForm({ ...ok, seed: -1 }, { schema, limits }).seed);
  assert.ok(checkForm({ ...ok, seed: MAX_SEED + 1 }, { schema, limits }).seed);
  assert.deepEqual(checkForm({ ...ok, seed: MAX_SEED }, { schema, limits }), {});
  assert.ok(checkForm({ ...ok, timeBudget: 5 }, { schema, limits }).timeBudget);
  assert.ok(checkForm({ ...ok, timeBudget: 121 }, { schema, limits }).timeBudget);
  assert.deepEqual(checkForm({ ...ok, timeBudget: 10 }, { schema, limits }), {});
  assert.ok(checkForm({ ...ok, preprocessing: { ...ok.preprocessing, max_missing_pct: 2 } }, { schema, limits }).max_missing_pct);
  assert.ok(checkForm({ ...ok, preprocessing: { ...ok.preprocessing, rare_category_min_frequency: 0.5 } }, { schema, limits }).rare_category_min_frequency);
  assert.equal(hasErrors({}), false);
  assert.equal(hasErrors({ a: 'x' }), true);
});

test('algorithms, metrics and problem kinds come from the catalogue, not from the browser', () => {
  const catalog = {
    algorithms: [{ key: 'ridge', problems: ['regression'] }, { key: 'knn', problems: ['classification', 'regression'] }, { key: 'naive_bayes', problems: ['classification'] }],
    metrics: { classification: [{ key: 'roc_auc', label: 'ROC-AUC' }], regression: [{ key: 'rmse', label: 'RMSE' }] },
  };
  assert.deepEqual(algorithmsFor(catalog, 'regression').map((a) => a.key), ['ridge', 'knn']);
  assert.deepEqual(algorithmsFor(catalog, 'classification').map((a) => a.key), ['knn', 'naive_bayes']);
  assert.equal(algorithmsFor(catalog, null).length, 3);
  assert.deepEqual(metricsFor(catalog, 'regression').map((m) => m.key), ['rmse']);
  assert.equal(metricsFor(catalog, null).length, 2);
  assert.equal(kindOfProblem('binary_classification'), 'classification');
  assert.equal(kindOfProblem('multiclass_classification'), 'classification');
  assert.equal(kindOfProblem('regression'), 'regression');
  assert.equal(kindOfProblem('auto'), null);
  assert.deepEqual(algorithmsFor(undefined, 'regression'), []);
});

test('a stored configuration is described in plain words', () => {
  const lines = describeConfig({ target: 'bought', algorithms: ['ridge'], test_size: 0.2, cv_folds: 5, primary_metric: 'roc_auc', seed: 3 });
  assert.deepEqual(lines, ['Predicting "bought"', 'Algorithms: ridge', '20% of rows held back for testing', '5-fold cross-validation', 'Ranked by roc auc', 'Seed 3']);
  assert.deepEqual(describeConfig(null), []);
});

/* ------------------------------------------------------------------ jobs */
test('polling backs off gently and never faster than once a second or slower than every five', () => {
  assert.equal(nextPollDelayMs(0), 1000);
  assert.equal(nextPollDelayMs(4), 1000);
  assert.equal(nextPollDelayMs(5), 2000);
  assert.equal(nextPollDelayMs(19), 2000);
  assert.equal(nextPollDelayMs(20), 5000);
  assert.equal(nextPollDelayMs(10_000), 5000);
  assert.equal(nextPollDelayMs(-3), 1000);
  assert.equal(nextPollDelayMs(NaN), 1000);
});

test('progress is a real percentage or unknown, never invented', () => {
  assert.equal(progressPercent(null), null);
  assert.equal(progressPercent({ status: 'running', progress: { fraction: 0.456 } }), 46);
  assert.equal(progressPercent({ status: 'running', progress: { fraction: 4 } }), 100);
  assert.equal(progressPercent({ status: 'running', progress: { fraction: -1 } }), 0);
  assert.equal(progressPercent({ status: 'running', progress: {} }), null);
  assert.equal(progressPercent({ status: 'running' }), null);
  assert.equal(progressPercent({ status: 'running', progress: { fraction: 'half' } }), null);
  assert.equal(progressPercent({ status: 'completed' }), 100);
});

test('only an unfinished job that has not already been asked to stop can be cancelled', () => {
  for (const status of ['queued', 'running']) assert.equal(canCancel({ status }), true, status);
  for (const status of ['completed', 'failed', 'cancelled']) assert.equal(canCancel({ status }), false, status);
  assert.equal(canCancel({ status: 'running', cancel_requested: true }), false);
  assert.equal(canCancel(null), false);
  assert.equal(isFinal('completed') && isFinal('failed') && isFinal('cancelled'), true);
  assert.equal(isFinal('running') || isFinal('queued'), false);
});

test('pollJob follows a job to its end, reports each answer, and waits between checks', async () => {
  const answers = [{ status: 'queued' }, { status: 'running' }, { status: 'running' }, { status: 'completed', model_id: 'm1' }];
  const seen = []; const waits = [];
  const final = await pollJob('j1', { fetchJob: async () => answers.shift(), onUpdate: (j) => seen.push(j.status), sleep: async (ms) => { waits.push(ms); } });
  assert.equal(final.status, 'completed');
  assert.deepEqual(seen, ['queued', 'running', 'running', 'completed']);
  assert.deepEqual(waits, [1000, 1000, 1000]);
});

test('pollJob retries a brief network failure but gives up after repeated ones, and stops at once on 404', async () => {
  let n = 0;
  const flaky = async () => { n += 1; if (n < 3) throw Object.assign(new Error('net'), { status: 0 }); return { status: 'failed' }; };
  assert.equal((await pollJob('j', { fetchJob: flaky, sleep: async () => {} })).status, 'failed');
  let calls = 0;
  await assert.rejects(pollJob('j', { fetchJob: async () => { calls += 1; throw Object.assign(new Error('down'), { status: 503 }); }, sleep: async () => {}, maxErrors: 3 }), /down/);
  assert.equal(calls, 3);
  let gone = 0;
  await assert.rejects(pollJob('j', { fetchJob: async () => { gone += 1; throw Object.assign(new Error('missing'), { status: 404 }); }, sleep: async () => {} }), /missing/);
  assert.equal(gone, 1);
});

test('pollJob stops quietly when the page asks it to, and a late answer is not reported', async () => {
  let stopped = false; let updates = 0;
  const result = await pollJob('j', { fetchJob: async () => { stopped = true; return { status: 'running' }; }, onUpdate: () => { updates += 1; }, sleep: async () => {}, isStopped: () => stopped });
  assert.equal(result, null);
  assert.equal(updates, 0);
});

/* ------------------------------------------------------------------ prediction inputs */
test('the prediction form is built only from the model\'s recorded inputs', () => {
  const fields = fieldsFromSchema([
    { name: 'x1', role: 'numeric', min: -2, max: 3, median: 0.2, integer_only: false, missing_share: 0.05 },
    { name: 'age', role: 'numeric', min: 1, max: 90, median: 40, integer_only: true },
    { name: 'plan', role: 'categorical', levels: ['basic', 'pro'], level_count: 2, most_common: 'basic' },
    { name: 'note', role: 'categorical', levels: null, level_count: 5000, most_common: 'ok' },
    { name: 'when', role: 'datetime', min: '2020-01-01T00:00:00', max: '2024-01-01T00:00:00' },
  ]);
  assert.deepEqual(fields.map((f) => [f.name, f.kind]), [['x1', 'number'], ['age', 'number'], ['plan', 'choice'], ['note', 'text'], ['when', 'date']]);
  assert.equal(fields[0].hint, 'Seen in training: -2 to 3');
  assert.equal(fields[1].integerOnly, true);
  assert.deepEqual(fields[2].levels, ['basic', 'pro']);
  assert.equal(fields[0].missingShare, 0.05);
  assert.deepEqual(fieldsFromSchema(), []);
});

test('blank fields are left out of the request instead of being sent as empty text', () => {
  const fields = fieldsFromSchema([{ name: 'x1', role: 'numeric' }, { name: 'plan', role: 'categorical', levels: ['a'] }, { name: 'note', role: 'categorical' }]);
  assert.deepEqual(valuesPayload(fields, { x1: ' 2.5 ', plan: '', note: '   ' }), { x1: 2.5 });
  assert.deepEqual(valuesPayload(fields, { x1: 'abc', plan: 'a' }), { x1: 'abc', plan: 'a' });
  assert.deepEqual(valuesPayload(fields, {}), {});
  assert.deepEqual(valuesPayload(fields, { ghost: '1' }), {});
  assert.deepEqual(valuesPayload(fields, { x1: 0 }), { x1: 0 });
});

test('entered values get instant hints but the server decides', () => {
  const fields = fieldsFromSchema([{ name: 'x1', role: 'numeric' }, { name: 'age', role: 'numeric', integer_only: true }, { name: 'when', role: 'datetime' }]);
  assert.equal(checkEntered(fields, {})._form, 'Enter at least one value.');
  assert.deepEqual(checkEntered(fields, { x1: '1.5', age: '30', when: '2024-02-03' }), {});
  assert.equal(checkEntered(fields, { x1: 'abc' }).x1, 'Enter a number.');
  assert.match(checkEntered(fields, { age: '3.5' }).age, /whole numbers/);
  assert.match(checkEntered(fields, { when: '3 Feb' }).when, /YYYY-MM-DD/);
});

test('typical values are medians and most common categories, and are only a starting point', () => {
  const fields = fieldsFromSchema([{ name: 'x1', role: 'numeric', median: 0.25 }, { name: 'plan', role: 'categorical', levels: ['a', 'b'], most_common: 'b' }, { name: 'when', role: 'datetime' }, { name: 'z', role: 'numeric' }]);
  assert.deepEqual(typicalValues(fields), { x1: '0.25', plan: 'b' });
});

test('notes about the values entered are shown errors first', () => {
  const sorted = sortedIssues([{ severity: 'info', column: 'a' }, { severity: 'warning', column: 'b' }, { severity: 'error', column: 'c' }, { severity: 'odd', column: 'd' }]);
  assert.deepEqual(sorted.map((i) => i.column), ['c', 'b', 'a', 'd']);
  assert.deepEqual(sortedIssues(), []);
});

/* ------------------------------------------------------------------ the universal Explanation */
test('an explanation is always titled "Explanation"; one the server cannot give says so and gives its reason', () => {
  assert.equal(EXPLANATION_TITLE, 'Explanation');
  const none = explanationView(undefined);
  assert.equal(none.title, 'Explanation');
  assert.equal(none.available, false);
  assert.equal(none.present, false);
  assert.equal(none.summary, '');
  assert.match(none.reason, /not available/);
  const unavailable = explanationView({ status: 'unavailable', reason: 'Too few rows to say anything reliable.', summary: 'THIS MUST NOT SHOW' });
  assert.equal(unavailable.available, false);
  assert.equal(unavailable.summary, '');
  assert.equal(unavailable.reason, 'Too few rows to say anything reliable.');
  const empty = explanationView({ status: 'available', summary: '' });
  assert.equal(empty.available, false);
  const ok = explanationView({
    status: 'available', summary: 'The model is right 78% of the time.', points: ['one', '', 3, 'two'], cautions: ['careful'], terms: [{ term: 'AUC', meaning: 'area' }, { term: 'bad' }], basis: { numbers_cited: 4 },
  });
  assert.equal(ok.available, true);
  assert.equal(ok.summary, 'The model is right 78% of the time.');
  assert.deepEqual(ok.points, ['one', 'two']);
  assert.deepEqual(ok.terms, [{ term: 'AUC', meaning: 'area' }]);
  assert.deepEqual(ok.cautions, ['careful']);
  assert.equal(ok.cited, 4);
});

test('the explanation of a result is found wherever the server put it, and never made up', () => {
  const e = { status: 'available', summary: 's' };
  assert.equal(explanationOf({ explanation: e }), e);
  assert.equal(explanationOf({ answer: { explanation: e } }), e);
  assert.equal(explanationOf({}), undefined);
  assert.equal(explanationOf(null), undefined);
});

/* ------------------------------------------------------------------ chart geometry */
test('ROC and precision-recall geometry sit on a fixed 0-1 square and keep every server point', () => {
  const roc = rocGeometry([{ class: 'yes', auc: 0.9, fpr: [0, 0.5, 1], tpr: [0, 0.8, 1] }]);
  assert.equal(roc.x(0), AREA.x0);
  assert.equal(roc.x(1), AREA.x1);
  assert.equal(roc.y(0), AREA.y1);
  assert.equal(roc.y(1), AREA.y0);
  assert.deepEqual(roc.diagonal, { x1: AREA.x0, y1: AREA.y1, x2: AREA.x1, y2: AREA.y0 });
  assert.equal(roc.series[0].points, 3);
  assert.equal(roc.series[0].auc, 0.9);
  assert.equal(roc.series[0].d.startsWith(`M${AREA.x0.toFixed(2)},${AREA.y1.toFixed(2)}`), true);
  assert.equal(roc.table.rows.length, 3);
  assert.deepEqual(roc.table.rows[1], ['yes', 0.5, 0.8]);
  assert.deepEqual(rocGeometry([]).series, []);
  const pr = prGeometry([{ class: 'yes', average_precision: 0.8, prevalence: 0.4, recall: [0, 1], precision: [1, 0.4] }]);
  assert.equal(pr.series[0].baselineY, pr.y(0.4));
  assert.equal(prGeometry([{ class: 'a', recall: [0], precision: [1] }]).series[0].baselineY, null);
});

test('a curve with a missing value keeps the gap instead of joining across it', () => {
  const roc = rocGeometry([{ class: 'c', auc: 0.5, fpr: [0, 0.5, 1], tpr: [0, null, 1] }]);
  assert.equal(roc.series[0].points, 2);
});

test('the confusion matrix uses the server\'s counts and its row shares for shading', () => {
  const cm = confusionCells({ labels: ['no', 'yes'], counts: [[40, 10], [5, 45]], row_normalised: [[0.8, 0.2], [0.1, 0.9]] });
  assert.equal(cm.size, 2);
  assert.equal(cm.cells.length, 4);
  assert.deepEqual(cm.cells.map((c) => [c.actual, c.predicted, c.count, c.share, c.correct]), [
    ['no', 'no', 40, 0.8, true], ['no', 'yes', 10, 0.2, false], ['yes', 'no', 5, 0.1, false], ['yes', 'yes', 45, 0.9, true],
  ]);
  assert.equal(cm.cells[0].rowTotal, 50);
  assert.equal(confusionCells(null), null);
  assert.equal(confusionCells({ labels: ['a'] }), null);
  assert.equal(confusionCells({ labels: [1, 2], counts: [[1, 0], [0, 1]] }).cells[0].share, null);
  assert.deepEqual(confusionCells({ labels: [1, 2], counts: [[1, 0], [0, 1]] }).labels, ['1', '2']);
});

test('importance bars are sorted, relative to the largest, and a negative importance is flagged rather than hidden', () => {
  const bars = importanceBars([
    { feature: 'noise', importance: -0.02, std: 0.01, share: 0, reliable: false },
    { feature: 'x1', importance: 0.2, std: 0.02, share: 0.7, reliable: true },
    { feature: 'plan', importance: 0.1, share: 0.3 },
    { feature: 'broken', importance: null },
  ]);
  assert.deepEqual(bars.map((b) => b.feature), ['x1', 'plan', 'noise']);
  assert.equal(bars[0].width, 1);
  assert.equal(bars[1].width, 0.5);
  assert.equal(bars[2].width, 0);
  assert.equal(bars[2].negative, true);
  assert.equal(bars[1].std, null);
  assert.equal(bars[1].reliable, null);
  assert.equal(importanceBars([{ feature: 'a', importance: 0.5 }, { feature: 'b', importance: 0.4 }], { top: 1 }).length, 1);
  assert.deepEqual(importanceBars(), []);
  assert.equal(importanceBars([{ feature: 'z', importance: 0 }])[0].width, 0);
});

test('histogram bars need one more edge than counts and are scaled to the tallest bin', () => {
  const bars = histogramBars({ edges: [0, 1, 2, 3], counts: [2, 8, 4] });
  assert.deepEqual(bars.map((b) => [b.lo, b.hi, b.count, b.height]), [[0, 1, 2, 0.25], [1, 2, 8, 1], [2, 3, 4, 0.5]]);
  assert.equal(histogramBars({ edges: [0, 1], counts: [1, 2] }), null);
  assert.equal(histogramBars(null), null);
  assert.equal(histogramBars({ counts: [1] }), null);
});

test('actual-against-predicted uses one shared scale, so the "perfect" line is a true diagonal', () => {
  const g = scatterGeometry({ actual: [0, 10, 20], predicted: [1, 9, 25], residual: [-1, 1, -5], points: 3, of: 3 });
  assert.equal(g.domain[0] < 0, true);
  assert.equal(g.domain[1] > 25, true);
  const run = g.diagonal.x2 - g.diagonal.x1;
  const rise = g.diagonal.y1 - g.diagonal.y2;
  assert.ok(Math.abs(run / rise - (AREA.x1 - AREA.x0) / (AREA.y1 - AREA.y0)) < 1e-9);      // equal data span on both axes
  assert.equal(g.points[0].cx, g.x(0));
  assert.equal(g.points[2].cy, g.y(25));
  assert.equal(g.table.rows.length, 3);
  assert.equal(scatterGeometry({ actual: [1, 2], predicted: [1] }), null);
  assert.equal(scatterGeometry(null), null);
});

test('residuals are centred on zero whichever side the larger miss is on', () => {
  const g = residualGeometry({ predicted: [1, 2, 3], residual: [-3, 1, 0.5] });
  assert.ok(Math.abs((g.y(0) - AREA.y0) - (AREA.y1 - g.y(0))) < 1e-9);
  assert.equal(g.zeroY, g.y(0));
  assert.equal(residualGeometry({ predicted: [1], residual: [] }), null);
});

test('error-metric learning curves are shown as ordinary positive errors', () => {
  const curve = { available: true, metric: 'neg_root_mean_squared_error', points: [
    { train_rows: 50, train_score: -0.8, validation_score: -1.2, validation_std: 0.1 }, { train_rows: 100, train_score: -0.7, validation_score: -1.0, validation_std: 0.05 }] };
  const shown = learningGeometry(curve, { flip: true });
  assert.deepEqual(shown.points.map((p) => [p.train_score, p.validation_score]), [[0.8, 1.2], [0.7, 1]]);
  assert.deepEqual(shown.table.rows[0], [50, 0.8, 1.2, 0.1]);
  const raw = learningGeometry(curve);
  assert.equal(raw.points[0].train_score, -0.8);
  assert.equal(learningGeometry({ available: false, points: curve.points }), null);
  assert.equal(learningGeometry({ available: true, points: [curve.points[0]] }), null);
  assert.equal(learningGeometry(undefined), null);
});

test('partial dependence draws numbers as a line and categories as separate points', () => {
  const numeric = pdpGeometry({ available: true, feature: 'x1', grid: [0, 1, 2], series: { yes: [0.2, 0.5, 0.9] }, low: [0.1, 0.4, 0.8], high: [0.3, 0.6, 1] });
  assert.equal(numeric.numeric, true);
  assert.ok(numeric.series[0].d.startsWith('M'));
  assert.ok(numeric.band.startsWith('M') && numeric.band.endsWith('Z'));
  assert.deepEqual(numeric.table.columns, ['x1', 'yes', 'Low (10th pct)', 'High (90th pct)']);
  const categorical = pdpGeometry({ available: true, feature: 'plan', grid: ['basic', 'pro'], series: { value: [10, 20] } });
  assert.equal(categorical.numeric, false);
  assert.equal(categorical.series[0].d, '');
  assert.equal(categorical.series[0].points.length, 2);
  assert.ok(categorical.series[0].points[0].cx < categorical.series[0].points[1].cx);
  assert.equal(categorical.band, null);
  const multi = pdpGeometry({ available: true, feature: 'x', grid: [0, 1], series: { a: [1, 2], b: [2, 1] }, low: [0, 0], high: [3, 3] });
  assert.equal(multi.band, null);
  assert.equal(multi.series.length, 2);
  assert.equal(pdpGeometry({ available: false, grid: [1] }), null);
  assert.equal(pdpGeometry({ available: true, grid: [] }), null);
});

test('ranking bars keep the server order and scale cross-validation, spread and held-out marker on one axis', () => {
  const bars = rankingBars([
    { id: 'a', label: 'A', rank: 1, cv_primary_shown: 0.8, cv_std: 0.05, test_primary_shown: 0.85, overfitting: 'low', fit_s: 1, model_bytes: 10 },
    { id: 'b', label: 'B', rank: 2, cv_primary_shown: 0.4, test_primary_shown: null, overfitting: 'high' },
    { id: 'c', label: 'C', rank: 3, cv_primary_shown: null },
  ]);
  assert.deepEqual(bars.map((b) => b.id), ['a', 'b']);
  const max = 0.85;
  assert.ok(Math.abs(bars[0].cvWidth - 0.8 / max) < 1e-12);
  assert.ok(Math.abs(bars[0].testAt - 1) < 1e-12);
  assert.equal(bars[1].testAt, null);
  assert.equal(bars[1].std, 0);
  assert.deepEqual(rankingBars(), []);
});

test('prediction distribution, probability and effect bars only use the numbers they are given', () => {
  assert.equal(distributionBars({}), null);
  assert.equal(distributionBars({ prediction_counts: [] }), null);
  const d = distributionBars({ prediction_counts: [{ class: 'no', rows: 23, share: 0.575 }, { class: 'yes', rows: 17, share: 0.425 }] });
  assert.deepEqual(d.map((b) => [b.label, b.rows, b.share]), [['no', 23, 0.575], ['yes', 17, 0.425]]);
  assert.equal(d[0].width, 1);
  assert.ok(Math.abs(d[1].width - 17 / 23) < 1e-12);
  const p = probabilityBars([{ class: 'a', probability: 1.2 }, { class: 'b', probability: -0.1 }, { class: 'c', probability: 0.25 }, { class: 'd', probability: null }]);
  assert.deepEqual(p.map((b) => [b.label, b.width]), [['a', 1], ['b', 0], ['c', 0.25]]);
  assert.equal(p[0].probability, 1.2);                                                   // the printed number is the server's; only the bar is clamped
  const e = effectBars([{ feature: 'x', value: 1, effect: 0.2 }, { feature: 'y', value: 2, effect: -0.1 }, { feature: 'z', value: 3, effect: 'n/a' }]);
  assert.deepEqual(e.map((b) => [b.feature, b.direction, b.width]), [['x', 'raises', 1], ['y', 'lowers', 0.5]]);
  assert.deepEqual(effectBars(), []);
});

/* ------------------------------------------------------------------ display helpers */
test('scores, shares and times are formatted without losing digits or inventing them', () => {
  assert.equal(formatScore(0.5), '0.5');
  assert.equal(formatScore(0.87654), '0.877');
  assert.equal(formatScore(10), '10');
  assert.equal(formatScore(100.0001), '100');
  assert.equal(formatScore(1000, 0), '1000');
  assert.equal(formatScore(-0.0001), '0');
  assert.equal(formatScore(-0.25), '-0.25');
  assert.equal(formatScore(0), '0');
  assert.equal(formatScore(null), '—');
  assert.equal(formatScore(undefined), '—');
  assert.equal(formatScore(NaN), '—');
  assert.equal(formatScore(Infinity), '—');
  assert.equal(formatScore('n/a'), 'n/a');
  assert.equal(formatShare(0.425, 1), '42.5%');
  assert.equal(formatShare(0.5), '50%');
  assert.equal(formatShare(null), '—');
  assert.equal(formatSeconds(0.25), '250 ms');
  assert.equal(formatSeconds(3.456), '3.5 s');
  assert.equal(formatSeconds(45), '45 s');
  assert.equal(formatSeconds(125), '2 min 5 s');
  assert.equal(formatSeconds(null), '—');
  assert.equal(formatMs(1500), '1.5 s');
  assert.equal(formatMs(undefined), '—');
  assert.equal(phaseLabel('training'), 'Training models');
  assert.equal(phaseLabel('something_new'), 'something new');
  assert.equal(phaseLabel(undefined), 'Working');
});

test('the headline says which rows the score comes from, and which direction is better', () => {
  assert.equal(headline({ primary_label: 'ROC-AUC', test_score: 0.8776, cv_score: 0.83, lower_is_better: false }), 'ROC-AUC 0.878 on held-out rows');
  assert.equal(headline({ primary_label: 'RMSE', test_score: 0.935, cv_score: 0.95, lower_is_better: true }), 'RMSE 0.935 on held-out rows (lower is better)');
  assert.equal(headline({ primary_label: 'R²', test_score: null, cv_score: 0.9, lower_is_better: false }), 'R² 0.9 in cross-validation');
  assert.equal(headline(null), '');
  assert.equal(modelTitle({ name: 'My model' }), 'My model');
  assert.equal(modelTitle({ algorithm_label: 'Ridge regression', target: 'price' }), 'Ridge regression for price');
});

test('the storage footprint names saved models and analyses', () => {
  const lines = footprintLines({ items: [{ kind: 'models', objects: 3, bytes: 2048 }, { kind: 'analyses', objects: 1, bytes: 10 }] }, (n) => `${n} B`);
  assert.deepEqual(lines.map((r) => r.label), ['Prediction models, reports and predictions', 'Saved analysis results']);
  assert.equal(lines[0].text, '3 stored items, 2048 B');
});

/* ------------------------------------------------------------------ navigation and routes */
test('Predictions is marked as built and the navigation model stays consistent; the registry became real in Phase 7', () => {
  assert.deepEqual(validateNavigation(), []);
  const predictions = NAV_ITEMS.find((i) => i.id === 'predictions');
  assert.equal(predictions.status, 'existing');
  assert.equal(predictions.path, '/predictions');
  const registry = NAV_ITEMS.find((i) => i.id === 'models');   // Phase 7 built it, so it is no longer a planned placeholder
  assert.equal(registry.status, 'existing');
  assert.equal(registry.phase, 7);
});

test('the prediction pages are routed with fixed paths before the ones that take an id', () => {
  const source = readFileSync(new URL('../src/app/routes.jsx', import.meta.url), 'utf8');
  const paths = [...source.matchAll(/path: '(\/predictions[^']*)'/g)].map((m) => m[1]);
  assert.deepEqual(paths, ['/predictions/forecast', '/predictions/models', '/predictions/compare', '/predictions/models/:modelId', '/predictions/data/:assetId']);
  const table = paths.map((path) => ({ path }));
  assert.equal(matchRoute(paths.map((path) => ({ path })), '/predictions/forecast').route.path, '/predictions/forecast');
  assert.equal(matchRoute(table, '/predictions/models').route.path, '/predictions/models');
  assert.equal(matchRoute(table, '/predictions/compare').route.path, '/predictions/compare');
  const model = matchRoute(table, '/predictions/models/abc%2F1');
  assert.equal(model.route.path, '/predictions/models/:modelId');
  assert.equal(model.params.modelId, 'abc/1');
  assert.equal(matchRoute(table, '/predictions/data/ds-9').params.assetId, 'ds-9');
  assert.equal(matchRoute(table, '/predictions/data'), null);
  assert.equal(matchRoute(table, '/predictions/models/a/b'), null);
});

test('every result view that can be explained renders the shared Explanation section', () => {
  const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8');
  const wired = {
    '../src/components/analytics/ResultViews.jsx': 'result.explanation',
    '../src/pages/analytics/InsightsTab.jsx': 'i.explanation',
    '../src/pages/analytics/AskTab.jsx': 'a.explanation',
    '../src/pages/projects/ProfilePanel.jsx': 'current.explanation',
    '../src/pages/predictive/EvaluationPanel.jsx': 'ex.evaluation',
    '../src/pages/predictive/ComparisonPanel.jsx': 'ex.ranking',
    '../src/pages/predictive/ExplainPanel.jsx': 'ex.importance',
    '../src/pages/predictive/PredictPanel.jsx': 'result.explanation',
    '../src/pages/predictive/BatchPanel.jsx': 's.explanation',
    '../src/pages/predictive/ObjectivePanel.jsx': 'result.explanation',
    '../src/pages/predictive/PreparationPanel.jsx': 'r.explanation',
    '../src/pages/predictive/ComparePage.jsx': 'c.explanation',
  };
  for (const [file, expression] of Object.entries(wired)) {
    const text = read(file);
    assert.ok(text.includes("Explanation.jsx'"), `${file} imports the shared component`);
    assert.ok(text.includes(`<Explanation value={${expression}`), `${file} shows ${expression}`);
  }
});
