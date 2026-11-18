export function formatDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

export function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

/** Turn a code the way people type it (any case, spaces, odd dashes) into the canonical form. */
export function normaliseAccessCode(raw) {
  return String(raw ?? '').trim().toUpperCase().replace(/[‐-―−\s]+/g, '-');
}
