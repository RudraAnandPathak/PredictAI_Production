import { useState } from 'react';
import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { SESSION_STATUS_LABEL, SESSION_STATUS_TONE, labelOf } from '../../features/research/labels.js';
import { Badge, Empty, Notice } from './shared.jsx';

const PAGE = 20;

/** Saved research for this project, newest first: open it, read its plan, or delete it. */
export function HistoryPanel({ projectId }) {
  const [page, setPage] = useState(0);
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState(null);
  const list = useResource(() => research.sessions(projectId, { limit: PAGE, offset: page * PAGE }), [projectId, page]);
  const found = useResource(() => (query ? research.search(projectId, query) : Promise.resolve(null)), [projectId, query]);
  const tooShort = text.trim().length > 0 && text.trim().length < 2;
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;

  async function remove(session) {
    if (!window.confirm(`Delete “${session.title}” and everything found for it? Notes and saved results for this research are deleted too.`)) return;
    setMessage(null);
    try { await research.deleteSession(session.id); setMessage({ kind: 'info', text: 'Research deleted.' }); list.reload(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <section className="stack" aria-label="Research history">
      <div className="card stack">
        <div className="card__row">
          <h2 className="card__title">Your research</h2>
          <form className="filterbar" role="search" onSubmit={(e) => { e.preventDefault(); if (!tooShort) { setQuery(text.trim()); } }}>
            <label className="sr-only" htmlFor="history-q">Search your earlier research</label>
            <input id="history-q" className="field__input" type="search" aria-label="Search earlier research" placeholder="Search earlier research" value={text} onChange={(e) => { setText(e.target.value); if (!e.target.value.trim()) setQuery(''); }} />
            <button type="submit" className="btn btn--ghost btn--sm" disabled={tooShort || !text.trim()}>Search</button>
          </form>
        </div>
        {tooShort ? <p className="field__hint">Type at least two characters to search.</p> : null}
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        {query ? (
          <div className="stack" role="region" aria-label="Search results">
            <h3 className="sub">Results for “{query}”</h3>
            {found.error ? <ErrorState title="Search failed" message={describeError(found.error)} onRetry={() => found.reload()} /> : null}
            {found.data && found.data.items.length === 0 ? <p className="muted">No earlier research matches. Search looks at titles, questions and saved conclusions.</p> : null}
            <ul className="list">
              {(found.data?.items ?? []).map((h) => (
                <li key={h.id} className="row">
                  <div className="row__main">
                    <span className="row__title"><Link to={`/research/session/${encodeURIComponent(h.id)}`}>{h.title || h.question}</Link></span>
                    <span className="row__meta">{h.snippet}</span>
                  </div>
                  <Badge tone={SESSION_STATUS_TONE[h.status] ?? 'muted'}>{labelOf(SESSION_STATUS_LABEL, h.status)}</Badge>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {list.loading && !list.data ? <PageLoading label="Loading your research…" /> : null}
        {list.error ? <ErrorState title="Could not load your research" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
        {!list.loading && !list.error && items.length === 0 ? (
          <Empty title="No research yet">
            <p>Start with a question. PredictAI shows its plan before it does anything, and nothing leaves this server unless you allow it.</p>
            <Link to={`/research/p/${encodeURIComponent(projectId)}/new`} className="btn">New research</Link>
          </Empty>
        ) : null}
        <ul className="list">
          {items.map((s) => (
            <li key={s.id} className="row" data-session-id={s.id}>
              <div className="row__main">
                <span className="row__title"><Link to={`/research/session/${encodeURIComponent(s.id)}`}>{s.title || s.question}</Link></span>
                <span className="row__meta">{s.level_label} · {s.run_count} run{s.run_count === 1 ? '' : 's'} · {s.last_run_at ? `last run ${formatWhen(s.last_run_at)}` : `created ${formatWhen(s.created_at)}`}{s.saved ? ' · saved' : ''}{s.options?.web ? ' · web allowed' : ''}</span>
              </div>
              <Badge tone={SESSION_STATUS_TONE[s.status] ?? 'muted'}>{labelOf(SESSION_STATUS_LABEL, s.status)}</Badge>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove(s)} aria-label={`Delete ${s.title || s.question}`}>Delete</button>
            </li>
          ))}
        </ul>
        {total > PAGE ? (
          <div className="btn-row" aria-label="Pages">
            <button type="button" className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
            <span className="row__meta">{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
            <button type="button" className="btn btn--ghost btn--sm" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Older</button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
