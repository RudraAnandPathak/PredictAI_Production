import { useState } from 'react';
import { research } from '../../api/index.js';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { canCancel, describeProgress, isFinalJob, phaseLabel, progressPercent } from '../../features/research/jobs.js';
import { JobStatusBadge, Notice } from './shared.jsx';

/** Live progress of one background job, with a cancel button. Everything shown is what the server reports; nothing is estimated here. */
export function JobProgress({ job, error, title = 'Working', onCancelled, children }) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState('');
  if (!job && !error) return null;
  if (!job) return <Notice kind="error" title="Could not follow the job">{error}</Notice>;
  const pct = progressPercent(job);
  const active = !isFinalJob(job);

  async function cancel() {
    setCancelling(true);
    setCancelError('');
    try { await research.cancelJob(job.id); onCancelled?.(); } catch (e) { setCancelError(describeError(e)); } finally { setCancelling(false); }
  }

  return (
    <section className="card stack job" aria-label={title} data-job-status={job.status}>
      <div className="card__row"><h2 className="card__title">{title}</h2><JobStatusBadge status={job.status} /></div>
      {active ? (
        <>
          <div className="progress" role="progressbar" aria-label={`${title} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined} aria-valuetext={pct === null ? 'In progress' : `${pct}%`}>
            <div className={`progress__bar${pct === null ? ' progress__bar--busy' : ''}`} style={pct === null ? undefined : { width: `${pct}%` }} />
          </div>
          <p role="status"><Spinner label="Working" /> <strong>{phaseLabel(job.progress?.phase)}</strong>{describeProgress(job) ? ` — ${describeProgress(job)}` : ''}{pct === null ? '' : ` (${pct}%)`}</p>
          {job.cancel_requested ? <p className="muted">Stopping… it ends at the next safe point.</p> : null}
          <div className="btn-row">
            <button type="button" className="btn btn--ghost" disabled={!canCancel(job) || cancelling} onClick={cancel}>{cancelling ? 'Cancelling…' : 'Cancel'}</button>
            <span className="row__meta">You can leave this page; it keeps running and the result appears here when it finishes.</span>
          </div>
        </>
      ) : null}
      {job.status === 'failed' ? <Notice kind="error" title="This did not finish">{job.error_message || 'It stopped with an error.'}{job.error_code ? <span className="row__meta"> ({job.error_code})</span> : null}</Notice> : null}
      {job.status === 'cancelled' ? <Notice kind="info">This was cancelled. Nothing was saved.</Notice> : null}
      {cancelError ? <Notice kind="warn">{cancelError}</Notice> : null}
      {error ? <Notice kind="warn">{error}</Notice> : null}
      {children}
    </section>
  );
}
