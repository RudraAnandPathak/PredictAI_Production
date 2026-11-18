import { useEffect, useState } from 'react';
import { ri } from '../../../api/index.js';
import { Notice, attempt, when } from '../../../pages/phase7/shared.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { citationWhere } from '../labels.js';

function Passage({ wid, target, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [msg, setMsg] = useState(null);
  const chunkId = target.chunk_id ?? target.id;
  useEffect(() => {
    let live = true;
    setState({ loading: true, data: null, error: null });
    ri.sources.passage(wid, chunkId).then((data) => live && setState({ loading: false, data, error: null })).catch((error) => live && setState({ loading: false, data: null, error }));
    return () => { live = false; };
  }, [wid, chunkId]);
  const d = state.data;
  return (
    <div className="ri-passage" role="region" aria-label="Cited passage">
      <p className="row__meta">{target.source_title ? citationWhere(target) : 'Passage'} <button type="button" className="link-button" onClick={onClose}>Close</button></p>
      {state.loading ? <p className="row__meta">Loading passage…</p> : null}
      {state.error ? <Notice kind="error">The passage could not be loaded. It may have been removed with its source.</Notice> : null}
      {d ? (
        <>
          {d.before ? <p className="citation__around">{d.before.text}</p> : null}
          <blockquote className="citation__quote">{d.passage.text}</blockquote>
          {d.after ? <p className="citation__around">{d.after.text}</p> : null}
          <div className="btn-row"><button type="button" className="btn btn--ghost" onClick={() => attempt(() => ri.findings.create(wid, d.passage.source_id, chunkId, 'evidence'), setMsg, 'Saved as evidence.')}>Save as evidence</button></div>
        </>
      ) : null}
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
    </div>
  );
}

function Notes({ wid }) {
  const res = useResource(() => ri.notes.list(wid), [wid]);
  const [body, setBody] = useState('');
  const [msg, setMsg] = useState(null);
  async function add(e) {
    e.preventDefault();
    const out = await attempt(() => ri.notes.create(wid, body.trim()), setMsg);
    if (out) { setBody(''); res.reload({ quiet: true }); }
  }
  const items = res.data?.items ?? [];
  return (
    <section aria-label="Notes">
      <h3 className="card__title">Notes</h3>
      <ul className="list">{items.map((n) => (
        <li key={n.id} className="row"><div className="row__main"><span className="note__body">{n.body}</span><span className="row__meta">{when(n.created_at)} <button type="button" className="link-button" onClick={() => attempt(() => ri.notes.remove(wid, n.id), setMsg).then((o) => o && res.reload({ quiet: true }))}>Delete</button></span></div></li>
      ))}</ul>
      {items.length === 0 && !res.loading ? <p className="row__meta">No notes yet.</p> : null}
      <form onSubmit={add}>
        <div className="field"><label className="field__label" htmlFor="ri-note">New note</label><textarea id="ri-note" className="field__input" rows={2} maxLength={25000} value={body} onChange={(e) => setBody(e.target.value)} /></div>
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <button className="btn btn--ghost" disabled={!body.trim()}>Add note</button>
      </form>
    </section>
  );
}

function Findings({ wid, refreshKey }) {
  const res = useResource(() => ri.findings.list(wid), [wid, refreshKey]);
  const items = res.data?.items ?? [];
  return (
    <section aria-label="Saved evidence">
      <h3 className="card__title">Saved evidence</h3>
      {items.length === 0 ? <p className="row__meta">Nothing saved yet. Open a citation and choose “Save as evidence”.</p> : (
        <ul className="list">{items.map((f) => <li key={f.id} className="row"><div className="row__main"><span className="note__body">{f.text}</span><span className="row__meta">{f.kind} · {when(f.created_at)}</span></div></li>)}</ul>
      )}
    </section>
  );
}

export function EvidencePanel({ wid, target, onClose }) {
  const [tick, setTick] = useState(0);
  useEffect(() => { setTick((t) => t + 1); }, [target]);
  return (
    <section className="card ri-panel" aria-label="Evidence and notes">
      <h2 className="card__title">Evidence</h2>
      {target ? <Passage wid={wid} target={target} onClose={onClose} /> : <p className="row__meta">Click a citation such as [1] in an answer, or “View passages” on a source, to read the exact stored passage here.</p>}
      <Findings wid={wid} refreshKey={tick} />
      <Notes wid={wid} />
    </section>
  );
}
