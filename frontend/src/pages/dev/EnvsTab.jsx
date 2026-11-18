import { useState } from 'react';
import { dev } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { ENV_STATUS_LABEL } from '../../features/dev/labels.js';
import { Badge, Empty, Loaded, Notice, attempt, when } from './shared.jsx';

/** Per-project Python environments. Builds are wheels-only from approved sources and every version is immutable. */
export function EnvsTab({ projectId }) {
  const envs = useResource(() => dev.envs.list(projectId), [projectId]);
  const [name, setName] = useState('');
  const [reqs, setReqs] = useState('');
  const [pkg, setPkg] = useState('');
  const [msg, setMsg] = useState(null);
  const create = async (e) => {
    e.preventDefault();
    const out = await attempt(() => dev.envs.create(projectId, { name, requirements: reqs.split('\n').map((l) => l.trim()).filter(Boolean) }), setMsg, 'Environment queued. It builds in the worker.');
    if (out) { setName(''); setReqs(''); envs.reload({ quiet: true }); }
  };
  const act = async (fn, ok) => { const out = await attempt(fn, setMsg, ok); if (out) envs.reload({ quiet: true }); };
  return (
    <div>
      <form className="card" onSubmit={create} aria-label="New environment">
        <h3 className="card__title">New environment</h3>
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
        <Field as="textarea" label="Packages, one per line" rows={4} value={reqs} onChange={(e) => setReqs(e.target.value)} hint="Pin exact versions, for example pandas==2.2.2. Only approved package sources and prebuilt wheels are used." />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={!name.trim()}>Create</button></div>
      </form>
      <Loaded res={envs} what="environments">
        {(d) => (d.items.length === 0 ? <Empty title="No environments yet"><p>Jobs without an environment use the base Python only.</p></Empty> : (
          <>
            <p className="row__meta">Package sources: {(d.package_sources?.indexes ?? []).join(', ') || 'none configured'}{d.package_sources?.wheelhouse ? ' · local wheelhouse in use' : ''}</p>
            <ul className="list">{d.items.map((e) => (
              <li key={e.id} className="row"><div className="row__main"><span className="row__title">{e.name} <Badge tone={e.latest?.status === 'ready' ? 'ok' : e.latest?.status === 'failed' ? 'danger' : 'muted'}>{ENV_STATUS_LABEL[e.latest?.status] ?? e.latest?.status ?? '—'}</Badge></span>
                <span className="row__meta">Version {e.current_version ?? '—'} · {when(e.latest?.created_at ?? e.created_at)}{e.latest?.error ? ` · ${e.latest.error}` : ''}</span></div>
                <div className="card__actions">
                  <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.envs.rebuild(projectId, e.id), 'Rebuild queued.')}>Rebuild</button>
                  <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.envs.remove(projectId, e.id, e.name), 'Environment deleted.')}>Delete</button>
                </div>
                <form onSubmit={(ev) => { ev.preventDefault(); act(() => dev.envs.addPackage(projectId, e.id, pkg.trim()), 'New version queued with that package.'); setPkg(''); }}>
                  <Field label={`Add a package to ${e.name}`} value={pkg} onChange={(ev) => setPkg(ev.target.value)} placeholder="name==1.2.3" />
                </form>
              </li>))}</ul>
          </>
        ))}
      </Loaded>
    </div>
  );
}
