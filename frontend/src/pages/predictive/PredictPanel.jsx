import { useState } from 'react';
import { predictive } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { EffectBars, ProbabilityBars } from '../../components/predictive/MlCharts.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatScore, formatShare, formatWhen } from '../../features/predictive/format.js';
import { checkEntered, fieldsFromSchema, sortedIssues, typicalValues, valuesPayload } from '../../features/predictive/inputs.js';
import { Badge, Notice } from './shared.jsx';

const ISSUE_TONE = { error: 'danger', warning: 'warn', info: 'muted' };

export function InputField({ f, value, error, onChange }) {
  const common = { label: f.name, value: value ?? '', error, hint: f.hint || undefined };
  if (f.kind === 'choice') {
    return (
      <Field as="select" {...common} onChange={(e) => onChange(e.target.value)}>
        <option value="">Not given</option>
        {f.levels.map((l) => <option key={String(l)} value={String(l)}>{String(l)}</option>)}
      </Field>
    );
  }
  if (f.kind === 'number') return <Field {...common} type="text" inputMode="decimal" placeholder="Not given" onChange={(e) => onChange(e.target.value)} />;
  if (f.kind === 'date') return <Field {...common} type="text" placeholder="YYYY-MM-DD" onChange={(e) => onChange(e.target.value)} />;
  return <Field {...common} type="text" placeholder="Not given" maxLength={500} onChange={(e) => onChange(e.target.value)} />;
}

/** One saved or fresh single prediction. Every figure is the server's; the wording about probabilities and effects is the server's too. */
export function PredictionResult({ result }) {
  const regression = result.prediction?.value !== undefined;
  const issues = sortedIssues(result.input_issues);
  return (
    <div className="card stack prediction" aria-label="Prediction" data-prediction-kind={regression ? 'regression' : 'classification'}>
      <h3 className="card__title">Prediction</h3>
      {regression ? (
        <>
          <p className="prediction__headline"><strong>{formatScore(result.prediction.value, 4)}</strong></p>
          {result.typical_error ? <p>{result.typical_error.reading} Typical distance: {formatScore(result.typical_error.within_80, 3)} (80% of held-out predictions), {formatScore(result.typical_error.within_95, 3)} (95%). A range of {formatScore(result.typical_error.range_80?.[0], 3)} to {formatScore(result.typical_error.range_80?.[1], 3)} would have held for about 80% of held-out cases.</p> : null}
        </>
      ) : (
        <>
          <p className="prediction__headline"><strong>{String(result.prediction?.label)}</strong>{result.prediction?.probability !== undefined ? <span className="muted"> — {formatShare(result.prediction.probability, 1)} probability</span> : null}</p>
          <ProbabilityBars probabilities={result.probabilities} positive={result.positive?.class} />
          {result.probability_reading ? <p className="row__meta">{result.probability_reading}</p> : null}
        </>
      )}
      {issues.length ? (
        <ul className="issue-list" aria-label="Notes about the values entered">
          {issues.map((i, k) => <li key={k}><Badge tone={ISSUE_TONE[i.severity] ?? 'muted'}>{i.severity}</Badge> <strong>{i.column}</strong>: {i.message}</li>)}
        </ul>
      ) : null}
      {result.local?.effects?.length ? (
        <div className="stack" aria-label="Why this prediction">
          <h4 className="sub">What moved this prediction</h4>
          <p className="muted">{result.local.method}. Focus: {result.local.focus}.</p>
          <EffectBars local={result.local} />
          <p className="row__meta">{result.local.reading} {result.local.note}</p>
        </div>
      ) : null}
      <Explanation value={result.explanation} />
    </div>
  );
}

function History({ modelId, version, onOpen }) {
  const list = useResource(() => predictive.predictions(modelId, { kind: 'single', limit: 20 }), [modelId, version]);
  const items = list.data?.items ?? [];
  if (list.loading && !list.data) return <p className="muted">Loading history…</p>;
  if (list.error) return <Notice kind="error">{describeError(list.error)}</Notice>;
  if (!items.length) return <p className="muted">No single predictions saved yet.</p>;
  return (
    <ul className="list" aria-label="Prediction history">
      {items.map((p) => (
        <li key={p.id} className="row">
          <div className="row__main">
            <span className="row__title">{formatWhen(p.created_at)} — {p.result?.prediction?.label !== undefined ? String(p.result.prediction.label) : formatScore(p.result?.prediction?.value, 4)}</span>
            <span className="row__meta">{Object.entries(p.inputs ?? {}).slice(0, 6).map(([k, v]) => `${k} = ${v}`).join(', ')}</span>
          </div>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onOpen(p.result)}>Show</button>
        </li>
      ))}
    </ul>
  );
}

/** Interface 8: Individual Prediction. The form is built from the model's own record of its inputs, so it can only ask for what the model uses. */
export function PredictPanel({ model }) {
  const fields = fieldsFromSchema(model.input_schema ?? []);
  const [entered, setEntered] = useState({});
  const [fieldErrors, setFieldErrors] = useState({});
  const [state, setState] = useState({ busy: false, result: null, error: '' });
  const [version, setVersion] = useState(0);
  const [save, setSave] = useState(true);

  async function run(event) {
    event.preventDefault();
    const problems = checkEntered(fields, entered);
    setFieldErrors(problems);
    if (Object.keys(problems).length) { setState((s) => ({ ...s, error: problems._form ?? 'Fix the highlighted values first.' })); return; }
    setState({ busy: true, result: null, error: '' });
    try {
      const result = await predictive.predict(model.id, valuesPayload(fields, entered), { explain: true, save });
      setState({ busy: false, result, error: '' });
      if (save) setVersion((v) => v + 1);
    } catch (e) { setState({ busy: false, result: null, error: describeError(e) }); }
  }
  if (!fields.length) return <Notice kind="warn">This model has no recorded inputs, so a prediction form cannot be built.</Notice>;
  return (
    <section className="stack" aria-label="Individual prediction">
      <p className="muted">Enter values for one case. Anything you leave blank is treated as “not given” and filled the same way it was during training; the result says so. Values far outside what the model saw are flagged.</p>
      <form className="card stack" onSubmit={run} noValidate aria-label="Values for one prediction">
        <div className="form-grid">
          {fields.map((f) => <InputField key={f.name} f={f} value={entered[f.name]} error={fieldErrors[f.name]} onChange={(v) => setEntered((e) => ({ ...e, [f.name]: v }))} />)}
        </div>
        <label className="check-field"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /><span><span className="check-field__label">Save this prediction to the history</span></span></label>
        {state.error ? <Notice kind="error">{state.error}</Notice> : null}
        <div className="btn-row">
          <button type="submit" className="btn btn--primary" disabled={state.busy}>{state.busy ? <><Spinner label="Predicting" /> Predicting…</> : 'Predict'}</button>
          <button type="button" className="btn btn--ghost" onClick={() => { setEntered(typicalValues(fields)); setFieldErrors({}); }}>Fill with typical values</button>
          <button type="button" className="btn btn--ghost" onClick={() => { setEntered({}); setFieldErrors({}); setState({ busy: false, result: null, error: '' }); }}>Clear</button>
        </div>
        <p className="row__meta">“Typical values” means the middle value of each number and the most common category in the training data. It is a starting point to edit, not a real case.</p>
      </form>
      {state.result ? <PredictionResult result={state.result} /> : null}
      <div className="card stack">
        <h3 className="card__title">History</h3>
        <History modelId={model.id} version={version} onOpen={(result) => setState({ busy: false, result, error: '' })} />
      </div>
    </section>
  );
}
