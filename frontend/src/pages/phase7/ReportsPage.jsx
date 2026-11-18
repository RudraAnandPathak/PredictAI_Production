import { useState } from 'react';
import { phase7, research, workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { READINESS_LABEL, READINESS_TONE, RESOURCE_LABEL, SOURCE_LABEL } from '../../features/phase7/labels.js';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

// What each kind of report is built from. Every source is a result that already exists: creating a report never re-runs research or training.
const SOURCES = {
  research: { key: 'bundle_ids', label: 'Research results', load: (pid) => research.bundles(pid, { limit: 50 }).then((d) => (d.items ?? []).map((b) => ({ id: b.id, label: b.question ?? b.title ?? b.id }))) },
  prediction: { key: 'version_ids', label: 'Registered model versions', load: (pid) => phase7.registry.models({ project_id: pid, limit: 100 }).then((d) => d.items.filter((m) => m.project_id === pid).flatMap((m) => (m.latest ? [{ id: m.latest.id, label: `${m.name} · version ${m.latest.version}` }] : []))) },
  scenario: { key: 'adv_run_ids', label: 'Saved analyses and scenarios', load: (pid) => phase7.intelligence.runs({ project_id: pid, limit: 50 }).then((d) => d.items.map((r) => ({ id: r.id, label: r.title }))) },
};

function CreateForm() {
  const { navigate } = useRouter();
  const projects = useResource(() => workspace.projects.list({ status: 'active', limit: 100 }), []);
  const [projectId, setProjectId] = useState('');
  const [kind, setKind] = useState('blank');
  const [picked, setPicked] = useState([]);
  const [title, setTitle] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const src = SOURCES[kind];
  const options = useResource(() => (projectId && src ? src.load(projectId) : Promise.resolve([])), [projectId, kind]);
  async function submit(e) {
    e.preventDefault();
    if (!projectId) { setMsg({ kind: 'error', text: 'Choose a project.' }); return; }
    setBusy(true);
    const body = { source_kind: kind, source: src ? { [src.key]: picked } : {}, title: title || undefined };
    const out = await attempt(() => phase7.reports.create(projectId, body), setMsg);
    setBusy(false);
    if (out) navigate(`/reports/${encodeURIComponent(out.id)}`);
  }
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  return (
    <form className="card" onSubmit={submit} aria-label="Create a report">
      <h2 className="card__title">New report</h2>
      <p className="row__meta">A report is assembled from results you already have. Nothing is researched, trained or calculated again, and every statement keeps a link to its evidence.</p>
      <Field as="select" label="Project" value={projectId} onChange={(e) => { setProjectId(e.target.value); setPicked([]); }}>
        <option value="">Choose…</option>
        {(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </Field>
      <Field as="select" label="Built from" value={kind} onChange={(e) => { setKind(e.target.value); setPicked([]); }}>
        <option value="blank">Nothing yet (a blank outline)</option>
        {Object.keys(SOURCES).map((k) => <option key={k} value={k}>{SOURCE_LABEL[k]}</option>)}
      </Field>
      {src && projectId ? (
        <fieldset className="field">
          <legend className="field__label">{src.label}</legend>
          {options.loading ? <p className="row__meta">Loading…</p> : (options.data ?? []).length === 0 ? <p className="row__meta">None in this project yet.</p> : (
            options.data.map((o) => <label key={o.id} className="row__check"><input type="checkbox" checked={picked.includes(o.id)} onChange={() => toggle(o.id)} /> {o.label}</label>)
          )}
        </fieldset>
      ) : null}
      <Field label="Title (optional)" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={busy || (src && picked.length === 0)}>{busy ? 'Creating…' : 'Create report'}</button></div>
    </form>
  );
}

/** Reports: everything you can open, and a form to build a new one from saved results. */
export function ReportsPage() {
  const res = useResource(() => phase7.reports.list({ limit: 100 }), []);
  return (
    <article className="page">
      <PageHeader eyebrow="Reports" title="Reports" lead="Structured, cited reports built from your saved results. Edit them, keep versions, export them and, if you wish, share them with a workspace or schedule delivery." />
      <Loaded res={res} what="your reports">
        {(d) => (
          <>
            {d.items.length === 0 ? <Empty title="No reports yet"><p>Create one below.</p></Empty> : (
              <ul className="list">{d.items.map((r) => (
                <li key={r.id} className="row"><div className="row__main">
                  <span className="row__title"><Link to={`/reports/${encodeURIComponent(r.id)}`}>{r.title}</Link></span>
                  <span className="row__meta">{r.report_type?.replace(/_/g, ' ')} · version {r.version} · updated {when(r.updated_at)}{r.readiness ? <> · <Badge tone={READINESS_TONE[r.readiness]}>{READINESS_LABEL[r.readiness]}</Badge></> : null}</span>
                </div></li>))}</ul>
            )}
            {d.shared_with_me?.length ? <section className="card"><h2 className="card__title">Shared with you</h2><ul className="list">{d.shared_with_me.map((s) => <li key={s.grant_id} className="row"><div className="row__main"><span className="row__title"><Link to={`/reports/${encodeURIComponent(s.resource_id)}`}>{RESOURCE_LABEL[s.resource_type] ?? 'Item'} in {s.workspace_name}</Link></span><span className="row__meta">{s.level} access</span></div></li>)}</ul></section> : null}
            <CreateForm />
          </>
        )}
      </Loaded>
    </article>
  );
}
