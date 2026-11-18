// Pure helpers: what to say about a source, an answer and a limit. No React, no network, so they are unit-tested.
// Principle: say only what the server reported. A lexical index is never described as "semantic"; an extractive answer is never described as written by a model.

export const SOURCE_STATUS = Object.freeze({
  queued: { label: 'Waiting', tone: 'pending', hint: 'Waiting for the research worker to pick it up. If this stays for long, the worker (scripts/run_research_worker.py) is not running.' },
  processing: { label: 'Processing', tone: 'architectural', hint: 'Being read and indexed.' },
  ready: { label: 'Ready', tone: 'existing', hint: '' },
  failed: { label: 'Failed', tone: 'planned', hint: '' },
  cancelled: { label: 'Cancelled', tone: 'planned', hint: 'Processing was cancelled. You can retry it.' },
});

export const statusInfo = (source) => {
  const base = SOURCE_STATUS[source?.status] ?? { label: String(source?.status ?? 'Unknown'), tone: 'planned', hint: '' };
  const hint = source?.status === 'failed' && source.error_message ? source.error_message : base.hint;
  const detail = source?.status === 'processing' && source.stage ? source.stage : null;
  return { ...base, hint, detail };
};

export const ACCESS_NOTE = Object.freeze({
  full_text: null,
  abstract_only: 'Only the abstract and bibliographic data are available, so answers can cite the abstract only.',
  metadata_only: 'Only bibliographic data is available (no abstract, no full text). There is nothing to search or cite.',
});
export const accessNote = (source) => ACCESS_NOTE[source?.access] ?? null;

export const embeddingNote = (source) => {
  if (!source || source.status !== 'ready' || source.chunk_count === 0) return null;
  if (source.embed_status === 'failed') return 'Meaning-based indexing failed; keyword search still works.';
  if (source.embedding_kind === 'semantic') return `Meaning-based matching (${source.embed_model}).`;
  if (source.embedding_kind === 'lexical') return 'Matches shared words only (built-in index). Ask the platform owner to enable an embedding model for meaning-based matching.';
  return null;
};

export const sourceWarnings = (source) => {
  const out = [];
  if (!source) return out;
  if (source.injection_flags > 0) out.push(`${source.injection_flags} passage(s) contain text that looks like instructions to an AI. They are treated as plain text and never followed.`);
  if (source.malware === 'not_scanned') out.push('Not scanned for malware (no scanner is installed on this server).');
  if (source.malware === 'infected') out.push('A malware scan flagged this file.');
  return out;
};

export const FORMAT_LABEL = Object.freeze({ pdf: 'PDF', docx: 'Word', txt: 'Text', markdown: 'Markdown', csv: 'CSV', xlsx: 'Excel', html: 'Web page', metadata: 'Record' });
export const ORIGIN_LABEL = Object.freeze({ upload: 'Uploaded', url: 'Web address', doi: 'DOI', arxiv: 'arXiv', discovery: 'Discovery' });

export const SUPPORTED_EXTENSIONS = Object.freeze(['.pdf', '.docx', '.txt', '.md', '.markdown', '.csv', '.xlsx']);
export const ACCEPT_ATTRIBUTE = SUPPORTED_EXTENSIONS.join(',');

export const checkFile = (file, maxMb) => {
  if (!file || !file.name) return { ok: false, reason: 'Choose a file.' };
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!SUPPORTED_EXTENSIONS.includes(ext)) return { ok: false, reason: `“${ext || 'This file'}” is not supported. Use PDF, DOCX, TXT, Markdown, CSV or XLSX.` };
  if (file.size === 0) return { ok: false, reason: 'The file is empty.' };
  if (maxMb && file.size > maxMb * 1024 * 1024) return { ok: false, reason: `This file is larger than your ${maxMb} MB limit.` };
  return { ok: true, reason: null };
};

export const formatBytes = (n) => {
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};

export const looksLikeReference = (text) => {
  const t = String(text ?? '').trim();
  return /^https?:\/\/\S+$/i.test(t) || /^(doi:\s*)?10\.\d{4,9}\/\S+$/i.test(t) || /^(arxiv:\s*)?\d{4}\.\d{4,5}(v\d+)?$/i.test(t) || /^(https?:\/\/)?(dx\.)?doi\.org\/10\.\S+$/i.test(t);
};

/** Limits as short sentences, showing the real numbers the server enforces. */
export const limitLines = (limits) => {
  if (!limits) return [];
  const lines = [
    `Workspaces: ${limits.workspaces_used} of ${limits.max_workspaces}`,
    `Sources per workspace: up to ${limits.max_sources_per_workspace}`,
    `File size: up to ${limits.max_upload_mb} MB`,
    `Imports today: ${limits.imports_today} of ${limits.imports_per_day}`,
    `Questions today: ${limits.ai_requests_today} of ${limits.ai_requests_per_day}`,
  ];
  return lines;
};

export const nearLimit = (used, limit) => Number.isFinite(used) && Number.isFinite(limit) && limit > 0 && used / limit >= 0.8;

export const ANSWER_MODE = Object.freeze({
  model: 'Written by an AI model, from your sources',
  extractive: 'Direct quotes from your sources — no AI model wrote this',
  insufficient_evidence: 'Not enough evidence in the selected sources',
});

/** The honest one-paragraph description of how an answer was produced. */
export const answerNote = (answer) => {
  if (!answer) return null;
  const parts = [ANSWER_MODE[answer.mode] ?? answer.mode];
  if (answer.mode === 'model') {
    parts.push(`${answer.provider}${answer.model ? ` · ${answer.model}` : ''}`);
    if (answer.external) parts.push('parts of your documents were sent to an external provider (allowed by this workspace)');
    if (answer.removed_unsupported > 0) parts.push(`${answer.removed_unsupported} sentence(s) without support in your sources were removed`);
  }
  const r = answer.retrieval ?? {};
  if (typeof r.passages_used === 'number') parts.push(`${r.passages_used} passage(s) from ${r.sources_searched} source(s)`);
  if (r.flagged_passages > 0) parts.push(`${r.flagged_passages} passage(s) contained instruction-like text`);
  return parts.join(' · ');
};

/** Split an answer into text and citation markers. [3] becomes a link only when citation 3 exists, so a stray bracket never points at nothing. */
export const answerSegments = (body, citations) => {
  const byLabel = new Map((citations ?? []).map((c) => [String(c.label), c]));
  const out = [];
  let last = 0;
  for (const m of String(body ?? '').matchAll(/\[(\d{1,2})\]/g)) {
    const cite = byLabel.get(m[1]);
    if (!cite) continue;
    if (m.index > last) out.push({ kind: 'text', text: body.slice(last, m.index) });
    out.push({ kind: 'cite', label: m[1], citation: cite });
    last = m.index + m[0].length;
  }
  if (last < String(body ?? '').length) out.push({ kind: 'text', text: body.slice(last) });
  return out;
};

export const citationWhere = (c) => {
  const place = c.page ? `page ${c.page}` : c.locator;
  return `${c.source_title ?? 'Source'}${place ? `, ${place}` : ''}`;
};

export const POLICY_TEXT = Object.freeze({
  local_only: 'Documents stay on this computer. Only AI models running here may read them; otherwise answers are direct quotes.',
  external_allowed: 'Passages from your documents may be sent to the external AI provider the platform owner enabled when you ask a question.',
});

export const policyChangeBody = (policy) => (policy === 'external_allowed' ? { ai_data_policy: policy, confirm: true } : { ai_data_policy: policy, confirm: false });

export const workerWaiting = (sources) => (sources ?? []).some((s) => s.status === 'queued');
export const hasActive = (sources) => (sources ?? []).some((s) => s.status === 'queued' || s.status === 'processing');
export const readyCount = (sources) => (sources ?? []).filter((s) => s.status === 'ready' && s.included && s.chunk_count > 0).length;
