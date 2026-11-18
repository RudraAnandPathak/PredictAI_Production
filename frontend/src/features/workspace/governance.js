/** Pure helpers for retention and deletion screens (no network, no React). */
export const FOOTPRINT_LABEL = {
  original: 'Original file',
  table: 'Cleaned and converted tables',
  extracted_text: 'Extracted text and structure',
  profiles: 'Data profiles',
  previews: 'Unconfirmed cleaning previews',
  analyses: 'Saved analysis results',
  models: 'Prediction models, reports and predictions',
};

/** The date part of a stored timestamp, for an <input type="date">. Empty when there is none. */
export function dateInputValue(iso) {
  return typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : '';
}

/** Earliest date the server will accept (tomorrow, in UTC), as YYYY-MM-DD. */
export function earliestRetention(now = new Date()) {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  return next.toISOString().slice(0, 10);
}

/** Client-side mirror of the server rule, so people get an instant, plain message. */
export function checkRetention(value, now = new Date()) {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) return 'Choose a valid date.';
  if (value < earliestRetention(now)) return 'Choose a date in the future.';
  const limit = new Date(Date.UTC(now.getUTCFullYear() + 10, now.getUTCMonth(), now.getUTCDate())).toISOString().slice(0, 10);
  return value > limit ? 'Choose a date within the next ten years.' : null;
}

export function footprintLines(fp, formatBytes) {
  return (fp?.items ?? []).map((i) => ({ kind: i.kind, label: FOOTPRINT_LABEL[i.kind] ?? i.kind, text: `${i.objects} stored ${i.objects === 1 ? 'item' : 'items'}, ${formatBytes(i.bytes)}` }));
}

export const DELETION_SCOPE = { asset: 'File deleted', project: 'Project deleted', retention: 'File expired and deleted', account: 'Account data deleted' };
