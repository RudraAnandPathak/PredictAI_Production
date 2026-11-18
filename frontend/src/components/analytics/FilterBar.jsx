import { useState } from 'react';
import { buildFilter, describeFilter, FILTER_OPS } from '../../features/analytics/format.js';

/** Row filters sent to the server as typed objects (never as text to be evaluated). Every analysis records the filters it used. */
export function FilterBar({ columns, filters, onChange, label = 'Filters' }) {
  const [adding, setAdding] = useState(false);
  const [col, setCol] = useState('');
  const [op, setOp] = useState('eq');
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [error, setError] = useState('');
  const role = columns.find((c) => c.name === col)?.role ?? 'categorical';
  const ops = FILTER_OPS[role] ?? FILTER_OPS.categorical;

  function add() {
    const res = buildFilter(col, role, op, a, b);
    if (res.error) { setError(res.error); return; }
    onChange([...filters, res.filter]);
    setAdding(false); setCol(''); setA(''); setB(''); setError('');
  }
  return (
    <div className="filterbar" role="group" aria-label={label}>
      <span className="filterbar__label">{label}</span>
      {filters.length === 0 && !adding ? <span className="muted">All rows</span> : null}
      {filters.map((f, i) => (
        <span className="chip" key={i}>{describeFilter(f)}<button type="button" aria-label={`Remove filter ${describeFilter(f)}`} onClick={() => onChange(filters.filter((_, j) => j !== i))}>×</button></span>
      ))}
      {!adding ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAdding(true)} disabled={filters.length >= 20}>Add filter</button> : (
        <form className="filterbar__form" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <select aria-label="Filter column" value={col} onChange={(e) => { setCol(e.target.value); const r = columns.find((c) => c.name === e.target.value)?.role ?? 'categorical'; setOp((FILTER_OPS[r] ?? FILTER_OPS.categorical)[0][0]); setError(''); }}>
            <option value="">Column…</option>{columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
          <select aria-label="Filter condition" value={op} onChange={(e) => setOp(e.target.value)}>{ops.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          {op !== 'is_null' && op !== 'not_null' ? <input aria-label="Filter value" value={a} onChange={(e) => setA(e.target.value)} placeholder={role === 'datetime' ? 'YYYY-MM-DD' : op === 'in' ? 'a, b, c' : 'value'} /> : null}
          {op === 'between' ? <input aria-label="Filter upper value" value={b} onChange={(e) => setB(e.target.value)} placeholder={role === 'datetime' ? 'YYYY-MM-DD' : 'upper'} /> : null}
          <button type="submit" className="btn btn--sm">Apply</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setAdding(false); setError(''); }}>Cancel</button>
          {error ? <span className="field-error" role="alert">{error}</span> : null}
        </form>
      )}
    </div>
  );
}
