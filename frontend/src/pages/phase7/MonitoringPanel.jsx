import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { MONITORING_LIMITS, driftLabel, driftRows, driftSentence, driftTone, outcomeProblem, performanceLabel, performanceTone, psiText } from '../../features/monitoring/format.js';
import { useDatasets } from '../predictive/useDatasets.js';
import { Badge, Notice, attempt, when } from './shared.jsx';

function DriftResult({ check }) {
  const rows = driftRows(check);
  return (
    <div className="stack" aria-label="Drift result">
      <p><Badge tone={driftTone(check.status)}>{driftLabel(check.status)}</Badge> {driftSentence(check)}</p>
      <table className="table table--compact">
        <thead><tr><th scope="col">Input</th><th scope="col">Result</th><th scope="col">PSI</th><th scope="col">Detail</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.name}>
              <th scope="row">{r.name}</th>
              <td><Badge tone={driftTone(r.status)}>{driftLabel(r.status)}</Badge></td>
              <td>{psiText(r.psi)}</td>
              <td>{r.unseen.length ? `New categories: ${r.unseen.join(', ')}` : r.note ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {Object.keys(check.not_compared ?? {}).length ? <p className="row__meta">Not compared: {Object.entries(check.not_compared).map(([k, v]) => `${k} (${v})`).join('; ')}.</p> : null}
      <p className="row__meta">{check.note}</p>
    </div>
  );
}

function Outcomes({ versionId }) {
  const [predictionId, setPredictionId] = useState('');
  const [actual, setActual] = useState('');
  const [msg, setMsg] = useState(null);
  const perf = useResource(() => phase7.registry.performance(versionId), [versionId]);
  async function save(e) {
    e.preventDefault();
    const regression = perf.data?.family === 'regression';
    const problem = outcomeProblem(actual, regression) || (predictionId.trim() ? null : 'Enter the prediction id (shown in the prediction history).');
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    const out = await attempt(() => phase7.registry.recordOutcome(versionId, predictionId.trim(), actual.trim()), setMsg, 'Outcome recorded.');
    if (out) { setActual(''); setPredictionId(''); perf.reload({ quiet: true }); }
  }
  const p = perf.data;
  return (
    <div className="stack" aria-label="Recorded outcomes">
      {p ? <p><Badge tone={performanceTone(p.status)}>{performanceLabel(p.status)}</Badge> {p.note}</p> : null}
      {p && p.n ? <p className="row__meta">{p.n} outcome{p.n === 1 ? '' : 's'} recorded. {p.family === 'regression' ? `Typical error ${p.mae ?? 'n/a'} (hold-out ${p.baseline_mae ?? 'n/a'}), bias ${p.bias ?? 'n/a'}.` : `Accuracy ${p.accuracy ?? 'n/a'} (hold-out ${p.baseline_accuracy ?? 'n/a'}).`}</p> : null}
      <form className="stack" onSubmit={save} noValidate aria-label="Record an outcome">
        <Field label="Prediction id" value={predictionId} maxLength={64} onChange={(e) => setPredictionId(e.target.value)} hint="From a saved single prediction made with this version." />
        <Field label="What really happened" value={actual} maxLength={120} onChange={(e) => setActual(e.target.value)} />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn btn--ghost">Record outcome</button></div>
      </form>
    </div>
  );
}

/** Monitoring for one registered version. Owner only: it reads the owner's own datasets and predictions. */
export function MonitoringPanel({ version }) {
  const checks = useResource(() => phase7.registry.driftChecks(version.id), [version.id]);
  const sets = useDatasets();
  const [assetId, setAssetId] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [shown, setShown] = useState(null);

  async function run(e) {
    e.preventDefault();
    if (!assetId) { setMsg({ kind: 'error', text: 'Choose the newer dataset to compare.' }); return; }
    setBusy(true);
    const out = await attempt(() => phase7.registry.checkDrift(version.id, assetId), setMsg);
    setBusy(false);
    if (out) { setShown(out); checks.reload({ quiet: true }); }
  }
  async function open(id) {
    const out = await attempt(() => phase7.registry.driftCheck(id), setMsg);
    if (out) setShown(out);
  }
  const items = checks.data?.items ?? [];
  return (
    <section className="stack" aria-label={`Monitoring for version ${version.version}`}>
      <h4 className="sub">Monitoring</h4>
      <p className="row__meta">{MONITORING_LIMITS}</p>
      <form className="stack" onSubmit={run} aria-label="Check for drift">
        <Field as="select" label="Newer dataset (same project)" value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">Choose a dataset</option>
          {sets.groups.map((g) => <optgroup key={g.project.id} label={g.project.name}>{g.assets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</optgroup>)}
        </Field>
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn btn--ghost" disabled={busy}>{busy ? 'Comparing…' : 'Compare with training data'}</button></div>
      </form>
      {shown ? <DriftResult check={shown} /> : null}
      {items.length ? (
        <div>
          <h5 className="sub">Earlier checks</h5>
          <ul>{items.map((c) => <li key={c.id}><button type="button" className="btn btn--ghost" onClick={() => open(c.id)}>{when(c.created_at)} — {driftLabel(c.status)}</button></li>)}</ul>
        </div>
      ) : null}
      <h5 className="sub">Performance from real outcomes</h5>
      <Outcomes versionId={version.id} />
    </section>
  );
}
