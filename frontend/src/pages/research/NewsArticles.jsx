import { useState } from 'react';
import { world } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { ARTICLE_STATUS_LABEL, TONE_LABEL, TONE_NOTE, labelOf } from '../../features/research/labels.js';
import { EMPTY_FILTERS, PAGE_SIZE, articleParams, hasFilters } from '../../features/research/news.js';
import { Badge, Empty, ExternalLink, Notice, StatementBadge } from './shared.jsx';

function ArticleDetail({ id }) {
  const res = useResource(() => world.article(id), [id]);
  if (res.loading && !res.data) return <p className="muted">Loading…</p>;
  if (res.error) return <Notice kind="error">{describeError(res.error)}</Notice>;
  const a = res.data;
  const d = a.derived ?? {};
  return (
    <div className="stack article__detail">
      <p>{a.summary || <span className="muted">The feed gave no summary.</span>}</p>
      <dl className="kv">
        <div className="kv__pair"><dt>Published</dt><dd>{a.published_at ?? 'not given'}</dd></div>
        <div className="kv__pair"><dt>Read on</dt><dd>{a.retrieved_at}</dd></div>
        <div className="kv__pair"><dt>Keeps until</dt><dd>{a.expires_at ?? '—'}</dd></div>
        {d.topics?.topics?.length ? <div className="kv__pair"><dt>Topic (matched words)</dt><dd>{d.topics.topics.map((t) => `${t.label}: ${t.matched.join(', ')}`).join('; ')}</dd></div> : null}
        {d.tone ? <div className="kv__pair"><dt>Tone words</dt><dd>{[...(d.tone.basis?.negative ?? []), ...(d.tone.basis?.positive ?? [])].join(', ') || 'none found'}</dd></div> : null}
        {d.entities?.entities?.length ? <div className="kv__pair"><dt>Names found</dt><dd>{d.entities.entities.map((e) => (typeof e === 'string' ? e : e.text)).join(', ')}</dd></div> : null}
        {a.quarantine_reason ? <div className="kv__pair"><dt>Why it was held back</dt><dd>{a.quarantine_reason}</dd></div> : null}
      </dl>
      <p className="row__meta">{a.note}</p>
    </div>
  );
}

/** Stored articles with filters. Only what the feed published is shown: headline, link, date and the feed's own summary. */
export function NewsArticles({ projectId, onChanged }) {
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState(null);
  const [message, setMessage] = useState(null);
  const topics = useResource(() => world.topics(projectId), [projectId]);
  const sources = useResource(() => world.sources(projectId), [projectId]);
  const key = JSON.stringify([filters, page]);
  const list = useResource(() => world.articles(projectId, articleParams(filters, page)), [projectId, key]); // eslint-disable-line react-hooks/exhaustive-deps
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));

  async function remove(a) {
    if (!window.confirm(`Delete this stored article?\n\n${a.title}`)) return;
    try { await world.deleteArticle(a.id); setMessage({ kind: 'info', text: 'Article deleted.' }); list.reload({ quiet: true }); onChanged?.(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <section className="stack" aria-label="Articles">
      <form className="card form-grid" role="search" aria-label="Filter articles" onSubmit={(e) => { e.preventDefault(); setFilters(draft); setPage(0); }}>
        <Field label="Words" value={draft.q} onChange={(e) => set({ q: e.target.value })} maxLength={200} />
        <Field label="Topic" as="select" value={draft.topic} onChange={(e) => set({ topic: e.target.value })}><option value="">Any</option>{(topics.data?.items ?? []).map((t) => <option key={t.topic} value={t.topic}>{t.label}</option>)}</Field>
        <Field label="Feed" as="select" value={draft.source_id} onChange={(e) => set({ source_id: e.target.value })}><option value="">Any</option>{(sources.data?.items ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Field>
        <Field label="Wording" as="select" value={draft.tone} onChange={(e) => set({ tone: e.target.value })} hint={TONE_NOTE}><option value="">Any</option>{Object.entries(TONE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Field>
        <Field label="Name mentioned" value={draft.entity} onChange={(e) => set({ entity: e.target.value })} maxLength={200} />
        <Field label="From" type="date" value={draft.since} onChange={(e) => set({ since: e.target.value })} />
        <Field label="To" type="date" value={draft.until} onChange={(e) => set({ until: e.target.value })} />
        <Field label="Status" as="select" value={draft.status} onChange={(e) => set({ status: e.target.value })}><option value="">Stored</option>{['quarantined', 'expired'].map((k) => <option key={k} value={k}>{ARTICLE_STATUS_LABEL[k]}</option>)}</Field>
        <div className="btn-row"><button type="submit" className="btn">Apply filters</button>{hasFilters(draft) || hasFilters(filters) ? <button type="button" className="btn btn--ghost" onClick={() => { setDraft(EMPTY_FILTERS); setFilters(EMPTY_FILTERS); setPage(0); }}>Clear</button> : null}</div>
      </form>

      <div className="card stack">
        <h2 className="card__title">Articles{list.data ? ` (${total})` : ''}</h2>
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        {list.loading && !list.data ? <PageLoading label="Loading articles…" /> : null}
        {list.error ? <ErrorState title="Could not load articles" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
        {!list.loading && !list.error && items.length === 0 ? <Empty title="No articles"><p>{hasFilters(filters) ? 'No stored article matches those filters.' : 'Add a feed and read it to see articles here.'}</p></Empty> : null}
        <ul className="list">
          {items.map((a) => (
            <li key={a.id} className="row article" data-article-id={a.id}>
              <div className="row__main">
                <span className="row__title"><ExternalLink url={a.url}>{a.title}</ExternalLink></span>
                <span className="row__meta">{a.publisher ?? a.domain} · {a.published_at ?? 'date not given'} · {a.topics?.[0]?.label ?? 'General'}</span>
                <span className="row__meta"><Badge tone="muted" title="Wording only; not a measure of truth or importance">{labelOf(TONE_LABEL, a.tone)}</Badge> <StatementBadge kind={a.statement_kind} />{a.status !== 'active' ? <> <Badge tone="warn">{labelOf(ARTICLE_STATUS_LABEL, a.status)}</Badge></> : null}{a.duplicate_of ? <> <Badge tone="muted">Duplicate</Badge></> : null}</span>
                {open === a.id ? <ArticleDetail id={a.id} /> : null}
              </div>
              <div className="btn-row">
                <button type="button" className="btn btn--ghost btn--sm" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>{open === a.id ? 'Hide' : 'Details'}</button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove(a)} aria-label={`Delete ${a.title}`}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
        {total > PAGE_SIZE ? (
          <div className="btn-row">
            <button type="button" className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <span className="row__meta">{page * PAGE_SIZE + 1}–{Math.min(total, (page + 1) * PAGE_SIZE)} of {total}</span>
            <button type="button" className="btn btn--ghost btn--sm" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
