import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { ReviewBox } from '../../components/reviews/ReviewBox.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { EXPORT_FORMATS, EXPORT_FORMAT_LABEL, READINESS_LABEL, READINESS_TONE, ops, readinessProblems } from '../../features/phase7/labels.js';
import { saveBlob } from '../research/shared.jsx';
import { Badge, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

const STYLES = ['apa', 'mla', 'harvard', 'ieee', 'chicago'];

function Claim({ block, claim, index, editable, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(claim.text);
  return (
    <span className="claim">
      {editing ? (
        <span>
          <Field as="textarea" label="Statement" rows={3} maxLength={1500} value={text} onChange={(e) => setText(e.target.value)} />
          <span className="btn-row">
            <button type="button" className="btn" onClick={async () => { if (await onEdit(ops.editClaim(block.id, index, text))) setEditing(false); }}>Save</button>
            <button type="button" className="btn btn--ghost" onClick={() => { setText(claim.text); setEditing(false); }}>Cancel</button>
          </span>
        </span>
      ) : (
        <>
          {claim.text} {claim.marker ? <sup className="row__meta">{claim.marker}</sup> : <Badge tone="warn" title="This statement has no evidence attached">no evidence</Badge>}
          {claim.caveats?.length ? <span className="row__meta"> ({claim.caveats.join('; ')})</span> : null}
          {editable ? <button type="button" className="link-button" onClick={() => setEditing(true)}>Edit</button> : null}{' '}
        </>
      )}
    </span>
  );
}

function Blocks({ blocks, editable, onEdit }) {
  return blocks.map((b) => {
    if (b.type === 'paragraph') return <p key={b.id}>{b.claims.map((c, i) => <Claim key={i} block={b} claim={c} index={i} editable={editable} onEdit={onEdit} />)}</p>;
    if (b.type === 'table') return (
      <figure key={b.id}>
        <table className="table"><thead><tr>{(b.columns ?? []).map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
          <tbody>{(b.rows ?? []).map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v ?? '')}</td>)}</tr>)}</tbody></table>
        {b.caption ? <figcaption className="row__meta">{b.caption} {b.marker}</figcaption> : null}
      </figure>
    );
    if (b.type === 'figure') return <p key={b.id} className="row__meta">Figure: {b.caption ?? b.title ?? 'chart'} {b.marker} (included in exports as an image)</p>;
    return <Notice key={b.id} kind="info">{b.text}</Notice>;
  });
}

function ExportPanel({ report, onDone }) {
  const [format, setFormat] = useState('pdf');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const exports = useResource(() => phase7.reports.exports(report.id), [report.id, report.version]);
  const blocked = report.readiness.status === 'incomplete';
  async function make() {
    setBusy(true);
    const out = await attempt(() => phase7.reports.createExport(report.id, { format, version: report.version, style: report.view.citation_style }), setMsg);
    setBusy(false);
    if (out) { setMsg({ kind: 'success', text: `${EXPORT_FORMAT_LABEL[format]} created.${out.readiness && out.readiness !== 'ready' ? ' It carries the same review notes shown on this page.' : ''}` }); exports.reload({ quiet: true }); onDone?.(); }
  }
  async function save(id) { await attempt(async () => saveBlob(await phase7.reports.download(id)), setMsg); }
  return (
    <section className="card" aria-label="Export">
      <h2 className="card__title">Export</h2>
      <Field as="select" label="Format" value={format} onChange={(e) => setFormat(e.target.value)}>{EXPORT_FORMATS.map((f) => <option key={f} value={f}>{EXPORT_FORMAT_LABEL[f]}</option>)}</Field>
      {blocked ? <Notice kind="warn">This report is incomplete, so an export will say so on its first page.</Notice> : null}
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button type="button" className="btn" disabled={busy} onClick={make}>{busy ? 'Creating the file…' : 'Create export'}</button></div>
      {(exports.data?.items ?? []).length ? (
        <ul className="list">{exports.data.items.map((x) => <li key={x.id} className="row"><div className="row__main"><span className="row__title">{EXPORT_FORMAT_LABEL[x.format] ?? x.format} · version {x.version}</span><span className="row__meta">{when(x.created_at)}</span></div><button type="button" className="btn btn--ghost" onClick={() => save(x.id)}>Download</button></li>)}</ul>
      ) : null}
    </section>
  );
}

function VersionsPanel({ report, onView }) {
  const v = useResource(() => phase7.reports.versions(report.id), [report.id, report.version]);
  return (
    <section className="card" aria-label="Versions"><h2 className="card__title">Versions</h2>
      <Loaded res={v} what="versions">{(d) => <ul className="list">{d.items.slice().reverse().map((x) => <li key={x.version} className="row"><div className="row__main"><span className="row__title">Version {x.version}{x.version === d.current_version ? ' (current)' : ''}</span><span className="row__meta">{when(x.created_at)}{x.note ? ` · ${x.note}` : ''}</span></div>{x.version !== report.version ? <button type="button" className="btn btn--ghost" onClick={() => onView(x.version)}>View</button> : null}</li>)}</ul>}</Loaded>
    </section>
  );
}

/** One report: the cited text, review state, editing with optimistic version checks, exports, versions, and a link to schedule delivery. */
export function ReportPage() {
  const { reportId } = useParams('/reports/:reportId');
  const { navigate } = useRouter();
  const [wanted, setWanted] = useState(null);
  const res = useResource(() => phase7.reports.get(reportId, wanted ? { version: Number(wanted) } : undefined), [reportId, wanted]);
  const [msg, setMsg] = useState(null);
  const [title, setTitle] = useState(null);

  async function edit(op, note) {
    const r = res.data;
    if (r.version !== r.current_version) { setMsg({ kind: 'error', text: 'Open the current version to edit.' }); return false; }
    const out = await attempt(() => phase7.reports.edit(r.id, r.current_version, [op], note), setMsg, 'Saved as a new version.');
    if (out) { await res.reload({ quiet: true }); return true; }
    if (res.data) res.reload({ quiet: true });
    return false;
  }
  async function remove() {
    if (!window.confirm('Delete this report? Its exports and schedules stop too.')) return;
    const out = await attempt(() => phase7.reports.remove(reportId), setMsg);
    if (out) navigate('/reports');
  }
  return (
    <article className="page">
      <Loaded res={res} what="this report">
        {(r) => {
          const view = r.view;
          const problems = readinessProblems(r.readiness);
          return (
            <>
              <PageHeader eyebrow={`Version ${r.version}`} title={r.title} back={{ to: '/reports', label: 'Reports' }} lead={view.subtitle ?? undefined} />
              <ReviewBox key={r.id} kind="report" targetId={r.id} />
              <div className="btn-row"><Badge tone={READINESS_TONE[r.readiness.status]}>{READINESS_LABEL[r.readiness.status]}</Badge><span className="row__meta">Your access: {r.your_access}</span></div>
              {problems.length ? <Notice kind="warn" title="Before you rely on this report">{<ul>{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}</Notice> : null}
              {r.version !== r.current_version ? <Notice kind="info">You are looking at an earlier version. <button type="button" className="link-button" onClick={() => setWanted(null)}>Back to the current version</button>.</Notice> : null}
              {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
              {r.editable ? (
                <form className="card" onSubmit={async (e) => { e.preventDefault(); if (title !== null && await edit(ops.title(title))) setTitle(null); }}>
                  <Field label="Title" value={title ?? r.title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
                  <Field as="select" label="Citation style" value={view.citation_style} onChange={(e) => edit(ops.citationStyle(e.target.value))}>{STYLES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}</Field>
                  <div className="card__actions"><button className="btn btn--ghost" disabled={title === null || title === r.title}>Save title</button></div>
                </form>
              ) : null}
              {view.sections.map((s) => (
                <section key={s.id} className="card" aria-labelledby={`h-${s.id}`}>
                  <div className="card__row"><h2 className="card__title" id={`h-${s.id}`}>{s.heading}</h2>{s.status === 'incomplete' ? <Badge tone="warn" title={s.why ?? ''}>incomplete</Badge> : null}</div>
                  {s.why && s.status === 'incomplete' ? <p className="row__meta">{s.why}</p> : null}
                  <Blocks blocks={s.blocks} editable={r.editable} onEdit={edit} />
                  {s.subsections.map((x) => <div key={x.id}><h3>{x.heading}</h3><Blocks blocks={x.blocks} editable={r.editable} onEdit={edit} /></div>)}
                </section>
              ))}
              <section className="card" aria-label="References"><h2 className="card__title">References</h2>
                {view.references.length === 0 ? <p>No sources cited.</p> : <ol>{view.references.map((x) => <li key={x.ref} value={x.number}>{x.text}{x.missing?.length ? <span className="row__meta"> (missing: {x.missing.join(', ')})</span> : null} <span className="row__meta">[{x.verification}]</span></li>)}</ol>}
              </section>
              <ExportPanel report={r} />
              <VersionsPanel report={r} onView={setWanted} />
              <div className="btn-row">
                <Link to={`/schedules?report=${encodeURIComponent(r.id)}`} className="btn btn--ghost">Schedule delivery</Link>
                <Link to="/workspaces" className="btn btn--ghost">Share through a workspace</Link>
                {r.your_access !== 'view' && r.editable ? <button type="button" className="btn btn--ghost" onClick={remove}>Delete report</button> : null}
              </div>
              <p className="row__meta">{r.restriction_note}</p>
            </>
          );
        }}
      </Loaded>
    </article>
  );
}
