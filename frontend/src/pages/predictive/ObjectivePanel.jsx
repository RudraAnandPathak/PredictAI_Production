import { Field } from '../../components/forms/Field.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { kindOfProblem, metricsFor } from '../../features/predictive/config.js';
import { PROBLEM_LABEL, SUITABILITY_LABEL, SUITABILITY_TONE, formatShare } from '../../features/predictive/format.js';
import { Badge, Notice } from './shared.jsx';

const PROBLEM_CHOICES = ['auto', 'binary_classification', 'multiclass_classification', 'regression'];

/** Interface 3: Prediction Objective Selection. Pick the column to predict, see which kind of problem it is and why, and override it when you know better. */
export function ObjectivePanel({ ws }) {
  const { schema, catalog, form, errors, assessment } = ws;
  const columns = schema.columns ?? [];
  const targets = columns.filter((c) => c.can_be_target);
  const col = columns.find((c) => c.name === form.target);
  const result = assessment.data;
  const problem = result?.problem;
  const kind = kindOfProblem(form.problemType !== 'auto' ? form.problemType : (problem?.chosen ?? problem?.recommended ?? null));
  const metrics = metricsFor(catalog, kind);
  const classes = problem?.classes ?? [];
  const showPositive = (form.problemType === 'binary_classification') || (form.problemType === 'auto' && problem?.recommended === 'binary_classification');

  return (
    <section className="stack" aria-label="Prediction objective">
      <p className="muted">Choose the column you want the model to predict. PredictAI works out whether that is a yes/no question, a choice between several categories, or a number, and explains why. You can change that if you know better.</p>

      <div className="card stack">
        <h2 className="card__title">What do you want to predict?</h2>
        <div className="form-grid">
          <Field as="select" label="Column to predict" value={form.target} error={errors.target} onChange={(e) => ws.update({ target: e.target.value, positiveLabel: '', primaryMetric: 'auto' })}>
            <option value="">Choose a column…</option>
            {targets.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.role}{c.missing ? `, ${formatNumber(c.missing, 0)} empty` : ''})</option>)}
          </Field>
          <Field as="select" label="Kind of problem" value={form.problemType} error={errors.problemType} hint="“Detect automatically” looks at the values in the column."
            onChange={(e) => ws.update({ problemType: e.target.value, primaryMetric: 'auto', positiveLabel: '' })}>
            {PROBLEM_CHOICES.map((p) => <option key={p} value={p}>{p === 'auto' ? 'Detect automatically (recommended)' : PROBLEM_LABEL[p]}</option>)}
          </Field>
          <Field as="select" label="Score used to rank models" value={form.primaryMetric} hint="“Automatic” picks a measure that suits the data, for example one that copes with rare outcomes."
            onChange={(e) => ws.update({ primaryMetric: e.target.value })}>
            <option value="auto">Automatic (recommended)</option>
            {metrics.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </Field>
          {showPositive ? (
            classes.length ? (
              <Field as="select" label="Outcome to treat as “positive”" value={form.positiveLabel} hint="Probabilities, precision and recall are reported for this outcome."
                onChange={(e) => ws.update({ positiveLabel: e.target.value })}>
                <option value="">Chosen automatically</option>
                {classes.map((c) => <option key={String(c.label)} value={String(c.label)}>{String(c.label)} ({formatShare(c.share, 0)})</option>)}
              </Field>
            ) : (
              <Field label="Outcome to treat as “positive”" value={form.positiveLabel} placeholder="Chosen automatically" hint="Check the objective to list the outcomes."
                onChange={(e) => ws.update({ positiveLabel: e.target.value })} />
            )
          ) : null}
        </div>
        {col ? (
          <p className="row__meta">“{col.name}”: {col.role} column{col.missing ? `, ${formatNumber(col.missing, 0)} rows have no value and cannot be used for training` : ', no empty values'}. {col.hint}</p>
        ) : null}
        <div className="btn-row">
          <button type="button" className="btn" disabled={!form.target || Boolean(errors.target) || assessment.busy} onClick={() => ws.runAssess()}>
            {assessment.busy ? <><Spinner label="Checking" /> Checking…</> : 'Check this objective'}
          </button>
          {result ? <button type="button" className="btn btn--ghost" onClick={() => ws.setTab('prepare')}>Review the data preparation</button> : null}
        </div>
        {assessment.error ? <Notice kind="error" title="This objective could not be checked">{assessment.error}</Notice> : null}
      </div>

      {result && problem ? (
        <div className={`card stack${assessment.stale ? ' is-stale' : ''}`} aria-label="Detected problem">
          <h2 className="card__title">What the data says about this objective</h2>
          {assessment.stale ? <Notice kind="warn">These results are for earlier settings. Check the objective again to update them.</Notice> : null}
          <dl className="kv">
            <div className="kv__pair"><dt>Recommended</dt><dd>{PROBLEM_LABEL[problem.recommended] ?? problem.recommended}</dd></div>
            <div className="kv__pair"><dt>Why</dt><dd>{problem.why}</dd></div>
            <div className="kv__pair"><dt>You chose</dt><dd>{problem.chosen ? (PROBLEM_LABEL[problem.chosen] ?? problem.chosen) : 'This setting cannot be used (see below)'}</dd></div>
            {result.dataset ? <div className="kv__pair"><dt>Rows with a value to learn from</dt><dd>{formatNumber(result.dataset.labelled_rows, 0)} of {formatNumber(result.dataset.rows, 0)}</dd></div> : null}
            {result.metrics ? <div className="kv__pair"><dt>Models will be ranked by</dt><dd>{result.metrics.primary_label} ({result.metrics.chosen_by === 'user' ? 'your choice' : 'chosen by rule'})</dd></div> : null}
          </dl>
          {problem.errors?.map((e, i) => <Notice key={i} kind="error">{e}</Notice>)}
          {problem.warnings?.map((w, i) => <Notice key={i} kind="warn">{w}</Notice>)}
          {problem.alternatives?.length ? (
            <div><h3 className="sub">Another reading</h3><ul>{problem.alternatives.map((a, i) => <li key={i}><strong>{PROBLEM_LABEL[a.problem] ?? a.problem}.</strong> {a.why}</li>)}</ul></div>
          ) : null}
          {classes.length ? (
            <div className="table-wrap" tabIndex={0} role="region" aria-label="Outcomes in the data">
              <table className="table table--compact">
                <thead><tr><th scope="col">Outcome</th><th scope="col">Rows</th><th scope="col">Share</th></tr></thead>
                <tbody>{classes.map((c) => <tr key={String(c.label)}><td>{String(c.label)}</td><td>{formatNumber(c.count, 0)}</td><td>{formatShare(c.share, 1)}</td></tr>)}</tbody>
              </table>
            </div>
          ) : null}
          {result.metrics?.reasons?.length ? <ul>{result.metrics.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : null}
          {result.suitability ? (
            <p><Badge tone={SUITABILITY_TONE[result.suitability.verdict]}>{SUITABILITY_LABEL[result.suitability.verdict] ?? result.suitability.verdict}</Badge> <span className="muted">{result.suitability.summary}</span></p>
          ) : null}
          <Explanation value={result.explanation} level={3} />
          {result.proposed_config ? (
            <div className="btn-row">
              <button type="button" className="btn btn--ghost" onClick={() => ws.applyConfig(result.proposed_config)}>Use the recommended settings</button>
              <span className="row__meta">Fills in every setting with what the checks recommend. You can still change anything.</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
