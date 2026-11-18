// Presentation helpers for profiles. A profile labels every result as a fact, a heuristic or an estimate; the UI must keep those labels visible.
export const BASIS = {
  fact: { label: 'Exact', badge: 'badge--ok', help: 'Computed exactly from every stored value.' },
  heuristic: { label: 'Rule of thumb', badge: 'badge--warn', help: 'A rule of thumb. It can be wrong for your data.' },
  estimate: { label: 'Estimate', badge: 'badge--warn', help: 'An approximation, not an exact count.' },
};

export function basisInfo(basis) {
  return BASIS[basis] ?? { label: basis ?? 'Unknown', badge: 'badge--muted', help: '' };
}

export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (Number.isInteger(value)) return value.toLocaleString();
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 1 : abs >= 10 ? 2 : 4;
  return Number(value.toFixed(digits)).toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatPct(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value.toFixed(value < 10 ? 1 : 0))}%`;
}

/** Heights (0-100) for histogram bars; the tallest bin is 100. */
export function histogramHeights(bins) {
  const max = Math.max(0, ...bins.map((b) => b.count));
  return bins.map((b) => (max ? Math.max(b.count ? 4 : 0, Math.round((b.count / max) * 100)) : 0));
}

export function sourceLine(result) {
  const s = result?.profile?.source;
  if (!s) return '';
  return `version ${s.version_number} · SHA-256 ${s.checksum_sha256.slice(0, 12)}…`;
}
