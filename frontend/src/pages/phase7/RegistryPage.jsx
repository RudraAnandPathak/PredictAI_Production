import { useState } from 'react';
import { phase7, predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { LIFECYCLE_LABEL, LIFECYCLE_NOTE, LIFECYCLE_TONE, needsConfirm } from '../../features/phase7/labels.js';
import { MonitoringPanel } from './MonitoringPanel.jsx';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt } from './shared.jsx';

function RegisterForm({ onDone }) {
  const models = useResource(() => predictive.models({ limit: 100 }), []);
  const [sourceId, setSourceId] = useState('');
  const [name, setName] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const items = models.data?.items ?? [];
  async function submit(e) {
    e.preventDefault();
    const src = items.find((m) => m.id === sourceId);
    if (!src) { setMsg({ kind: 'error', text: 'Choose a trained model.' }); return; }
    setBusy(true);
    const out = await attempt(() => phase7.registry.register(src.project_id, { source_model_id: sourceId, name: name || undefined }), setMsg, 'Registered. It starts as a draft.');
    setBusy(false);
    if (out) { setName(''); onDone(); }
  }
  return (
    <form className="card" onSubmit={submit} aria-label="Register a trained model">
      <h2 className="card__title">Register a trained model</h2>
      <p className="row__meta">Registering keeps a verified copy and starts a version history. The original stays under Predictions.</p>
      <Field as="select" label="Trained model" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
        <option value="">Choose…</option>
        {items.map((m) => <option key={m.id} value={m.id}>{m.name || m.algorithm_label} · target “{m.target}”</option>)}
      </Field>
      <Field label="Registry name (optional)" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} hint="Registering again under an existing name adds a new version." />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={busy || !sourceId}>{busy ? 'Registering…' : 'Register'}</button></div>
    </form>
  );
}

/** Model registry: every registered model with its versions and lifecycle state. */
export function RegistryPage() {
  const res = useResource(() => phase7.registry.models({ limit: 100 }), []);
  return (
    <article className="page">
      <PageHeader eyebrow="Models" title="Model registry" lead="Versioned, verified copies of your trained models, with a lifecycle you control. Only you decide when a version becomes active." />
      <Notice kind="info">{LIFECYCLE_NOTE}</Notice>
      <Loaded res={res} what="the registry">
        {(d) => (
          <>
            {d.items.length === 0 ? <Empty title="Nothing registered yet"><p>Register one of your trained models below.</p></Empty> : (
              <ul className="list">
                {d.items.map((m) => (
                  <li key={m.id} className="row">
                    <div className="row__main">
                      <span className="row__title"><Link to={`/models/${encodeURIComponent(m.id)}`}>{m.name}</Link></span>
                      <span className="row__meta">{m.model_type.replace('_', ' ')} · {m.version_count} version(s){m.latest ? <> · latest <Badge tone={LIFECYCLE_TONE[m.latest.status]}>{LIFECYCLE_LABEL[m.latest.status]}</Badge></> : null}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {d.shared_with_me?.length ? <section className="card"><h2 className="card__title">Shared with you</h2><ul className="list">{d.shared_with_me.map((s) => <li key={s.grant_id} className="row"><div className="row__main"><span className="row__title"><Link to={`/models/${encodeURIComponent(s.resource_id)}`}>Model in {s.workspace_name}</Link></span><span className="row__meta">{s.level} access</span></div></li>)}</ul></section> : null}
            <RegisterForm onDone={() => res.reload({ quiet: true })} />
          </>
        )}
      </Loaded>
    </article>
  );
}

function VersionPanel({ model, version, onChanged }) {
  const detail = useResource(() => phase7.registry.version(version.id), [version.id, version.status]);
  const [msg, setMsg] = useState(null);
  const [reason, setReason] = useState('');
  const [confirming, setConfirming] = useState(null);
  const v = detail.data;
  const canEdit = model.your_access !== 'view';
  async function move(to) {
    const out = await attempt(() => phase7.registry.transition(version.id, to, { confirm: confirming === to, reason }), setMsg, `Moved to ${LIFECYCLE_LABEL[to] ?? to}.`);
    if (out) { setConfirming(null); setReason(''); onChanged(); }
  }
  async function verify() {
    const out = await attempt(() => phase7.registry.verify(version.id), setMsg);
    if (out) { setMsg({ kind: out.ok === false ? 'error' : 'success', text: out.ok === false ? `Integrity check failed: ${out.reason ?? 'the stored copy does not match.'}` : 'Integrity check passed: the stored copy matches what was registered.' }); onChanged(); }
  }
  return (
    <section className="card" aria-label={`Version ${version.version}`}>
      <div className="card__row"><h3 className="card__title">Version {version.version}</h3><Badge tone={LIFECYCLE_TONE[version.status]}>{LIFECYCLE_LABEL[version.status]}</Badge></div>
      <p className="row__meta">{version.algorithm} · target “{version.target}” · {version.evaluation_summary.metric}: holdout {version.evaluation_summary.holdout ?? 'n/a'}</p>
      <p className="row__meta">Copy {version.artifact.last_verified_ok === null ? 'not yet verified' : version.artifact.last_verified_ok ? 'verified' : 'FAILED verification'}.</p>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      {model.your_access === 'owner' ? <MonitoringPanel version={version} /> : null}
      {canEdit && v ? (
        <>
          <Field label="Reason (optional)" value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
          <div className="btn-row">
            {v.allowed_transitions.map((to) => needsConfirm(version.status, to) && confirming !== to
              ? <button key={to} type="button" className="btn btn--ghost" onClick={() => setConfirming(to)}>Move to {LIFECYCLE_LABEL[to]}…</button>
              : <button key={to} type="button" className={confirming === to ? 'btn' : 'btn btn--ghost'} onClick={() => move(to)}>{confirming === to ? `Confirm: ${LIFECYCLE_LABEL[to]}` : `Move to ${LIFECYCLE_LABEL[to]}`}</button>)}
            <button type="button" className="btn btn--ghost" onClick={verify}>Verify copy</button>
          </div>
          {confirming ? <p className="row__meta">This changes which version predictions use by default. Press the confirm button to proceed, or choose another action to cancel.</p> : null}
        </>
      ) : null}
    </section>
  );
}

/** One registered model: versions, lifecycle changes (with confirmation), integrity checks and the full history. */
export function RegistryModelPage() {
  const { modelId } = useParams('/models/:modelId');
  const res = useResource(() => phase7.registry.model(modelId), [modelId]);
  const hist = useResource(() => phase7.registry.history(modelId), [modelId]);
  return (
    <article className="page">
      <Loaded res={res} what="this model">
        {(m) => (
          <>
            <PageHeader eyebrow={m.name} title={m.name} back={{ to: '/models', label: 'Model registry' }} lead={m.description || LIFECYCLE_NOTE} />
            {m.your_access === 'view' ? <Notice kind="info">You can view this model. Only editors and its owner can change it.</Notice> : null}
            {m.versions.slice().reverse().map((v) => <VersionPanel key={v.id} model={m} version={v} onChanged={() => { res.reload({ quiet: true }); hist.reload({ quiet: true }); }} />)}
            <section className="card"><h2 className="card__title">History</h2>
              {(hist.data?.items ?? []).length === 0 ? <p>No events yet.</p> : <ul className="list">{hist.data.items.map((e) => <li key={e.id} className="row"><div className="row__main"><span className="row__title">{e.event.replace(/_/g, ' ')}</span><span className="row__meta">{new Date(e.at).toLocaleString()}</span></div></li>)}</ul>}
            </section>
          </>
        )}
      </Loaded>
    </article>
  );
}
