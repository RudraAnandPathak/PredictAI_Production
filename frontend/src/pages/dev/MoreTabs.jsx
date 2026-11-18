import { useState } from 'react';
import { dev } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { SESSION_KIND_LABEL, bytesText, lineageRows } from '../../features/dev/labels.js';
import { Badge, Empty, JobStatus, Loaded, Notice, attempt, when } from './shared.jsx';

export function NotebooksTab({ projectId }) {
  const sessions = useResource(() => dev.sessions.status(projectId), [projectId]);
  const [path, setPath] = useState('');
  const [msg, setMsg] = useState(null);
  const create = async (e) => { e.preventDefault(); const out = await attempt(() => dev.notebooks.create(projectId, path.trim(), null), setMsg, 'Notebook created. Run it from the Jobs tab.'); if (out) setPath(''); };
  return (
    <div>
      <form className="card" onSubmit={create} aria-label="New notebook">
        <Field label="New notebook path" value={path} onChange={(e) => setPath(e.target.value)} placeholder="notebooks/analysis.ipynb" />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={!path.trim()}>Create</button></div>
      </form>
      <Loaded res={sessions} what="session status">
        {(d) => (
          <section><h3>Interactive sessions</h3>
            {Object.entries(d.availability).map(([kind, a]) => (
              <div key={kind} className="row"><div className="row__main"><span className="row__title">{SESSION_KIND_LABEL[kind] ?? kind} <Badge tone={a.available ? 'ok' : 'muted'}>{a.available ? 'Available' : 'Not available'}</Badge></span>
                {!a.available ? <span className="row__meta">{a.reason}</span> : null}</div>
                {a.available ? <button className="btn btn--ghost" type="button" onClick={async () => { const out = await attempt(() => dev.sessions.start(projectId, kind), setMsg, 'Session started.'); if (out) sessions.reload({ quiet: true }); }}>Start</button> : null}</div>))}
            {d.sessions.length ? <ul className="list">{d.sessions.map((s) => <li key={s.id} className="row"><div className="row__main"><span className="row__title">{SESSION_KIND_LABEL[s.kind] ?? s.kind} · {s.status}</span><span className="row__meta">started {when(s.started_at)} · stops after {d.idle_minutes} idle minutes</span></div>
              {s.status === 'running' || s.status === 'starting' ? <button className="btn btn--ghost" type="button" onClick={async () => { await attempt(() => dev.sessions.stop(projectId, s.id), setMsg, 'Stopped.'); sessions.reload({ quiet: true }); }}>Stop</button> : null}</li>)}</ul> : null}
          </section>
        )}
      </Loaded>
    </div>
  );
}

export function ArtifactsTab({ projectId }) {
  const arts = useResource(() => dev.artifacts.list(projectId), [projectId]);
  const sum = useResource(() => dev.artifacts.summary(projectId), [projectId]);
  const [msg, setMsg] = useState(null);
  return (
    <div>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <Loaded res={sum} what="storage">{(d) => <p className="row__meta">Using {bytesText(d.used_bytes)} of {bytesText(d.limit_bytes)}.</p>}</Loaded>
      <Loaded res={arts} what="artifacts">
        {(d) => (d.items.length === 0 ? <Empty title="No artifacts yet"><p>Files a job writes to its outputs folder are kept here with a checksum and the job that made them.</p></Empty> : (
          <ul className="list">{d.items.map((a) => (
            <li key={a.id} className="row"><div className="row__main"><span className="row__title">{a.name}</span><span className="row__meta">{a.bucket} · v{a.version} · {bytesText(a.bytes)} · sha256 {String(a.sha256).slice(0, 12)}… · {when(a.created_at)}{a.retention_until ? ` · kept until ${when(a.retention_until)}` : ''}</span></div>
              <button className="btn btn--ghost" type="button" onClick={async () => { const out = await attempt(() => dev.artifacts.remove(projectId, a.id, a.name), setMsg, 'Deleted.'); if (out) { arts.reload({ quiet: true }); sum.reload({ quiet: true }); } }}>Delete</button></li>))}</ul>
        ))}
      </Loaded>
    </div>
  );
}

export function ExperimentsTab({ projectId }) {
  const list = useResource(() => dev.experiments.list(projectId), [projectId]);
  const [name, setName] = useState('');
  const [msg, setMsg] = useState(null);
  const [lineage, setLineage] = useState(null);
  const create = async (e) => { e.preventDefault(); const out = await attempt(() => dev.experiments.create(projectId, { name }), setMsg, 'Experiment recorded.'); if (out) { setName(''); list.reload({ quiet: true }); } };
  const showLineage = async (id) => { const l = await attempt(() => dev.experiments.lineage(projectId, id), setMsg); if (l) setLineage({ id, rows: lineageRows(l) }); };
  return (
    <div>
      <form className="card" onSubmit={create} aria-label="New experiment"><Field label="Experiment name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}<div className="card__actions"><button className="btn" disabled={!name.trim()}>Record</button></div></form>
      <Loaded res={list} what="experiments">
        {(d) => (d.items.length === 0 ? <Empty title="No experiments yet" /> : (
          <ul className="list">{d.items.map((x) => (
            <li key={x.id} className="row"><div className="row__main"><span className="row__title">{x.name} <Badge tone="muted">{x.status}</Badge> {x.approval_state && x.approval_state !== 'none' ? <Badge tone="accent">{x.approval_state}</Badge> : null}</span><span className="row__meta">{when(x.created_at)}{x.seed != null ? ` · seed ${x.seed}` : ''}</span></div>
              <button className="btn btn--ghost" type="button" onClick={() => showLineage(x.id)}>Lineage</button>
              {lineage?.id === x.id ? <ul className="list">{lineage.rows.map((r, i) => <li key={`${r.kind}-${i}`} className="row"><div className="row__main"><span className="row__title">{r.label} <Badge tone={r.present ? 'ok' : 'warn'}>{r.present ? 'Recorded' : 'Missing'}</Badge></span>{r.detail ? <span className="row__meta">{String(r.detail)}</span> : null}</div></li>)}</ul> : null}</li>))}</ul>
        ))}
      </Loaded>
      <Notice kind="info">Approving an experiment or a pipeline run records a decision. Nothing is deployed by this release.</Notice>
    </div>
  );
}

export function GitTab({ projectId }) {
  const git = useResource(() => dev.git.status(projectId), [projectId]);
  const [message, setMessage] = useState('');
  const [msg, setMsg] = useState(null);
  const act = async (fn, ok) => { const out = await attempt(fn, setMsg, ok); if (out) git.reload({ quiet: true }); return out; };
  return (
    <Loaded res={git} what="Git status">
      {(d) => (!d.git_available ? <Notice kind="warn">Git is not installed on this computer, so version control is unavailable here.</Notice> : !d.initialised ? (
        <div><Empty title="Git is not set up for this project"><p>Initialise a local repository. Nothing leaves your machine until you connect a remote and give consent.</p></Empty>
          <button className="btn" type="button" onClick={() => act(() => dev.git.init(projectId), 'Repository created.')}>Initialise Git</button>{msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}</div>
      ) : (
        <div>
          <p className="row__meta">Branch <b>{d.branch}</b> · {d.changes?.length ?? 0} changed file(s){d.repo ? ` · remote ${d.repo.provider} (${d.repo.consent_scope})` : ' · no remote connected'}</p>
          <form className="card" onSubmit={(e) => { e.preventDefault(); act(() => dev.git.commit(projectId, message), 'Committed.'); setMessage(''); }} aria-label="Commit">
            <Field label="Commit message" value={message} onChange={(e) => setMessage(e.target.value)} hint="A commit that contains something that looks like a credential is refused." />
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <div className="card__actions"><button className="btn" disabled={!message.trim()}>Commit</button>
              {d.repo ? <><button className="btn btn--ghost" type="button" onClick={() => act(() => dev.git.pull(projectId), 'Pulled.')}>Pull</button>
                <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.git.push(projectId), 'Pushed.')}>Push</button></> : null}</div>
          </form>
          <p className="row__meta">Git credentials are stored encrypted on the server, never in notebooks or project files. Force-push is not offered.</p>
        </div>
      ))}
    </Loaded>
  );
}

export function PipelinesTab({ projectId }) {
  const pipes = useResource(() => dev.pipelines.list(projectId), [projectId]);
  const runs = useResource(() => dev.pipelines.runs(projectId), [projectId]);
  const [msg, setMsg] = useState(null);
  const act = async (fn, ok) => { const out = await attempt(fn, setMsg, ok); if (out) { pipes.reload({ quiet: true }); runs.reload({ quiet: true }); } };
  return (
    <div>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <Loaded res={pipes} what="pipelines">
        {(d) => (
          <>
            {!d.enabled ? <Notice kind="warn" title="Pipelines are switched off">Pipeline runs are a feature-flagged part of this release and are off on this installation. Definitions can be saved; runs are refused until the owner turns them on. Deploying is disabled in every configuration.</Notice> : null}
            {d.items.length === 0 ? <Empty title="No pipelines defined" /> : <ul className="list">{d.items.map((p) => <li key={p.id} className="row"><div className="row__main"><span className="row__title">{p.name}</span><span className="row__meta">{(p.stages ?? []).map((s) => s.type).join(' → ')}</span></div>
              <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.pipelines.run(projectId, p.id), 'Run queued.')}>Run</button></li>)}</ul>}
          </>
        )}
      </Loaded>
      <Loaded res={runs} what="pipeline runs">
        {(d) => (d.items.length === 0 ? null : <section><h3>Runs</h3><ul className="list">{d.items.map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title"><JobStatus status={r.status} /></span>
          <span className="row__meta">{when(r.created_at)} · {r.branch ?? 'no branch'} · decision: {r.approval_state ?? 'none'}</span></div></li>)}</ul></section>)}
      </Loaded>
    </div>
  );
}

export function HistoryTab({ projectId }) {
  const h = useResource(() => dev.history(projectId, { limit: 50 }), [projectId]);
  return (
    <Loaded res={h} what="history">
      {(d) => (d.items.length === 0 ? <Empty title="No history yet" /> : <ul className="list">{d.items.map((e) => <li key={e.id} className="row"><div className="row__main"><span className="row__title">{e.action}</span><span className="row__meta">{e.outcome} · {e.actor} · {when(e.created_at)}</span></div></li>)}</ul>)}
    </Loaded>
  );
}
