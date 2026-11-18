// Helpers for showing an evidence bundle: quotes in context, which conclusion rests on which passage, and plain locations.
// Everything displayed was written or measured by the server; the browser only arranges it and never rewrites a quote.
import { labelOf, CONFIDENCE_LABEL, STATEMENT_LABEL } from './labels.js';

/** Splits a citation context into three parts so the quoted text can be marked. If the quote is not in the context the quote is shown on its own. */
export function quoteInContext(context) {
  const quote = String(context?.quote ?? '');
  return { before: String(context?.before ?? ''), quote, after: String(context?.after ?? '') };
}

/** "Page 3", "Pages 3–4 (estimated)", "Section: Method" — built from the saved location; a missing location says so. */
export function locationLabel(location) {
  if (!location) return 'Location not recorded';
  const parts = [];
  const start = location.page_start;
  const end = location.page_end;
  if (start !== null && start !== undefined) {
    const pages = end && end !== start ? `Pages ${start}–${end}` : `Page ${start}`;
    parts.push(location.page_estimated ? `${pages} (estimated)` : pages);
  }
  if (location.section) parts.push(`Section: ${location.section}`);
  return parts.length ? parts.join(', ') : 'Location not recorded';
}

/** Evidence passages behind one conclusion, in the order the conclusion lists them. A listed id that is missing is reported, not skipped silently. */
export function conclusionEvidence(bundle, conclusion) {
  const byId = new Map((bundle?.evidence ?? []).map((e) => [e.id, e]));
  const found = [];
  const missing = [];
  for (const id of conclusion?.evidence ?? []) {
    if (byId.has(id)) found.push(byId.get(id));
    else missing.push(id);
  }
  return { found, missing };
}

export function sourceById(bundle, id) {
  return (bundle?.sources ?? []).find((s) => s.id === id) ?? null;
}

export function sourceTitle(source) {
  return source?.title || source?.url || 'Untitled source';
}

/** Groups the evidence list by sub-topic title using the plan, so a reader sees the question's parts in order. Evidence with an unknown part goes last. */
export function evidenceBySubtopic(bundle) {
  const parts = bundle?.plan?.subtopics ?? [];
  const groups = parts.map((p) => ({ id: p.id, title: p.title, items: [] }));
  const index = new Map(groups.map((g) => [g.id, g]));
  const other = { id: '', title: 'Other', items: [] };
  for (const e of bundle?.evidence ?? []) (index.get(e.subtopic) ?? other).items.push(e);
  if (other.items.length) groups.push(other);
  return groups;
}

/** Conclusion line for tables and exports: the text, how it is known, and how many passages and sources stand behind it. */
export function conclusionMeta(conclusion) {
  return {
    kind: labelOf(STATEMENT_LABEL, conclusion?.kind),
    confidence: labelOf(CONFIDENCE_LABEL, conclusion?.confidence),
    evidenceCount: conclusion?.evidence?.length ?? 0,
    sourceCount: conclusion?.sources?.length ?? 0,
    aiAuthored: conclusion?.authored_by && conclusion.authored_by !== 'extracted',
  };
}

/** Warning and failing checks first, so the reader meets problems before passes. */
export function sortedChecks(quality) {
  const rank = { fail: 0, warn: 1, info: 2, pass: 3 };
  return [...(quality?.checks ?? [])].sort((a, b) => (rank[a.status] ?? 4) - (rank[b.status] ?? 4));
}

/** Download formats the server offers for a bundle. */
export const EXPORT_FORMATS = Object.freeze([
  { key: 'markdown', label: 'Markdown', ext: 'md' },
  { key: 'csv', label: 'CSV (evidence table)', ext: 'csv' },
  { key: 'json', label: 'JSON (full bundle)', ext: 'json' },
]);

/** Sources the person can open in a new tab (http/https only) — others are listed without a link. */
export function describeSourceLine(source) {
  const bits = [source?.kind ? String(source.kind).replace(/_/g, ' ') : null];
  if (source?.reliability?.level) bits.push(`reliability: ${source.reliability.level}`);
  if (source?.freshness?.label && source.freshness.label !== 'not applicable') bits.push(source.freshness.label);
  return bits.filter(Boolean).join(' · ');
}
