// The training form. State -> the closed set of settings the server accepts. Nothing here is executable or free-form: every value is a number,
// a switch, or a choice from the server's own catalogue. The server validates again; these checks only give instant, plain messages.

export const MAX_SEED = 2 ** 31 - 1;

export const PREPROCESSING_DEFAULTS = Object.freeze({
  numeric_impute: 'median', categorical_impute: 'most_frequent', missing_indicator: false, scaling: 'auto', encoding: 'onehot', max_onehot_levels: 20,
  high_cardinality: 'frequency', rare_category_min_frequency: 0.01, outliers: 'none', log_transform: 'none', datetime_features: true, drop_duplicates: true, max_missing_pct: 60,
});

export const OPTION_LABELS = Object.freeze({
  numeric_impute: { median: 'Median', mean: 'Mean', most_frequent: 'Most common value' },
  categorical_impute: { most_frequent: 'Most common value', constant: 'A "missing" category' },
  scaling: { auto: 'Automatic (by algorithm)', standard: 'Standardise (mean 0, spread 1)', robust: 'Robust (median and IQR)', minmax: 'Rescale to 0-1', none: 'None' },
  encoding: { onehot: 'One column per category', ordinal: 'Whole numbers', frequency: 'How common each category is' },
  high_cardinality: { frequency: 'Replace by how common each is', ordinal: 'Whole numbers', drop: 'Leave the column out' },
  outliers: { none: 'Keep as they are', clip_iqr: 'Limit extreme values (IQR rule)' },
  log_transform: { auto: 'Automatic for very skewed columns', none: 'None', log1p: 'Always (log of 1 + value)' },
  cv_strategy: { auto: 'Automatic', kfold: 'K-fold', stratified_kfold: 'Stratified K-fold (keeps class shares)', repeated_kfold: 'Repeated K-fold', time_series: 'Time-ordered (past predicts future)' },
  hpo_method: { none: 'No tuning (default settings)', grid: 'Grid search (try every combination)', random: 'Randomised search (try a sample)' },
  class_weight: { auto: 'Automatic', balanced: 'Balance the classes', none: 'Treat every row equally' },
  rare_classes: { error: 'Stop and tell me', drop: 'Leave out classes with too few rows' },
  column_role: { use: 'Use as an input', drop: 'Leave out', numeric: 'Treat as a number', categorical: 'Treat as a category' },
});

export function defaultForm() {
  return {
    target: '', problemType: 'auto', positiveLabel: '', primaryMetric: 'auto',
    exclude: [], roles: {},                                  // roles: { column: 'use' | 'drop' | 'numeric' | 'categorical' }
    preprocessing: { ...PREPROCESSING_DEFAULTS },
    algorithms: 'auto',                                      // 'auto' or an array of catalogue keys
    testSize: 0.2, cvStrategy: 'auto', cvFolds: 5, cvRepeats: 2, shuffle: true, timeColumn: '',
    hpoMethod: 'none', hpoIter: 10, hpoTopK: 2, hpoFolds: 3,
    classWeight: 'auto', rareClasses: 'error', seed: 20240601, timeBudget: '',
  };
}

const num = (v) => (v === '' || v === null || v === undefined ? NaN : Number(v));

/** Form state -> the request body's `config`. Only settings that differ from the server defaults are sent, so the stored configuration stays small and readable. */
export function buildConfig(form) {
  const f = { ...defaultForm(), ...form };
  const config = { target: f.target };
  if (f.problemType !== 'auto') config.problem_type = f.problemType;
  if (f.positiveLabel !== '') config.positive_label = String(f.positiveLabel);
  if (f.primaryMetric !== 'auto') config.primary_metric = f.primaryMetric;
  const features = {};
  if (f.exclude.length) features.exclude = [...f.exclude];
  const overrides = {};
  for (const [col, role] of Object.entries(f.roles ?? {})) if (role && role !== 'default') overrides[col] = { role };
  if (Object.keys(overrides).length) features.overrides = overrides;
  if (Object.keys(features).length) config.features = features;
  const pp = {};
  for (const [k, v] of Object.entries(PREPROCESSING_DEFAULTS)) if (f.preprocessing?.[k] !== undefined && f.preprocessing[k] !== v) pp[k] = f.preprocessing[k];
  if (Object.keys(pp).length) config.preprocessing = pp;
  if (f.algorithms !== 'auto' && Array.isArray(f.algorithms) && f.algorithms.length) config.algorithms = [...f.algorithms];
  const val = {};
  if (f.testSize !== 0.2) val.test_size = Number(f.testSize);
  if (f.cvStrategy !== 'auto') val.cv_strategy = f.cvStrategy;
  if (f.cvFolds !== 5) val.cv_folds = Number(f.cvFolds);
  if (f.cvRepeats !== 2) val.cv_repeats = Number(f.cvRepeats);
  if (f.shuffle !== true) val.shuffle = Boolean(f.shuffle);
  if (f.timeColumn) val.time_column = f.timeColumn;
  if (Object.keys(val).length) config.validation = val;
  if (f.hpoMethod !== 'none') config.hpo = { method: f.hpoMethod, n_iter: Number(f.hpoIter), top_k: Number(f.hpoTopK), cv_folds: Number(f.hpoFolds) };
  if (f.classWeight !== 'auto') config.class_weight = f.classWeight;
  if (f.rareClasses !== 'error') config.rare_classes = f.rareClasses;
  if (num(f.seed) !== 20240601 && !Number.isNaN(num(f.seed))) config.seed = Number(f.seed);
  if (f.timeBudget !== '' && !Number.isNaN(num(f.timeBudget))) config.time_budget_s = Number(f.timeBudget);
  return config;
}

/** A stored/proposed config (as the server returns it) -> form state, so "use these settings" and "train again with the same settings" round-trip. */
export function formFromConfig(config = {}) {
  const f = defaultForm();
  const c = config ?? {};
  f.target = c.target ?? '';
  f.problemType = c.problem_type ?? 'auto';
  f.positiveLabel = c.positive_label ?? '';
  f.primaryMetric = c.primary_metric ?? 'auto';
  f.exclude = [...(c.features?.exclude ?? [])];
  f.roles = Object.fromEntries(Object.entries(c.features?.overrides ?? {}).filter(([, o]) => o?.role).map(([k, o]) => [k, o.role]));
  f.preprocessing = { ...PREPROCESSING_DEFAULTS, ...(c.preprocessing ?? {}) };
  f.algorithms = Array.isArray(c.algorithms) ? [...c.algorithms] : 'auto';
  const v = c.validation ?? {};
  f.testSize = v.test_size ?? f.testSize; f.cvStrategy = v.cv_strategy ?? f.cvStrategy; f.cvFolds = v.cv_folds ?? f.cvFolds; f.cvRepeats = v.cv_repeats ?? f.cvRepeats;
  f.shuffle = v.shuffle ?? f.shuffle; f.timeColumn = v.time_column ?? '';
  const h = c.hpo ?? {};
  f.hpoMethod = h.method ?? 'none'; f.hpoIter = h.n_iter ?? f.hpoIter; f.hpoTopK = h.top_k ?? f.hpoTopK; f.hpoFolds = h.cv_folds ?? f.hpoFolds;
  f.classWeight = c.class_weight ?? 'auto'; f.rareClasses = c.rare_classes ?? 'error';
  f.seed = c.seed ?? f.seed; f.timeBudget = c.time_budget_s ?? '';
  return f;
}

const rangeMsg = (lo, hi) => `Enter a number from ${lo} to ${hi}.`;

/** Instant, plain checks that mirror the server's rules. They never replace the server's validation. Returns { field: message }. */
export function checkForm(form, { schema, limits = {} } = {}) {
  const f = { ...defaultForm(), ...form };
  const errors = {};
  const col = schema?.columns?.find((c) => c.name === f.target);
  if (!f.target) errors.target = 'Choose the column you want to predict.';
  else if (schema && !col) errors.target = 'Choose a column that exists in this dataset.';
  else if (col && !col.can_be_target) errors.target = 'A date column cannot be predicted directly. Choose a number or a category.';
  if (f.problemType === 'regression' && col && col.role === 'categorical') errors.problemType = 'Regression predicts a number, but this column holds text. Choose classification instead.';
  if (f.exclude.includes(f.target)) errors.exclude = 'The column you want to predict cannot also be an input.';
  const ts = num(f.testSize);
  if (!(ts === 0 || (ts >= 0.05 && ts <= 0.5))) errors.testSize = 'Hold back 5% to 50% of the rows for testing (or 0 for none).';
  const folds = num(f.cvFolds); const fmax = limits.max_cv_folds ?? 5;
  if (!Number.isInteger(folds) || folds < 2 || folds > fmax) errors.cvFolds = rangeMsg(2, fmax);
  const reps = num(f.cvRepeats); const rmax = limits.max_cv_repeats ?? 2;
  if (!Number.isInteger(reps) || reps < 1 || reps > rmax) errors.cvRepeats = rangeMsg(1, rmax);
  if (f.cvStrategy === 'time_series' && f.shuffle) errors.shuffle = 'Shuffling would put the future in the training data; turn it off for time-ordered validation.';
  if (f.cvStrategy === 'time_series' && !f.timeColumn) errors.timeColumn = 'Choose the date or number column that gives the order of the rows.';
  if (f.cvStrategy === 'stratified_kfold' && f.problemType === 'regression') errors.cvStrategy = 'Stratified folds only apply to classification.';
  if (f.hpoMethod !== 'none') {
    const nmax = limits.max_hpo_trials ?? 30; const n = num(f.hpoIter);
    if (!Number.isInteger(n) || n < 1 || n > nmax) errors.hpoIter = rangeMsg(1, nmax);
    const kmax = limits.max_hpo_algorithms ?? 2; const k = num(f.hpoTopK);
    if (!Number.isInteger(k) || k < 1 || k > kmax) errors.hpoTopK = rangeMsg(1, kmax);
    const hf = num(f.hpoFolds);
    if (!Number.isInteger(hf) || hf < 2 || hf > fmax) errors.hpoFolds = rangeMsg(2, fmax);
  }
  const seed = num(f.seed);
  if (!Number.isInteger(seed) || seed < 0 || seed > MAX_SEED) errors.seed = rangeMsg(0, MAX_SEED);
  if (f.timeBudget !== '') {
    const tb = num(f.timeBudget); const tmax = limits.time_budget_s ?? 120;
    if (!Number.isInteger(tb) || tb < 10 || tb > tmax) errors.timeBudget = rangeMsg(10, tmax);
  }
  const pre = f.preprocessing ?? {};
  const mp = num(pre.max_missing_pct);
  if (!(mp >= 5 && mp <= 100)) errors.max_missing_pct = rangeMsg(5, 100);
  const ml = num(pre.max_onehot_levels);
  if (!Number.isInteger(ml) || ml < 2 || ml > 100) errors.max_onehot_levels = rangeMsg(2, 100);
  const rf = num(pre.rare_category_min_frequency);
  if (!(rf >= 0 && rf <= 0.2)) errors.rare_category_min_frequency = 'Enter a share from 0 to 0.2.';
  return errors;
}

export const hasErrors = (errors) => Object.keys(errors).length > 0;

/** Which catalogue algorithms can handle this kind of target. `kind` is 'classification' | 'regression' | null (unknown: show all). */
export function algorithmsFor(catalog, kind) {
  const all = catalog?.algorithms ?? [];
  return kind ? all.filter((a) => a.problems.includes(kind)) : all;
}

export function kindOfProblem(problem) {
  if (problem === 'regression') return 'regression';
  if (problem === 'binary_classification' || problem === 'multiclass_classification') return 'classification';
  return null;
}

/** The metrics offered for a problem kind, from the catalogue (never hard-coded here). */
export function metricsFor(catalog, kind) {
  if (!kind) return [...(catalog?.metrics?.classification ?? []), ...(catalog?.metrics?.regression ?? [])];
  return catalog?.metrics?.[kind] ?? [];
}

/** Plain-English lines describing a stored configuration, for job lists and model details. */
export function describeConfig(config = {}) {
  const c = config ?? {};
  const lines = [];
  if (c.target) lines.push(`Predicting "${c.target}"`);
  if (c.problem_type && c.problem_type !== 'auto') lines.push(`Problem type set to ${c.problem_type.replace(/_/g, ' ')}`);
  if (Array.isArray(c.algorithms)) lines.push(`Algorithms: ${c.algorithms.join(', ')}`);
  else if (c.algorithms === 'auto') lines.push('Algorithms chosen automatically');
  if (c.hpo && c.hpo !== 'none') lines.push(`Tuning: ${c.hpo}`);
  if (c.test_size !== undefined && c.test_size !== null) lines.push(`${Math.round(c.test_size * 100)}% of rows held back for testing`);
  if (c.cv_folds) lines.push(`${c.cv_folds}-fold cross-validation`);
  if (c.primary_metric && c.primary_metric !== 'auto') lines.push(`Ranked by ${c.primary_metric.replace(/_/g, ' ')}`);
  if (c.seed !== undefined && c.seed !== null) lines.push(`Seed ${c.seed}`);
  return lines;
}
