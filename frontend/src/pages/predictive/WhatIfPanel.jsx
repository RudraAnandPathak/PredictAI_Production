import { useState } from 'react';
import { predictive } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { checkEntered, fieldsFromSchema, typicalValues, valuesPayload } from '../../features/predictive/inputs.js';
import { MAX_SCENARIOS, buildScenarios, scenarioChange } from '../../features/monitoring/format.js';
import { InputField } from './PredictPanel.jsx';
import { Badge, Notice } from './shared.jsx';

const blank = () => ({ name: '', slots: [{ column: '', value: '' }, { column: '', value: '' }] });

/** "What if…": the model's answer for one case against its answer when some inputs change. Every figure is the server's; nothing is saved. */
export function WhatIfPanel({ model }) {
  const fields = fieldsFromSchema(model.input_schema ?? []);
  const [entered, setEntered] = useState(() => typicalValues(fields));
  const [rows, setRows] = useState([blank()]);
  const [fieldErrors, setFieldErrors] = useState({});
  const [state, setState] = useState({ busy: false, result: null, error: '' });

  const setRow = (i, patch) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const setSlot = (i, j, patch) => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, slots: r.slots.map((s, m) => (m === j ? { ...s, ...patch } : s)) } : r)));

  async function run(event) {
    event.preventDefault();
    const problems = checkEntered(fields, entered);
    setFieldErrors(problems);
    if (Object.keys(problems).length) { setState({ busy: false, result: null, error: problems._form ?? 'Fix the highlighted values first.' }); return; }
    const scenarios = buildScenarios(rows.map((r) => ({ name: r.name, changes: Object.fromEntries(r.slots.filter((s) => s.column).map((s) => [s.column, s.value])) })));
    if (!scenarios.length) { setState({ busy: false, result: null, error: 'Choose at least one input and a new value for it.' }); return; }
    setState({ busy: true, result: null, error: '' });
    try {
      setState({ busy: false, result: await predictive.whatIf(model.id, valuesPayload(fields, entered), scenarios), error: '' });
    } catch (e) { setState({ busy: false, result: null, error: describeError(e) }); }
  }
  if (!fields.length) return <Notice kind="warn">This model has no recorded inputs, so scenarios cannot be built.</Notice>;
  const r = state.result;
  return (
    <section className="stack" aria-label="What if">
      <p className="muted">Set a starting case, then describe up to {MAX_SCENARIOS} changes to see how the model's answer moves. This shows how the <strong>model</strong> responds. It does not show what would happen in real life, and nothing is saved.</p>
      <form className="card stack" onSubmit={run} noValidate aria-label="What-if scenarios">
        <h3 className="card__title">Starting case</h3>
        <div className="form-grid">
          {fields.map((f) => <InputField key={f.name} f={f} value={entered[f.name]} error={fieldErrors[f.name]} onChange={(v) => setEntered((e) => ({ ...e, [f.name]: v }))} />)}
        </div>
        <h3 className="card__title">Changes to try</h3>
        {rows.map((row, i) => (
          <fieldset key={i} className="card stack">
            <legend>Scenario {i + 1}</legend>
            <Field label="Name (optional)" value={row.name} maxLength={80} onChange={(e) => setRow(i, { name: e.target.value })} />
            {row.slots.map((s, j) => (
              <div className="form-grid" key={j}>
                <Field as="select" label={`Input ${j + 1}`} value={s.column} onChange={(e) => setSlot(i, j, { column: e.target.value })}>
                  <option value="">Not used</option>
                  {fields.map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
                </Field>
                <Field label="New value" value={s.value} maxLength={200} onChange={(e) => setSlot(i, j, { value: e.target.value })} />
              </div>
            ))}
            {rows.length > 1 ? <button type="button" className="btn btn--ghost" onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}>Remove scenario {i + 1}</button> : null}
          </fieldset>
        ))}
        {state.error ? <Notice kind="error">{state.error}</Notice> : null}
        <div className="btn-row">
          <button type="submit" className="btn btn--primary" disabled={state.busy}>{state.busy ? <><Spinner label="Comparing" /> Comparing…</> : 'Compare'}</button>
          {rows.length < MAX_SCENARIOS ? <button type="button" className="btn btn--ghost" onClick={() => setRows((rs) => [...rs, blank()])}>Add a scenario</button> : null}
        </div>
      </form>
      {r ? (
        <div className="card stack" aria-label="What-if results">
          <h3 className="card__title">Results for “{r.target}”</h3>
          <p>Starting case: <strong>{String(r.baseline.prediction.label ?? r.baseline.prediction.value)}</strong></p>
          <table className="table">
            <thead><tr><th scope="col">Scenario</th><th scope="col">Changed</th><th scope="col">Model's answer</th><th scope="col">Compared with the start</th></tr></thead>
            <tbody>
              {r.scenarios.map((s, i) => (
                <tr key={i}>
                  <th scope="row">{s.name}</th>
                  <td>{Object.entries(s.changes).map(([k, v]) => `${k} = ${v}`).join(', ')}</td>
                  <td>{String(s.prediction.label ?? s.prediction.value)}{s.input_issues?.length ? <> <Badge tone="warn">check inputs</Badge></> : null}</td>
                  <td>{scenarioChange(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="row__meta">{r.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}
