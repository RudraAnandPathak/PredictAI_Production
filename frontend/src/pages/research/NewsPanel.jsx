import { useState } from 'react';
import { world } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { EVENT_STATUS_LABEL } from '../../features/research/labels.js';
import { NewsArticles } from './NewsArticles.jsx';
import { NewsFeeds } from './NewsFeeds.jsx';
import { NewsTrends } from './NewsTrends.jsx';
import { EventStatusBadge, Empty, Notice, Stat } from './shared.jsx';

const PAGE = 20;

function EventsList({ projectId, onChanged }) {
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const events = useResource(() => world.events(projectId, { status, limit: PAGE, offset: page * PAGE }), [projectId, status, page]);
  const items = events.data?.items ?? [];
  const total = events.data?.total ?? 0;

  async function rebuild() {
    setBusy(true);
    setMessage(null);
    try { await world.rebuild(projectId); setMessage({ kind: 'info', text: 'Events were regrouped from the stored articles.' }); events.reload({ quiet: true }); onChanged?.(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }

  return (
    <section className="stack" aria-label="Events">
      <div className="card stack">
        <div className="card__row">
          <h2 className="card__title">Events</h2>
          <div className="filterbar">
            <label className="sr-only" htmlFor="event-status">Show events that are</label>
            <select id="event-status" className="field__input" value={status} onChange={(e) => { setStatus(e.target.value); setPage(0); }}>
              <option value="">All events</option>
              {Object.entries(EVENT_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <button type="button" className="btn btn--ghost btn--sm" onClick={rebuild} disabled={busy}>{busy ? 'Regrouping…' : 'Regroup articles into events'}</button>
          </div>
        </div>
        <p className="row__meta">An event is a group of articles that appear to describe the same thing. “Confirmed” means several outlets report matching facts and none disagrees; it is agreement between outlets, not verification by PredictAI.</p>
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        {events.loading && !events.data ? <PageLoading label="Loading events…" /> : null}
        {events.error ? <ErrorState title="Could not load events" message={describeError(events.error)} onRetry={() => events.reload()} /> : null}
        {!events.loading && !events.error && items.length === 0 ? <Empty title="No events"><p>{status ? 'No events have that status.' : 'Add a news feed and read it; articles about the same thing are grouped here.'}</p></Empty> : null}
        <ul className="list">
          {items.map((e) => (
            <li key={e.id} className="row" data-event-id={e.id}>
              <div className="row__main">
                <span className="row__title"><Link to={`/research/news/event/${encodeURIComponent(e.id)}`}>{e.label}</Link></span>
                <span className="row__meta">{e.source_count} outlet{e.source_count === 1 ? '' : 's'} · {e.article_count} article{e.article_count === 1 ? '' : 's'} · {e.first_seen ? `first seen ${e.first_seen}` : 'date unknown'}</span>
              </div>
              <EventStatusBadge status={e.status} />
            </li>
          ))}
        </ul>
        {total > PAGE ? (
          <div className="btn-row">
            <button type="button" className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="row__meta">{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
            <button type="button" className="btn btn--ghost btn--sm" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** News dashboard for one project: what is switched on, events, articles, trends and the feeds themselves. Feed content is outside material and is labelled so everywhere. */
export function NewsPanel({ projectId }) {
  const [tab, setTab] = useState('events');
  const overview = useResource(() => world.overview(projectId), [projectId]);
  const o = overview.data;
  const refresh = () => overview.reload({ quiet: true });

  const tabs = [
    { id: 'events', label: 'Events', panel: <EventsList key="e" projectId={projectId} onChanged={refresh} /> },
    { id: 'articles', label: 'Articles', panel: <NewsArticles key="a" projectId={projectId} onChanged={refresh} /> },
    { id: 'trends', label: 'Trends', panel: <NewsTrends key="t" projectId={projectId} /> },
    { id: 'feeds', label: 'Feeds', panel: <NewsFeeds key="f" projectId={projectId} newsAllowed={Boolean(o?.news_allowed)} whyNot={o?.why_not ?? []} onChanged={refresh} /> },
  ];
  return (
    <section className="stack" aria-label="News and events">
      {overview.loading && !o ? <PageLoading label="Loading the news dashboard…" /> : null}
      {overview.error ? <ErrorState title="Could not load the news dashboard" message={describeError(overview.error)} onRetry={() => overview.reload()} /> : null}
      {o ? (
        <>
          <div className="stat-grid" aria-label="News summary">
            <Stat label="Feeds" value={o.sources} />
            <Stat label="Articles" value={o.articles} />
            <Stat label="Events" value={Object.values(o.events).reduce((a, b) => a + b, 0)} hint={Object.entries(o.events).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ') || undefined} />
            <Stat label="Held back" value={o.quarantined} hint="Articles whose text looked like instructions" />
          </div>
          {!o.news_allowed ? <Notice kind="info" title="Reading news is off">{o.why_not.length ? <ul>{o.why_not.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}<p>Articles already stored can still be read. <Link to="/research/settings">Change your settings</Link> or the <Link to={`/research/p/${encodeURIComponent(projectId)}?tab=privacy`}>project’s privacy</Link>.</p></Notice> : null}
          {o.freshness_warnings.length ? <Notice kind="warn" title="Some feeds are out of date"><ul>{o.freshness_warnings.map((w, i) => <li key={i}>{typeof w === 'string' ? w : w.message ?? JSON.stringify(w)}</li>)}</ul></Notice> : null}
        </>
      ) : null}
      <Tabs label="News" tabs={tabs} active={tab} onChange={setTab} />
    </section>
  );
}
