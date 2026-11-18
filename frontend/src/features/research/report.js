// View-models for the combined research report (bundle.report) and for follow-up questions. The report is written by fixed rules on the server from the finished evidence; nothing
// here decides what is true, ranks a source, or writes a sentence. This file only arranges what the server sent and says plainly when an older result has no report.
import {
  CONFIDENCE_LABEL, CONFIDENCE_TONE, ORIGIN_HINT, ORIGIN_LABEL, ORIGIN_TONE, SOURCE_CLASS_LABEL, SOURCE_CLASS_SHORT, STATEMENT_LABEL, STATEMENT_TONE, labelOf,
} from './labels.js';
import { sourceById } from './evidence.js';

export const FOLLOW_UP_MAX = 2000;

/** The report of a result, or null for a result made before reports existed (it still shows its conclusions and evidence). */
export function reportOf(bundle) {
  const r = bundle?.report;
  return r && typeof r === 'object' && Array.isArray(r.sections) ? r : null;
}

/** "In short": the strongest line of each part of the question, each with what kind of evidence it is and the passages it rests on. */
export function answerLines(report) {
  return (report?.answer ?? []).map((a) => ({
    key: `${a.conclusion}|${a.subtopic}`, conclusion: a.conclusion, section: a.section, text: a.text, label: a.label || labelOf(SOURCE_CLASS_LABEL, a.class), origin: a.origin, kind: a.kind,
    confidence: a.confidence, caveats: a.caveats ?? [], evidence: a.evidence ?? [], title: a.title || '',
  }));
}

/** One block per part of the plan, in plan order. A part with nothing found says so, in the server's words. */
export function reportSections(report) {
  return (report?.sections ?? []).map((s) => ({
    id: s.subtopic, title: s.title, scope: s.scope, intent: s.intent, answered: s.status === 'answered', note: s.note || '', items: s.items ?? [],
  }));
}

/** What was calculated on the person's data: one entry per dataset with the runs behind it. */
export function datasetSummary(report) {
  const d = report?.dataset;
  if (!d?.present) return null;
  const datasets = (d.datasets ?? []).map((x) => ({
    assetId: x.asset_id, name: x.name, runs: (x.calculations ?? []).map((c) => ({ runId: c.run_id, kind: c.kind, operations: c.operations ?? [], rows: c.rows_used ?? null, status: c.evidence_status ?? '' })),
  }));
  return { datasets, notes: d.notes ?? [], calculationCount: datasets.reduce((n, x) => n + x.runs.length, 0) };
}

/** The outside sources the report used, grouped by what kind they are (Wikipedia, arXiv preprints, web pages), plus the ones that could not be read. */
export function externalGroups(report, bundle) {
  const ext = report?.external;
  if (!ext?.present) return { groups: [], unread: ext?.unread ?? [], requested: Boolean(ext?.requested), used: Boolean(ext?.used) };
  const groups = Object.entries(ext.by_class ?? {}).map(([cls, ids]) => ({
    cls, label: labelOf(SOURCE_CLASS_SHORT, cls), description: labelOf(SOURCE_CLASS_LABEL, cls),
    sources: ids.map((id) => sourceById(bundle, id)).filter(Boolean),
  }));
  return { groups, unread: ext.unread ?? [], requested: Boolean(ext.requested), used: Boolean(ext.used) };
}

/** Calculated results placed beside outside passages that mention the same variables. `relation` says how close the match is; it is a match of words, never of meaning. */
export function comparisonRows(report) {
  return (report?.comparison ?? []).map((c, i) => ({
    key: `${c.calculated?.evidence}|${i}`, calculated: c.calculated, external: c.external ?? [], relation: c.relation, note: c.note,
  }));
}

/** True when the report is about both the person's data and outside sources, which is when the "kept apart" explanation is shown. */
export const hasSeparation = (report) => Boolean(report?.separation_note);

/** Request body for POST /research/sessions/{id}/follow-up. Returns { body } or { errors }. A follow-up keeps the earlier question's files and settings; web research is only switched
 *  by the person here, and a new plan (with its own approval) is made either way. */
export function buildFollowUpBody(form) {
  const errors = {};
  const question = String(form?.question ?? '').trim();
  if (question.length < 3) errors.question = 'Write the follow-up question.';
  else if (question.length > FOLLOW_UP_MAX) errors.question = `Keep the question under ${FOLLOW_UP_MAX} characters.`;
  if (Object.keys(errors).length) return { errors };
  const options = {};
  if (form?.web === true || form?.web === false) options.web = form.web;
  return { body: { question, options } };
}

/** The line shown on a follow-up session saying what it builds on. */
export function followUpLine(session) {
  const parent = session?.parent;
  if (!parent) return '';
  return parent.question ? `Follow-up to: “${parent.question}”. It starts from the pages and articles that question already read.` : 'A follow-up to an earlier question.';
}

/** The badges shown under one line of the report, with repeats removed: a calculated sentence would otherwise carry four badges that all say "Calculated". Order: where it comes from, what kind of
 *  statement it is, how strong it is, and what kind of source it was read from. Two badges with the same words are shown once. */
export function evidenceTags({ origin, kind, confidence, label }) {
  const all = [
    origin ? { key: 'origin', label: labelOf(ORIGIN_LABEL, origin), tone: ORIGIN_TONE[origin] ?? 'muted', title: ORIGIN_HINT[origin] } : null,
    kind ? { key: 'kind', label: labelOf(STATEMENT_LABEL, kind), tone: STATEMENT_TONE[kind] ?? 'muted' } : null,
    confidence ? { key: 'confidence', label: labelOf(CONFIDENCE_LABEL, confidence), tone: CONFIDENCE_TONE[confidence] ?? 'muted' } : null,
    label ? { key: 'source', label, tone: 'muted' } : null,
  ].filter(Boolean);
  const seen = new Set();
  return all.filter((t) => { const k = t.label.trim().toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; });
}

/** The coherent explanation written by fixed rules from the checked evidence (bundle.report.explanation), or null for an older result. Every statement in it is a quotation with its place. */
export function explanationOf(report) {
  const x = report?.explanation;
  if (!x || x.schema !== 'predictai.explanation/1') return null;
  return {
    subject: x.subject ?? [], task: x.task || '', howToRead: x.how_to_read || '',
    direct: (x.direct_answer ?? []).map((d, i) => ({ key: `d${i}`, part: d.part, text: d.text, support: d.support, cites: d.cites ?? [], evidence: d.evidence ?? [] })),
    sideBySide: (x.side_by_side ?? []).map((e) => ({ key: e.key, name: e.name || e.key, years: e.years_mentioned ?? [], statements: e.statements ?? [] })),
    connections: x.connections ?? [], timing: x.timing ?? null, unknown: x.unknown ?? [],
  };
}

/** Per-provider outcomes of a run: what was asked, how many results came back, how many were read, how many were set aside, and whether the provider worked at all. */
export function providerRows(report) {
  return (report?.providers ?? []).map((p) => ({ provider: p.provider, status: p.status, note: p.note || '', results: p.results ?? 0, read: p.read ?? 0, rejected: p.rejected ?? 0, errors: p.errors ?? [] }));
}

export const PROVIDER_STATUS_LABEL = { ok: 'Worked', partial: 'Partly worked', no_results: 'Nothing found', no_relevant_results: 'Found only unrelated results', failed: 'Unavailable', not_run: 'Not searched', skipped: 'Not searched' };
export const PROVIDER_STATUS_TONE = { ok: 'ok', partial: 'warn', no_results: 'muted', no_relevant_results: 'muted', failed: 'danger', not_run: 'muted', skipped: 'muted' };

/** Results that were found but not used, each with the reason, so a wrong-looking page is explained rather than silently dropped. */
export function rejectedRows(report) {
  return (report?.rejected ?? []).map((r, i) => ({ key: `${r.provider}|${r.url || r.title}|${i}`, title: r.title || r.url || '', url: r.url || '', provider: r.provider || '', reason: r.reason || '' }));
}
