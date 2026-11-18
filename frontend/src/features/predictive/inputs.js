// The prediction form is built from the model's own record of its inputs (input_schema), so it can only ask for what the model uses.

/** One field description per model input. `levels` present = a pick list. */
export function fieldsFromSchema(schema = []) {
  return schema.map((s) => {
    const base = { name: s.name, role: s.role, missingShare: s.missing_share ?? 0 };
    if (s.role === 'numeric') {
      return { ...base, kind: 'number', min: s.min, max: s.max, median: s.median, integerOnly: Boolean(s.integer_only),
               hint: s.min !== undefined && s.max !== undefined ? `Seen in training: ${s.min} to ${s.max}` : '' };
    }
    if (s.role === 'datetime') return { ...base, kind: 'date', min: s.min, max: s.max, hint: s.min ? `Seen in training: ${String(s.min).slice(0, 10)} to ${String(s.max).slice(0, 10)}` : 'Use the format YYYY-MM-DD' };
    if (Array.isArray(s.levels) && s.levels.length) return { ...base, kind: 'choice', levels: s.levels, mostCommon: s.most_common, hint: `${s.level_count ?? s.levels.length} categories seen in training` };
    return { ...base, kind: 'text', mostCommon: s.most_common, hint: s.level_count ? `${s.level_count} different values seen in training` : '' };
  });
}

/** Leaves blank fields out entirely, so the server treats them as "not given" (and says so) instead of receiving an empty-string value. */
export function valuesPayload(fields, entered) {
  const out = {};
  for (const f of fields) {
    const raw = entered?.[f.name];
    if (raw === undefined || raw === null) continue;
    const text = String(raw).trim();
    if (text === '') continue;
    if (f.kind === 'number') {
      const n = Number(text);
      out[f.name] = Number.isFinite(n) ? n : text;        // a non-number is sent as typed; the server reports it against that input
    } else out[f.name] = text;
  }
  return out;
}

/** Instant hints only: the server decides. Returns { field: message }. */
export function checkEntered(fields, entered) {
  const errors = {};
  let any = false;
  for (const f of fields) {
    const raw = entered?.[f.name];
    const text = raw === undefined || raw === null ? '' : String(raw).trim();
    if (text === '') continue;
    any = true;
    if (f.kind === 'number' && !Number.isFinite(Number(text))) errors[f.name] = 'Enter a number.';
    if (f.kind === 'number' && f.integerOnly && Number.isFinite(Number(text)) && !Number.isInteger(Number(text))) errors[f.name] = 'This input only took whole numbers in training.';
    if (f.kind === 'date' && !/^\d{4}-\d{2}-\d{2}/.test(text)) errors[f.name] = 'Use the format YYYY-MM-DD.';
  }
  if (!any) errors._form = 'Enter at least one value.';
  return errors;
}

/** A starting point people can edit: each number at its training median, each category at its most common value. Marked as typical values, never as a real case. */
export function typicalValues(fields) {
  const out = {};
  for (const f of fields) {
    if (f.kind === 'number' && f.median !== undefined && f.median !== null) out[f.name] = String(f.median);
    else if ((f.kind === 'choice' || f.kind === 'text') && f.mostCommon !== undefined) out[f.name] = String(f.mostCommon);
  }
  return out;
}

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 };
export function sortedIssues(issues = []) {
  return [...issues].sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3));
}
