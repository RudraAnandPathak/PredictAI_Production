// Display helpers for exam prediction and the AI Brain. They only FORMAT what the server computed and check form input; nothing here ranks a chapter,
// tests a ranking or decides whether an AI request is allowed. The server does all of that, and says so in its own words, which are shown as given.

export const PRIORITY_LABEL = Object.freeze({
  high: 'Likely to carry weight', medium: 'May carry weight', low: 'Less likely to carry weight', never_asked: 'Not asked in these papers',
});
export const PRIORITY_TONE = Object.freeze({ high: 'ok', medium: 'accent', low: 'muted', never_asked: 'muted' });
export const VERDICT_LABEL = Object.freeze({ better_than_chance: 'Better than chance on your papers', not_clearly_better_than_chance: 'Not clearly better than chance', not_tested: 'Could not be tested' });
export const VERDICT_TONE = Object.freeze({ better_than_chance: 'ok', not_clearly_better_than_chance: 'warn', not_tested: 'muted' });
export const FLAG_LABEL = Object.freeze({ POSSIBLY_REMOVED: 'Check the syllabus', MISSING_FROM_LATEST: 'Not in the latest paper' });
export const AI_MODES = Object.freeze([
  { value: 'all', label: 'AI writes the questions', hint: 'One request writes new questions for every slot. Anything that fails the checks is replaced from your own material.' },
  { value: 'gaps', label: 'Your material first, AI for the gaps', hint: 'Only slots your own papers and notes cannot fill are sent to the AI.' },
  { value: 'off', label: 'No AI', hint: 'Questions come only from your own past papers and notes.' },
]);
export const PREDICTION_NOTE = 'A prediction is an estimate from the papers you supplied. It is not the real paper, and no topic is guaranteed to be asked.';
export const MAX_ARTICLES = 8;

export const priorityLabel = (p) => PRIORITY_LABEL[p] ?? String(p ?? '');
export const priorityTone = (p) => PRIORITY_TONE[p] ?? 'muted';
export const verdictLabel = (v) => VERDICT_LABEL[v] ?? String(v ?? '');
export const verdictTone = (v) => VERDICT_TONE[v] ?? 'muted';

/** Table rows for the ranking, in the server's order (already highest first). */
export function chapterRows(result) {
  return (result?.chapters ?? []).map((c) => ({
    chapter: c.chapter, rank: c.rank, score: c.score, history: c.history_score, web: c.web_bonus ?? 0, priority: c.priority, why: c.why ?? [], flags: c.flags ?? [], webSentences: c.web_sentences ?? [],
  }));
}

export const scoreText = (n) => (typeof n === 'number' && Number.isFinite(n) ? n.toFixed(1) : '—');
export const bonusText = (n) => (n > 0 ? `+${Number(n).toFixed(1)}` : '—');

/** The test of the ranking on the person's own papers, as one sentence taken from the server plus the verdict label. */
export function backtestLine(bt) {
  if (!bt) return 'The ranking was not tested.';
  return bt.message ?? verdictLabel(bt.verdict);
}

/** Why the AI did or did not write the questions, and what it did, in the order a person needs it. */
export function aiLines(ai) {
  if (!ai) return [];
  const out = [];
  if (ai.used_ai) out.push(`${ai.accepted} question(s) written by ${ai.model ?? ai.provider} in ${ai.requests} request(s); ${ai.rejected?.length ?? 0} did not pass the checks.`);
  else out.push('No AI was used for this paper.');
  for (const r of ai.reasons ?? []) out.push(r);
  for (const r of (ai.rejected ?? []).slice(0, 5)) out.push(`Slot ${r.slot ?? '?'}: ${r.reason}`);
  return out;
}

export function predictProblem({ yearFrom, yearTo, maxArticles, examName }) {
  for (const [label, v] of [['First year', yearFrom], ['Last year', yearTo]]) {
    if (v !== '' && v != null && !/^\d{4}$/.test(String(v))) return `${label} must be a four-digit year.`;
  }
  if (yearFrom && yearTo && Number(yearFrom) > Number(yearTo)) return 'The first year cannot be after the last year.';
  const n = Number(maxArticles);
  if (!Number.isInteger(n) || n < 1 || n > MAX_ARTICLES) return `Read between 1 and ${MAX_ARTICLES} articles.`;
  if (examName && examName.length > 120) return 'The exam name is too long (120 characters at most).';
  return null;
}

export function predictBody({ yearFrom, yearTo, useWeb, maxArticles, examName }) {
  return { ...(yearFrom ? { year_from: Number(yearFrom) } : {}), ...(yearTo ? { year_to: Number(yearTo) } : {}), use_web: Boolean(useWeb), max_articles: Number(maxArticles),
    ...(examName?.trim() ? { exam_name: examName.trim() } : {}) };
}

/** What the options screen tells the person before they press anything. */
export function optionLines(opts) {
  if (!opts) return [];
  const out = [];
  out.push(opts.web?.allowed ? 'Exam articles from the web can be read.' : `Web articles are off: ${(opts.web?.reasons ?? []).join(' ') || 'not available.'}`);
  out.push(opts.ai?.ready ? 'The AI is ready to write questions.' : `The AI will not be used: ${opts.ai?.note ?? 'not available.'}`);
  return out;
}

/** "Used 3 of 15 requests today. Resets …": the figures are the server's. */
export function usageLine(u) {
  if (!u) return 'No usage yet.';
  const cap = u.daily_request_limit ? ` of ${u.daily_request_limit}` : '';
  const reset = u.resets_at ? ` The count resets at ${new Date(u.resets_at).toLocaleString()} (${u.reset_zone}).` : '';
  return `Used ${u.requests_today}${cap} request(s) today; ${u.requests_last_minute} in the last minute${u.requests_per_minute ? ` (limit ${u.requests_per_minute})` : ''}.${reset}`;
}

export function geminiSetupProblem({ model, daily, perMinute }) {
  if (!model?.trim()) return 'Name the model, for example one from the list below.';
  if (/\s/.test(model.trim())) return 'A model name has no spaces.';
  for (const [label, v] of [['Daily request limit', daily], ['Per-minute limit', perMinute]]) {
    if (v !== '' && v != null && (!/^\d+$/.test(String(v)) || Number(v) < 1)) return `${label} must be a whole number of 1 or more, or empty.`;
  }
  return null;
}

export function setupBody({ model, daily, perMinute }) {
  return { model: model.trim(), enable: true, ...(daily ? { daily_request_limit: Number(daily) } : {}), ...(perMinute ? { requests_per_minute: Number(perMinute) } : {}) };
}
