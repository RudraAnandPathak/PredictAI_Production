// View-models for the "Search tools" tab: testing each search provider, searching one on its own, and opening one article, paper or page section by section.
// The server decides what is allowed and what is sent; these helpers only validate the form and arrange the answers.
import { DIAGNOSIS_LABEL, DIAGNOSIS_TONE, labelOf } from './labels.js';

export const QUERY_MAX = 300;
export const PROVIDER_ORDER = Object.freeze(['wikipedia', 'arxiv', 'searxng']);

/** Request body for a single provider search. Returns { body } or { errors }. */
export function buildSearchBody(form) {
  const errors = {};
  const query = String(form?.query ?? '').trim();
  if (query.length < 2) errors.query = 'Write what to search for.';
  else if (query.length > QUERY_MAX) errors.query = `Keep the search under ${QUERY_MAX} characters.`;
  if (!form?.provider) errors.provider = 'Choose where to search.';
  if (Object.keys(errors).length) return { errors };
  return { body: { provider: form.provider, query, limit: 8 } };
}

/** What to send to open one hit. A Wikipedia hit has a page id or title, an arXiv hit an arXiv id, a web hit its address. Returns null when the hit cannot be opened. */
export function openBody(provider, hit) {
  if (!hit) return null;
  const extra = hit.extra ?? {};
  if (provider === 'wikipedia') return extra.pageid ? { provider, ref: { pageid: Number(extra.pageid) } } : (hit.title ? { provider, ref: { title: String(hit.title) } } : null);
  if (provider === 'arxiv') return extra.arxiv_id ? { provider, ref: { arxiv_id: String(extra.arxiv_id) } } : null;
  return hit.url ? { provider, ref: { url: String(hit.url) } } : null;
}

/** Rows for the test results: the server's plain message and the one thing to do about it. */
export function diagnosisRows(result) {
  return (result?.results ?? []).map((r) => ({
    provider: r.provider, label: r.label || r.provider, ok: Boolean(r.ok), status: r.status, statusLabel: labelOf(DIAGNOSIS_LABEL, r.status), tone: DIAGNOSIS_TONE[r.status] ?? 'muted',
    message: r.message || '', fix: r.fix || '', host: r.host || '', checkedAt: r.checked_at || '',
  })).sort((a, b) => PROVIDER_ORDER.indexOf(a.provider) - PROVIDER_ORDER.indexOf(b.provider));
}

/** Providers that can be searched now: the ones the person switched on and that are set up on this server. */
export function searchableProviders(capabilities) {
  return (capabilities?.search_providers ?? []).filter((p) => p.available && p.allowed);
}

/** Why a provider cannot be used, in the server's words, for the ones that are not searchable. */
export function unavailableProviders(capabilities) {
  return (capabilities?.search_providers ?? []).filter((p) => !(p.available && p.allowed)).map((p) => ({
    key: p.key, label: p.label, reason: !p.available ? (p.reason || 'Not set up on this server.') : 'Switched off in your research settings for this project.',
  }));
}
