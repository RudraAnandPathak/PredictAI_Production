import { useState } from 'react';
import { research } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { SEARCH_MODES, buildSearchBody, documentRows, hitPlace, hitWhy } from '../../features/research/documents.js';
import { Empty, Notice } from './shared.jsx';

/** Search inside this project's own documents. Results are quoted passages with their page and section; nothing here leaves the server. */
export function DocumentsPanel({ projectId }) {
  const status = useResource(() => research.documentStatus(projectId), [projectId]);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('hybrid');
  const [picked, setPicked] = useState([]);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [result, setResult] = useState(null);
  const rows = documentRows(status.data);
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  async function submit(event) {
    event.preventDefault();
    const built = buildSearchBody({ query, mode, assetIds: picked, k: 8 });
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({}); setMessage(null); setBusy(true);
    try { setResult(await research.searchDocuments(projectId, built.body)); status.reload({ quiet: true }); } catch (e) { setResult(null); setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }

  return (
    <section className="stack" aria-label="Search documents">
      <form className="card stack" onSubmit={submit} noValidate aria-label="Search your documents">
        <h2 className="card__title">Search your documents</h2>
        <p className="row__meta">Looks only in documents uploaded to this project. Nothing is sent outside this server. Each result is a quoted passage with its page.</p>
        <Field label="What do you want to find?" type="search" value={query} maxLength={2000} onChange={(e) => setQuery(e.target.value)} error={errors.query} />
        <Field label="How to search" as="select" value={mode} onChange={(e) => setMode(e.target.value)} error={errors.mode}>{SEARCH_MODES.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</Field>
        {status.loading && !status.data ? <PageLoading label="Looking at your documents…" /> : null}
        {status.error ? <ErrorState title="Could not load your documents" message={describeError(status.error)} onRetry={() => status.reload()} /> : null}
        {!status.loading && !status.error && rows.length === 0 ? <Empty title="No documents yet"><p>Upload a document to this project to search it.</p></Empty> : null}
        {rows.length ? (
          <fieldset className="stack"><legend className="row__meta">Search only these (leave all unticked to search every document)</legend>
            {rows.map((d) => (
              <label key={d.id} className="check-field"><input type="checkbox" checked={picked.includes(d.id)} disabled={!d.searchable} onChange={() => toggle(d.id)} /><span><span className="check-field__label">{d.name}</span><span className="field__hint">{d.state}</span></span></label>
            ))}
          </fieldset>
        ) : null}
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></div>
      </form>
      {result ? (
        <div className="card stack" role="region" aria-label="Document search results">
          <h2 className="card__title">{result.hits.length ? `${result.hits.length} passage${result.hits.length === 1 ? '' : 's'}` : 'Nothing found'}</h2>
          {(result.notes ?? []).map((n) => <p key={n} className="row__meta">{n}</p>)}
          {(result.skipped ?? []).length ? <p className="row__meta">{result.skipped.length} document(s) were skipped because their text is not ready.</p> : null}
          <ol className="list">
            {result.hits.map((h) => (
              <li key={h.chunk_id} className="row" data-chunk-id={h.chunk_id}>
                <div className="row__main">
                  <span className="row__title">{h.document} · {hitPlace(h)}</span>
                  <blockquote className="citation__quote">{h.text}</blockquote>
                  <span className="row__meta">{hitWhy(h)}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  );
}
