// View-models for the research plan screens: what will be searched, what will leave the server, and the request body for a new session.
// The server owns the plan and the consent decision; this file only arranges what it sent, and checks the form before sending.
import { labelOf, PURPOSE_LABEL } from './labels.js';

export const QUESTION_MAX = 2000;
export const TITLE_MAX = 300;

/** Request body for POST /research/sessions. Returns { body } or { errors }. Nothing is sent when the question is empty or too long. */
export function buildSessionBody(form) {
  const errors = {};
  const question = String(form?.question ?? '').trim();
  if (!question) errors.question = 'Write the question you want researched.';
  else if (question.length > QUESTION_MAX) errors.question = `Keep the question under ${QUESTION_MAX} characters.`;
  const title = String(form?.title ?? '').trim();
  if (title.length > TITLE_MAX) errors.title = `Keep the title under ${TITLE_MAX} characters.`;
  const days = form?.freshnessDays;
  let freshness;
  if (days !== undefined && days !== null && days !== '') {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 3650) errors.freshnessDays = 'Use a whole number of days from 1 to 3650.';
    else freshness = n;
  }
  if (Object.keys(errors).length) return { errors };
  const options = {
    web: Boolean(form?.web),
    documents: [...new Set(form?.documents ?? [])],
    datasets: [...new Set(form?.datasets ?? [])],
    ai_summary: Boolean(form?.aiSummary),
    reuse_earlier: Boolean(form?.reuseEarlier),
  };
  if (form?.aiPlan) options.ai_plan = true;
  if (['auto', 'always', 'never'].includes(form?.arxivMode) && form.arxivMode !== 'auto') options.arxiv_mode = form.arxivMode;
  if (options.web && Array.isArray(form?.providers) && form.providers.length) options.providers = [...new Set(form.providers)];
  if (freshness !== undefined) options.freshness_days = freshness;
  const body = { question, level: form?.level || 'topic', purpose: form?.purpose === 'education' ? 'education' : 'general', options };
  if (title) body.title = title;
  return { body };
}

/** Whether the next step is to approve the plan first. The server decides (needs_approval); the screen never runs without it. */
export const needsApproval = (session) => Boolean(session?.needs_approval) && !session?.plan_approved;

/** What leaves this server if the person runs the research now. Empty when nothing does.
 *  The server sends one entry per phrase: { query, providers, removed, variants: [{ provider, query, role }] }. Each provider gets its own wording (an arXiv search is written in arXiv's
 *  query language), so `items[].variants` lists exactly what each one would receive. A plain string is also accepted. */
export function outboundSummary(session) {
  const web = session?.web ?? {};
  const will = web.will_send ?? [];
  const raw = Array.isArray(will) ? will : (will.queries ?? []);
  const items = raw.map((q) => {
    if (q && typeof q === 'object') {
      const variants = Array.isArray(q.variants) ? q.variants.filter((v) => v && typeof v.query === 'string').map((v) => ({ provider: String(v.provider ?? ''), query: v.query, role: v.role ? String(v.role) : '' })) : [];
      return { text: String(q.query ?? ''), providers: Array.isArray(q.providers) ? q.providers.map(String) : [], removed: Array.isArray(q.removed) ? q.removed.map(String) : [], variants };
    }
    return { text: String(q), providers: [], removed: [], variants: [] };
  }).filter((i) => i.text || i.variants.length);
  if (!web.allowed_now || !items.length) return { leaves: false, queries: [], items: [], providers: [], note: web.note || '' };
  const providers = session?.plan?.web?.providers ?? [];
  return { leaves: true, queries: items.map((i) => i.text), items, providers, note: web.note || '' };
}

/** Exactly what each provider would be sent, grouped by provider: [{ provider, queries: [{ query, role }] }]. This is the list the person approves. */
export function sentByProvider(summary) {
  const map = new Map();
  for (const item of summary?.items ?? []) {
    const variants = item.variants.length ? item.variants : item.providers.map((p) => ({ provider: p, query: item.text, role: '' }));
    for (const v of variants) {
      if (!map.has(v.provider)) map.set(v.provider, []);
      const list = map.get(v.provider);
      if (!list.some((x) => x.query === v.query)) list.push({ query: v.query, role: v.role });
    }
  }
  return [...map.entries()].map(([provider, queries]) => ({ provider, queries }));
}

/** Why web research is off for this session, in the server's words, plus the layer that said no. */
export function webOffReasons(session, capabilities) {
  const own = session?.web?.why_not ?? capabilities?.web?.why_not ?? [];
  return Array.isArray(own) ? own.filter(Boolean) : [];
}

/** Short list of what the plan will do, for the plan card. Counts come from the server's plan. */
export function planLines(plan) {
  if (!plan) return [];
  const lines = [];
  const parts = plan.subtopics?.length ?? 0;
  lines.push(`${parts} part${parts === 1 ? '' : 's'} to look into${plan.level_label ? ` (${plan.level_label})` : ''}`);
  const docs = plan.local?.documents?.length ?? 0;
  const sets = plan.local?.datasets?.length ?? 0;
  if (docs || sets) lines.push(`Your material: ${docs} document${docs === 1 ? '' : 's'}, ${sets} dataset${sets === 1 ? '' : 's'}. This stays on this server.${sets ? ' Figures about a dataset are calculated here by PredictAI, not written by a language model.' : ''}`);
  else lines.push('Your material: none chosen.');
  if (plan.web?.enabled) lines.push(`Web: up to ${plan.web.max_pages} pages from ${(plan.web.providers ?? []).join(', ') || 'no provider'}.`);
  else lines.push('Web: off. Nothing leaves this server.');
  if (plan.min_sources) lines.push(`Aims for at least ${plan.min_sources} independent source${plan.min_sources === 1 ? '' : 's'}.`);
  return lines;
}

export const purposeLabel = (purpose) => labelOf(PURPOSE_LABEL, purpose);

/** Which documents and datasets can be picked: only ready, current files of the right kind in this project. */
export function pickableAssets(assets, kind) {
  return (assets ?? []).filter((a) => a && a.kind === kind && !a.deleted_at && (a.status === undefined || a.status === 'ready' || a.status === 'active'));
}

/** The topic part of a consent decision as a person reads it: what is allowed and which layer would have to change. */
export function consentLines(settings) {
  if (!settings) return [];
  const eff = settings.effective ?? {};
  const lines = [];
  lines.push(eff.web_allowed ? `Web research is allowed${eff.providers?.length ? ` through ${eff.providers.join(', ')}` : ''}.` : 'Web research is off.');
  lines.push(eff.news_allowed ? 'Reading news feeds is allowed.' : 'Reading news feeds is off.');
  lines.push(eff.external_ai ? 'Sending text to an outside AI service is allowed.' : 'Nothing is sent to an outside AI service.');
  for (const reason of eff.reasons ?? []) lines.push(reason);
  return lines;
}

export const ARXIV_MODE_LABEL = { auto: 'Only when the subject fits (recommended)', always: 'Always search arXiv', never: 'Never search arXiv' };

/** The server's "which one do you mean?" block for a question it cannot answer without guessing, or null. It stops the plan being approved or run until the question is made clearer
 *  (or the person says to go on anyway). Nothing is sent anywhere while it is open. */
export function clarificationOf(session) {
  const c = session?.plan?.clarification;
  if (!c || !c.needed) return null;
  return { reason: String(c.reason || ''), message: String(c.message || ''), options: Array.isArray(c.options) ? c.options.map((o) => ({ label: String(o.label ?? o.question ?? ''), question: String(o.question ?? '') })).filter((o) => o.label) : [] };
}

/** Outside providers the plan left out, each with the reason (for example arXiv for a history question). Shown so that "not searched" is never mistaken for "searched, nothing found". */
export function skippedProviders(session) {
  const list = session?.plan?.web?.skipped_providers;
  return Array.isArray(list) ? list.map((x) => ({ provider: String(x.provider || ''), reason: String(x.reason || '') })).filter((x) => x.provider) : [];
}

/** What the plan understood the question to be about (subjects, task and field), as short text for the plan card. */
export function understandingLine(session) {
  const u = session?.plan?.understanding;
  if (!u) return '';
  const names = (u.entities ?? []).filter((e) => e.role !== 'context').map((e) => e.name);
  return [names.length ? `About: ${names.join(', ')}` : '', u.task ? `Kind of question: ${String(u.task).replace(/_/g, ' ')}` : '', u.domain ? `Field: ${String(u.domain).replace(/_/g, ' ')}` : ''].filter(Boolean).join(' · ');
}
