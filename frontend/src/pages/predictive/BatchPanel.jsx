import { useState } from 'react';
import { predictive, workspace } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { downloadBlob } from '../../components/analytics/ChartFrame.jsx';
import { DistributionBars } from '../../components/predictive/MlCharts.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatNumber } from '../../features/analytics/format.js';
import { formatScore, formatShare, formatWhen } from '../../features/predictive/format.js';
import { Notice } from './shared.jsx';

const ISSUE_LABEL = { missing: 'no value', invalid: 'not a valid value', unseen: 'a category the model has not seen', range: 'outside the range seen in training' };

function PredictionSummary({ summary }) {
  if (!summary) return null;
  const rows = [['Lowest', summary.min], ['25th percentile', summary.p25], ['Median', summary.median], ['75th percentile', summary.p75], ['Highest', summary.max], ['Mean', summary.mean]];
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Distribution of predicted values">
      <table className="table table--compact">
        <thead><tr><th scope="col">Predicted value</th><th scope="col">Amount</th></tr></thead>
        <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{formatScore(v, 4)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function BatchSummary({ item, onDownload, downloading }) {
  const s = item.summary ?? {};
  const issues = Object.entries(s.issues_by_column ?? {});
  return (
    <div className="card stack" aria-label="Batch prediction result" data-batch-id={item.id}>
      <h3 className="card__title">{formatNumber(item.rows ?? s.rows, 0)} rows predicted</h3>
      <dl className="kv">
        <div className="kv__pair"><dt>Rows with a warning</dt><dd>{formatNumber(s.rows_with_warnings ?? 0, 0)}</dd></div>
        {s.mean_top_probability !== undefined && s.mean_top_probability !== null ? <div className="kv__pair"><dt>Average confidence</dt><dd>{formatShare(s.mean_top_probability, 1)}</dd></div> : null}
        {s.possible_drift?.length ? <div className="kv__pair"><dt>Possible drift</dt><dd>{s.possible_drift.map((d) => (typeof d === 'string' ? d : d.column ?? JSON.stringify(d))).join(', ')}</dd></div> : null}
      </dl>
      {s.notes?.length ? <ul className="warnings">{s.notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
      {issues.length ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Problems by column">
          <table className="table table--compact">
            <thead><tr><th scope="col">Column</th><th scope="col">What was found</th></tr></thead>
            <tbody>{issues.map(([col, v]) => <tr key={col}><td>{col}</td><td>{Object.entries(v).filter(([, n]) => n).map(([k, n]) => `${ISSUE_LABEL[k] ?? k}: ${formatNumber(n, 0)} rows`).join('; ')}</td></tr>)}</tbody>
          </table>
        </div>
      ) : null}
      <DistributionBars summary={s} />
      <PredictionSummary summary={s.prediction_summary} />
      {s.typical_error ? <p className="row__meta">Typical error on held-out rows: 80% of predictions within {formatScore(s.typical_error.within_80, 3)}, 95% within {formatScore(s.typical_error.within_95, 3)}.</p> : null}
      <Explanation value={s.explanation} />
      {item.downloadable ? (
        <div className="btn-row">
          <button type="button" className="btn" disabled={downloading} onClick={() => onDownload(item)}>{downloading ? <><Spinner label="Preparing" /> Preparing…</> : 'Download predictions (CSV)'}</button>
          <span className="row__meta">Your original columns plus the prediction for each row. Your dataset itself is unchanged.</span>
        </div>
      ) : <p className="muted">No downloadable file was kept for this run.</p>}
    </div>
  );
}

/** Interface 9: Batch Prediction. Predict every row of another dataset in the same project; the results are saved and can be downloaded. */
export function BatchPanel({ model }) {
  const datasets = useResource(() => workspace.assets.list(model.project_id, { kind: 'dataset', limit: 100 }), [model.project_id]);
  const [choice, setChoice] = useState('');
  const [state, setState] = useState({ busy: false, item: null, error: '' });
  const [downloading, setDownloading] = useState('');
  const [version, setVersion] = useState(0);
  const ready = (datasets.data?.items ?? []).filter((a) => a.status === 'ready');
  const history = useResource(() => predictive.predictions(model.id, { kind: 'batch', limit: 20 }), [model.id, version]);

  async function run() {
    setState({ busy: true, item: null, error: '' });
    try {
      const item = await predictive.predictBatch(model.id, choice);
      setState({ busy: false, item, error: '' });
      setVersion((v) => v + 1);
    } catch (e) { setState({ busy: false, item: null, error: describeError(e) }); }
  }
  async function download(item) {
    setDownloading(item.id);
    try { const { blob, filename } = await predictive.downloadPrediction(item.id); downloadBlob(blob, filename); } catch (e) { setState((s) => ({ ...s, error: describeError(e) })); } finally { setDownloading(''); }
  }
  return (
    <section className="stack" aria-label="Batch prediction">
      <p className="muted">Pick a dataset from the same project. The model predicts every row and checks the columns against what it was trained on; columns that are missing, new or look different are reported rather than silently ignored.</p>
      <div className="card stack">
        <div className="toolbar">
          <Field as="select" label="Dataset to predict" value={choice} onChange={(e) => setChoice(e.target.value)} hint={datasets.loading ? 'Loading datasets…' : ready.length ? undefined : 'There are no other ready datasets in this project.'}>
            <option value="">Choose a dataset…</option>
            {ready.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Field>
          <button type="button" className="btn btn--primary" disabled={!choice || state.busy} onClick={run}>{state.busy ? <><Spinner label="Predicting" /> Predicting…</> : 'Predict every row'}</button>
        </div>
        {datasets.error ? <Notice kind="error">{describeError(datasets.error)}</Notice> : null}
        {state.error ? <Notice kind="error" title="The batch could not be completed">{state.error}</Notice> : null}
      </div>
      {state.item ? <BatchSummary item={state.item} onDownload={download} downloading={downloading === state.item.id} /> : null}
      <div className="card stack" aria-label="Earlier batch predictions">
        <h3 className="card__title">Earlier batch predictions</h3>
        {history.loading && !history.data ? <p className="muted">Loading…</p> : null}
        {!history.loading && !(history.data?.items ?? []).length ? <p className="muted">None yet.</p> : null}
        <ul className="list">
          {(history.data?.items ?? []).map((p) => (
            <li key={p.id} className="row">
              <div className="row__main"><span className="row__title">{formatWhen(p.created_at)} — {formatNumber(p.rows, 0)} rows</span></div>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setState({ busy: false, item: p, error: '' })}>Show</button>
              {p.downloadable ? <button type="button" className="btn btn--ghost btn--sm" disabled={downloading === p.id} onClick={() => download(p)}>Download CSV</button> : null}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
