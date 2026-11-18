import { Badge, Notice, Stat } from '../predictive/shared.jsx';
import { LINK_REL, domainOf, safeHref } from '../../features/research/links.js';
import {
  CONFIDENCE_LABEL, CONFIDENCE_TONE, EVENT_STATUS_HINT, EVENT_STATUS_LABEL, EVENT_STATUS_TONE, FRESHNESS_LABEL, FRESHNESS_TONE, JOB_STATUS_LABEL, JOB_STATUS_TONE, ORIGIN_HINT, ORIGIN_LABEL,
  ORIGIN_TONE, STATEMENT_LABEL, STATEMENT_TONE, labelOf,
} from '../../features/research/labels.js';

export { Badge, Notice, Stat };

/** Where a piece of material came from. Always shown beside any quoted or computed statement. */
export const OriginBadge = ({ origin }) => <Badge tone={ORIGIN_TONE[origin] ?? 'muted'} title={ORIGIN_HINT[origin]}>{labelOf(ORIGIN_LABEL, origin)}</Badge>;
export const StatementBadge = ({ kind }) => <Badge tone={STATEMENT_TONE[kind] ?? 'muted'}>{labelOf(STATEMENT_LABEL, kind)}</Badge>;
export const ConfidenceBadge = ({ confidence }) => <Badge tone={CONFIDENCE_TONE[confidence] ?? 'muted'}>{labelOf(CONFIDENCE_LABEL, confidence)}</Badge>;
export const EventStatusBadge = ({ status }) => <Badge tone={EVENT_STATUS_TONE[status] ?? 'muted'} title={EVENT_STATUS_HINT[status]}>{labelOf(EVENT_STATUS_LABEL, status)}</Badge>;
export const FreshnessBadge = ({ freshness }) => <Badge tone={FRESHNESS_TONE[freshness] ?? 'muted'}>{labelOf(FRESHNESS_LABEL, freshness)}</Badge>;
export const JobStatusBadge = ({ status }) => <Badge tone={JOB_STATUS_TONE[status] ?? 'muted'}>{labelOf(JOB_STATUS_LABEL, status)}</Badge>;

/** A link that came from outside. Only plain http(s) addresses are clickable and they open without a referrer; anything else is shown as text. */
export function ExternalLink({ url, children }) {
  const href = safeHref(url);
  const text = children ?? (domainOf(url) || String(url ?? ''));
  if (!href) return <span title="This address is not a plain web link, so it is shown as text only.">{text}</span>;
  return <a href={href} target="_blank" rel={LINK_REL}>{text}</a>;
}

export function Empty({ title, children }) {
  return <div className="state" role="status"><div className="state__body"><h3 className="state__title">{title}</h3>{children}</div></div>;
}

/** Saves a downloaded blob under the server's file name. */
export function saveBlob({ blob, filename }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'download';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
