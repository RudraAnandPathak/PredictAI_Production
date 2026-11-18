// Presentation of an influence analysis. The server only ever reports association over time ("causal_status: not established"); this file keeps that
// wording attached to every result so no screen can show it as cause and effect.
import { formatNumber } from '../analytics/format.js';

export const ASSOCIATION_BANNER = 'This shows whether coverage and your data moved together, and which came first. It does not show that one caused the other.';
export const AGGREGATES = Object.freeze([['mean', 'Average'], ['sum', 'Total'], ['max', 'Highest'], ['min', 'Lowest'], ['last', 'Last value of the day']]);
// Bounds and choices mirror the server (world/service.py, world/influence.py MAX_LAG): the server still checks them, this only saves a round trip.
export const MEASURES = Object.freeze([['count', 'Number of articles per day'], ['tone', 'Average wording tone per day']]);
export const MAX_LAG_LIMITS = Object.freeze({ min: 0, max: 14 });

/** Request body for POST …/world/influence. */
export function buildInfluenceBody(form) {
  const errors = {};
  if (!form?.assetId) errors.assetId = 'Choose a dataset.';
  if (!form?.dateColumn) errors.dateColumn = 'Choose the date column.';
  if (!form?.valueColumn) errors.valueColumn = 'Choose the value column.';
  const lag = Number(form?.maxLag ?? 7);
  if (!Number.isInteger(lag) || lag < MAX_LAG_LIMITS.min || lag > MAX_LAG_LIMITS.max) errors.maxLag = `Use a whole number of days from ${MAX_LAG_LIMITS.min} to ${MAX_LAG_LIMITS.max}.`;
  if (Object.keys(errors).length) return { errors };
  const body = { asset_id: form.assetId, date_column: form.dateColumn, value_column: form.valueColumn, aggregate: form.aggregate || 'mean', measure: form.measure || 'count', max_lag: lag };
  for (const [from, to] of [['topic', 'topic'], ['entity', 'entity'], ['label', 'label']]) {
    const v = String(form?.[from] ?? '').trim();
    if (v) body[to] = v;
  }
  return { body };
}

/** Everything a result screen needs, with the integrity and causation caveats always present. */
export function influenceView(analysis) {
  const result = analysis?.payload?.result ?? {};
  const best = result.best ?? null;
  const warnings = [...(analysis?.warnings ?? []), ...(result.warnings ?? [])];
  return {
    banner: ASSOCIATION_BANNER,
    damaged: analysis?.integrity_ok === false,
    status: result.status ?? 'unknown',
    causalStatus: result.causal_status ?? 'not established',
    evidence: result.evidence ?? 'temporal',
    reading: result.reading ?? '',
    best: best ? { lag: best.lag, rho: formatNumber(best.rho, 2), p: formatNumber(best.p_adjusted, 3), n: best.n } : null,
    overlapDays: result.overlap_days ?? null,
    coverageArticles: analysis?.payload?.coverage_articles ?? null,
    coverageDays: analysis?.payload?.coverage_days ?? null,
    trend: result.trend ?? null,
    whyNotCausal: result.why_not_causal ?? [],
    whatWouldHelp: result.what_would_help ?? [],
    warnings: [...new Set(warnings)],
    lags: (result.raw?.lags ?? []).map((l) => ({ lag: l.lag, rho: l.rho, n: l.n, p: l.p, q: l.q })),
    dataset: analysis?.payload?.dataset ?? null,
    method: result.method ?? analysis?.rule_version ?? '',
  };
}

/** Which way the lag points, in words. Positive lag means coverage came first; the wording never says one thing "drove" the other. */
export function lagMeaning(lag) {
  if (typeof lag !== 'number') return '';
  if (lag === 0) return 'Coverage and your data moved on the same day.';
  if (lag > 0) return `Coverage came ${lag} day${lag === 1 ? '' : 's'} before the change in your data.`;
  return `Your data changed ${-lag} day${lag === -1 ? '' : 's'} before the coverage.`;
}
