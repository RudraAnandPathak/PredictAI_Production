// Polling rules for background training jobs. Pure, so the timing and the stop conditions can be tested without a browser.
import { FINAL_STATUSES } from './format.js';

export const isFinal = (status) => FINAL_STATUSES.includes(status);

/** Seconds until the next status check: quick at first, then gentler; never more often than once a second, never less often than every 5 s. */
export function nextPollDelayMs(attempt) {
  const a = Math.max(0, Math.floor(attempt) || 0);
  if (a < 5) return 1000;
  if (a < 20) return 2000;
  return 5000;
}

/** 0-100 for a progress bar. The server sends `fraction` (0-1); anything else is "unknown" (null) rather than a made-up number. */
export function progressPercent(job) {
  if (!job) return null;
  if (job.status === 'completed') return 100;
  const f = job.progress?.fraction;
  if (typeof f !== 'number' || Number.isNaN(f)) return null;
  return Math.max(0, Math.min(100, Math.round(f * 100)));
}

export function describeProgress(job) {
  if (!job) return '';
  const p = job.progress ?? {};
  return p.message || '';
}

/** Whether the person can still stop the job. */
export const canCancel = (job) => Boolean(job) && !isFinal(job.status) && !job.cancel_requested;

/**
 * Drives polling. `fetchJob(id)` resolves a job; `onUpdate(job)` gets every answer; resolves with the final job.
 * `sleep` and `isStopped` are injectable so tests need no timers. Transient errors are retried up to `maxErrors` times in a row.
 */
export async function pollJob(jobId, { fetchJob, onUpdate = () => {}, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), isStopped = () => false, maxErrors = 4 }) {
  let attempt = 0;
  let errors = 0;
  for (;;) {
    if (isStopped()) return null;
    let job;
    try {
      job = await fetchJob(jobId);
      errors = 0;
    } catch (error) {
      errors += 1;
      if (error?.status === 404 || errors >= maxErrors) throw error;
      await sleep(nextPollDelayMs(attempt++));
      continue;
    }
    if (isStopped()) return null;
    onUpdate(job);
    if (isFinal(job.status)) return job;
    await sleep(nextPollDelayMs(attempt++));
  }
}
