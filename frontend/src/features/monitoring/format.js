// Display helpers for monitoring, forecasting and what-if. They only FORMAT what the server computed; nothing here measures drift, forecasts or predicts.

export const DRIFT_LABEL = Object.freeze({ stable: 'Stable', moderate: 'Moderate change', significant: 'Significant change', insufficient_data: 'Not enough data' });
export const DRIFT_TONE = Object.freeze({ stable: 'ok', moderate: 'warn', significant: 'danger', insufficient_data: 'muted' });
export const PERFORMANCE_LABEL = Object.freeze({ within_expectation: 'Within expectation', degraded: 'Worse than expected', insufficient_outcomes: 'Not enough outcomes yet', no_baseline: 'No baseline to compare with' });
export const PERFORMANCE_TONE = Object.freeze({ within_expectation: 'ok', degraded: 'danger', insufficient_outcomes: 'muted', no_baseline: 'muted' });
export const MONITORING_LIMITS = 'Monitoring runs only when you ask. Input drift means new data looks different; it does not show that predictions are wrong. Only outcomes you record can show that.';

export const driftLabel = (s) => DRIFT_LABEL[s] ?? String(s ?? '');
export const driftTone = (s) => DRIFT_TONE[s] ?? 'muted';
export const performanceLabel = (s) => PERFORMANCE_LABEL[s] ?? String(s ?? '');
export const performanceTone = (s) => PERFORMANCE_TONE[s] ?? 'muted';

/** Columns sorted so the biggest change is first; columns that were not compared come last. Missing figures sort as 'no change' (never invented). */
export function driftRows(check) {
  const cols = check?.columns ?? {};
  const order = { significant: 0, moderate: 1, stable: 2, insufficient_data: 3 };
  return Object.entries(cols)
    .map(([name, c]) => ({ name, status: c.status, kind: c.kind, psi: c.psi ?? null, note: c.note ?? null, unseen: c.unseen_categories ?? [] }))
    .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9) || (b.psi ?? 0) - (a.psi ?? 0) || a.name.localeCompare(b.name));
}

export const psiText = (psi) => (psi === null || psi === undefined ? '—' : Number(psi).toFixed(3));

/** One plain sentence for a drift check. */
export function driftSentence(check) {
  if (!check) return '';
  const cols = check.drifted_columns ?? [];
  if (check.status === 'insufficient_data') return 'There was not enough data to compare. At least 30 usable values are needed on each side.';
  if (!cols.length) return 'No input column changed noticeably compared with the training data.';
  return `${cols.length} input column${cols.length === 1 ? '' : 's'} changed compared with the training data: ${cols.slice(0, 5).join(', ')}${cols.length > 5 ? '…' : ''}.`;
}

/** What to enter as an outcome, and why a value is refused before it is sent. */
export function outcomeProblem(text, regression) {
  const t = String(text ?? '').trim();
  if (!t) return 'Enter what really happened.';
  if (t.length > 120) return 'Use at most 120 characters.';
  if (regression && !Number.isFinite(Number(t))) return 'Enter the real outcome as a number.';
  return null;
}

// ------------------------------------------------------------------------------------------------ forecast
export const FORECAST_AGG = Object.freeze([['mean', 'Average'], ['sum', 'Total'], ['median', 'Median'], ['min', 'Lowest'], ['max', 'Highest'], ['count', 'Number of rows']]);
export const FORECAST_FREQ = Object.freeze([['auto', 'Choose for me'], ['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['quarter', 'Quarter'], ['year', 'Year']]);
export const MAX_HORIZON = 24;

export function forecastProblem({ date, measure, agg, horizon }) {
  if (!date) return 'Choose the date column.';
  if (agg !== 'count' && !measure) return 'Choose the number to forecast, or use “Number of rows”.';
  const h = Number(horizon);
  if (!Number.isInteger(h) || h < 1 || h > MAX_HORIZON) return `Forecast between 1 and ${MAX_HORIZON} periods.`;
  return null;
}

/** Rows for the forecast table. Bands that the server did not compute stay empty. */
export function forecastRows(result) {
  return (result?.forecast ?? []).map((r) => ({ period: r.period, point: r.point, low80: r.lower80 ?? null, high80: r.upper80 ?? null, low95: r.lower95 ?? null, high95: r.upper95 ?? null }));
}

export function methodRows(result) {
  const m = result?.backtest?.methods ?? {};
  return Object.entries(m).map(([key, v]) => ({ key, label: v.label, mase: v.mase, mae: v.mae, folds: v.folds, chosen: key === result?.method?.key })).sort((a, b) => (a.mase ?? Infinity) - (b.mase ?? Infinity));
}

export function forecastHeadline(result) {
  if (!result) return '';
  const m = result.method;
  return m.validated_by_backtest
    ? `${m.label} gave the smallest errors when tested on the most recent periods.`
    : `${m.label} was used, but the series was too short to test it, so its accuracy is unknown.`;
}

// ------------------------------------------------------------------------------------------------ what-if
export const MAX_SCENARIOS = 5;

/** Turns the form (one row per scenario: name + changed inputs as text) into the request. Empty rows are dropped; values that look numeric are sent as numbers. */
export function buildScenarios(rows) {
  return rows
    .map((r) => {
      const changes = {};
      for (const [k, v] of Object.entries(r.changes ?? {})) {
        const t = String(v ?? '').trim();
        if (t === '') continue;
        changes[k] = Number.isFinite(Number(t)) ? Number(t) : t;
      }
      return { name: String(r.name ?? '').trim() || undefined, changes };
    })
    .filter((r) => Object.keys(r.changes).length > 0)
    .slice(0, MAX_SCENARIOS);
}

export function scenarioChange(s) {
  if (s.change !== undefined) return `${s.change > 0 ? '+' : ''}${s.change}`;
  if (s.probability_change !== undefined) return `${s.probability_change > 0 ? '+' : ''}${(s.probability_change * 100).toFixed(1)} points for “${s.positive_class}”`;
  return s.label_changed ? 'The predicted class changes.' : 'The predicted class stays the same.';
}
