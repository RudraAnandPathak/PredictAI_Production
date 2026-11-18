import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from '../phase7/shared.jsx';
import { JOB_STATUS_LABEL, JOB_STATUS_TONE, percentOf } from '../../features/dev/labels.js';

export { Badge, Empty, Loaded, Notice, PageHeader, attempt, when };

export const JobStatus = ({ status }) => <Badge tone={JOB_STATUS_TONE[status] ?? 'muted'}>{JOB_STATUS_LABEL[status] ?? status}</Badge>;

/** A real measured value against its limit. Shows the numbers as text too, so colour is never the only signal. */
export function Meter({ label, used, limit, format = (n) => String(n) }) {
  const pct = percentOf(used, limit);
  return (
    <div className="meter">
      <p className="row__meta">{label}: {format(used)} of {format(limit)} ({pct}%){pct >= 80 ? ' — close to the limit' : ''}</p>
      <progress value={pct} max="100" aria-label={`${label}: ${pct} percent used`} />
    </div>
  );
}

/** Resolves the project id from the address, or shows the picker prompt. */
export const projectIdFromPath = (pathname) => (pathname.match(/^\/development\/([^/]+)/) ?? [])[1] ?? null;
