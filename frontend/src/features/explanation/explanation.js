// Normalises the server's "Explanation" object so every screen shows it the same way. It never adds wording of its own about a result.
export const EXPLANATION_TITLE = 'Explanation';
export const NO_EXPLANATION_REASON = 'An explanation is not available for this result.';

/** Returns { available, title, summary, points, terms, cautions, reason, cited } for any value the server may have sent (including nothing). */
export function explanationView(raw) {
  if (!raw || typeof raw !== 'object') {
    return { present: false, available: false, title: EXPLANATION_TITLE, summary: '', points: [], terms: [], cautions: [], reason: NO_EXPLANATION_REASON, cited: 0 };
  }
  const available = raw.status === 'available' && typeof raw.summary === 'string' && raw.summary.length > 0;
  return {
    present: true,
    available,
    title: EXPLANATION_TITLE,                                   // the section is always called exactly this, wherever it appears
    summary: available ? raw.summary : '',
    points: available && Array.isArray(raw.points) ? raw.points.filter((p) => typeof p === 'string' && p) : [],
    terms: available && Array.isArray(raw.terms) ? raw.terms.filter((t) => t && typeof t.term === 'string' && typeof t.meaning === 'string') : [],
    cautions: available && Array.isArray(raw.cautions) ? raw.cautions.filter((c) => typeof c === 'string' && c) : [],
    reason: available ? '' : (typeof raw.reason === 'string' && raw.reason) || NO_EXPLANATION_REASON,
    cited: Number(raw.basis?.numbers_cited) || 0,
  };
}

/** Finds the explanation object a given result carries, wherever the server put it. Returns undefined when it has none (the UI then shows the "unavailable" state, never invented text). */
export function explanationOf(result) {
  if (!result || typeof result !== 'object') return undefined;
  return result.explanation ?? result.answer?.explanation ?? undefined;
}
