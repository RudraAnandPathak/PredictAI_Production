import { useEffect, useState } from 'react';
import { analytics, workspace, world } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { AGGREGATES, ASSOCIATION_BANNER, MAX_LAG_LIMITS, MEASURES, buildInfluenceBody } from '../../features/research/influence.js';
import { JobProgress } from './JobProgress.jsx';
import { Empty, Notice } from './shared.jsx';
import { useFollowJob } from './useFollowJob.js';
import { pickableAssets } from '../../features/research/plan.js';

const BLANK = { assetId: '', dateColumn: '', valueColumn: '', aggregate: 'mean', measure: 'count', topic: '', entity: '', maxLag: 7, label: '' };

/** Test whether news coverage and one number in your data move together. The result is always shown as association over time, never as cause and effect. */
export function InfluencePanel({ projectId }) {
  const list = useResource(() => world.influences(projectId), [projectId]);
  const datasets = useResource(() => workspace.assets.list(projectId, { kind: 'dataset', limit: 100 }), [projectId]);
  const topics = useResource(() => world.topics(projectId), [projectId]);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const follower = useFollowJob({ onFinished: () => list.reload({ quiet: true }) });
  const schema = useResource(() => (form.assetId ? analytics.schema(form.assetId) : Promise.resolve(null)), [form.assetId]);
  const columns = schema.data?.columns ?? [];
  const dateCols = columns.filter((c) => c.role === 'datetime');
  const numberCols = columns.filter((c) => c.role === 'numeric');
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const ready = pickableAssets(datasets.data?.items, 'dataset');
  useEffect(() => { setForm((f) => ({ ...f, dateColumn: '', valueColumn: '' })); }, [form.assetId]);
  const running = Boolean(follower.job && !['completed', 'failed', 'cancelled'].includes(follower.job.status));

  async function submit(event) {
    event.preventDefault();
    const built = buildInfluenceBody(form);
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({});
    setBusy(true);
    setMessage(null);
    try { const job = await world.startInfluence(projectId, built.body); follower.follow(job.id, job); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }
  async function remove(a) {
    if (!window.confirm(`Delete the analysis “${a.label}”?`)) return;
    try { await world.deleteInfluence(a.id); list.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <section className="stack" aria-label="Influence analysis">
      <Notice kind="info" title="Association, not cause">{ASSOCIATION_BANNER}</Notice>

      <form className="card form-grid" onSubmit={submit} noValidate aria-label="New influence analysis">
        <h2 className="card__title">New analysis</h2>
        <Field label="Dataset" as="select" value={form.assetId} onChange={(e) => set({ assetId: e.target.value })} error={errors.assetId}><option value="">Choose…</option>{ready.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Field>
        <Field label="Date column" as="select" value={form.dateColumn} onChange={(e) => set({ dateColumn: e.target.value })} error={errors.dateColumn} disabled={!form.assetId}><option value="">Choose…</option>{dateCols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field>
        <Field label="Number to compare" as="select" value={form.valueColumn} onChange={(e) => set({ valueColumn: e.target.value })} error={errors.valueColumn} disabled={!form.assetId}><option value="">Choose…</option>{numberCols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field>
        <Field label="If several rows share a day, use the" as="select" value={form.aggregate} onChange={(e) => set({ aggregate: e.target.value })}>{AGGREGATES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Field>
        <Field label="Coverage measure" as="select" value={form.measure} onChange={(e) => set({ measure: e.target.value })}>{MEASURES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Field>
        <Field label="Only articles about (optional)" as="select" value={form.topic} onChange={(e) => set({ topic: e.target.value })}><option value="">All topics</option>{(topics.data?.items ?? []).map((t) => <option key={t.topic} value={t.topic}>{t.label}</option>)}</Field>
        <Field label="Only articles mentioning (optional)" value={form.entity} onChange={(e) => set({ entity: e.target.value })} maxLength={200} />
        <Field label="Longest gap to test (days)" inputMode="numeric" value={form.maxLag} onChange={(e) => set({ maxLag: e.target.value })} error={errors.maxLag} hint={`0 to ${MAX_LAG_LIMITS.max}.`} />
        <Field label="Name (optional)" value={form.label} onChange={(e) => set({ label: e.target.value })} maxLength={300} />
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn" disabled={busy || running}>{busy ? 'Starting…' : 'Run the analysis'}</button></div>
        {datasets.loading || ready.length > 0 ? null : <p className="muted">This project has no dataset yet. Upload a CSV or Excel file under Projects first.</p>}
        {form.assetId && schema.data && dateCols.length === 0 ? <p className="field__hint">This dataset has no date column, so it cannot be compared with news over time.</p> : null}
        {schema.error ? <Notice kind="error">{describeError(schema.error)}</Notice> : null}
      </form>

      <JobProgress job={follower.job} error={follower.error} title="Influence analysis">
        {follower.job?.status === 'completed' && follower.job.result_id ? <div className="btn-row"><Link className="btn" to={`/research/influence/${encodeURIComponent(follower.job.result_id)}`}>Open the result</Link></div> : null}
      </JobProgress>

      <div className="card stack">
        <h2 className="card__title">Saved analyses</h2>
        {list.loading && !list.data ? <PageLoading label="Loading analyses…" /> : null}
        {list.error ? <ErrorState title="Could not load the analyses" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
        {!list.loading && !list.error && (list.data?.items ?? []).length === 0 ? <Empty title="No analyses yet"><p>Run one above to see whether coverage and your data moved together.</p></Empty> : null}
        <ul className="list">
          {(list.data?.items ?? []).map((a) => (
            <li key={a.id} className="row" data-analysis-id={a.id}>
              <div className="row__main"><span className="row__title"><Link to={`/research/influence/${encodeURIComponent(a.id)}`}>{a.label}</Link></span><span className="row__meta">{formatWhen(a.created_at)} · up to {a.config.max_lag} days · {a.config.topic || a.config.entity || 'all coverage'}</span></div>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove(a)} aria-label={`Delete ${a.label}`}>Delete</button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
