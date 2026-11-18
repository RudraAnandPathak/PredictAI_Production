// Display helpers for predictive modelling. They only FORMAT figures the server computed; nothing here trains, scores or explains a model.
import { formatNumber } from '../analytics/format.js';

export const PROBLEM_LABEL = Object.freeze({
  binary_classification: 'Binary classification (two outcomes)',
  multiclass_classification: 'Multiclass classification (several outcomes)',
  regression: 'Regression (predict a number)',
});

/** The kind of problem a report or model is about. A report carries it as an object (`report.problem.type`); a model row carries `problem_type`. Always a string or null. */
export function problemTypeOf(report, model) {
  const fromReport = report && typeof report.problem === 'object' && report.problem ? report.problem.type : report?.problem;
  const value = fromReport ?? model?.problem_type ?? null;
  return typeof value === 'string' ? value : null;
}

export const PROBLEM_SHORT = Object.freeze({ binary_classification: 'Two outcomes', multiclass_classification: 'Several outcomes', regression: 'A number' });

export const JOB_STATUS_LABEL = Object.freeze({ queued: 'Waiting to start', running: 'Training', completed: 'Finished', failed: 'Did not finish', cancelled: 'Cancelled' });
export const JOB_STATUS_TONE = Object.freeze({ queued: 'muted', running: 'warn', completed: 'ok', failed: 'danger', cancelled: 'muted' });
export const FINAL_STATUSES = Object.freeze(['completed', 'failed', 'cancelled']);

export const OVERFIT_LABEL = Object.freeze({ low: 'No sign of overfitting', moderate: 'Some sign of overfitting', high: 'Strong signs of overfitting' });
export const OVERFIT_TONE = Object.freeze({ low: 'ok', moderate: 'warn', high: 'danger' });

export const SUITABILITY_LABEL = Object.freeze({ ready: 'Ready to model', caution: 'Can be modelled, with cautions', blocked: 'Cannot be modelled yet' });
export const SUITABILITY_TONE = Object.freeze({ ready: 'ok', caution: 'warn', blocked: 'danger' });

export const ACTION_LABEL = Object.freeze({ use: 'Use as input', drop: 'Leave out', target: 'Target' });

/** A metric score as people read it: ordinary numbers, three decimals; percentages stay as the server sent them. */
export function formatScore(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (typeof value !== 'number') return String(value);
  if (!Number.isFinite(value)) return '—';
  const text = value.toFixed(digits);
  if (!text.includes('.')) return text;                       // whole numbers keep every digit (1000 stays 1000)
  const trimmed = text.replace(/\.?0+$/, '') || '0';
  return trimmed === '-0' ? '0' : trimmed;                    // a tiny negative that rounds to nothing is just 0
}

export function formatShare(value, places = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(places)}%`;
}

export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 90) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  return `${m} min ${Math.round(seconds - m * 60)} s`;
}

export function formatMs(ms) {
  return ms === null || ms === undefined ? '—' : formatSeconds(ms / 1000);
}

export function formatWhen(iso) {
  if (typeof iso !== 'string' || !iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}

/** Whole-model headline: "ROC-AUC 0.878 on held-out rows". Error metrics are already shown positive by the server. */
export function headline(model) {
  if (!model) return '';
  const score = model.test_score ?? model.cv_score;
  const basis = model.test_score !== null && model.test_score !== undefined ? 'on held-out rows' : 'in cross-validation';
  return `${model.primary_label} ${formatScore(score)} ${basis}${model.lower_is_better ? ' (lower is better)' : ''}`;
}

export function modelTitle(model) {
  return model?.name || `${model?.algorithm_label ?? 'Model'} for ${model?.target ?? 'target'}`;
}

/** Counts people can read: 1,234 rows. */
export function rows(n) {
  return `${formatNumber(n, 0)} ${n === 1 ? 'row' : 'rows'}`;
}

export function plural(n, one, many = `${one}s`) {
  return n === 1 ? one : many;
}

/** The label to show for a class/value the server returned. Boolean-like columns arrive as true/false; say so once rather than inventing the original words. */
export function classLabel(value) {
  return value === null || value === undefined ? '—' : String(value);
}

export const PROGRESS_PHASES = Object.freeze({
  queued: 'Waiting to start', loading: 'Reading the dataset', preparing: 'Preparing the data', baseline: 'Measuring the baseline', training: 'Training models',
  tuning: 'Tuning settings', evaluating: 'Testing on held-out rows', explaining: 'Working out what the model relies on', saving: 'Saving the model', done: 'Finished',
});

export function phaseLabel(phase) {
  return PROGRESS_PHASES[phase] ?? (phase ? String(phase).replace(/_/g, ' ') : 'Working');
}
