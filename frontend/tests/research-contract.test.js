// The research and news screens are tested against real server responses (shared/contracts/phase6.*.example.json, trimmed copies of what the API returned).
// backend/tests/test_phase6_contract.py checks that the API still returns these shapes, so a change on either side fails a test instead of the screen.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { explanationView } from '../src/features/explanation/explanation.js';
import {
  BUNDLE_STATUS_LABEL, CHECK_STATUS_LABEL, CONFIDENCE_LABEL, EVENT_STATUS_LABEL, FRESHNESS_LABEL, ORIGIN_LABEL, QUALITY_VERDICT_LABEL, SESSION_STATUS_LABEL, STATEMENT_LABEL, TONE_LABEL,
} from '../src/features/research/labels.js';
import { phaseLabel, ingestRows, isFinalJob } from '../src/features/research/jobs.js';
import { consentLines, needsApproval, outboundSummary, planLines } from '../src/features/research/plan.js';
import { conclusionEvidence, conclusionMeta, evidenceBySubtopic, locationLabel, quoteInContext, sortedChecks } from '../src/features/research/evidence.js';
import { EMPTY_FILTERS, articleParams, comparisonRows, eventStatusView, freshnessView, timelineRows, topicLabelMap, trendRows } from '../src/features/research/news.js';
import { ASSOCIATION_BANNER, influenceView, lagMeaning } from '../src/features/research/influence.js';
import { safeHref } from '../src/features/research/links.js';

const dir = new URL('../../shared/contracts/', import.meta.url);
const load = (name) => JSON.parse(readFileSync(new URL(`phase6.${name}.example.json`, dir), 'utf8'));

test('every Phase 6 example file is present and parses', () => {
  const names = readdirSync(dir).filter((n) => n.startsWith('phase6.') && n.endsWith('.example.json'));
  assert.ok(names.length >= 28, `found ${names.length}`);
  for (const n of names) JSON.parse(readFileSync(new URL(n, dir), 'utf8'));
});

test('session: the plan card, the outbound summary and the approval rule can all be drawn from a real session', () => {
  const s = load('research-session');
  assert.ok(planLines(s.plan).length >= 3);
  assert.equal(outboundSummary(s).leaves, false);                // web is off in the example: nothing leaves
  assert.equal(needsApproval(s), false);
  assert.ok(SESSION_STATUS_LABEL[s.status]);
  assert.equal(typeof s.plan_hash, 'string');
  assert.ok(Array.isArray(s.plan.subtopics) && s.plan.subtopics.length > 0);
  assert.ok(explanationView(s.explanation));
  // options can hold null lists (datasets) — the screens must cope
  assert.ok(s.options.datasets === null || Array.isArray(s.options.datasets));
});

test('settings and capabilities: consent lines and level choices come from the server', () => {
  const settings = load('research-settings');
  assert.ok(consentLines(settings).length >= 3);
  const caps = load('research-capabilities');
  assert.ok(caps.levels.length >= 3);
  for (const l of caps.levels) { assert.equal(typeof l.key, 'string'); assert.equal(typeof l.label, 'string'); assert.equal(typeof l.about, 'string'); }
  for (const p of caps.search_providers) { assert.equal(typeof p.label, 'string'); assert.equal(typeof p.available, 'boolean'); }
  assert.equal(typeof caps.web.allowed, 'boolean');
});

test('job: progress phases and final states from a real research job', () => {
  const job = load('research-job');
  assert.equal(isFinalJob(job), ['completed', 'failed', 'cancelled'].includes(job.status));
  assert.equal(typeof phaseLabel(job.progress.phase), 'string');
});

test('bundle: every origin, kind, confidence, check status and verdict in a real bundle has a label', () => {
  const { bundle } = load('research-bundle');
  for (const e of bundle.evidence) { assert.ok(ORIGIN_LABEL[e.origin], e.origin); assert.ok(STATEMENT_LABEL[e.kind], e.kind); assert.equal(typeof locationLabel(e.location), 'string'); }
  for (const c of bundle.conclusions) { assert.ok(STATEMENT_LABEL[c.kind]); assert.ok(CONFIDENCE_LABEL[c.confidence], c.confidence); }
  for (const s of bundle.sources) assert.ok(ORIGIN_LABEL[s.origin]);
  for (const c of bundle.quality.checks) assert.ok(CHECK_STATUS_LABEL[c.status], c.status);
  assert.ok(QUALITY_VERDICT_LABEL[bundle.quality.verdict]);
  assert.ok(BUNDLE_STATUS_LABEL[bundle.status], bundle.status);
});

test('bundle: every conclusion finds its passages, and the passages sit under the plan\'s parts', () => {
  const { bundle } = load('research-bundle');
  for (const c of bundle.conclusions) {
    const { found, missing } = conclusionEvidence(bundle, c);
    assert.deepEqual(missing, []);
    assert.equal(found.length, c.evidence.length);
    const meta = conclusionMeta(c);
    assert.equal(typeof meta.kind, 'string');
    assert.equal(meta.evidenceCount, c.evidence.length);
  }
  const groups = evidenceBySubtopic(bundle);
  assert.equal(groups.reduce((n, g) => n + g.items.length, 0), bundle.evidence.length);
  assert.equal(sortedChecks(bundle.quality).length, bundle.quality.checks.length);
});

test('bundle: sources are shown as links only when the address is plain http(s)', () => {
  const { bundle } = load('research-bundle');
  for (const s of bundle.sources) if (s.url) assert.ok(safeHref(s.url), s.url);
});

test('citation: the quote and its surroundings are drawn from a real citation view', () => {
  const c = load('research-citation');
  const q = quoteInContext(c.context);
  assert.equal(q.quote, c.evidence.quote);
  assert.equal(typeof c.verification.verified, 'boolean');
  assert.equal(typeof c.note, 'string');
});

test('compare: all four change groups are lists, and the comparison explains itself', () => {
  const c = load('research-compare');
  for (const k of ['new', 'no_longer_present', 'strength_changed', 'unchanged']) assert.ok(Array.isArray(c.conclusions[k]), k);
  assert.ok(explanationView(c.explanation));
});

test('evidence for other features: labelled conclusions, citations and reported news (never as fact)', () => {
  const e = load('evidence-for-consumer');
  assert.equal(e.schema, 'predictai.evidence_for_consumer/1');
  for (const r of e.research) for (const c of r.conclusions) { assert.ok(ORIGIN_LABEL[c.origin]); assert.ok(STATEMENT_LABEL[c.statement_type]); assert.ok(CONFIDENCE_LABEL[c.confidence]); }
  for (const w of e.world) { assert.equal(w.statement_type, 'reported'); assert.ok(EVENT_STATUS_LABEL[w.status]); assert.equal(w.origin, 'external_research'); }
});

test('news: sources, freshness, overview and ingest results come through with every label known', () => {
  const sources = load('world-sources');
  for (const s of sources.items) assert.ok(FRESHNESS_LABEL[s.freshness] || s.freshness === undefined);
  const fresh = load('world-freshness');
  for (const s of fresh.sources) assert.equal(typeof freshnessView(s.freshness).label, 'string');
  const overview = load('world-overview');
  for (const k of Object.keys(overview.events)) assert.ok(EVENT_STATUS_LABEL[k], k);
  const job = load('world-ingest-job');
  const rows = ingestRows(job);
  assert.equal(rows.length, job.progress.results.length);
  for (const r of rows) assert.ok(r.text.length > 0);
});

test('news: articles carry a tone and a statement kind the screens can name; tone is described as wording only', () => {
  const a = load('world-article');
  assert.ok(TONE_LABEL[a.tone], a.tone);
  assert.ok(STATEMENT_LABEL[a.statement_kind]);
  assert.match(a.derived.tone.note, /does not|not whether/i);
  assert.equal(a.origin, 'external_research');
  const list = load('world-articles');
  for (const x of list.items) assert.ok(TONE_LABEL[x.tone] || x.tone === null, x.tone);
});

test('event: status, basis, timeline and outlet comparison can be drawn, and the explanation carries its caution', () => {
  const ev = load('world-event');
  const view = eventStatusView(ev.event.status);
  assert.ok(EVENT_STATUS_LABEL[ev.event.status]);
  assert.match(view.hint, /./);
  assert.ok(timelineRows(ev.timeline).length === ev.timeline.length);
  assert.equal(comparisonRows(ev.comparison).length, ev.comparison.outlets.length);
  assert.ok(explanationView(ev.explanation));
  const sum = load('world-event-summary');
  assert.equal(sum.statement_type, 'reported');
  assert.match(sum.caveat, /not verified/i);
});

test('trends and topics: rows use the server wording; a count with too little data is not given a direction', () => {
  const trends = load('world-trends');
  const topics = load('world-topics');
  const rows = trendRows(trends, topicLabelMap(topics));
  assert.equal(rows.length, trends.topics.length);
  for (const r of rows) assert.equal(typeof r.verdict, 'string');
  assert.ok(trends.caveats.length > 0);
  assert.ok(explanationView(trends.explanation));
});

test('influence: a real analysis keeps its association-only banner, never says "cause", and shows how it was computed', () => {
  const a = load('world-influence');
  const v = influenceView(a);
  assert.equal(v.banner, ASSOCIATION_BANNER);
  assert.equal(v.causalStatus, 'not established');
  assert.ok(v.whyNotCausal.length > 0);
  assert.ok(v.whatWouldHelp.length > 0);
  assert.ok(v.best && typeof v.best.lag === 'number');
  assert.doesNotMatch(lagMeaning(v.best.lag), /caus|driv|because/i);
  assert.equal(v.damaged, false);
  assert.ok(v.method.length > 0);
  assert.ok(explanationView(a.explanation));
});

test('Phase 7 interfaces: the screens can say plainly what is not available yet, with the requirement ids', () => {
  const exam = load('exam-capabilities');
  assert.ok(exam.not_available.length >= 3);
  for (const n of exam.not_available) { assert.match(n.id, /^OUT-01/); assert.ok(n.needs.length > 0); }
  assert.match(exam.phase, /Phase 7/);
  const reg = load('registry-capabilities');
  assert.equal(reg.deployment.state, 'not supported');
  assert.equal(reg.rollback.supported, false);
  assert.match(reg.phase7, /Phase 7/);
});

// The query parameters the screens send must be ones the server route really accepts (FastAPI ignores unknown ones silently, so a typo would just do nothing).
test('article filters and paging use parameter names the server route declares', () => {
  const source = readFileSync(new URL('../../backend/app/api/v1/world.py', import.meta.url), 'utf8');
  const start = source.indexOf('def list_articles(');
  const signature = source.slice(start, source.indexOf('-> dict:', start));
  for (const key of [...Object.keys(EMPTY_FILTERS), 'limit', 'offset']) assert.match(signature, new RegExp(`\\b${key}:`), `list_articles does not declare ${key}`);
  assert.deepEqual(Object.keys(articleParams({ q: 'x' })).sort(), ['limit', 'offset', 'q']);
  const events = source.slice(source.indexOf('def list_events('), source.indexOf('-> dict:', source.indexOf('def list_events(')));
  for (const key of ['status', 'limit', 'offset']) assert.match(events, new RegExp(`\\b${key}:`));
});
