// Job helpers for research runs and news reads. Polling itself is the shared rule from the predictive feature (same lifecycle: queued → running → final).
import { FINAL_JOB_STATUSES } from './labels.js';

export { nextPollDelayMs, pollJob, progressPercent, describeProgress, canCancel } from '../predictive/jobs.js';
export const isFinalJob = (job) => Boolean(job) && FINAL_JOB_STATUSES.includes(job.status);

// These are the phase names the server actually sends (research/engine.py, world/service.py); an unknown one is shown humanised, not hidden.
export const PHASE_LABEL = Object.freeze({
  queued: 'Waiting to start', context: 'Collecting your material', searching: 'Searching', reading: 'Reading', extracting: 'Extracting evidence', wording: 'Writing conclusions',
  saving: 'Saving', fetching: 'Reading feeds', analysing: 'Grouping events', coverage: 'Counting coverage by day', testing: 'Testing for association',
  completed: 'Finished', failed: 'Did not finish', cancelled: 'Cancelled',
});
export const phaseLabel = (phase) => (phase && PHASE_LABEL[phase]) || (phase ? String(phase).replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Working');

/** One line per feed from a finished news read, using only the counts the server reported. */
export function ingestRows(job) {
  const rows = job?.progress?.results;
  if (!Array.isArray(rows)) return [];
  return rows.map((r) => ({
    key: r.source_id ?? r.name,
    name: r.name ?? 'Feed',
    ok: r.status === 'ok',
    text: r.status === 'ok'
      ? `${r.new ?? 0} new, ${r.seen_before ?? 0} already stored${r.duplicates ? `, ${r.duplicates} duplicate` : ''}${r.quarantined ? `, ${r.quarantined} held back` : ''}`
      : (r.status === 'skipped' ? 'Skipped (read too recently)' : `Could not be read${r.error ? ` (${r.error})` : ''}`),
    quarantined: r.quarantined ?? 0,
    removed: r.removed_sentences ?? 0,
  }));
}
