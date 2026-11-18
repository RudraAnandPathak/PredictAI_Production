import { Link } from '../../app/router/Link.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { canCancel, describeProgress, isFinal, progressPercent } from '../../features/predictive/jobs.js';
import { describeConfig } from '../../features/predictive/config.js';
import { formatMs, formatWhen, headline, modelTitle, phaseLabel } from '../../features/predictive/format.js';
import { JobBadge, Notice } from './shared.jsx';

/** Interface 4 (part): live progress of one training job, with cancel. Everything shown is what the server reports; nothing is estimated here. */
export function JobPanel({ job, error, onCancel, cancelling, onDismiss }) {
  if (!job && !error) return null;
  if (!job) return <Notice kind="error" title="Could not follow the training job">{error}</Notice>;
  const pct = progressPercent(job);
  const active = !isFinal(job.status);
  return (
    <section className="card stack job" aria-label="Training progress" data-job-status={job.status}>
      <div className="card__row">
        <h2 className="card__title">{job.label || 'Training run'}</h2>
        <JobBadge status={job.status} />
      </div>
      {active ? (
        <>
          <div className="progress" role="progressbar" aria-label="Training progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined} aria-valuetext={pct === null ? 'In progress' : `${pct}%`}>
            <div className={`progress__bar${pct === null ? ' progress__bar--busy' : ''}`} style={pct === null ? undefined : { width: `${pct}%` }} />
          </div>
          <p role="status"><Spinner label="Working" /> <strong>{phaseLabel(job.progress?.phase)}</strong>{describeProgress(job) ? ` — ${describeProgress(job)}` : ''}{pct === null ? '' : ` (${pct}%)`}</p>
          {job.cancel_requested ? <p className="muted">Stopping… the run ends at the next safe point.</p> : null}
          <div className="btn-row">
            <button type="button" className="btn btn--ghost" disabled={!canCancel(job) || cancelling} onClick={onCancel}>{cancelling ? 'Cancelling…' : 'Cancel training'}</button>
            <span className="row__meta">You can leave this page; the run continues and appears under Models when it finishes.</span>
          </div>
        </>
      ) : null}
      {job.status === 'completed' && job.model ? (
        <>
          <p><strong>{modelTitle(job.model)}</strong> was the best model: {headline(job.model)}.</p>
          <div className="btn-row">
            <Link className="btn" to={`/predictions/models/${encodeURIComponent(job.model.id)}`}>Open the model</Link>
            {onDismiss ? <button type="button" className="btn btn--ghost" onClick={onDismiss}>Train again</button> : null}
          </div>
        </>
      ) : null}
      {job.status === 'failed' ? <Notice kind="error" title="Training did not finish">{job.error?.message || 'The run stopped with an error.'}{job.error?.code ? <span className="row__meta"> ({job.error.code})</span> : null}</Notice> : null}
      {job.status === 'cancelled' ? <Notice kind="info">This run was cancelled. Nothing was saved.</Notice> : null}
      <details>
        <summary>Settings and timing</summary>
        <ul>{describeConfig(job.config).map((l, i) => <li key={i}>{l}</li>)}</ul>
        <p className="row__meta">Started {formatWhen(job.started_at ?? job.created_at)}{job.duration_ms ? ` · took ${formatMs(job.duration_ms)}` : ''}</p>
      </details>
      {error ? <Notice kind="warn">{error}</Notice> : null}
    </section>
  );
}
