import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { FINDING_KINDS, RELATIONS, canLink, relationLabel } from '../logic.js';

/** Saved findings with kind and comment, connections between findings, and groups. */
export function StudioEvidence({ wid }) {
  const ev = useResource(() => ri.studio.evidence(wid), [wid]);
  const links = useResource(() => ri.studio.links(wid), [wid]);
  const groups = useResource(() => ri.studio.groups(wid), [wid]);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rel, setRel] = useState('supports');
  const [gname, setGname] = useState('');
  const [msg, setMsg] = useState(null);
  const items = (ev.data?.items ?? []).filter((f) => (!kind || f.kind === kind) && (!q.trim() || `${f.text} ${f.comment ?? ''}`.toLowerCase().includes(q.trim().toLowerCase())));
  const byId = new Map((ev.data?.items ?? []).map((f) => [f.id, f]));
  const short = (id) => (byId.get(id)?.text ?? 'a removed finding').slice(0, 70);
  const reloadAll = () => { ev.reload({ quiet: true }); links.reload({ quiet: true }); groups.reload({ quiet: true }); };
  const act = (fn, ok) => attempt(fn, setMsg, ok).then((o) => { if (o) reloadAll(); return o; });
  return (
    <section className="card" aria-label="Findings and connections">
      <h2 className="card__title">2. Findings, connections and groups</h2>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="ri-filters">
        <Field label="Search findings" value={q} onChange={(e) => setQ(e.target.value)} />
        <Field as="select" label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All</option>{FINDING_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Field>
      </div>
      {ev.data && items.length === 0 ? <p className="row__meta">No findings match. Save passages from a workspace’s evidence panel (“Save as evidence”).</p> : null}
      <ul className="list">{items.map((f) => (
        <li key={f.id} className="row"><div className="row__main">
          <blockquote className="citation__quote">{f.text}</blockquote>
          <span className="row__meta">{f.source_title ?? 'Source'}{f.locator ? ` · ${f.locator}` : ''} · {f.links} connection(s)</span>
          <label className="field__label" htmlFor={`k-${f.id}`}>Kind</label>
          <select id={`k-${f.id}`} className="field__input" value={f.kind} onChange={(e) => act(() => ri.findings.patch(wid, f.id, { kind: e.target.value }))}>{FINDING_KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <Comment key={`${f.id}-${f.comment ?? ''}`} f={f} onSave={(c) => act(() => ri.findings.patch(wid, f.id, { comment: c }), 'Comment saved.')} />
          <button type="button" className="link-button" onClick={() => act(() => ri.findings.remove(wid, f.id))}>Delete finding</button>
        </div></li>
      ))}</ul>

      <h3 className="card__title">Connect two findings</h3>
      <div className="ri-filters">
        <Field as="select" label="From" value={from} onChange={(e) => setFrom(e.target.value)}><option value="">Choose…</option>{(ev.data?.items ?? []).map((f) => <option key={f.id} value={f.id}>{f.text.slice(0, 60)}</option>)}</Field>
        <Field as="select" label="Relation" value={rel} onChange={(e) => setRel(e.target.value)}>{RELATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Field>
        <Field as="select" label="To" value={to} onChange={(e) => setTo(e.target.value)}><option value="">Choose…</option>{(ev.data?.items ?? []).map((f) => <option key={f.id} value={f.id}>{f.text.slice(0, 60)}</option>)}</Field>
      </div>
      <button type="button" className="btn btn--ghost" disabled={!canLink(from, to)} onClick={() => act(() => ri.studio.link(wid, from, to, rel), 'Connected.')}>Connect</button>
      <ul className="list">{(links.data?.items ?? []).map((l) => <li key={l.id} className="row"><div className="row__main"><span className="note__body">“{short(l.from_id)}…” <strong>{relationLabel(l.relation)}</strong> “{short(l.to_id)}…”</span><button type="button" className="link-button" onClick={() => act(() => ri.studio.unlink(wid, l.id))}>Remove connection</button></div></li>)}</ul>

      <h3 className="card__title">Groups</h3>
      <form onSubmit={(e) => { e.preventDefault(); act(() => ri.studio.createGroup(wid, gname.trim()), 'Group created.').then((o) => o && setGname('')); }}>
        <Field label="New group name" value={gname} maxLength={120} onChange={(e) => setGname(e.target.value)} />
        <button className="btn btn--ghost" disabled={!gname.trim()}>Create group</button>
      </form>
      <ul className="list">{(groups.data?.items ?? []).map((g) => (
        <li key={g.id} className="row"><div className="row__main">
          <fieldset className="ri-fieldset"><legend>{g.name} ({g.finding_ids.length})</legend>
            {(ev.data?.items ?? []).map((f) => <label key={f.id} className="ri-check"><input type="checkbox" checked={g.finding_ids.includes(f.id)} onChange={() => act(() => ri.studio.setGroupItems(wid, g.id, g.finding_ids.includes(f.id) ? g.finding_ids.filter((x) => x !== f.id) : [...g.finding_ids, f.id]))} /> {f.text.slice(0, 80)}</label>)}
          </fieldset>
          <button type="button" className="link-button" onClick={() => act(() => ri.studio.removeGroup(wid, g.id), 'Group deleted. Its findings were kept.')}>Delete group</button>
        </div></li>
      ))}</ul>
    </section>
  );
}

function Comment({ f, onSave }) {
  const [v, setV] = useState(f.comment ?? '');
  return (
    <div className="field">
      <label className="field__label" htmlFor={`c-${f.id}`}>Your comment</label>
      <textarea id={`c-${f.id}`} className="field__input" rows={2} maxLength={2000} value={v} onChange={(e) => setV(e.target.value)} />
      <button type="button" className="btn btn--ghost" disabled={v === (f.comment ?? '')} onClick={() => onSave(v)}>Save comment</button>
    </div>
  );
}
