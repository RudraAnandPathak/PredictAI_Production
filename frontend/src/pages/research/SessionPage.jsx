import { useEffect, useState } from 'react';
import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { isFinalJob } from '../../features/research/jobs.js';
import { BUNDLE_STATUS_LABEL, BUNDLE_STATUS_TONE, QUALITY_VERDICT_LABEL, QUALITY_VERDICT_TONE, SESSION_STATUS_LABEL, SESSION_STATUS_TONE, labelOf } from '../../features/research/labels.js';
import { clarificationOf, needsApproval, outboundSummary, skippedProviders, understandingLine, planLines, purposeLabel, sentByProvider } from '../../features/research/plan.js';
import { followUpLine } from '../../features/research/report.js';
import { JobProgress } from './JobProgress.jsx';
import { NotesPanel } from './NotesPanel.jsx';
import { Badge, Empty, Notice } from './shared.jsx';
import { useFollowJob } from './useFollowJob.js';

function PlanCard({ session }) {
  const plan = session.plan;
  const out = outboundSummary(session);
  return (
    <section className="card stack" aria-label="Plan">
      <h2 className="card__title">The plan</h2>
      <ul>{planLines(plan).map((l, i) => <li key={i}>{l}</li>)}</ul>
      {understandingLine(session) ? <p className="row__meta">{understandingLine(session)}</p> : null}
      {skippedProviders(session).length ? <Notice kind="info" title="Not searched"><ul>{skippedProviders(session).map((x) => <li key={x.provider}><strong>{x.provider}</strong>: {x.reason}</li>)}</ul></Notice> : null}
      <ol className="steps" aria-label="Parts to look into">
        {plan.subtopics.map((s) => <li key={s.id}><strong>{s.title}</strong> <span className="row__meta">{s.purpose}</span></li>)}
      </ol>
      {out.leaves ? (
        <Notice kind="warn" title="This would leave this server">
          <p>Each provider would be sent only the phrases listed under its name, worded the way that provider searches:</p>
          {sentByProvider(out).map((g) => (
            <div key={g.provider} data-provider={g.provider}>
              <p><strong>{g.provider}</strong></p>
              <ul>{g.queries.map((q, i) => <li key={i}><code>{q.query}</code></li>)}</ul>
            </div>
          ))}
          {out.items.some((i) => i.removed.length) ? <p className="row__meta">Left out of the phrases because it looked private: {[...new Set(out.items.flatMap((i) => i.removed))].join(', ')}.</p> : null}
          <p className="row__meta">{out.note}</p>
        </Notice>
      ) : (
        <Notice kind="info" title="Nothing leaves this server">{session.web?.why_not?.length ? <ul>{session.web.why_not.map((w, i) => <li key={i}>{w}</li>)}</ul> : <p>{session.web?.note}</p>}</Notice>
      )}
      {plan.limitations?.length ? <details><summary>Limits of this plan</summary><ul>{plan.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></details> : null}
    </section>
  );
}

function RunsCard({ projectId, runs, session, onCompared }) {
  const [pair, setPair] = useState([]);
  const [compare, setCompare] = useState({ loading: false, data: null, error: '' });
  const toggle = (id) => setPair((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));
  async function run() {
    setCompare({ loading: true, data: null, error: '' });
    try { const data = await research.compare(pair[0], pair[1]); setCompare({ loading: false, data, error: '' }); onCompared?.(data); } catch (e) { setCompare({ loading: false, data: null, error: describeError(e) }); }
  }
  if (!runs.length) return null;
  const c = compare.data;
  return (
    <section className="card stack" aria-label="Runs">
      <h2 className="card__title">Results of each run</h2>
      <ul className="list">
        {runs.map((b) => (
          <li key={b.id} className="row" data-bundle-id={b.id}>
            <div className="row__main">
              {runs.length > 1 ? <label className="row__check"><input type="checkbox" checked={pair.includes(b.id)} onChange={() => toggle(b.id)} aria-label={`Select run ${b.number} to compare`} /></label> : null}
              <span className="row__title"><Link to={`/research/bundle/${encodeURIComponent(b.id)}`}>Run {b.number}{b.label ? ` — ${b.label}` : ''}</Link></span>
              <span className="row__meta">{formatWhen(b.created_at)} · {b.finding_count} finding{b.finding_count === 1 ? '' : 's'} · {b.source_count} source{b.source_count === 1 ? '' : 's'}{b.conflict_count ? ` · ${b.conflict_count} possible disagreement${b.conflict_count === 1 ? '' : 's'}` : ''}{b.used_web ? ' · used the web' : ' · your files only'}{b.saved ? ' · saved' : ''}</span>
            </div>
            <Badge tone={BUNDLE_STATUS_TONE[b.status] ?? 'muted'}>{labelOf(BUNDLE_STATUS_LABEL, b.status)}</Badge>
            {b.quality_verdict ? <Badge tone={QUALITY_VERDICT_TONE[b.quality_verdict] ?? 'muted'}>{labelOf(QUALITY_VERDICT_LABEL, b.quality_verdict)}</Badge> : null}
          </li>
        ))}
      </ul>
      {runs.length > 1 ? (
        <div className="btn-row">
          <button type="button" className="btn btn--ghost" disabled={pair.length !== 2 || compare.loading} onClick={run}>{compare.loading ? 'Comparing…' : 'Compare the two selected runs'}</button>
          <span className="row__meta">Pick two runs to see what changed.</span>
        </div>
      ) : null}
      {compare.error ? <Notice kind="error">{compare.error}</Notice> : null}
      {c ? (
        <div className="stack" aria-label="Comparison">
          <h3 className="sub">What changed between run {c.earlier.number} and run {c.later.number}</h3>
          {c.same_question === false ? <Notice kind="warn">The two runs were not asked the same question, so differences may come from that.</Notice> : null}
          <dl className="kv">
            <div className="kv__pair"><dt>New conclusions</dt><dd>{c.conclusions.new.length}</dd></div>
            <div className="kv__pair"><dt>No longer present</dt><dd>{c.conclusions.no_longer_present.length}</dd></div>
            <div className="kv__pair"><dt>Changed strength</dt><dd>{c.conclusions.strength_changed.length}</dd></div>
            <div className="kv__pair"><dt>Unchanged</dt><dd>{c.conclusions.unchanged.length}</dd></div>
          </dl>
          {['new', 'no_longer_present'].map((k) => c.conclusions[k].length ? (
            <div key={k}><h4 className="sub">{k === 'new' ? 'New in the later run' : 'In the earlier run only'}</h4><ul>{c.conclusions[k].map((x, i) => <li key={i}>{x.text}</li>)}</ul></div>
          ) : null)}
          {c.sources ? <p className="row__meta">Sources: {c.sources.added?.length ?? 0} added, {c.sources.removed?.length ?? 0} no longer used, {c.sources.changed?.length ?? 0} with changed content.</p> : null}
          <Explanation value={c.explanation} />
          <p className="row__meta">{c.note}</p>
        </div>
      ) : null}
    </section>
  );
}

/** One research session: its plan, the consent to run it, live progress, every run's result, a comparison between runs, and notes. */
export function SessionPage() {
  const { sessionId } = useParams('/research/session/:sessionId');
  const session = useResource(() => research.session(sessionId), [sessionId]);
  const s = session.data;
  const bundles = useResource(() => (s ? research.bundles(s.project_id, { session_id: sessionId }) : Promise.resolve({ items: [] })), [sessionId, s?.latest_bundle_id, s?.run_count]); // eslint-disable-line react-hooks/exhaustive-deps
  const follower = useFollowJob({ onFinished: () => { session.reload({ quiet: true }); bundles.reload({ quiet: true }); } });
  const [starting, setStarting] = useState(false);
  const [editing, setEditing] = useState('');
  const [message, setMessage] = useState(null);
  const activeId = s?.active_job?.id ?? s?.active_job;

  useEffect(() => { if (activeId && !follower.job) follower.follow(activeId); }, [activeId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (session.loading && !s) return <PageLoading label="Opening research…" />;
  if (session.error) {
    const missing = session.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Research not found' : 'Could not open the research'} message={missing ? 'This research does not exist, or it is not yours.' : describeError(session.error)} onRetry={missing ? null : () => session.reload()} />
        <p><Link to="/research" className="btn btn--ghost">Back to research</Link></p>
      </article>
    );
  }
  const projectHome = `/research/p/${encodeURIComponent(s.project_id)}`;
  const running = Boolean(follower.job && !isFinalJob(follower.job));
  const mustApprove = needsApproval(s);

  async function start() {
    setStarting(true);
    setMessage(null);
    try {
      const job = await research.run(s.id, mustApprove ? s.plan_hash : undefined);
      follower.follow(job.id, job);
      session.reload({ quiet: true });
    } catch (e) {
      setMessage({ kind: 'error', text: describeError(e) });
    } finally { setStarting(false); }
  }

  const clarify = clarificationOf(s);
  async function useQuestion(question) {
    setMessage(null);
    try { await research.updateSession(s.id, { question }); setEditing(''); session.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }
  async function proceedAnyway() {
    setMessage(null);
    try { await research.updateSession(s.id, { options: { proceed_anyway: true } }); session.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  async function toggleSaved() {
    try { await research.updateSession(s.id, { saved: !s.saved }); session.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to={projectHome}>Research</Link></p>
        <h1 className="page-title">{s.title || s.question}</h1>
        <p className="lead">{s.question}</p>
        {s.parent ? <p className="row__meta">{followUpLine(s)}</p> : null}
        <p className="row__meta"><Badge tone={SESSION_STATUS_TONE[s.status] ?? 'muted'}>{labelOf(SESSION_STATUS_LABEL, s.status)}</Badge> {s.level_label} · {purposeLabel(s.purpose)} · {s.run_count} run{s.run_count === 1 ? '' : 's'}{s.last_run_at ? ` · last ${formatWhen(s.last_run_at)}` : ''}</p>
      </header>

      {clarify ? (
        <section className="card stack" aria-label="Which one do you mean?">
          <Notice kind="warn" title="This question needs to be clearer before it is researched">
            <p>{clarify.message}</p>
            <p>Nothing has been searched or sent anywhere.</p>
          </Notice>
          {clarify.options.length ? (
            <div className="btn-row" role="group" aria-label="Choose what you mean">
              {clarify.options.map((o) => <button key={o.label} type="button" className="btn btn--ghost" onClick={() => useQuestion(o.question || o.label)}>{o.label}</button>)}
            </div>
          ) : null}
          <label className="field"><span className="field__label">Or write the question again</span>
            <input type="text" value={editing || s.question} onChange={(e) => setEditing(e.target.value)} />
          </label>
          <div className="btn-row">
            <button type="button" className="btn" disabled={!editing.trim() || editing.trim() === s.question} onClick={() => useQuestion(editing.trim())}>Use this question</button>
            <button type="button" className="btn btn--ghost" onClick={proceedAnyway}>Go on with the question as written</button>
          </div>
        </section>
      ) : null}

      <PlanCard session={s} />

      <section className="card stack" aria-label="Run">
        <h2 className="card__title">{s.run_count ? 'Run it again' : 'Run the research'}</h2>
        {mustApprove ? <Notice kind="warn" title="Approval needed">This plan would send search phrases outside this server. Running it counts as your approval of exactly the plan shown above; if you change the question or options, you will be asked again.</Notice> : null}
        <div className="btn-row">
          <button type="button" className="btn" disabled={starting || running || Boolean(clarify)} onClick={start}>{starting ? 'Starting…' : mustApprove ? 'Approve this plan and run' : 'Run the research'}</button>
          <button type="button" className="btn btn--ghost" onClick={toggleSaved}>{s.saved ? 'Remove from saved' : 'Save this research'}</button>
        </div>
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
      </section>

      <JobProgress job={follower.job} error={follower.error} title="Research progress" onCancelled={() => session.reload({ quiet: true })}>
        {follower.job?.status === 'completed' && s.latest_bundle_id ? <div className="btn-row"><Link className="btn" to={`/research/bundle/${encodeURIComponent(s.latest_bundle_id)}`}>Open the result</Link></div> : null}
      </JobProgress>

      {bundles.error ? <ErrorState title="Could not load the results" message={describeError(bundles.error)} onRetry={() => bundles.reload()} /> : null}
      {!bundles.loading && !bundles.error && (bundles.data?.items ?? []).length === 0 && !running ? <Empty title="No results yet"><p>Run the research to see conclusions with their evidence.</p></Empty> : null}
      <RunsCard projectId={s.project_id} runs={bundles.data?.items ?? []} session={s} />

      <Explanation value={s.explanation} />
      <NotesPanel sessionId={s.id} />
    </article>
  );
}
