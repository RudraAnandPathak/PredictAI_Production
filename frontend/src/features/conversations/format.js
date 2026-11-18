// Pure helpers for the Conversations page (no React, no network), so they can be tested on their own.

export const KIND_LABEL = Object.freeze({ data: 'Dataset', research: 'Research' });
export const kindLabel = (kind) => KIND_LABEL[kind] ?? 'Conversation';

export const TARGET_NOUN = Object.freeze({ data: 'dataset', research: 'research workspace' });

/** A short line for the list: the last message, one line, never more than `max` characters. */
export function previewOf(text, max = 100) {
  const one = String(text ?? '').replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max - 1).trimEnd()}…` : one;
}

/** Day heading for a list entry, from ISO time strings: Today, Yesterday, This week, Earlier. Local dates, injected clock for tests. */
export function dayGroup(iso, now = new Date()) {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return 'Earlier';
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(t)) / 86400000);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return 'This week';
  return 'Earlier';
}

export const GROUP_ORDER = Object.freeze(['Today', 'Yesterday', 'This week', 'Earlier']);

/** Items grouped by day heading, in display order, empty groups left out. Input order (newest first) is kept inside each group. */
export function groupByDay(items, now = new Date()) {
  const buckets = new Map(GROUP_ORDER.map((g) => [g, []]));
  for (const item of items ?? []) buckets.get(dayGroup(item.updated_at, now)).push(item);
  return GROUP_ORDER.filter((g) => buckets.get(g).length).map((g) => ({ label: g, items: buckets.get(g) }));
}

export const MAX_QUESTION = 2400;                       // the server accepts 2,500; this keeps a margin for what it trims

/** Whether the question box holds something that can be sent, and why not if it does not. */
export function questionProblem(text) {
  const t = String(text ?? '').trim();
  if (!t) return 'Type a question.';
  if (t.length > MAX_QUESTION) return `Keep the question under ${MAX_QUESTION} characters (it is ${t.length}).`;
  return null;
}

/** The computed steps behind a dataset answer, as plain strings, when the server stored them. */
export function stepsOf(message) {
  const steps = message?.details?.steps;
  return Array.isArray(steps) ? steps.filter((s) => typeof s === 'string' && s.trim()).slice(0, 12) : [];
}

/** What the server says about how a research answer was produced, in words; null for messages that carry no such record. */
export function answerNote(message) {
  const a = message?.answer;
  if (!a) return null;
  if (a.mode === 'model') return `Written by ${a.provider || 'a language model'}; sentences the sources did not support were removed${a.removed_unsupported ? ` (${a.removed_unsupported})` : ''}.`;
  if (a.mode === 'extractive') return 'Quoted directly from your sources. No language model was used.';
  if (a.mode === 'insufficient_evidence') return 'Your sources did not contain enough to answer, so nothing is asserted.';
  return null;
}

/** Which actions make sense for the conversation, given what the person picked in the "new conversation" form. */
export function canStart(kind, targetId) {
  return (kind === 'data' || kind === 'research') && typeof targetId === 'string' && targetId.length > 0;
}
