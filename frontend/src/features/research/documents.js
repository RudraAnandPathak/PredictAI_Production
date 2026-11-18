// Document search: what to send, and how to describe a hit. The server ranks, cites and verifies; this file only shapes the request and turns numbers into plain words.

export const SEARCH_MODES = Object.freeze([
  { key: 'hybrid', label: 'Keyword and meaning together' },
  { key: 'keyword', label: 'Exact words (keyword)' },
  { key: 'semantic', label: 'Meaning (local, from your own documents)' },
]);

export const QUERY_LIMITS = Object.freeze([2, 2000]);

/** The request body for a search, or { errors }. Only documents the person ticked are searched; none ticked means all of the project's documents. */
export function buildSearchBody({ query, mode, assetIds, k }) {
  const text = String(query ?? '').trim();
  const errors = {};
  if (text.length < QUERY_LIMITS[0]) errors.query = 'Write what you want to find (at least two characters).';
  else if (text.length > QUERY_LIMITS[1]) errors.query = `Keep the question under ${QUERY_LIMITS[1]} characters.`;
  if (!SEARCH_MODES.some((m) => m.key === mode)) errors.mode = 'Choose how to search.';
  if (Object.keys(errors).length) return { errors };
  const body = { query: text, mode, k: Number.isInteger(k) ? Math.max(1, Math.min(k, 50)) : 8 };
  if (assetIds?.length) body.asset_ids = [...assetIds];
  return { body };
}

/** Where in the document a passage sits, in words. Page numbers for text without real pages are estimates, and say so. */
export function hitPlace(hit) {
  const a = hit?.page_start; const b = hit?.page_end;
  const pages = a == null ? null : (b != null && b !== a ? `pages ${a}–${b}` : `page ${a}`);
  const label = pages ? (hit.page_estimated ? `${pages} (estimated)` : pages) : 'place not recorded';
  return hit?.section ? `${label} · ${hit.section}` : label;
}

/** Which ranking methods agreed on a hit, for the "why this result" line. */
export function hitWhy(hit) {
  const parts = Object.entries(hit?.methods ?? {}).map(([name, m]) => `${name} rank ${m.rank}`);
  const terms = (hit?.matched_terms ?? []).length ? `Matched words: ${hit.matched_terms.join(', ')}.` : 'No exact words matched; this was found by meaning.';
  return `${parts.length ? `Ranked by ${parts.join(' and ')}. ` : ''}${terms}`;
}

/** Documents that can be searched now, with the reason for any that cannot. */
export function documentRows(status) {
  return (status?.documents ?? []).map((d) => ({
    id: d.asset_id, name: d.name, searchable: Boolean(d.extraction_ready),
    state: !d.extraction_ready ? 'Text not extracted yet' : (d.indexed ? (d.stale ? 'Index out of date; it is rebuilt when you search' : `Ready · ${d.chunks} passages`) : 'Index is built the first time you search'),
  }));
}
