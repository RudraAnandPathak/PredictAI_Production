import { useState } from 'react';
import { world } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { ageLabel, buildSourceBody } from '../../features/research/news.js';
import { ingestRows } from '../../features/research/jobs.js';
import { JobProgress } from './JobProgress.jsx';
import { Empty, FreshnessBadge, Notice } from './shared.jsx';
import { useFollowJob } from './useFollowJob.js';

const BLANK = { name: '', feedUrl: '', publisher: '', retentionDays: '', termsAcknowledged: false };

/** The feeds this project follows: add one (with the terms you accepted), read them now, pause or remove them. Only the headline and summary a feed publishes are stored. */
export function NewsFeeds({ projectId, newsAllowed, whyNot, onChanged }) {
  const fresh = useResource(() => world.freshness(projectId), [projectId]);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const follower = useFollowJob({ onFinished: () => { fresh.reload({ quiet: true }); onChanged?.(); } });
  const sources = fresh.data?.sources ?? [];
  const reading = Boolean(follower.job && !['completed', 'failed', 'cancelled'].includes(follower.job.status));

  async function add(event) {
    event.preventDefault();
    const built = buildSourceBody(form);
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({});
    setBusy(true);
    setMessage(null);
    try { await world.addSource(projectId, built.body); setForm(BLANK); setMessage({ kind: 'success', text: 'Feed added. Read it now to fetch its articles.' }); fresh.reload({ quiet: true }); onChanged?.(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }
  async function change(action, ok) {
    setMessage(null);
    try { await action(); if (ok) setMessage({ kind: 'info', text: ok }); fresh.reload({ quiet: true }); onChanged?.(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }
  async function readNow(ids) {
    setMessage(null);
    try { const job = await world.ingest(projectId, ids); follower.follow(job.id, job); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <section className="stack" aria-label="Feeds">
      <div className="card stack">
        <div className="card__row">
          <h2 className="card__title">Feeds</h2>
          <button type="button" className="btn btn--sm" disabled={!newsAllowed || reading || sources.length === 0} onClick={() => readNow(undefined)}>{reading ? 'Reading…' : 'Read all feeds now'}</button>
        </div>
        {!newsAllowed ? <Notice kind="info" title="Reading is off">{whyNot.length ? <ul>{whyNot.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}</Notice> : null}
        {fresh.loading && !fresh.data ? <PageLoading label="Loading feeds…" /> : null}
        {fresh.error ? <ErrorState title="Could not load the feeds" message={describeError(fresh.error)} onRetry={() => fresh.reload()} /> : null}
        {!fresh.loading && !fresh.error && sources.length === 0 ? <Empty title="No feeds yet"><p>Add an RSS or Atom feed address below. PredictAI stores only each article’s headline, link, date and the summary the feed itself publishes.</p></Empty> : null}
        <ul className="list">
          {sources.map((s) => (
            <li key={s.id} className="row" data-source-id={s.id}>
              <div className="row__main">
                <span className="row__title">{s.name}{s.status === 'paused' ? ' (paused)' : ''}</span>
                <span className="row__meta">{s.publisher ? `${s.publisher} · ` : ''}{s.article_count} article{s.article_count === 1 ? '' : 's'} · {s.last_fetched_at ? `read ${ageLabel(s.hours_since_fetch)}` : 'never read'}{s.last_status && s.last_status !== 'ok' ? ` · last read failed (${s.last_error_code ?? s.last_status})` : ''}{s.quarantined ? ` · ${s.quarantined} held back` : ''} · kept {s.policy.retention_days} days</span>
                <span className="row__meta">{s.feed_url}</span>
              </div>
              <FreshnessBadge freshness={s.freshness} />
              <div className="btn-row">
                <button type="button" className="btn btn--ghost btn--sm" disabled={!newsAllowed || reading} onClick={() => readNow([s.id])}>Read</button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => change(() => world.updateSource(s.id, { status: s.status === 'paused' ? 'active' : 'paused' }), s.status === 'paused' ? 'Feed resumed.' : 'Feed paused.')}>{s.status === 'paused' ? 'Resume' : 'Pause'}</button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => { if (window.confirm(`Stop following “${s.name}” and delete its ${s.article_count} stored article(s)?`)) change(() => world.deleteSource(s.id), 'Feed and its articles deleted.'); }}>Remove</button>
              </div>
            </li>
          ))}
        </ul>
        {fresh.data ? <p className="row__meta">{fresh.data.note} Fresh means read within {fresh.data.thresholds_hours.fresh} hours; stale means older than {fresh.data.thresholds_hours.aging}.</p> : null}
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
      </div>

      <JobProgress job={follower.job} error={follower.error} title="Reading feeds">
        {follower.job && follower.job.status !== 'running' && follower.job.status !== 'queued' ? (
          <ul className="list" aria-label="Result for each feed">{ingestRows(follower.job).map((r) => <li key={r.key} className="row"><div className="row__main"><span className="row__title">{r.name}</span><span className="row__meta">{r.text}{r.removed ? ` · ${r.removed} instruction-like sentence(s) removed` : ''}</span></div></li>)}</ul>
        ) : null}
      </JobProgress>

      <form className="card stack" onSubmit={add} noValidate aria-label="Add a feed">
        <h2 className="card__title">Add a feed</h2>
        <Field label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} error={errors.name} maxLength={200} />
        <Field label="Feed address" type="url" inputMode="url" value={form.feedUrl} onChange={(e) => setForm({ ...form, feedUrl: e.target.value })} error={errors.feedUrl} placeholder="https://example.org/feed.xml" hint="An RSS or Atom address. Addresses that point at this server or a private network are refused." />
        <Field label="Publisher (optional)" value={form.publisher} onChange={(e) => setForm({ ...form, publisher: e.target.value })} maxLength={200} />
        <Field label="Keep articles for (days, optional)" inputMode="numeric" value={form.retentionDays} onChange={(e) => setForm({ ...form, retentionDays: e.target.value })} error={errors.retentionDays} hint="Articles are deleted after this long (headline kept as an expired stub). Default 30." />
        <label className="check-field"><input type="checkbox" checked={form.termsAcknowledged} onChange={(e) => setForm({ ...form, termsAcknowledged: e.target.checked })} aria-invalid={errors.termsAcknowledged ? 'true' : undefined} /><span><span className="check-field__label">I may read this feed under its publisher’s terms</span><span className="field__hint">PredictAI reads the feed politely (not more often than every 15 minutes) and does not download article pages.</span>{errors.termsAcknowledged ? <span className="field__error">{errors.termsAcknowledged}</span> : null}</span></label>
        <div className="btn-row"><button type="submit" className="btn" disabled={busy}>{busy ? 'Adding…' : 'Add feed'}</button></div>
      </form>
    </section>
  );
}
