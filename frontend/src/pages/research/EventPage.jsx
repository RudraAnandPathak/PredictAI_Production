import { world } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { EVENT_STATUS_HINT, STATEMENT_LABEL, TONE_LABEL, TONE_NOTE, labelOf } from '../../features/research/labels.js';
import { comparisonRows, eventBasis, timelineRows } from '../../features/research/news.js';
import { Badge, EventStatusBadge, ExternalLink, Notice, StatementBadge } from './shared.jsx';

/** One event: how it is known, the timeline of reports, outlets side by side, and related events. Everything here was reported by outside outlets; none of it is verified by PredictAI. */
export function EventPage() {
  const { eventId } = useParams('/research/news/event/:eventId');
  const res = useResource(() => world.event(eventId), [eventId]);
  if (res.loading && !res.data) return <PageLoading label="Opening the event…" />;
  if (res.error) {
    const missing = res.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Event not found' : 'Could not open the event'} message={missing ? 'This event does not exist, it was regrouped, or it is not yours.' : describeError(res.error)} onRetry={missing ? null : () => res.reload()} />
        <p><Link to="/research" className="btn btn--ghost">Back to research</Link></p>
      </article>
    );
  }
  const { event, articles, comparison, related, explanation } = res.data;
  const timeline = timelineRows(res.data.timeline);
  const outlets = comparisonRows(comparison);
  const back = `/research/p/${encodeURIComponent(event.project_id)}?tab=news`;

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to={back}>News and events</Link></p>
        <h1 className="page-title">{event.label}</h1>
        <p className="row__meta"><EventStatusBadge status={event.status} /> {event.source_count} outlet{event.source_count === 1 ? '' : 's'} · {event.article_count} article{event.article_count === 1 ? '' : 's'} · first seen {event.first_seen ?? 'unknown'}{event.undated ? ` · ${event.undated} without a date` : ''}</p>
      </header>

      <Notice kind="info" title="How this is known">
        <p>{eventBasis(event)}</p>
        <p className="row__meta">{EVENT_STATUS_HINT[event.status]}</p>
        <p className="row__meta">{event.note}</p>
      </Notice>

      {event.disagreements?.length ? (
        <section className="card stack" aria-label="Where outlets disagree">
          <h2 className="card__title">Where outlets disagree</h2>
          <ul>{event.disagreements.map((d, i) => <li key={i}>{typeof d === 'string' ? d : d.note ?? JSON.stringify(d)}</li>)}</ul>
        </section>
      ) : null}

      <section className="card stack" aria-label="Timeline">
        <h2 className="card__title">Timeline of reports</h2>
        <ol className="timeline">
          {timeline.map((t) => (
            <li key={t.key}>
              <span className="row__meta">{t.time ?? 'date not given'}{t.first ? ' · first report' : ''}</span>
              <span className="row__title"><ExternalLink url={t.url}>{t.headline}</ExternalLink></span>
              <span className="row__meta">{t.publisher} · <StatementBadge kind={t.kind} /></span>
            </li>
          ))}
        </ol>
      </section>

      <section className="card stack" aria-label="Outlets side by side">
        <h2 className="card__title">Outlets side by side</h2>
        <p className="row__meta">{comparison.note} {TONE_NOTE}</p>
        <div className="table-wrap"><table className="table">
          <thead><tr><th scope="col">Outlet</th><th scope="col">Headline and opening</th><th scope="col">Numbers it gives</th><th scope="col">Wording</th><th scope="col">Reported</th></tr></thead>
          <tbody>
            {outlets.map((o) => (
              <tr key={o.key}>
                <th scope="row">{o.outlet}</th>
                <td><ExternalLink url={o.url}>{o.headline}</ExternalLink><br /><span className="row__meta">{o.lead}</span></td>
                <td>{o.numbers.length ? o.numbers.join(', ') : '—'}{o.extra.length ? <><br /><span className="row__meta">Only here: {o.extra.join(', ')}</span></> : null}</td>
                <td><Badge tone="muted">{labelOf(TONE_LABEL, o.tone)}</Badge><br /><span className="row__meta">{labelOf(STATEMENT_LABEL, o.kind)}</span></td>
                <td>{o.hoursAfterFirst == null ? '—' : o.hoursAfterFirst === 0 ? 'First' : `${o.hoursAfterFirst} h later`}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
        {comparison.tone_differs ? <p className="row__meta">The outlets word this differently. That is about style and emphasis, not about which is right.</p> : null}
      </section>

      {related?.length ? (
        <section className="card stack" aria-label="Related events">
          <h2 className="card__title">Related events</h2>
          <ul className="list">{related.map((r) => <li key={r.id ?? r.event?.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/research/news/event/${encodeURIComponent(r.id ?? r.event?.id)}`}>{r.label ?? r.event?.label}</Link></span><span className="row__meta">{r.reason ?? r.why ?? 'Shares topics or names'}</span></div></li>)}</ul>
        </section>
      ) : null}

      <section className="card stack" aria-label="Articles in this event">
        <h2 className="card__title">Articles ({articles.length})</h2>
        <ul className="list">{articles.map((a) => <li key={a.id} className="row"><div className="row__main"><span className="row__title"><ExternalLink url={a.url}>{a.title}</ExternalLink></span><span className="row__meta">{a.publisher ?? a.domain} · {a.published_at ?? 'date not given'}</span></div></li>)}</ul>
      </section>

      <Explanation value={explanation} />
    </article>
  );
}
