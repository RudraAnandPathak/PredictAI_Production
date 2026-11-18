// Pure helpers for the Notebook screens. They format what the server returned and check form input; the server decides what an output may say, which sources it used and what left the platform.

export const MAX_INSTRUCTIONS = 600;
const CITE = /\[(S\d{1,3})\]/g;

/** Split one line into plain, bold and citation parts. Nothing is ever treated as HTML. */
export function inlineParts(line) {
  const out = [];
  String(line ?? '').split(/(\*\*[^*]+\*\*)/g).forEach((chunk) => {
    if (!chunk) return;
    const bold = chunk.startsWith('**') && chunk.endsWith('**') && chunk.length > 4;
    const text = bold ? chunk.slice(2, -2) : chunk;
    let last = 0;
    for (const m of text.matchAll(CITE)) {
      if (m.index > last) out.push({ type: bold ? 'bold' : 'text', value: text.slice(last, m.index) });
      out.push({ type: 'cite', value: m[1] });
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ type: bold ? 'bold' : 'text', value: text.slice(last) });
  });
  return out;
}

/** Markdown from the server as simple blocks: headings, bullets, numbered items and paragraphs. */
export function blocks(markdown) {
  const out = [];
  for (const raw of String(markdown ?? '').split('\n')) {
    const line = raw.trimEnd();
    if (!line.trim()) continue;
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { out.push({ type: 'heading', level: Math.min(h[1].length + 1, 5), parts: inlineParts(h[2]) }); continue; }
    const li = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (li) { out.push({ type: 'item', parts: inlineParts(li[1]) }); continue; }
    out.push({ type: 'paragraph', parts: inlineParts(line.trim().replace(/^_(.*)_$/, '$1')), note: /^_.*_$/.test(line.trim()) });
  }
  return out;
}

/** The citation entry for a label, in the shape the evidence panel opens. */
export function citeTarget(output, label) {
  const c = output?.citations?.[label];
  return c ? { chunk_id: c.chunk_id, source_id: c.source_id, source_title: c.source_title, locator: c.locator } : null;
}

export function modeNote(output) {
  if (!output) return '';
  if (output.kind === 'deep_research') return output.mode === 'model' ? `Searches planned by ${output.model ?? 'an AI model'}; results came from the search providers.` : 'No AI model planned the searches, so your question was searched as written.';
  if (output.mode === 'model') return `Written by ${output.model ?? 'an AI model'} from your sources. Every [S] mark opens the passage it came from; check it.`;
  return 'Extract only: no AI model answered, so this lists passages from your sources instead of a written output.';
}

export function warnings(output) {
  const m = output?.meta ?? {};
  const out = [];
  if (m.warning) out.push(m.warning);
  if (output?.mode === 'model' && m.uncited_lines > 0) out.push(`${m.uncited_lines} line(s) state something without a source mark. Treat those as unchecked.`);
  if (output?.mode === 'extractive') for (const r of m.reasons ?? []) out.push(r);
  return out;
}

export function studioProblem({ kind, instructions, readySources }) {
  if (!kind) return 'Choose what to make.';
  if (readySources < 1) return 'Add a source and wait until it has been read.';
  if ((instructions ?? '').length > MAX_INSTRUCTIONS) return `Write up to ${MAX_INSTRUCTIONS} characters.`;
  return null;
}

export const readySourceCount = (sources) => (sources ?? []).filter((s) => s.status === 'ready' && s.included).length;

// ------------------------------------------------------------------ structured outputs (Stage 2 and 3)
export const LETTERS = ['A', 'B', 'C', 'D'];
export const MAX_QUESTION = 400;
export const MAX_DEEP_SOURCES = 8;

/** Score a quiz from { questionIndex: chosenOptionIndex }. Unanswered questions count as wrong. */
export function quizScore(questions, answers) {
  const total = (questions ?? []).length;
  const right = (questions ?? []).filter((q, i) => answers?.[i] === q.answer).length;
  return { right, total, answered: Object.keys(answers ?? {}).length, percent: total ? Math.round((100 * right) / total) : 0 };
}

/** Next flashcard index, wrapping around; a negative step goes back. */
export function nextCard(index, step, count) {
  if (!count) return 0;
  return (((index + step) % count) + count) % count;
}

/** A mind map as rows to indent: [{ label, depth, sources, path }]. Each path is unique, so it can be a React key and a fold key. */
export function mindMapRows(map, folded = new Set()) {
  const out = [];
  const walk = (nodes, depth, prefix) => (nodes ?? []).forEach((n, i) => {
    const path = `${prefix}${i}`;
    out.push({ label: n.label, depth, sources: n.sources ?? [], path, hasChildren: (n.children ?? []).length > 0, folded: folded.has(path) });
    if (!folded.has(path)) walk(n.children, depth + 1, `${path}.`);
  });
  if (map) walk(map.children, 1, '');
  return out;
}

/** Two different voices for the two hosts when the browser offers them; otherwise the same voice at two pitches. */
export function pickVoices(voices, lang = 'en') {
  const mine = (voices ?? []).filter((v) => (v.lang ?? '').toLowerCase().startsWith(lang));
  const pool = mine.length ? mine : (voices ?? []);
  return { A: pool[0] ?? null, B: pool[1] ?? pool[0] ?? null, samePitchShift: pool.length < 2 };
}

/** The audio script as speakable lines: text only, no source marks. */
export function speechLines(script) {
  return (script?.lines ?? []).map((l) => ({ speaker: l.speaker === 'B' ? 'B' : 'A', text: String(l.text ?? '').replace(/\[S\d{1,3}\]/g, '').trim() })).filter((l) => l.text);
}

export function deepProblem({ question, maxSources }) {
  const q = (question ?? '').trim();
  if (!q) return 'Ask a question to research.';
  if (q.length > MAX_QUESTION) return `Keep the question under ${MAX_QUESTION} characters.`;
  const n = Number(maxSources);
  if (!Number.isInteger(n) || n < 1 || n > MAX_DEEP_SOURCES) return `Choose between 1 and ${MAX_DEEP_SOURCES} sources.`;
  return null;
}

export const EXPORT_LABEL = Object.freeze({ markdown: 'Markdown', csv: 'CSV', pptx: 'PowerPoint' });

export function importLines(out) {
  if (!out) return [];
  const lines = [`${out.imported.length} report(s) copied into notebooks.`];
  for (const f of out.failed ?? []) lines.push(`Not copied: ${f.title ?? f.session_id} (${f.reason}).`);
  if (out.remaining > 0) lines.push(`${out.remaining} still to copy; run the import again.`);
  return lines;
}
