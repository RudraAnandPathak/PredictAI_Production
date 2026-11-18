import { useState } from 'react';
import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { describeSourceLine, sourceTitle } from '../../features/research/evidence.js';
import { Empty, ExternalLink, Notice, OriginBadge } from './shared.jsx';

/** Every source this project's research has used, with where it came from and what is known about it. Saved sources are kept until you remove them. */
export function SourcesPanel({ projectId }) {
  const sources = useResource(() => research.sources(projectId, { limit: 100 }), [projectId]);
  const bundles = useResource(() => research.bundles(projectId), [projectId]);
  const cache = useResource(() => research.cache(projectId), [projectId]);
  const [message, setMessage] = useState(null);
  const items = sources.data?.items ?? [];

  async function toggle(s) {
    setMessage(null);
    try { await research.saveSource(s.id, !s.saved); sources.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }
  async function clearCache() {
    if (!window.confirm('Clear the saved copies of web pages for this project? Results you already have are not affected.')) return;
    try { await research.clearCache(projectId); setMessage({ kind: 'info', text: 'Saved web pages cleared.' }); cache.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <section className="stack" aria-label="Sources and evidence">
      <div className="card stack">
        <h2 className="card__title">Sources used</h2>
        {sources.loading && !sources.data ? <PageLoading label="Loading sources…" /> : null}
        {sources.error ? <ErrorState title="Could not load the sources" message={describeError(sources.error)} onRetry={() => sources.reload()} /> : null}
        {!sources.loading && !sources.error && items.length === 0 ? <Empty title="No sources yet"><p>Sources appear here after research has run.</p></Empty> : null}
        <ul className="list">
          {items.map((s) => (
            <li key={s.id} className="row" data-source-id={s.id}>
              <div className="row__main">
                <span className="row__title">{s.url ? <ExternalLink url={s.url}>{sourceTitle(s)}</ExternalLink> : sourceTitle(s)}</span>
                <span className="row__meta">{describeSourceLine(s)}{s.retrieved_at ? ` · read ${s.retrieved_at}` : ''}{s.saved ? ' · saved' : ''}</span>
                {s.bundle_id ? <span className="row__meta"><Link to={`/research/bundle/${encodeURIComponent(s.bundle_id)}`}>Open the result that used it</Link></span> : null}
              </div>
              <OriginBadge origin={s.origin} />
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => toggle(s)}>{s.saved ? 'Unsave' : 'Save'}</button>
            </li>
          ))}
        </ul>
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
      </div>

      <div className="card stack">
        <h2 className="card__title">Evidence bundles</h2>
        <p className="row__meta">A bundle is the sealed result of one run. Other parts of PredictAI (the assistant, analytics, predictions, reports) can reuse its labelled conclusions and citations.</p>
        {bundles.error ? <ErrorState title="Could not load the bundles" message={describeError(bundles.error)} onRetry={() => bundles.reload()} /> : null}
        {!bundles.loading && (bundles.data?.items ?? []).length === 0 ? <p className="muted">No bundles yet.</p> : null}
        <ul className="list">
          {(bundles.data?.items ?? []).map((b) => (
            <li key={b.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/research/bundle/${encodeURIComponent(b.id)}`}>{b.label || b.question}</Link></span><span className="row__meta">Run {b.number} · {b.evidence_count} passages · {b.source_count} sources{b.saved ? ' · saved' : ''}</span></div></li>
          ))}
        </ul>
      </div>

      <div className="card stack">
        <h2 className="card__title">Saved web pages</h2>
        <p className="row__meta">{cache.data ? `${cache.data.entries ?? 0} saved page${cache.data.entries === 1 ? '' : 's'} (${Math.round((cache.data.bytes ?? 0) / 1024)} KB). They are kept for a limited time so repeat research is faster and kinder to the websites.` : 'Reading…'}</p>
        <div className="btn-row"><button type="button" className="btn btn--ghost btn--sm" onClick={clearCache} disabled={!cache.data?.entries}>Clear saved pages</button></div>
      </div>
    </section>
  );
}
