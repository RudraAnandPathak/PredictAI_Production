// Display helpers for analytics. They only FORMAT numbers the server computed; nothing here calculates a statistic.

export function formatNumber(value, places = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (typeof value !== 'number') return String(value);
  if (!Number.isFinite(value)) return '—';
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return value.toLocaleString('en-US');
  const abs = Math.abs(value);
  if (abs !== 0 && abs < 10 ** -places) return value.toExponential(2);
  if (abs >= 1000) return Math.round(value).toLocaleString('en-US');
  return value.toLocaleString('en-US', { maximumFractionDigits: places });
}

export function formatP(p) {
  if (p === null || p === undefined) return '—';
  return p < 0.001 ? 'p < 0.001' : `p = ${p.toFixed(3)}`;
}

export function formatPercent(value, places = 1) {
  return value === null || value === undefined ? '—' : `${Number(value).toFixed(places)}%`;
}

export const EVIDENCE_LABEL = Object.freeze({
  VERIFIED: 'Evidence verified',
  INCOMPLETE: 'Evidence incomplete',
});

export const CATEGORY_LABEL = Object.freeze({
  descriptive: 'Descriptive',
  relationship: 'Relationship',
  statistical: 'Statistical',
  anomaly: 'Unusual values',
  trend: 'Trend',
  group_difference: 'Group difference',
  data_quality: 'Data quality',
});

export const KIND_LABEL = Object.freeze({
  dashboard: 'Overview', descriptive: 'Descriptive', relationships: 'Relationships', compare: 'Comparison', hypothesis: 'Statistical test',
  insights: 'Insights', chart: 'Chart', question: 'Question',
});

export const FILTER_OPS = Object.freeze({
  numeric: [['eq', 'is'], ['ne', 'is not'], ['gt', 'greater than'], ['ge', 'at least'], ['lt', 'less than'], ['le', 'at most'], ['between', 'between'], ['is_null', 'is empty'], ['not_null', 'is not empty']],
  datetime: [['ge', 'on or after'], ['lt', 'before'], ['between', 'between'], ['is_null', 'is empty'], ['not_null', 'is not empty']],
  categorical: [['eq', 'is'], ['ne', 'is not'], ['contains', 'contains'], ['in', 'is one of'], ['is_null', 'is empty'], ['not_null', 'is not empty']],
});

export function describeFilter(f) {
  const ops = Object.fromEntries(Object.values(FILTER_OPS).flat());
  const label = ops[f.op] ?? f.op;
  if (f.op === 'is_null' || f.op === 'not_null') return `${f.column} ${label}`;
  if (f.op === 'between' && Array.isArray(f.value)) return `${f.column} ${label} ${f.value[0]} and ${f.value[1]}`;
  if (Array.isArray(f.value)) return `${f.column} ${label} ${f.value.join(', ')}`;
  return `${f.column} ${label} ${f.value}`;
}

/** Turns what a person typed into a typed filter (or an error message). Numbers stay numbers; nothing is sent as code. */
export function buildFilter(column, role, op, rawA, rawB = '') {
  if (!column) return { error: 'Choose a column.' };
  if (op === 'is_null' || op === 'not_null') return { filter: { column, op } };
  const one = (raw) => {
    const text = String(raw ?? '').trim();
    if (!text) return { error: 'Enter a value.' };
    if (role === 'numeric') {
      const n = Number(text.replace(/,/g, ''));
      return Number.isFinite(n) ? { value: n } : { error: `“${text}” is not a number.` };
    }
    if (text.length > 200) return { error: 'That value is too long.' };
    return { value: text };
  };
  if (op === 'between') {
    const a = one(rawA); const b = one(rawB);
    if (a.error || b.error) return { error: a.error || b.error };
    return { filter: { column, op, value: [a.value, b.value] } };
  }
  if (op === 'in') {
    const parts = String(rawA).split(',').map((s) => s.trim()).filter(Boolean);
    if (!parts.length) return { error: 'Enter at least one value, separated by commas.' };
    return { filter: { column, op, value: parts.slice(0, 200) } };
  }
  const v = one(rawA);
  return v.error ? { error: v.error } : { filter: { column, op, value: v.value } };
}

export function roleOf(columns, name) {
  return columns.find((c) => c.name === name)?.role ?? 'categorical';
}
