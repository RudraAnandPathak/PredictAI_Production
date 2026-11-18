// Pure helpers behind the cleaning step builder. Mirrors the server's rules so the form can guide people, but the server always validates.
export const OPS = {
  drop_duplicates: { label: 'Remove duplicate rows', help: 'Keeps the first of each repeated row.' },
  fill_missing: { label: 'Fill empty values', help: 'Replaces empty cells in one column.' },
  drop_missing: { label: 'Remove rows with empty values', help: 'Drops rows that have empty cells.' },
  convert_type: { label: 'Fix a column’s type', help: 'Converts values to numbers, yes/no or dates.' },
  trim_whitespace: { label: 'Trim extra spaces', help: 'Removes spaces at the start and end of values.' },
  change_case: { label: 'Change upper/lower case', help: 'Makes text lower, upper or title case.' },
  rename_column: { label: 'Rename a column', help: 'Only the name changes.' },
  drop_columns: { label: 'Remove columns', help: 'Deletes the chosen columns from the new version.' },
  filter_rows: { label: 'Keep or remove rows by a condition', help: 'For example keep rows where score is at least 50.' },
};
export const OP_ORDER = Object.keys(OPS);

export const FILL = { constant: 'a fixed value', mean: 'the mean', median: 'the median', mode: 'the most common value' };
export const CONVERT = { integer: 'Whole number', float: 'Decimal number', boolean: 'Yes / no', date: 'Date' };
export const DATE_FORMATS = { iso: 'Year-month-day (2024-12-31)', dmy: 'Day/month/year (31/12/2024)', mdy: 'Month/day/year (12/31/2024)' };
export const FILTERS = { eq: 'equals', ne: 'does not equal', gt: 'is greater than', gte: 'is at least', lt: 'is less than', lte: 'is at most',
  contains: 'contains', not_contains: 'does not contain', is_empty: 'is empty', not_empty: 'is not empty' };
export const CASES = { lower: 'lower case', upper: 'UPPER CASE', title: 'Title Case' };

export const MULTI = new Set(['drop_columns', 'change_case']);          // columns are required
export const OPTIONAL_MULTI = new Set(['drop_missing', 'trim_whitespace', 'drop_duplicates']); // empty = every column

export function defaultStep(op) {
  switch (op) {
    case 'fill_missing': return { op, column: '', strategy: 'median', value: '' };
    case 'drop_missing': return { op, columns: [], how: 'any' };
    case 'convert_type': return { op, column: '', to: 'float', on_invalid: 'empty', date_format: 'iso' };
    case 'rename_column': return { op, column: '', new_name: '' };
    case 'drop_columns': return { op, columns: [] };
    case 'filter_rows': return { op, column: '', filter: 'eq', value: '', action: 'keep' };
    case 'change_case': return { op, columns: [], case: 'lower' };
    default: return { op, columns: [] };
  }
}

/** Column names as they stand just before step `index` (earlier renames and removals applied). */
export function columnsBefore(steps, base, index) {
  let names = [...base];
  for (const step of steps.slice(0, index)) {
    if (step.op === 'rename_column' && step.column && step.new_name?.trim()) names = names.map((n) => (n === step.column ? step.new_name.trim() : n));
    if (step.op === 'drop_columns') names = names.filter((n) => !(step.columns ?? []).includes(n));
  }
  return names;
}

/** What the form knows is missing for a step ('' when it looks complete). */
export function stepProblem(step, available) {
  const has = (n) => available.includes(n);
  switch (step.op) {
    case 'fill_missing':
      if (!has(step.column)) return 'Choose a column.';
      return step.strategy === 'constant' && !String(step.value ?? '').length ? 'Enter the value to fill with.' : '';
    case 'convert_type': return has(step.column) ? '' : 'Choose a column.';
    case 'rename_column':
      if (!has(step.column)) return 'Choose a column.';
      return step.new_name?.trim() ? '' : 'Enter the new name.';
    case 'filter_rows':
      if (!has(step.column)) return 'Choose a column.';
      if (!['is_empty', 'not_empty'].includes(step.filter) && !String(step.value ?? '').length) return 'Enter a value to compare with.';
      return ['gt', 'gte', 'lt', 'lte'].includes(step.filter) && Number.isNaN(Number(step.value)) ? 'This condition needs a number.' : '';
    case 'drop_columns': case 'change_case':
      return (step.columns ?? []).length ? '' : 'Choose at least one column.';
    default: return '';
  }
}

/** The API payload for one step (only the fields that step uses). */
export function toOperation(step) {
  const { op } = step;
  const cols = step.columns?.length ? { columns: step.columns } : {};
  switch (op) {
    case 'drop_duplicates': return { op, ...cols };
    case 'trim_whitespace': return { op, ...cols };
    case 'drop_missing': return { op, how: step.how, ...cols };
    case 'fill_missing': return { op, column: step.column, strategy: step.strategy, ...(step.strategy === 'constant' ? { value: step.value } : {}) };
    case 'convert_type': return { op, column: step.column, to: step.to, on_invalid: step.on_invalid, ...(step.to === 'date' ? { date_format: step.date_format } : {}) };
    case 'rename_column': return { op, column: step.column, new_name: step.new_name.trim() };
    case 'drop_columns': return { op, columns: step.columns };
    case 'change_case': return { op, columns: step.columns, case: step.case };
    case 'filter_rows': return { op, column: step.column, filter: step.filter, action: step.action, ...(['is_empty', 'not_empty'].includes(step.filter) ? {} : { value: step.value }) };
    default: return { op };
  }
}

/** Turn an operation from the API (suggestions, history) back into an editable step. */
export function fromOperation(operation) {
  return { ...defaultStep(operation.op), ...operation, value: operation.value ?? '', new_name: operation.new_name ?? '', columns: operation.columns ?? [] };
}

export function describeVersion(v) {
  return v.kind === 'original' ? `Version ${v.number} — the original upload` : `Version ${v.number} — cleaned`;
}
