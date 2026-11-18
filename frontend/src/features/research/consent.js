// The consent form for web research, news and outside AI. Three layers decide what is allowed: the platform owner, you, and each project (which can only
// narrow what you allowed). The server combines them; this file only turns the form into the smallest change to send and checks the numbers.

export const FRESHNESS_LIMITS = Object.freeze([1, 3650]);
export const CACHE_LIMITS = Object.freeze([1, 90]);

/** Form state from a stored consent row (person or project). */
export function formFromConsent(row) {
  return {
    webEnabled: Boolean(row?.web_enabled),
    newsEnabled: Boolean(row?.news_enabled),
    externalAi: Boolean(row?.external_ai),
    providers: [...(row?.providers ?? [])],
    freshnessDays: row?.freshness_days == null ? '' : String(row.freshness_days),
    cacheDays: row?.cache_days == null ? '' : String(row.cache_days),
  };
}

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

/** Only what changed, in the shape the API takes. Returns { changes } (possibly empty) or { errors }. */
export function diffConsent(current, edited) {
  const errors = {};
  const changes = {};
  const was = formFromConsent(current);
  if (edited.webEnabled !== was.webEnabled) changes.web_enabled = edited.webEnabled;
  if (edited.newsEnabled !== was.newsEnabled) changes.news_enabled = edited.newsEnabled;
  if (edited.externalAi !== was.externalAi) changes.external_ai = edited.externalAi;
  if (!sameSet(edited.providers, was.providers)) changes.providers = [...edited.providers];
  for (const [field, key, [lo, hi], label] of [['freshnessDays', 'freshness_days', FRESHNESS_LIMITS, 'Days'], ['cacheDays', 'cache_days', CACHE_LIMITS, 'Days']]) {
    if (edited[field] === was[field]) continue;
    const text = String(edited[field]).trim();
    if (text === '') { errors[field] = 'Leave this as it is, or enter a number of days. It cannot be cleared here.'; continue; }
    const n = Number(text);
    if (!Number.isInteger(n) || n < lo || n > hi) errors[field] = `${label}: a whole number from ${lo} to ${hi}.`;
    else changes[key] = n;
  }
  if (Object.keys(errors).length) return { errors };
  return { changes };
}

/** Providers the person may tick: ones the platform can actually reach. */
export const choosableProviders = (platform) => (platform?.providers ?? []).filter((p) => p.available);

/** Plain sentences about the project layer: it can only narrow, so a project cannot allow what the person did not. */
export function projectNarrowingNote(settings) {
  const person = settings?.person ?? {};
  const notes = [];
  if (!person.web_enabled) notes.push('Web research is off in your own settings, so no project can turn it on.');
  if (!person.news_enabled) notes.push('Reading news is off in your own settings, so no project can turn it on.');
  if (!person.external_ai) notes.push('Outside AI is off in your own settings, so no project can turn it on.');
  return notes;
}
