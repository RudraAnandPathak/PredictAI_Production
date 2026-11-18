// Pure helpers for presenting extraction results (kept free of React so they can be unit-tested).
export const TYPE_LABEL = {
  integer: 'Whole number', float: 'Decimal number', boolean: 'Yes / no', date: 'Date', datetime: 'Date and time',
  text: 'Text', mixed: 'Mixed', empty: 'Empty',
};

export const METHOD_LABEL = { text: 'Text layer', ocr: 'Read with OCR', none: 'No text found' };

export function typeLabel(type) {
  return TYPE_LABEL[type] ?? type;
}

export function confidenceTone(confidence) {
  if (confidence == null) return null;
  if (confidence >= 85) return 'badge--ok';
  if (confidence >= 60) return 'badge--warn';
  return 'badge--danger';
}

export function describePage(page) {
  const method = METHOD_LABEL[page.method] ?? page.method;
  return page.method === 'ocr' && page.ocr_confidence != null ? `${method} (${Math.round(page.ocr_confidence)}% confidence)` : method;
}

/** Clamp a pager position: returns { offset, hasPrev, hasNext, from, to } for 0-based offsets. */
export function pageWindow(offset, limit, total) {
  const safeTotal = Math.max(0, Number(total) || 0);
  const start = Math.min(Math.max(0, offset), Math.max(0, safeTotal - 1));
  const end = Math.min(start + limit, safeTotal);
  return { offset: start, hasPrev: start > 0, hasNext: end < safeTotal, from: safeTotal ? start + 1 : 0, to: end };
}

export function columnQuality(column, rows) {
  if (!rows) return null;
  const filled = Math.max(0, rows - (column.missing ?? 0));
  return { filled, filledPct: rows ? Math.round((filled / rows) * 100) : 0 };
}
