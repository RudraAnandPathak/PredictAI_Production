// Plain-language labels and small pure helpers for the Development Workspace. No network, no React: unit-tested in tests/dev.test.js.

export const JOB_STATUS_LABEL = Object.freeze({
  queued: 'Waiting in the queue', starting: 'Starting', running: 'Running', cancelling: 'Stopping', succeeded: 'Finished', failed: 'Failed',
  timed_out: 'Stopped: time limit', cancelled: 'Cancelled', blocked: 'Blocked',
});
export const JOB_STATUS_TONE = Object.freeze({
  queued: 'muted', starting: 'accent', running: 'accent', cancelling: 'warn', succeeded: 'ok', failed: 'danger', timed_out: 'warn', cancelled: 'muted', blocked: 'warn',
});
export const ACTIVE_JOB = Object.freeze(['queued', 'starting', 'running', 'cancelling']);
export const isActive = (status) => ACTIVE_JOB.includes(status);
export const canCancel = (status) => ['queued', 'starting', 'running'].includes(status);
export const canRetry = (status) => ['failed', 'timed_out', 'cancelled'].includes(status);

export const BLOCKED_REASON_LABEL = Object.freeze({
  entitlement_revoked: 'Special access was removed, so this job was stopped. Your files are kept.',
  no_network_isolation: 'This computer cannot isolate a job from the network, so the job was not run.',
});
export const blockedReasonText = (reason) => (reason ? BLOCKED_REASON_LABEL[reason] ?? reason : null);

export const ERROR_CLASS_LABEL = Object.freeze({
  wall_clock: 'It ran past the time limit.', cpu_limit: 'It used more processor time than allowed.', memory_limit: 'It used more memory than allowed.',
  output_limit: 'It printed more than the output limit.', storage_limit: 'It wrote more files than allowed.', process_limit: 'It started too many processes.',
  runtime_error: 'The code raised an error.', syntax_error: 'The code has a syntax error.', missing_dependency: 'A package the code imports is not in the environment.',
});
export const errorClassText = (c) => (c ? ERROR_CLASS_LABEL[c] ?? c.replace(/_/g, ' ') : null);

export const ENV_STATUS_LABEL = Object.freeze({ pending: 'Waiting to build', building: 'Building', ready: 'Ready', failed: 'Build failed' });
export const WORKSPACE_STATUS_LABEL = Object.freeze({ active: 'Active', suspended: 'Suspended (access was removed; files are kept)' });
export const SESSION_KIND_LABEL = Object.freeze({ notebook: 'JupyterLab', ide: 'Browser code editor' });

export const STAGE_STATUS_LABEL = Object.freeze({ pending: 'Pending', running: 'Running', succeeded: 'Passed', failed: 'Failed', skipped: 'Skipped', cancelled: 'Cancelled', blocked: 'Blocked' });

/** "1.5 MB" style size. */
export function bytesText(n) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v < 1024) return `${v} B`;
  const units = ['KB', 'MB', 'GB'];
  let x = v / 1024, i = 0;
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1; }
  return `${x >= 10 ? Math.round(x) : x.toFixed(1)} ${units[i]}`;
}

export const secondsText = (s) => (s == null ? '—' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);

/** Share of a limit in use, 0..100, never NaN. */
export function percentOf(used, limit) {
  const u = Number(used), l = Number(limit);
  if (!Number.isFinite(u) || !Number.isFinite(l) || l <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((u / l) * 100)));
}
export const nearLimit = (used, limit) => percentOf(used, limit) >= 80;

/** Honest reading of the landing payload: why the area is locked or limited, never a guess. */
export function accessSummary(overview) {
  const a = overview?.access;
  if (!a) return { state: 'unknown', text: 'Access could not be determined.' };
  if (!a.allowed) return { state: 'locked', text: a.reason || 'The Development Workspace needs Special access. Activate a code in Account > Special access, or ask the platform owner.' };
  return { state: 'open', text: 'Special access is active.' };
}

/** What the person can actually do here, from the server's own statements. */
export function executionSummary(execution) {
  if (!execution) return { runs: false, text: 'Execution status is not available.' };
  if (!execution.enabled) return { runs: false, text: 'Running code is switched off on this installation. You can still edit and save files.' };
  const iso = execution.isolation;
  const safe = iso && iso.multi_user_safe;
  return { runs: true, text: `Jobs run in the ${iso?.provider ?? 'configured'} environment. ${safe ? '' : 'This is for one trusted person; it is not a sandbox for other people’s code.'}`.trim() };
}

/** A relative path the server will accept (no traversal, no absolute, not empty). The server re-checks everything. */
export function cleanPath(input) {
  const p = String(input ?? '').trim().replace(/\\/g, '/').replace(/^\.\//, '');
  if (!p || p.startsWith('/') || p.split('/').some((seg) => seg === '..' || seg === '')) return null;
  return p.length <= 240 ? p : null;
}

/** Tree of {path} items grouped by folder for display. */
export function groupByFolder(items) {
  const out = new Map();
  for (const it of items ?? []) {
    const i = it.path.lastIndexOf('/');
    const folder = i < 0 ? '' : it.path.slice(0, i);
    if (!out.has(folder)) out.set(folder, []);
    out.get(folder).push(it);
  }
  return [...out.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([folder, files]) => ({ folder, files: files.sort((x, y) => x.path.localeCompare(y.path)) }));
}

/** Lineage rows: each link is either a real record or reported missing. Never invents a link. */
export function lineageRows(lineage) {
  if (!lineage) return [];
  return (lineage.links ?? []).map((l) => ({ kind: l.stage, label: String(l.stage).replace(/_/g, ' '), present: l.present === true, detail: l.present ? (l.record?.id ?? null) : l.note }));
}

export const COMPUTE_LIMIT_LABEL = Object.freeze({
  job_wall_seconds: 'Time per job (seconds)', job_cpu_seconds: 'Processor time per job (seconds)', job_memory_mb: 'Memory per job (MB)', job_output_kb: 'Printed output per job (KB)',
  job_max_procs: 'Processes per job', job_storage_mb: 'Files written per job (MB)', user_concurrency: 'Jobs you can run at once', project_concurrency: 'Jobs per project at once',
  queue_depth: 'Jobs waiting in the queue', project_storage_mb: 'Project storage (MB)', user_cpu_seconds_per_day: 'Processor seconds per day', max_sessions: 'Notebook / editor sessions at once',
});
