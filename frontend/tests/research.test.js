// Pure logic of the research, news and AI-provider screens. No browser: these are the rules the screens rely on.
import assert from 'node:assert/strict';
import test from 'node:test';
import { aiApi, researchApi, worldApi } from '../src/api/research.js';
import { CONFIDENCE_LABEL, EVENT_STATUS_LABEL, ORIGIN_LABEL, STATEMENT_LABEL, humanize, labelOf } from '../src/features/research/labels.js';
import { LINK_REL, domainOf, safeHref } from '../src/features/research/links.js';
import { ingestRows, isFinalJob, phaseLabel } from '../src/features/research/jobs.js';
import { buildSessionBody, consentLines, needsApproval, outboundSummary, pickableAssets, planLines, webOffReasons } from '../src/features/research/plan.js';
import { conclusionEvidence, evidenceBySubtopic, locationLabel, quoteInContext, sortedChecks } from '../src/features/research/evidence.js';
import { EMPTY_FILTERS, ageLabel, articleParams, buildSourceBody, comparisonRows, eventStatusView, hasFilters, timelineRows, topicLabelMap, trendRows } from '../src/features/research/news.js';
import { ASSOCIATION_BANNER, buildInfluenceBody, influenceView, lagMeaning } from '../src/features/research/influence.js';
import { buildProviderBody, enableChecks, probeLine, usageErrors, usageRows } from '../src/features/research/providers.js';

// ---------------------------------------------------------------- API wrappers
function recorder() {
  const calls = [];
  const client = {
    request: (path, options = {}) => { calls.push({ path, ...options }); return Promise.resolve({ data: { ok: true } }); },
    download: (path, options) => { calls.push({ path, ...options, download: true }); return Promise.resolve({ blob: null, filename: 'x' }); },
  };
  return { calls, client };
}

test('research API: paths are encoded, bodies carry only what was given, and the plan hash travels with approve', async () => {
  const { calls, client } = recorder();
  const api = researchApi(client);
  await api.createSession('p/1', { question: 'q' });
  await api.approve('s 1', 'abc');
  await api.run('s1');
  await api.run('s1', 'abc');
  await api.compare('a', 'b');
  await api.exportBundle('b1', 'csv');
  await api.putSettings({ web_enabled: true }, 'p1');
  assert.equal(calls[0].path, '/projects/p%2F1/research/sessions');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[1].path, '/research/sessions/s%201/approve');
  assert.deepEqual(calls[1].body, { plan_hash: 'abc' });
  assert.deepEqual(calls[2].body, {});
  assert.deepEqual(calls[3].body, { approve_plan_hash: 'abc' });
  assert.equal(calls[4].path, '/research/compare?a=a&b=b');
  assert.equal(calls[5].path, '/research/bundles/b1/export?format=csv');
  assert.equal(calls[5].download, true);
  assert.deepEqual(calls[6].body, { web_enabled: true, project_id: 'p1' });
  assert.equal(calls[6].method, 'PATCH');
});

test('world API: filters become a query string, blank ones are dropped', async () => {
  const { calls, client } = recorder();
  const api = worldApi(client);
  await api.articles('p1', { q: 'flood', topic: '', limit: 25, offset: 0 });
  await api.addSource('p1', { name: 'A', feed_url: 'https://a.example/feed' });
  await api.ingest('p1');
  await api.ingest('p1', ['s1']);
  await api.trends('p1', 14);
  assert.equal(calls[0].path, '/projects/p1/world/articles?q=flood&limit=25&offset=0');
  assert.equal(calls[1].method, 'POST');
  assert.deepEqual(calls[2].body, {});
  assert.deepEqual(calls[3].body, { source_ids: ['s1'] });
  assert.equal(calls[4].path, '/projects/p1/world/trends?window_days=14');
});

test('AI API: enabling a paid provider sends the confirmation explicitly, never by default', async () => {
  const { calls, client } = recorder();
  const api = aiApi(client);
  await api.setEnabled('k', true);
  await api.setEnabled('k', true, true);
  assert.deepEqual(calls[0].body, { enabled: true, confirm_paid: false });
  assert.deepEqual(calls[1].body, { enabled: true, confirm_paid: true });
  assert.equal(calls[0].path, '/ai/providers/k/enabled');
});

// ---------------------------------------------------------------- labels
test('labels: known keys are named, unknown keys are shown as themselves and never mapped to a friendlier guess', () => {
  assert.equal(labelOf(ORIGIN_LABEL, 'model_prediction'), 'Model prediction — not a fact');
  assert.equal(labelOf(ORIGIN_LABEL, 'something_new'), 'Something new');
  assert.equal(labelOf(ORIGIN_LABEL, 'toString'), 'ToString');             // inherited object keys are not labels
  assert.equal(humanize(null), '—');
  assert.match(CONFIDENCE_LABEL.prediction, /not an established fact/);
  assert.match(STATEMENT_LABEL.stated, /by the source/);
  assert.match(EVENT_STATUS_LABEL.confirmed, /several outlets/);
  assert.doesNotMatch(Object.values(EVENT_STATUS_LABEL).join(' '), /verified fact|proven/i);
});

test('labels: every origin the server can send has a label, and external material is never described as checked', () => {
  for (const origin of ['user_provided', 'calculated', 'model_prediction', 'external_research']) assert.ok(ORIGIN_LABEL[origin]);
  assert.doesNotMatch(ORIGIN_LABEL.external_research, /verified|checked/i);
});

// ---------------------------------------------------------------- links
test('links: only plain http(s) addresses become links; script, data, file, credentials and markup do not', () => {
  assert.equal(safeHref('https://example.org/a?b=1'), 'https://example.org/a?b=1');
  assert.equal(safeHref(' http://example.org '), 'http://example.org/');
  for (const bad of ['javascript:alert(1)', 'data:text/html,hi', 'file:///etc/passwd', 'ftp://x.example/', '//x.example', 'https://user:pw@x.example/', 'https://x.example/"onclick=1', 'https://x.example/ a', '', null, undefined, 42, `https://x.example/${'a'.repeat(2100)}`]) {
    assert.equal(safeHref(bad), null, String(bad).slice(0, 40));
  }
  assert.equal(domainOf('https://www.example.org/x'), 'example.org');
  assert.equal(domainOf('javascript:1'), '');
  assert.match(LINK_REL, /noopener/);
  assert.match(LINK_REL, /noreferrer/);
  assert.match(LINK_REL, /nofollow/);
});

// ---------------------------------------------------------------- jobs
test('jobs: final states, phase names the server sends, unknown phases shown humanised', () => {
  assert.equal(isFinalJob({ status: 'completed' }), true);
  assert.equal(isFinalJob({ status: 'running' }), false);
  assert.equal(isFinalJob(null), false);
  assert.equal(phaseLabel('wording'), 'Writing conclusions');
  assert.equal(phaseLabel('brand_new_phase'), 'Brand new phase');
  assert.equal(phaseLabel(undefined), 'Working');
});

test('jobs: a news read is summarised from the counts the server reported, failures keep their reason code', () => {
  const rows = ingestRows({ progress: { results: [
    { source_id: 'a', name: 'A', status: 'ok', new: 2, seen_before: 1, duplicates: 1, quarantined: 1, removed_sentences: 3 },
    { source_id: 'b', name: 'B', status: 'failed', error: 'FETCH_FAILED' },
    { source_id: 'c', name: 'C', status: 'skipped' },
  ] } });
  assert.equal(rows.length, 3);
  assert.equal(rows[0].ok, true);
  assert.match(rows[0].text, /2 new, 1 already stored, 1 duplicate, 1 held back/);
  assert.equal(rows[0].removed, 3);
  assert.equal(rows[1].ok, false);
  assert.match(rows[1].text, /FETCH_FAILED/);
  assert.match(rows[2].text, /Skipped/);
  assert.deepEqual(ingestRows(null), []);
  assert.deepEqual(ingestRows({ progress: {} }), []);
});

// ---------------------------------------------------------------- plan
test('plan: the question is required and bounded, web stays off unless asked, and nothing is sent when the form is wrong', () => {
  assert.deepEqual(Object.keys(buildSessionBody({ question: '  ' }).errors), ['question']);
  assert.ok(buildSessionBody({ question: 'x'.repeat(2001) }).errors.question);
  assert.ok(buildSessionBody({ question: 'q', freshnessDays: '0' }).errors.freshnessDays);
  assert.ok(buildSessionBody({ question: 'q', freshnessDays: 'abc' }).errors.freshnessDays);
  const ok = buildSessionBody({ question: ' How high? ', documents: ['d1', 'd1'], level: 'basic' });
  assert.equal(ok.errors, undefined);
  assert.equal(ok.body.question, 'How high?');
  assert.equal(ok.body.level, 'basic');
  assert.equal(ok.body.purpose, 'general');
  assert.deepEqual(ok.body.options, { web: false, documents: ['d1'], datasets: [], ai_summary: false, reuse_earlier: false });
  const web = buildSessionBody({ question: 'q', web: true, providers: ['wikipedia', 'wikipedia'], freshnessDays: '30', purpose: 'education', title: ' T ' });
  assert.deepEqual(web.body.options.providers, ['wikipedia']);
  assert.equal(web.body.options.freshness_days, 30);
  assert.equal(web.body.purpose, 'education');
  assert.equal(web.body.title, 'T');
  assert.equal(buildSessionBody({ question: 'q', web: false, providers: ['wikipedia'] }).body.options.providers, undefined);   // providers are not even named when web is off
});

test('plan: running needs approval only when the server says so, and what leaves the server is exactly what it listed', () => {
  assert.equal(needsApproval({ needs_approval: true, plan_approved: false }), true);
  assert.equal(needsApproval({ needs_approval: true, plan_approved: true }), false);
  assert.equal(needsApproval({ needs_approval: false }), false);
  assert.equal(needsApproval(null), false);
  assert.deepEqual(outboundSummary({ web: { allowed_now: false, will_send: ['x'] } }).leaves, false);
  const out = outboundSummary({ web: { allowed_now: true, will_send: ['river flood'], note: 'n' }, plan: { web: { providers: ['wikipedia'] } } });
  assert.equal(out.leaves, true);
  assert.deepEqual(out.queries, ['river flood']);
  assert.deepEqual(out.providers, ['wikipedia']);
  assert.equal(outboundSummary(null).leaves, false);
  assert.deepEqual(webOffReasons({ web: { why_not: ['a', '', 'b'] } }), ['a', 'b']);
  assert.deepEqual(webOffReasons(null, { web: { why_not: ['c'] } }), ['c']);
});

test('plan: the plan card says plainly when nothing leaves the server', () => {
  const lines = planLines({ subtopics: [{}, {}], level_label: 'Topic overview', local: { documents: ['d'], datasets: [] }, web: { enabled: false }, min_sources: 2 });
  assert.match(lines[0], /2 parts .*Topic overview/);
  assert.match(lines[1], /1 document, 0 datasets.*stays on this server/);
  assert.match(lines[2], /Web: off. Nothing leaves this server/);
  assert.match(lines[3], /2 independent sources/);
  assert.match(planLines({ subtopics: [{}], web: { enabled: true, max_pages: 4, providers: ['arxiv'] }, local: {} }).join(' '), /up to 4 pages from arxiv/);
  assert.deepEqual(planLines(null), []);
});

test('plan: only ready files of the right kind in the list can be picked', () => {
  const assets = [{ id: 1, kind: 'document', status: 'ready' }, { id: 2, kind: 'dataset', status: 'ready' }, { id: 3, kind: 'document', status: 'failed' }, { id: 4, kind: 'document', deleted_at: 'x' }, null];
  assert.deepEqual(pickableAssets(assets, 'document').map((a) => a.id), [1]);
  assert.deepEqual(pickableAssets(assets, 'dataset').map((a) => a.id), [2]);
  assert.deepEqual(pickableAssets(undefined, 'document'), []);
});

test('plan: the consent summary repeats the server reasons and says nothing is sent when external AI is off', () => {
  const lines = consentLines({ effective: { web_allowed: false, news_allowed: true, external_ai: false, reasons: ['The platform owner has not enabled web research.'] } });
  assert.deepEqual(lines, ['Web research is off.', 'Reading news feeds is allowed.', 'Nothing is sent to an outside AI service.', 'The platform owner has not enabled web research.']);
  assert.deepEqual(consentLines(null), []);
});

// ---------------------------------------------------------------- evidence
test('evidence: a citation keeps its text exactly; locations say when a page number is an estimate', () => {
  assert.deepEqual(quoteInContext({ before: 'a', quote: 'b', after: 'c' }), { before: 'a', quote: 'b', after: 'c' });
  assert.deepEqual(quoteInContext(null), { before: '', quote: '', after: '' });
  assert.equal(locationLabel({ page_start: 3, page_end: 3 }), 'Page 3');
  assert.equal(locationLabel({ page_start: 3, page_end: 4, page_estimated: true }), 'Pages 3–4 (estimated)');
  assert.equal(locationLabel({ section: 'Method' }), 'Section: Method');
  assert.equal(locationLabel({ page_start: null }), 'Location not recorded');
  assert.equal(locationLabel(null), 'Location not recorded');
});

test('evidence: a conclusion lists its passages in order and a missing passage is reported, not skipped', () => {
  const bundle = { evidence: [{ id: 'E1', subtopic: 't1' }, { id: 'E2', subtopic: 'tx' }], plan: { subtopics: [{ id: 't1', title: 'Facts' }, { id: 't2', title: 'Background' }] } };
  const r = conclusionEvidence(bundle, { evidence: ['E2', 'E9', 'E1'] });
  assert.deepEqual(r.found.map((e) => e.id), ['E2', 'E1']);
  assert.deepEqual(r.missing, ['E9']);
  const groups = evidenceBySubtopic(bundle);
  assert.deepEqual(groups.map((g) => [g.title, g.items.length]), [['Facts', 1], ['Background', 0], ['Other', 1]]);
});

test('evidence: failing and warning checks are shown before passing ones', () => {
  const order = sortedChecks({ checks: [{ status: 'pass', id: 'a' }, { status: 'info', id: 'b' }, { status: 'fail', id: 'c' }, { status: 'warn', id: 'd' }] }).map((c) => c.id);
  assert.deepEqual(order, ['c', 'd', 'b', 'a']);
  assert.deepEqual(sortedChecks(null), []);
});

// ---------------------------------------------------------------- news
test('news: only filled filters and valid dates become parameters; paging is explicit', () => {
  assert.deepEqual(articleParams({}), { limit: 25, offset: 0 });
  assert.deepEqual(articleParams({ q: ' flood ', topic: 'disasters', since: '2026-01-01', until: 'yesterday', tone: '' }, 2), { q: 'flood', topic: 'disasters', since: '2026-01-01', limit: 25, offset: 50 });
  assert.equal(articleParams({}, -3).offset, 0);
  assert.equal(hasFilters(EMPTY_FILTERS), false);
  assert.equal(hasFilters({ q: ' x ' }), true);
});

test('news: an event status always carries its caveat, and an unknown status is treated as a claim', () => {
  assert.match(eventStatusView('confirmed').hint, /agreement, not proof/);
  assert.equal(eventStatusView('disputed').tone, 'danger');
  assert.match(eventStatusView('something_else').hint, /claim/);
});

test('news: ages are described without pretending to precision', () => {
  assert.equal(ageLabel(0.2), 'less than an hour ago');
  assert.equal(ageLabel(5), '5 hours ago');
  assert.equal(ageLabel(1), '1 hour ago');
  assert.equal(ageLabel(72), '3 days ago');
  assert.equal(ageLabel(null), 'unknown');
  assert.equal(ageLabel(-1), 'unknown');
});

test('news: trend rows keep the server wording and never invent a direction; topic names come from the topics list', () => {
  const rows = trendRows({ topics: [{ name: 'disasters', label: 'too few to say', recent: 3, previous: 0, z: null }, { name: 'x', label: 'rising', recent: 9, previous: 2, z: 2.4 }] }, topicLabelMap({ items: [{ topic: 'disasters', label: 'Disasters and weather' }] }));
  assert.deepEqual(rows.map((r) => [r.topic, r.verdict, r.z]), [['Disasters and weather', 'too few to say', null], ['x', 'rising', 2.4]]);
  assert.deepEqual(trendRows(null), []);
});

test('news: adding a feed needs a name, an http(s) address and the terms acknowledgement; full text is never requested', () => {
  assert.deepEqual(Object.keys(buildSourceBody({}).errors).sort(), ['feedUrl', 'name', 'termsAcknowledged']);
  assert.ok(buildSourceBody({ name: 'A', feedUrl: 'ftp://x.example/f', termsAcknowledged: true }).errors.feedUrl);
  assert.ok(buildSourceBody({ name: 'A', feedUrl: 'https://x.example/f', termsAcknowledged: true, retentionDays: '0' }).errors.retentionDays);
  const ok = buildSourceBody({ name: ' A ', feedUrl: 'https://x.example/f', termsAcknowledged: true, retentionDays: '14', publisher: ' P ' });
  assert.deepEqual(ok.body, { name: 'A', feed_url: 'https://x.example/f', policy: { terms_acknowledged: true, retention_days: 14 }, publisher: 'P' });
  assert.equal('full_text' in ok.body.policy, false);
});

test('news: timeline and comparison rows pass the server values through unchanged', () => {
  assert.deepEqual(timelineRows([{ article_id: 'a', time: 't', headline: 'h', publisher: 'p', kind: 'stated', tone: 'neutral', url: 'u', first_reported: true }])[0], { key: 'a', time: 't', headline: 'h', publisher: 'p', kind: 'stated', tone: 'neutral', url: 'u', first: true });
  const c = comparisonRows({ outlets: [{ outlet: 'o', headline: 'h', lead: 'l', kind: 'stated', tone: 'negative', numbers: ['1'], only_this_outlet_gives: [], url: 'u', hours_after_first: 2 }] });
  assert.equal(c[0].hoursAfterFirst, 2);
  assert.deepEqual(comparisonRows(null), []);
});

// ---------------------------------------------------------------- influence
test('influence: the form needs a dataset and both columns, and lag stays inside the server limit', () => {
  assert.deepEqual(Object.keys(buildInfluenceBody({}).errors).sort(), ['assetId', 'dateColumn', 'valueColumn']);
  assert.ok(buildInfluenceBody({ assetId: 'a', dateColumn: 'd', valueColumn: 'v', maxLag: 15 }).errors.maxLag);
  assert.ok(buildInfluenceBody({ assetId: 'a', dateColumn: 'd', valueColumn: 'v', maxLag: 1.5 }).errors.maxLag);
  assert.equal(buildInfluenceBody({ assetId: 'a', dateColumn: 'd', valueColumn: 'v', maxLag: 0 }).errors, undefined);
  const ok = buildInfluenceBody({ assetId: 'a', dateColumn: 'd', valueColumn: 'v', maxLag: '5', topic: ' disasters ', entity: '', label: 'L' });
  assert.deepEqual(ok.body, { asset_id: 'a', date_column: 'd', value_column: 'v', aggregate: 'mean', measure: 'count', max_lag: 5, topic: 'disasters', label: 'L' });
});

test('influence: whatever the result, the screen data keeps the association-only banner and the causal status', () => {
  const v = influenceView({ integrity_ok: false, payload: { result: { causal_status: 'not established', status: 'ok', best: { lag: 2, rho: 0.9, p_adjusted: 0.001, n: 38 }, warnings: ['w'] } }, warnings: ['w', 'x'] });
  assert.equal(v.banner, ASSOCIATION_BANNER);
  assert.match(v.banner, /does not show that one caused the other/);
  assert.equal(v.causalStatus, 'not established');
  assert.equal(v.damaged, true);
  assert.deepEqual(v.warnings, ['w', 'x']);
  const empty = influenceView(null);
  assert.equal(empty.banner, ASSOCIATION_BANNER);
  assert.equal(empty.causalStatus, 'not established');
  assert.equal(empty.best, null);
});

test('influence: lag wording says which came first and never says one drove the other', () => {
  assert.match(lagMeaning(2), /Coverage came 2 days before/);
  assert.match(lagMeaning(1), /1 day before/);
  assert.match(lagMeaning(-3), /data changed 3 days before the coverage/);
  assert.match(lagMeaning(0), /same day/);
  assert.equal(lagMeaning(null), '');
  for (const n of [-3, -1, 0, 1, 4]) assert.doesNotMatch(lagMeaning(n), /caus|driv|because|effect/i);
});

// ---------------------------------------------------------------- AI providers
test('providers: a secret is named by environment variable, never typed; limits mirror the server', () => {
  const base = { key: 'local-llm', label: 'Local', baseUrl: 'http://127.0.0.1:11434', model: 'llama3', kind: 'ollama' };
  assert.equal(buildProviderBody(base).errors, undefined);
  assert.ok(buildProviderBody({ ...base, secretEnv: 'sk-abcdef123456' }).errors.secretEnv);
  assert.ok(buildProviderBody({ ...base, secretEnv: 'MY_KEY' }).errors.secretEnv);
  assert.equal(buildProviderBody({ ...base, secretEnv: 'PREDICTAI_AI_KEY_HOST' }).body.secret_env, 'PREDICTAI_AI_KEY_HOST');
  assert.ok(buildProviderBody({ ...base, key: 'Bad Key' }).errors.key);
  assert.ok(buildProviderBody({ ...base, baseUrl: 'ollama:11434' }).errors.baseUrl);
  assert.ok(buildProviderBody({ ...base, timeoutS: 1 }).errors.timeoutS);
  assert.ok(buildProviderBody({ ...base, maxRetries: 4 }).errors.maxRetries);
  assert.ok(buildProviderBody({ ...base, priority: 0 }).errors.priority);
  const full = buildProviderBody({ ...base, key: 'LOCAL-LLM', tasks: ['summarize', 'bogus'], timeoutS: '30', paid: true });
  assert.equal(full.body.key, 'local-llm');
  assert.deepEqual(full.body.tasks, ['summarize']);
  assert.equal(full.body.timeout_s, 30);
  assert.equal(full.body.paid, true);
});

test('providers: a paid provider is never enabled without confirmation, and blocked ones say why', () => {
  const listing = { paid_allowed: false, remote_allowed: false };
  const paid = enableChecks({ paid: true, data_leaves_platform: true, secret_env: 'PREDICTAI_AI_KEY_X', has_secret: false }, listing);
  assert.match(paid.blocked, /Paid providers are switched off/);
  assert.equal(paid.needsPaidConfirm, true);
  assert.ok(paid.notes.some((n) => /leaves this server/.test(n)));
  assert.ok(paid.notes.some((n) => /PREDICTAI_AI_KEY_X is not set/.test(n)));
  const local = enableChecks({ paid: false, data_leaves_platform: false }, listing);
  assert.equal(local.blocked, '');
  assert.equal(local.needsPaidConfirm, false);
  assert.match(enableChecks(null, listing).blocked, /not found/);
});

// ---------------------------------------------------------------- consent form
import { choosableProviders, diffConsent, formFromConsent, projectNarrowingNote } from '../src/features/research/consent.js';

test('consent: only what changed is sent, and nothing is sent when nothing changed', () => {
  const stored = { web_enabled: false, news_enabled: false, external_ai: false, providers: [], freshness_days: null, cache_days: null };
  const form = formFromConsent(stored);
  assert.deepEqual(diffConsent(stored, form), { changes: {} });
  assert.deepEqual(diffConsent(stored, { ...form, webEnabled: true, providers: ['wikipedia'] }), { changes: { web_enabled: true, providers: ['wikipedia'] } });
  assert.deepEqual(diffConsent({ ...stored, providers: ['a', 'b'] }, { ...form, providers: ['b', 'a'] }), { changes: {} });          // order does not count as a change
  assert.deepEqual(diffConsent(stored, { ...form, externalAi: true, newsEnabled: true }).changes, { news_enabled: true, external_ai: true });
});

test('consent: numbers are checked before sending and an unchanged blank is left alone', () => {
  const stored = { providers: [], freshness_days: null, cache_days: 7 };
  const form = formFromConsent(stored);
  assert.deepEqual(diffConsent(stored, { ...form, freshnessDays: '30' }), { changes: { freshness_days: 30 } });
  assert.ok(diffConsent(stored, { ...form, freshnessDays: '0' }).errors.freshnessDays);
  assert.ok(diffConsent(stored, { ...form, freshnessDays: '3651' }).errors.freshnessDays);
  assert.ok(diffConsent(stored, { ...form, cacheDays: '91' }).errors.cacheDays);
  assert.ok(diffConsent(stored, { ...form, cacheDays: '1.5' }).errors.cacheDays);
  assert.ok(diffConsent(stored, { ...form, cacheDays: '' }).errors.cacheDays);
  assert.deepEqual(diffConsent(stored, { ...form, cacheDays: '90' }), { changes: { cache_days: 90 } });
});

test('consent: only providers the platform can reach are offered, and a project is told it can only narrow', () => {
  assert.deepEqual(choosableProviders({ providers: [{ key: 'a', available: true }, { key: 'b', available: false }] }).map((p) => p.key), ['a']);
  assert.deepEqual(choosableProviders(null), []);
  assert.equal(projectNarrowingNote({ person: { web_enabled: true, news_enabled: true, external_ai: true } }).length, 0);
  assert.equal(projectNarrowingNote({ person: { web_enabled: false, news_enabled: true, external_ai: false } }).length, 2);
});

test('providers: usage is shown as the server counted it, and a failed test keeps its reason', () => {
  const rows = usageRows({ rows: [{ provider: 'ollama', task: 'summarize', outcome: 'ok', calls: 3, units_in: 100, units_out: 40, avg_latency_ms: 812 }] });
  assert.deepEqual(rows[0], { key: 'ollama|summarize|ok|0', provider: 'ollama', task: 'summarize', outcome: 'ok', calls: 3, unitsIn: 100, unitsOut: 40, avgMs: 812 });
  assert.deepEqual(usageRows(null), []);
  assert.deepEqual(usageErrors({ errors: [{ provider: 'x', code: 'TIMEOUT', count: 2, last_at: 't' }] }), [{ key: 'x|TIMEOUT', provider: 'x', code: 'TIMEOUT', count: 2, lastAt: 't' }]);
  assert.equal(probeLine(null), 'Never tested.');
  assert.match(probeLine({ ok: true, checked_at: 'now' }), /worked \(now\)/);
  assert.match(probeLine({ ok: false, message: 'refused', error: 'E' }), /failed: refused/);
  assert.match(probeLine({ ok: false, error: 'E' }), /failed \(E\)/);
});
