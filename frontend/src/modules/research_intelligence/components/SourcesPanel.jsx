import { useRef, useState } from 'react';
import { ri } from '../../../api/index.js';
import { Badge, Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { ACCEPT_ATTRIBUTE, FORMAT_LABEL, ORIGIN_LABEL, accessNote, checkFile, embeddingNote, formatBytes, looksLikeReference, sourceWarnings, statusInfo, workerWaiting } from '../labels.js';

function SourceRow({ wid, source, onChanged, onOpen, setMsg }) {
  const st = statusInfo(source);
  const notes = [accessNote(source), embeddingNote(source), ...sourceWarnings(source)].filter(Boolean);
  const act = (fn, ok) => attempt(fn, setMsg, ok).then((out) => { if (out) onChanged(); });
  return (
    <li className="row ri-source">
      <div className="row__main">
        <span className="row__title">{source.title}</span>
        <span className="row__meta">
          <Badge tone={st.tone}>{st.label}{st.detail ? ` · ${st.detail}` : ''}</Badge>{' '}
          {FORMAT_LABEL[source.format] ?? source.format} · {ORIGIN_LABEL[source.origin] ?? source.origin}
          {source.byte_size ? ` · ${formatBytes(source.byte_size)}` : ''}{source.chunk_count ? ` · ${source.chunk_count} passages` : ''}
        </span>
        {st.hint ? <span className="row__meta">{st.hint}</span> : null}
        {notes.map((n) => <span key={n} className="row__meta">{n}</span>)}
        <span className="btn-row">
          <label className="ri-check"><input type="checkbox" checked={source.included} onChange={(e) => act(() => ri.sources.setIncluded(wid, source.id, e.target.checked))} /> Use in answers</label>
          {source.chunk_count > 0 ? <button type="button" className="link-button" onClick={() => onOpen(source)}>View passages</button> : null}
          {(source.status === 'failed' || source.status === 'cancelled' || source.status === 'ready') ? <button type="button" className="link-button" onClick={() => act(() => ri.sources.reindex(wid, source.id), 'Queued to be read again.')}>Read again</button> : null}
          <button type="button" className="link-button" onClick={() => { if (window.confirm(`Delete “${source.title}”? Its passages, citations and stored file are removed. This cannot be undone.`)) act(() => ri.sources.remove(wid, source.id), 'Source deleted.'); }}>Delete</button>
        </span>
      </div>
    </li>
  );
}

/** Add files or references, and list sources with their honest processing state. */
export function SourcesPanel({ wid, sources, limits, onChanged, onOpen }) {
  const [msg, setMsg] = useState(null);
  const [ref, setRef] = useState('');
  const [progress, setProgress] = useState(null);
  const input = useRef(null);

  async function upload(file) {
    const check = checkFile(file, limits?.max_upload_mb);
    if (!check.ok) { setMsg({ kind: 'error', text: check.reason }); return; }
    setProgress(0);
    const out = await attempt(() => ri.sources.upload(wid, file, { onProgress: (l, t) => setProgress(t ? Math.round((l / t) * 100) : null) }), setMsg, 'Uploaded. It will be read and indexed by the research worker.');
    setProgress(null);
    if (input.current) input.current.value = '';
    if (out) onChanged();
  }
  async function addRef(e) {
    e.preventDefault();
    const out = await attempt(() => ri.sources.addReference(wid, ref.trim()), setMsg, 'Added. The text is fetched by the research worker.');
    if (out) { setRef(''); onChanged(); }
  }
  return (
    <section className="card ri-panel" aria-label="Sources">
      <h2 className="card__title">Sources</h2>
      <div className="field">
        <label className="field__label" htmlFor="ri-file">Add a file</label>
        <input id="ri-file" ref={input} className="field__input" type="file" accept={ACCEPT_ATTRIBUTE} disabled={progress !== null} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <p className="field__hint">PDF, DOCX, TXT, Markdown, CSV or XLSX{limits ? `, up to ${limits.max_upload_mb} MB` : ''}. Scanned PDFs without text are not supported (no OCR).</p>
        {progress !== null ? <progress max="100" value={progress} aria-label="Upload progress" /> : null}
      </div>
      <form onSubmit={addRef} className="ri-ref">
        <div className="field">
          <label className="field__label" htmlFor="ri-ref">Add a web address, DOI or arXiv ID</label>
          <input id="ri-ref" className="field__input" value={ref} maxLength={400} placeholder="https://…, 10.1000/xyz or 2401.01234" onChange={(e) => setRef(e.target.value)} />
          {ref && !looksLikeReference(ref) ? <p className="field__hint">This does not look like a web address, a DOI or an arXiv ID.</p> : null}
        </div>
        <button className="btn" disabled={!looksLikeReference(ref)}>Add</button>
      </form>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      {workerWaiting(sources) ? <Notice kind="info">Some sources are waiting for the research worker. If they stay waiting, start it with <code>python backend/scripts/run_research_worker.py</code>.</Notice> : null}
      {sources.length === 0 ? <p className="row__meta">No sources yet. Add a document to start asking questions about it.</p> : (
        <ul className="list">{sources.map((s) => <SourceRow key={s.id} wid={wid} source={s} onChanged={onChanged} onOpen={onOpen} setMsg={setMsg} />)}</ul>
      )}
    </section>
  );
}
