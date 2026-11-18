import { useState } from 'react';
import { phase7, predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { FORECAST_AGG, FORECAST_FREQ, MAX_HORIZON, forecastHeadline, forecastProblem, forecastRows, methodRows } from '../../features/monitoring/format.js';
import { Badge, Notice } from './shared.jsx';
import { useDatasets } from './useDatasets.js';

const num = (v) => (v === null || v === undefined ? '—' : String(v));

/** Forecast one measure over time. The server tests several simple methods on the most recent periods and reports the errors, the bands and what a forecast cannot know. */
export function ForecastPage() {
  const sets = useDatasets();
  const [assetId, setAssetId] = useState('');
  const [columns, setColumns] = useState([]);
  const [form, setForm] = useState({ date: '', measure: '', agg: 'mean', freq: 'auto', horizon: 6 });
  const [state, setState] = useState({ busy: false, result: null, error: '' });
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function choose(id) {
    setAssetId(id);
    setColumns([]);
    setState({ busy: false, result: null, error: '' });
    set({ date: '', measure: '' });
    if (!id) return;
    try { setColumns((await predictive.schema(id)).columns ?? []); } catch (e) { setState({ busy: false, result: null, error: describeError(e) }); }
  }
  async function run(event) {
    event.preventDefault();
    const problem = !assetId ? 'Choose a dataset.' : forecastProblem(form);
    if (problem) { setState({ busy: false, result: null, error: problem }); return; }
    setState({ busy: true, result: null, error: '' });
    try {
      const body = { date: form.date, measure: form.agg === 'count' ? null : form.measure, agg: form.agg, freq: form.freq, horizon: Number(form.horizon) };
      setState({ busy: false, result: await phase7.forecast(assetId, body), error: '' });
    } catch (e) { setState({ busy: false, result: null, error: describeError(e) }); }
  }
  const dates = columns.filter((c) => c.role === 'datetime');
  const numbers = columns.filter((c) => c.role === 'numeric');
  const r = state.result;
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/predictions">Predictions</Link> / Forecast</p>
        <h1 className="page-title">Forecast a trend</h1>
        <p className="lead">Continue a pattern over time, such as monthly sales. The result shows how each method did on the most recent periods, a range around the forecast, and what a forecast cannot know.</p>
      </header>
      <form className="card stack" onSubmit={run} noValidate aria-label="Forecast settings">
        <Field as="select" label="Dataset" value={assetId} onChange={(e) => choose(e.target.value)}>
          <option value="">Choose a dataset</option>
          {sets.groups.map((g) => <optgroup key={g.project.id} label={g.project.name}>{g.assets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</optgroup>)}
        </Field>
        <div className="form-grid">
          <Field as="select" label="Date column" value={form.date} onChange={(e) => set({ date: e.target.value })}>
            <option value="">Choose</option>{dates.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Field>
          <Field as="select" label="Summarise each period as" value={form.agg} onChange={(e) => set({ agg: e.target.value })}>
            {FORECAST_AGG.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </Field>
          {form.agg !== 'count' ? (
            <Field as="select" label="Number to forecast" value={form.measure} onChange={(e) => set({ measure: e.target.value })}>
              <option value="">Choose</option>{numbers.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </Field>
          ) : null}
          <Field as="select" label="Period" value={form.freq} onChange={(e) => set({ freq: e.target.value })}>
            {FORECAST_FREQ.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </Field>
          <Field label={`Periods ahead (1 to ${MAX_HORIZON})`} type="text" inputMode="numeric" value={form.horizon} onChange={(e) => set({ horizon: e.target.value })} />
        </div>
        {assetId && !dates.length && columns.length ? <Notice kind="warn">This dataset has no date column, so it cannot be forecast.</Notice> : null}
        {state.error ? <Notice kind="error">{state.error}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn btn--primary" disabled={state.busy}>{state.busy ? <><Spinner label="Forecasting" /> Forecasting…</> : 'Forecast'}</button></div>
      </form>
      {r ? (
        <section className="stack" aria-label="Forecast result">
          <div className="card stack">
            <h2 className="card__title">Forecast <Badge tone={r.method.validated_by_backtest ? 'ok' : 'warn'}>{r.method.validated_by_backtest ? 'tested on recent periods' : 'not tested'}</Badge></h2>
            <p>{forecastHeadline(r)}</p>
            <table className="table table--compact">
              <thead><tr><th scope="col">Period</th><th scope="col">Forecast</th><th scope="col">80% range</th><th scope="col">95% range</th></tr></thead>
              <tbody>
                {forecastRows(r).map((x) => (
                  <tr key={x.period}><th scope="row">{x.period}</th><td>{num(x.point)}</td><td>{x.low80 === null ? '—' : `${x.low80} to ${x.high80}`}</td><td>{x.low95 === null ? '—' : `${x.low95} to ${x.high95}`}</td></tr>
                ))}
              </tbody>
            </table>
            {r.intervals.available ? <p className="row__meta">{r.intervals.method} {r.intervals.caveat}</p> : <p className="row__meta">No range is shown: {r.intervals.reason}</p>}
          </div>
          <div className="card stack">
            <h3 className="card__title">How each method did on recent periods</h3>
            {r.backtest.scored ? (
              <>
                <table className="table table--compact">
                  <thead><tr><th scope="col">Method</th><th scope="col">Error score (MASE)</th><th scope="col">Typical error</th><th scope="col">Tests</th></tr></thead>
                  <tbody>{methodRows(r).map((m) => <tr key={m.key}><th scope="row">{m.label}{m.chosen ? <> <Badge tone="ok">used</Badge></> : null}</th><td>{num(m.mase)}</td><td>{num(m.mae)}</td><td>{m.folds}</td></tr>)}</tbody>
                </table>
                <p className="row__meta">{r.backtest.scale}</p>
              </>
            ) : <p>{r.backtest.reason}</p>}
            {r.seasonality.checked ? <p className="row__meta">Repeating pattern: {r.seasonality.detected ? `detected (every ${r.seasonality.season_length} periods)` : 'not detected'}. {r.seasonality.note ?? ''}</p> : <p className="row__meta">Repeating pattern: not tested. {r.seasonality.reason}</p>}
          </div>
          <ul className="row__meta" aria-label="Limits of this forecast">{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </section>
      ) : null}
    </article>
  );
}
