// Pure helpers for Discovery, Studio and Reports (no React, no network) so they can be unit-tested in Node.
// The server remains the authority: these helpers shape requests and wording, they never decide what is allowed.

// ---- Navigation (R9: eight research destinations) -------------------------------------------------
export const RESEARCH_NAV = Object.freeze([
  { key: 'home', label: 'Research Home', to: '/research-intelligence' },
  { key: 'workspaces', label: 'My Workspaces', to: '/research-intelligence/workspaces' },
  { key: 'discover', label: 'Discover Research', to: '/research-intelligence/discover' },
  { key: 'library', label: 'Source Library', to: '/research-intelligence/library' },
  { key: 'assistant', label: 'Research Assistant', to: '/research-intelligence/assistant' },
  { key: 'studio', label: 'Research Studio', to: '/research-intelligence/studio' },
  { key: 'reports', label: 'Research Reports', to: '/research-intelligence/reports' },
  { key: 'settings', label: 'Research Settings', to: '/research-intelligence/settings' },
]);

/** The nav entry that should be marked current for a path. Longest matching prefix wins; the home entry only matches exactly. */
export const activeNav = (pathname) => {
  const p = String(pathname ?? '').replace(/\/+$/, '') || '/';
  let best = null;
  for (const n of RESEARCH_NAV) {
    const hit = n.key === 'home' ? p === n.to : p === n.to || p.startsWith(`${n.to}/`);
    if (hit && (!best || n.to.length > best.to.length)) best = n;
  }
  return best ? best.key : null;
};

// ---- Discovery -------------------------------------------------------------------------------------
export const SORTS = Object.freeze([['relevance', 'Relevance'], ['date', 'Newest first'], ['citations', 'Most cited']]);
export const PUB_TYPES = Object.freeze([['', 'Any type'], ['article', 'Journal article'], ['preprint', 'Preprint'], ['book', 'Book'], ['conference', 'Conference paper'], ['report', 'Report'], ['web', 'Web page']]);

const intOrNull = (v) => { const n = Number.parseInt(String(v ?? '').trim(), 10); return Number.isFinite(n) ? n : null; };

/** Form state -> request body. Empty fields are left out so the server applies its defaults; a bad year range is reported, not sent. */
export const buildSearchBody = (form, workspaceId) => {
  const errors = [];
  const body = {};
  for (const k of ['query', 'title', 'author', 'doi', 'field']) { const v = String(form[k] ?? '').trim(); if (v) body[k] = v; }
  const yf = intOrNull(form.year_from); const yt = intOrNull(form.year_to);
  if (yf !== null) body.year_from = yf;
  if (yt !== null) body.year_to = yt;
  if (yf !== null && yt !== null && yf > yt) errors.push('The first year must not be after the last year.');
  if (form.type) body.type = form.type;
  if (form.open_access) body.open_access = true;
  if (form.sort) body.sort = form.sort;
  if (Array.isArray(form.providers) && form.providers.length) body.providers = form.providers;
  if (workspaceId) body.workspace_id = workspaceId;
  if (!['query', 'title', 'author', 'doi'].some((k) => body[k])) errors.push('Enter keywords, a title, an author or a DOI.');
  return { body, errors };
};

/** Result-page filters -> query string for GET /discovery/searches/{id}. */
export const resultsQuery = (f) => {
  const q = new URLSearchParams();
  const set = (k, v) => { if (v !== undefined && v !== null && String(v).trim() !== '' && v !== false) q.set(k, String(v)); };
  set('page', f.page ?? 1); set('page_size', f.page_size ?? 20); set('sort', f.sort); set('provider', f.provider);
  set('year_from', f.year_from); set('year_to', f.year_to); set('type', f.type); set('open_access', f.open_access ? 'true' : ''); set('has_abstract', f.has_abstract ? 'true' : ''); set('within', f.within);
  return q.toString();
};

export const PROVIDER_STATE = Object.freeze({ ok: 'Answered', skipped: 'Not used', unavailable: 'Not available', error: 'Failed', rate_limited: 'Waiting (rate limit)', not_configured: 'Needs set-up' });
export const providerState = (s) => PROVIDER_STATE[s] ?? s ?? 'Unknown';

export const authorLine = (rec, max = 4) => {
  const a = (rec?.authors ?? []).filter(Boolean);
  if (a.length === 0) return 'Authors not reported';
  return a.length > max ? `${a.slice(0, max).join(', ')} and ${a.length - max} more` : a.join(', ');
};

/** What the provider actually reported, never filled in. */
export const accessLabel = (rec) => (rec?.oa_url ? 'Open full text reported by the provider' : 'No open full text reported — record and abstract only');
export const citationCountLabel = (rec) => (rec?.citation_count == null ? 'Citation count not reported' : `${rec.citation_count} citations (${rec.citation_source ?? 'provider'})`);
export const canCompare = (ids) => Array.isArray(ids) && ids.length >= 2 && ids.length <= 6;
export const toggleId = (list, id, max = Infinity) => (list.includes(id) ? list.filter((x) => x !== id) : list.length >= max ? list : [...list, id]);

// ---- Analyses --------------------------------------------------------------------------------------
export const ANALYSIS_STATE = Object.freeze({ queued: 'Waiting for the worker', running: 'Running', succeeded: 'Finished', failed: 'Failed', cancelled: 'Cancelled' });
export const analysisState = (s) => ANALYSIS_STATE[s] ?? s;
export const analysisActive = (a) => a?.status === 'queued' || a?.status === 'running';
export const analysisBody = ({ title, question, sourceIds, useModel }, readyIds) => {
  const errors = [];
  const ids = (sourceIds ?? []).filter((x) => !readyIds || readyIds.includes(x));
  if (ids.length < 2) errors.push('Choose at least two ready sources to compare.');
  const body = { source_ids: ids, use_model: Boolean(useModel) };
  if (title?.trim()) body.title = title.trim();
  if (question?.trim()) body.question = question.trim();
  return { body, errors };
};

// ---- Studio ----------------------------------------------------------------------------------------
export const RELATIONS = Object.freeze([['supports', 'Supports'], ['contradicts', 'Contradicts'], ['extends', 'Extends'], ['related', 'Related to']]);
export const relationLabel = (r) => (RELATIONS.find(([k]) => k === r) ?? [r, r])[1];
export const FINDING_KINDS = Object.freeze([['finding', 'Finding'], ['evidence', 'Evidence'], ['highlight', 'Highlight']]);
export const GENERATORS = Object.freeze([
  ['research_questions', 'Research questions', 'Questions the analysed sources leave open, each with the passage it came from.'],
  ['hypotheses', 'Hypotheses to test', 'Possible hypotheses with their quoted basis. They are suggestions, not validated claims.'],
  ['literature_review', 'Literature-review draft', 'A cited draft built from the analysis. Check and rewrite it before use.'],
]);
export const generatorLabel = (k) => (GENERATORS.find((g) => g[0] === k) ?? [k, k])[1];
export const canLink = (from, to) => Boolean(from) && Boolean(to) && from !== to;

/** Outline tree helpers. The server enforces depth <= 3 and <= 200 nodes; the UI refuses early with the same numbers. */
export const OUTLINE_MAX_DEPTH = 3;
export const OUTLINE_MAX_NODES = 200;
export const countNodes = (items) => (items ?? []).reduce((n, i) => n + 1 + countNodes(i.children), 0);
export const outlineDepth = (items) => (items ?? []).reduce((d, i) => Math.max(d, 1 + outlineDepth(i.children)), 0);
export const addOutlineItem = (items, heading, parentIndex = null) => {
  const t = String(heading ?? '').trim();
  if (!t) return { items, error: 'Write the heading first.' };
  if (countNodes(items) >= OUTLINE_MAX_NODES) return { items, error: `An outline can hold ${OUTLINE_MAX_NODES} entries.` };
  const next = JSON.parse(JSON.stringify(items ?? []));
  if (parentIndex === null) { next.push({ heading: t, notes: '', finding_ids: [], children: [] }); return { items: next, error: null }; }
  const parent = next[parentIndex];
  if (!parent) return { items, error: 'Choose a parent entry.' };
  parent.children = [...(parent.children ?? []), { heading: t, notes: '', finding_ids: [], children: [] }];
  if (outlineDepth(next) > OUTLINE_MAX_DEPTH) return { items, error: 'Outlines nest at most three levels.' };
  return { items: next, error: null };
};
export const removeOutlineItem = (items, index) => (items ?? []).filter((_, i) => i !== index);
export const moveOutlineItem = (items, index, dir) => {
  const j = index + dir;
  if (!items || j < 0 || j >= items.length) return items;
  const next = [...items];
  [next[index], next[j]] = [next[j], next[index]];
  return next;
};

// ---- Reports ---------------------------------------------------------------------------------------
export const ORIGIN_TEXT = Object.freeze({
  source: 'Quoted from a source',
  analysis: 'Computed by the analysis (not written by an AI model unless stated)',
  author: 'Written or edited by you',
});
export const originLabel = (o) => ORIGIN_TEXT[o] ?? o;
export const GRADE_TEXT = Object.freeze({ strong: 'Stronger evidence', moderate: 'Moderate evidence', limited: 'Limited evidence', weak: 'Weak evidence', unrated: 'Not rated' });
export const gradeLabel = (g) => GRADE_TEXT[g] ?? g;
export const EXPORT_FORMATS = Object.freeze([['pdf', 'PDF'], ['docx', 'Word (.docx)'], ['pptx', 'PowerPoint (.pptx)'], ['md', 'Markdown']]);

/** Quote and table blocks are evidence: the server refuses edits to them, so the editor offers only removal. */
export const isLocked = (block) => block?.type === 'quote' || block?.type === 'table';
export const isEditable = (block) => block?.type === 'text' || block?.type === 'bullets';
export const blockEmpty = (b) => (b.type === 'text' ? !String(b.text ?? '').trim() : b.type === 'bullets' ? (b.items ?? []).length === 0 : false);

const mapSection = (sections, sid, fn) => sections.map((s) => (s.id === sid ? fn(s) : s));
export const setBlockText = (sections, sid, bid, text) => mapSection(sections, sid, (s) => ({ ...s, blocks: s.blocks.map((b) => (b.id === bid && b.type === 'text' ? { ...b, text } : b)) }));
export const setBlockBullets = (sections, sid, bid, raw) => mapSection(sections, sid, (s) => ({ ...s, blocks: s.blocks.map((b) => (b.id === bid && b.type === 'bullets' ? { ...b, items: String(raw).split('\n').map((x) => x.trim()).filter(Boolean) } : b)) }));
export const removeBlock = (sections, sid, bid) => mapSection(sections, sid, (s) => ({ ...s, blocks: s.blocks.filter((b) => b.id !== bid) }));
export const addTextBlock = (sections, sid) => mapSection(sections, sid, (s) => ({ ...s, blocks: [...s.blocks, { id: `new-${s.blocks.length}-${Date.now()}`, type: 'text', text: '', cite: [] }] }));
export const addSection = (sections, heading) => [...sections, { id: `new-${sections.length}`, heading: String(heading ?? '').trim() || 'New section', blocks: [{ id: 'new-0', type: 'text', text: '', cite: [] }] }];
export const removeSection = (sections, sid) => sections.filter((s) => s.id !== sid);
export const moveSection = (sections, sid, dir) => {
  const i = sections.findIndex((s) => s.id === sid); const j = i + dir;
  if (i < 0 || j < 0 || j >= sections.length) return sections;
  const next = [...sections]; [next[i], next[j]] = [next[j], next[i]]; return next;
};
/** Empty text blocks added by the person are dropped before saving; the server rejects empty text anyway. */
export const cleanForSave = (sections) => sections.map((s) => ({ ...s, blocks: s.blocks.filter((b) => !(String(b.id).startsWith('new-') && blockEmpty(b))) })).filter((s) => s.blocks.length > 0 || !String(s.id).startsWith('new-'));
export const sectionsChanged = (a, b) => JSON.stringify(a) !== JSON.stringify(b);

export const reportCounts = (report) => {
  const c = { source: 0, analysis: 0, author: 0 };
  for (const s of report?.sections ?? []) for (const b of s.blocks ?? []) if (c[b.origin] !== undefined) c[b.origin] += 1;
  return c;
};

/** Citation label list for a block, e.g. "[1], [3]"; empty when none. */
export const citeText = (cite) => (cite && cite.length ? cite.map((c) => `[${c}]`).join(', ') : '');
export const sourceByLabel = (report, label) => (report?.sources ?? []).find((s) => String(s.label) === String(label)) ?? null;

export const downloadName = (filename, fallback) => (filename && filename !== 'download' ? filename : fallback);

export const REPORT_STATE_HINT = 'This report was built from your sources. It has not been peer reviewed or academically validated.';
