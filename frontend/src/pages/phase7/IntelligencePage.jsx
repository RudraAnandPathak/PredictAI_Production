import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { KIND_LABEL } from '../../features/phase7/labels.js';
import { useDatasets } from '../predictive/useDatasets.js';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

const REGISTER_LABEL = {
  retrieved_facts: 'Facts from your data', calculated_results: 'Calculated results', model_interpretations: 'Interpretations', suggestions: 'Suggestions to examine', unresolved_questions: 'What this cannot tell you',
};

function Registers({ registers }) {
  return Object.entries(REGISTER_LABEL).map(([k, label]) => (registers?.[k]?.length ? (
    <section key={k} aria-label={label}><h3>{label}</h3><ul>{registers[k].map((e, i) => <li key={i}>{e.text}</li>)}</ul></section>
  ) : null));
}

function Result({ run }) {
  const rows = run.result?.associations ?? run.result?.candidates ?? [];
  return (
    <div className="card" role="region" aria-label={run.title}>
      <h3 className="card__title">{run.title} <Badge tone="muted">{KIND_LABEL[run.kind] ?? run.kind}</Badge></h3>
      {run.result?.notice ? <Notice kind="info">{run.result.notice}</Notice> : null}
      {Array.isArray(rows) && rows.length && typeof rows[0] === 'object' ? (
        <table className="table"><thead><tr>{Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== 'object').map((k) => <th key={k} scope="col">{k.replace(/_/g, ' ')}</th>)}</tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}>{Object.keys(rows[0]).filter((k) => typeof rows[0][k] !== 'object').map((k) => <td key={k}>{String(r[k] ?? '')}</td>)}</tr>)}</tbody></table>
      ) : null}
      <Registers registers={run.registers} />
      <p className="row__meta">Saved {when(run.created_at)}. Result fingerprint {run.result_sha256?.slice(0, 12)}…; the same method on the same data with seed {run.reproduction?.seed} gives the same fingerprint.</p>
    </div>
  );
}

/** Advanced intelligence: possible drivers of an outcome. Findings are associations to examine, never proven causes. */
export function IntelligencePage() {
  const datasets = useDatasets();
  const runs = useResource(() => phase7.intelligence.runs({ limit: 25 }), []);
  const [assetId, setAssetId] = useState('');
  const [outcome, setOutcome] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState(null);
  async function run(e) {
    e.preventDefault();
    setBusy(true);
    const out = await attempt(() => phase7.intelligence.rootCause(assetId, { outcome }), setMsg);
    setBusy(false);
    if (out) { setShown(out); runs.reload({ quiet: true }); }
  }
  async function open(id) { const out = await attempt(() => phase7.intelligence.run(id), setMsg); if (out) setShown(out); }
  return (
    <article className="page">
      <PageHeader eyebrow="Analysis" title="Advanced intelligence" lead="Find which columns go with an outcome, how strongly, and what your data cannot say. These are associations to investigate, not proof of cause." />
      <form className="card" onSubmit={run}>
        <h2 className="card__title">Possible drivers of an outcome</h2>
        <Field as="select" label="Dataset" value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">Choose…</option>
          {datasets.groups.map((g) => <optgroup key={g.project.id} label={g.project.name}>{g.assets.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</optgroup>)}
        </Field>
        <Field label="Outcome column" value={outcome} maxLength={200} onChange={(e) => setOutcome(e.target.value)} hint="The exact column name you want to explain." />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={busy || !assetId || !outcome.trim()}>{busy ? 'Analysing…' : 'Analyse'}</button></div>
      </form>
      {shown ? <Result run={shown} /> : null}
      <section className="card" aria-label="Saved analyses"><h2 className="card__title">Saved analyses</h2>
        <Loaded res={runs} what="saved analyses">{(d) => d.items.length === 0 ? <Empty title="Nothing saved yet"><p>Your analyses are stored here with the exact settings used.</p></Empty> : (
          <ul className="list">{d.items.map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title">{r.title}</span><span className="row__meta">{KIND_LABEL[r.kind] ?? r.kind} · {when(r.created_at)}</span></div><button type="button" className="btn btn--ghost" onClick={() => open(r.id)}>Open</button></li>)}</ul>
        )}</Loaded>
      </section>
    </article>
  );
}
