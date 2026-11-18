// Plain-language labels and small pure helpers for the Phase 7 screens. No figure is computed here: every number shown comes from the server.

export const SECTION_STATE_TONE = Object.freeze({ ok: 'ok', degraded: 'warn', stale: 'warn', unavailable: 'danger' });
export const SECTION_STATE_LABEL = Object.freeze({
  ok: 'Working',
  degraded: 'Degraded',
  stale: 'No recent signal',
  unavailable: 'Cannot be measured',
});

export const READINESS_LABEL = Object.freeze({ ready: 'Ready to export', needs_review: 'Needs review', incomplete: 'Incomplete' });
export const READINESS_TONE = Object.freeze({ ready: 'ok', needs_review: 'warn', incomplete: 'danger' });

export const LIFECYCLE_LABEL = Object.freeze({
  draft: 'Draft', registered: 'Registered', evaluated: 'Evaluated', staging: 'Staging', active: 'Active', archived: 'Archived', failed: 'Failed',
});
export const LIFECYCLE_TONE = Object.freeze({ draft: 'muted', registered: 'muted', evaluated: 'accent', staging: 'warn', active: 'ok', archived: 'muted', failed: 'danger' });
// Mirrors the server's lifecycle rules (backend registry TRANSITIONS). The server decides on every request; the version page uses the server's own
// `allowed_transitions`, and this table is only a fallback for lists and is covered by a test against the server's rules.
export const LIFECYCLE_NEXT = Object.freeze({
  draft: ['registered', 'failed'], registered: ['evaluated', 'archived', 'failed'], evaluated: ['registered', 'staging', 'archived', 'failed'],
  staging: ['evaluated', 'active', 'archived'], active: ['staging', 'archived'], archived: ['registered', 'evaluated'], failed: ['registered', 'archived'],
});
// Moves the server refuses without `confirm: true` (backend NEEDS_CONFIRM).
export const CONFIRM_MOVES = Object.freeze([['staging', 'active'], ['active', 'archived'], ['active', 'staging']]);
export const LIFECYCLE_NOTE = 'A lifecycle state is a record inside PredictAI. It does not mean the model is deployed or serving anywhere.';

export const ROLE_LABEL = Object.freeze({ viewer: 'Viewer (can look)', editor: 'Editor (can change shared items)', admin: 'Admin (manages the workspace)' });
export const ROLE_HINT = Object.freeze({
  viewer: 'Can open what is shared, cannot change it.', editor: 'Can open and edit what is shared with edit permission.', admin: 'Can invite people, change roles and see the workspace event log.',
});

export const RESOURCE_LABEL = Object.freeze({
  report: 'Report', model: 'Model', research: 'Research result', assessment: 'Mock exam', analysis: 'Analysis', exam_analysis: 'Exam analysis', dataset: 'Dataset', document: 'Document', conversation: 'Conversation',
});

export const SOURCE_LABEL = Object.freeze({
  research: 'A research result', dataset: 'A dataset', prediction: 'A model’s predictions', exam: 'An exam analysis or mock exam', scenario: 'A scenario run', combined: 'Several sources together',
});

export const EXPORT_FORMATS = Object.freeze(['pdf', 'docx', 'pptx', 'md', 'csv', 'xlsx', 'png']);
export const EXPORT_FORMAT_LABEL = Object.freeze({ pdf: 'PDF', docx: 'Word (.docx)', pptx: 'PowerPoint (.pptx)', md: 'Markdown', csv: 'CSV (tables)', xlsx: 'Excel (tables)', png: 'Image of the first page' });

export const ORIGIN_LABEL = Object.freeze({
  past_paper: 'From a past paper', source_derived: 'Built from your material', template_generated: 'From a template', user_edited: 'Edited by you', ai_written: 'Written by AI, check it',
});

export const FREQUENCY_LABEL = Object.freeze({ once: 'Once', daily: 'Every day', weekly: 'Every week', monthly: 'Every month' });
export const SCHEDULE_STATUS_LABEL = Object.freeze({ active: 'Active', paused: 'Paused', ended: 'Ended', blocked: 'Blocked: access to the report ended' });
export const DELIVERY_OUTCOME_LABEL = Object.freeze({ pending: 'Waiting', sent: 'Delivered', failed: 'Failed, will retry', dead: 'Failed for good' });

export const KIND_LABEL = Object.freeze({
  root_cause: 'Possible drivers', explain_metric: 'Metric explained', scenario: 'Scenario', causal_candidates: 'Cause candidates',
});

/** A short, honest line for a section state. */
export function stateText(state) {
  return SECTION_STATE_LABEL[state] ?? 'Unknown';
}

/** Buttons to offer for a version in a given state (the server still decides). */
export function nextStates(status) {
  return LIFECYCLE_NEXT[status] ?? [];
}

export function needsConfirm(from, to) {
  return CONFIRM_MOVES.some(([a, b]) => a === from && b === to);
}

/** Splits an email list typed as "a@x.com, b@y.com" without accepting obvious junk. Returns { ok, bad }. */
export function parseEmails(text) {
  const parts = String(text ?? '').split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
  const ok = [];
  const bad = [];
  for (const p of parts) (/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(p) && p.length <= 254 ? ok : bad).push(p);
  return { ok: [...new Set(ok.map((e) => e.toLowerCase()))], bad };
}

/** Maximum documents text for the quota card. */
export function quotaText(used, max) {
  if (max === null || max === undefined) return `${used} documents (no limit with Special access)`;
  return `${used} of ${max} documents`;
}

/** Turns a number of bytes into text (the server sends sizes; this only formats them). */
export function bytesText(n) {
  if (!Number.isFinite(n) || n < 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

/** Reasons a report is not ready, exactly as the server wrote them. */
export function readinessProblems(readiness) {
  return Array.isArray(readiness?.reasons) ? readiness.reasons.filter((r) => typeof r === 'string' && r) : [];
}

/** Edit operation helpers: build the server's op objects so pages never hand-write them. */
export const ops = Object.freeze({
  title: (text) => ({ op: 'set_title', text }),
  heading: (sectionId, text) => ({ op: 'set_heading', section_id: sectionId, text }),
  editClaim: (blockId, claim, text) => ({ op: 'edit_claim', block_id: blockId, claim, text }),
  markSection: (sectionId, status) => ({ op: 'mark_section', section_id: sectionId, status }),
  removeBlock: (blockId) => ({ op: 'remove_block', block_id: blockId }),
  citationStyle: (style) => ({ op: 'set_settings', settings: { citation_style: style } }),
});
