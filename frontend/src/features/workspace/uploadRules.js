// Client-side upload checks. They only give quick feedback: the server repeats every check and is the authority.
export const DATASET_EXTENSIONS = ['csv', 'tsv', 'xlsx', 'xls', 'ods', 'json', 'xml'];
export const DOCUMENT_EXTENSIONS = ['pdf', 'docx', 'rtf', 'txt', 'md', 'markdown'];
export const SUPPORTED_EXTENSIONS = [...DATASET_EXTENSIONS, ...DOCUMENT_EXTENSIONS];
export const ACCEPT = SUPPORTED_EXTENSIONS.map((e) => `.${e}`).join(',');
export const SUPPORTED_HINT = 'Datasets: CSV, XLSX, XLS, ODS, JSON, XML. Documents: PDF, DOCX, RTF, TXT, Markdown.';

export function extensionOf(name) {
  const text = String(name ?? '');
  const dot = text.lastIndexOf('.');
  return dot > 0 ? text.slice(dot + 1).toLowerCase() : '';
}

export const kindOfName = (name) => (DOCUMENT_EXTENSIONS.includes(extensionOf(name)) ? 'document' : 'dataset');

/** Returns a plain-language problem, or null when the file can be sent. `limits` = { maxBytes }. */
export function checkFile(file, limits = {}) {
  if (!file || typeof file.name !== 'string') return 'That item is not a file.';
  const ext = extensionOf(file.name);
  if (!ext) return `“${file.name}” has no file extension. ${SUPPORTED_HINT}`;
  if (!SUPPORTED_EXTENSIONS.includes(ext)) return `“${file.name}” is a .${ext} file, which PredictAI cannot read. ${SUPPORTED_HINT}`;
  if (file.size === 0) return `“${file.name}” is empty. Check that the export finished and try again.`;
  if (limits.maxBytes && file.size > limits.maxBytes) {
    return `“${file.name}” is larger than the ${Math.round(limits.maxBytes / (1024 * 1024))} MB limit. Split it or remove data you do not need.`;
  }
  return null;
}

export function chunkRanges(size, chunkSize) {
  const ranges = [];
  for (let start = 0; start < size; start += chunkSize) ranges.push([start, Math.min(start + chunkSize, size)]);
  return ranges;
}
