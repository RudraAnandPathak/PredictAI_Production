// Helpers for the news and event screens. Everything shown (status, tone, topic, freshness, trend wording) was decided by the server's named, versioned
// rules; the browser only builds filter parameters and arranges what came back. Tone is never presented as truth or importance.
import { EVENT_STATUS_HINT, EVENT_STATUS_LABEL, EVENT_STATUS_TONE, FRESHNESS_LABEL, FRESHNESS_TONE, labelOf } from './labels.js';

export const PAGE_SIZE = 25;

export const EMPTY_FILTERS = Object.freeze({ q: '', topic: '', entity: '', tone: '', source_id: '', since: '', until: '', status: '' });

/** Query parameters for the article list: blank filters are dropped, dates must be YYYY-MM-DD, paging is explicit. */
export function articleParams(filters = {}, page = 0, pageSize = PAGE_SIZE) {
  const params = {};
  for (const key of Object.keys(EMPTY_FILTERS)) {
    const value = String(filters[key] ?? '').trim();
    if (!value) continue;
    if ((key === 'since' || key === 'until') && !/^\d{4}-\d{2}-\d{2}$/.test(value)) continue;
    params[key] = value;
  }
  params.limit = pageSize;   // the HTTP parameter is `limit`; `limit_` is only the service argument on the server
  params.offset = Math.max(0, Math.floor(page) || 0) * pageSize;
  return params;
}

export const hasFilters = (filters) => Object.keys(EMPTY_FILTERS).some((k) => String(filters?.[k] ?? '').trim());

export function eventStatusView(status) {
  return { label: labelOf(EVENT_STATUS_LABEL, status), tone: EVENT_STATUS_TONE[status] ?? 'muted', hint: EVENT_STATUS_HINT[status] ?? 'Treat as a claim, not a verified fact.' };
}

export function freshnessView(freshness) {
  return { label: labelOf(FRESHNESS_LABEL, freshness), tone: FRESHNESS_TONE[freshness] ?? 'muted' };
}

/** "3 hours ago" style age from hours; null stays unknown. */
export function ageLabel(hours) {
  if (typeof hours !== 'number' || !Number.isFinite(hours) || hours < 0) return 'unknown';
  if (hours < 1) return 'less than an hour ago';
  if (hours < 48) return `${Math.round(hours)} hour${Math.round(hours) === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}

/** Rows for the trend table. `topicLabels` (from the topics list) turns a topic key into its name; a topic the server said has too little data keeps its words
 *  ("too few to say") and no direction is invented from the counts. */
export function trendRows(trends, topicLabels = {}) {
  return (trends?.topics ?? []).map((t) => ({
    key: t.name,
    topic: topicLabels[t.name] ?? t.name,
    verdict: t.label,
    recent: t.recent,
    previous: t.previous,
    z: typeof t.z === 'number' ? t.z : null,
  }));
}

/** key → readable name map from the topics list. */
export const topicLabelMap = (topics) => Object.fromEntries((topics?.items ?? []).map((t) => [t.topic, t.label]));

/** Timeline entries in the order the server gave them (oldest first), with the first report marked. */
export function timelineRows(timeline) {
  return (timeline ?? []).map((t) => ({ key: t.article_id, time: t.time, headline: t.headline, publisher: t.publisher, kind: t.kind, tone: t.tone, url: t.url, first: Boolean(t.first_reported) }));
}

/** Side-by-side outlet comparison rows. Differences are listed only when the server found them. */
export function comparisonRows(comparison) {
  return (comparison?.outlets ?? []).map((o) => ({
    key: o.outlet, outlet: o.outlet, headline: o.headline, lead: o.lead, kind: o.kind, tone: o.tone, numbers: o.numbers ?? [], extra: o.only_this_outlet_gives ?? [], url: o.url, hoursAfterFirst: o.hours_after_first,
  }));
}

/** A one-line description of how an event is known, from the server's basis text. */
export const eventBasis = (event) => event?.basis || 'No basis recorded.';

/** Feed form → request body for adding a source. Full text is never requested (the server refuses it). */
export function buildSourceBody(form) {
  const errors = {};
  const name = String(form?.name ?? '').trim();
  const feedUrl = String(form?.feedUrl ?? '').trim();
  if (!name) errors.name = 'Give the feed a name.';
  if (!feedUrl) errors.feedUrl = 'Paste the feed address.';
  else if (!/^https?:\/\/\S+$/i.test(feedUrl)) errors.feedUrl = 'The address must start with http:// or https://';
  if (!form?.termsAcknowledged) errors.termsAcknowledged = 'Confirm that you may read this feed under its terms.';
  const retention = form?.retentionDays === '' || form?.retentionDays === undefined ? undefined : Number(form.retentionDays);
  if (retention !== undefined && (!Number.isInteger(retention) || retention < 1 || retention > 365)) errors.retentionDays = 'Use a whole number of days from 1 to 365.';
  if (Object.keys(errors).length) return { errors };
  const policy = { terms_acknowledged: true };
  if (retention !== undefined) policy.retention_days = retention;
  const body = { name, feed_url: feedUrl, policy };
  const publisher = String(form?.publisher ?? '').trim();
  if (publisher) body.publisher = publisher;
  return { body };
}
