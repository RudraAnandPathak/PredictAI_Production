import { useState } from 'react';
import { nb, ri } from '../../../api/index.js';
import { importLines } from '../logic.js';
import { Link } from '../../../app/router/Link.jsx';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Empty, Loaded, Notice, PageHeader, attempt, when } from '../../../pages/phase7/shared.jsx';

/** Your notebooks: each one holds sources, a cited chat, notes and Studio outputs. */
export function NotebookListPage() {
  const res = useResource(() => ri.workspaces.list(), []);
  const [name, setName] = useState('');
  const [msg, setMsg] = useState(null);
  const legacy = useResource(() => nb.legacy(), []);
  const [imp, setImp] = useState(null);
  const [importing, setImporting] = useState(false);
  async function runImport() {
    setImporting(true);
    const out = await attempt(() => nb.importLegacy(), setMsg);
    setImporting(false);
    if (out) { setImp(importLines(out)); legacy.reload({ quiet: true }); res.reload({ quiet: true }); }
  }
  async function create(e) {
    e.preventDefault();
    if (!name.trim()) { setMsg({ kind: 'error', text: 'Give the notebook a name.' }); return; }
    const out = await attempt(() => ri.workspaces.create(name.trim()), setMsg, 'Notebook created.');
    if (out) { setName(''); res.reload({ quiet: true }); }
  }
  return (
    <article className="page">
      <PageHeader eyebrow="Notebook" title="Notebooks" lead="Add sources or let Deep Research find them, ask questions that cite them, and make briefings, study guides, flashcards, quizzes, mind maps, tables, slides, infographics and audio overviews. Everything is grounded in your sources." />
      <form className="card" onSubmit={create}>
        <Field label="New notebook" value={name} onChange={(e) => setName(e.target.value)} />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn">Create notebook</button></div>
      </form>
      {legacy.data?.not_imported > 0 ? (
        <section className="card" aria-label="Older research">
          <p>{legacy.data.not_imported} report(s) from the older Research area are not in a notebook yet. Copy each into its own notebook (the originals are kept).</p>
          <div className="card__actions"><button type="button" className="btn" disabled={importing} onClick={runImport}>{importing ? 'Copying…' : 'Copy them into notebooks'}</button></div>
        </section>) : null}
      {imp ? <div role="status">{imp.map((l, i) => <p key={i} className="row__meta">{l}</p>)}</div> : null}
      <Loaded res={res} what="your notebooks">
        {(d) => d.items.length === 0 ? <Empty title="No notebooks yet">Create one above, then add a PDF, a document or a web address.</Empty> : (
          <ul className="list">{d.items.map((w) => (
            <li key={w.id} className="row"><div className="row__main"><Link to={`/notebook/${encodeURIComponent(w.id)}`} className="row__title">{w.name}</Link><span className="row__meta">{w.source_count ?? 0} source(s) · updated {when(w.updated_at)}</span></div></li>))}</ul>
        )}
      </Loaded>
    </article>
  );
}
