import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { ChartView } from '../../components/analytics/Charts.jsx';
import { EvidencePanel } from '../../components/analytics/EvidencePanel.jsx';
import { FilterBar } from '../../components/analytics/FilterBar.jsx';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { useRun } from './useRun.js';

const TYPES = {
  bar: { label: 'Bar: a number by category', x: ['Category', 'categorical'], y: ['Number (optional for counts)', 'numeric', true] },
  histogram: { label: 'Histogram: spread of a number', x: ['Number', 'numeric'] },
  scatter: { label: 'Scatter: two numbers', x: ['X (number)', 'numeric'], y: ['Y (number)', 'numeric'], color: true },
  heatmap: { label: 'Heatmap: correlations or a crosstab', kind: true },
  timeseries: { label: 'Time series: a number over time', x: ['Date column', 'datetime'], y: ['Number', 'numeric', true] },
};

export function ChartsTab({ schema }) {
  const cols = schema.columns;
  const [type, setType] = useState('bar');
  const [f, setF] = useState({ x: '', y: '', color: '', agg: 'mean', top_n: '20', bins: '', kind: 'correlation', method: 'spearman', freq: 'auto', trend_line: true, density: false });
  const [filters, setFilters] = useState([]);
  const [range, setRange] = useState({ from: '', to: '' });
  const [sel, setSel] = useState(null);
  const [problem, setProblem] = useState('');
  const r = useRun();
  const spec = TYPES[type];
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));
  const pick = (role) => cols.filter((c) => c.role === role);

  function build() {
    setProblem(''); setSel(null);
    const params = { type };
    if (spec.x) { if (!f.x) { setProblem(`Choose ${spec.x[0].toLowerCase()}.`); return; } params.x = f.x; }
    if (spec.y) { if (!f.y && !spec.y[2]) { setProblem(`Choose ${spec.y[0].toLowerCase()}.`); return; } if (f.y) params.y = f.y; }
    if (type === 'bar') { params.agg = f.y ? f.agg : 'count'; params.top_n = Number(f.top_n) || 20; }
    if (type === 'timeseries') { params.agg = f.y ? f.agg : 'count'; params.freq = f.freq; params.trend_line = f.trend_line; }
    if (type === 'histogram') { params.density = f.density; if (f.bins) params.bins = Number(f.bins); }
    if (type === 'scatter') { params.trend_line = f.trend_line; if (f.color) params.color = f.color; }
    if (type === 'heatmap') { params.kind = f.kind; if (f.kind === 'correlation') params.method = f.method; else { params.x = f.x; params.y = f.y; if (f.x && f.y && f.value) params.value = f.value; } }
    const all = [...filters];
    if (range.from || range.to) {
      const dateCol = type === 'timeseries' ? f.x : '';
      if (!dateCol) { setProblem('A date range applies to time-series charts. Use the filters for other charts.'); return; }
      if (range.from && range.to && range.from > range.to) { setProblem('The start date is after the end date.'); return; }
      if (range.from) all.push({ column: dateCol, op: 'ge', value: range.from });
      if (range.to) all.push({ column: dateCol, op: 'le', value: range.to });
    }
    if (type === 'heatmap' && f.kind === 'crosstab' && (!f.x || !f.y)) { setProblem('A crosstab needs two categorical columns.'); return; }
    r.start(() => analytics.run(schema.asset_id, { kind: 'chart', operations: [{ op: 'chart', params }], filters: all, version_id: schema.version_id }));
  }
  const chart = r.run?.result?.results?.[0];
  const select = (s, label) => setSel({ s, label });

  return (
    <div className="stack">
      <form className="card form-grid" onSubmit={(e) => { e.preventDefault(); build(); }} aria-label="Chart builder">
        <Field as="select" label="Chart type" name="type" value={type} onChange={(e) => { setType(e.target.value); setF((s) => ({ ...s, x: '', y: '', color: '' })); }}>
          {Object.entries(TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </Field>
        {type === 'heatmap' ? (
          <>
            <Field as="select" label="Show" name="kind" value={f.kind} onChange={set('kind')}><option value="correlation">Correlations between numbers</option><option value="crosstab">Counts across two categories</option></Field>
            {f.kind === 'correlation' ? <Field as="select" label="Correlation method" name="method" value={f.method} onChange={set('method')}><option value="spearman">Spearman (rank)</option><option value="pearson">Pearson (linear)</option><option value="kendall">Kendall</option></Field> : (
              <>
                <Field as="select" label="Rows" name="x" value={f.x} onChange={set('x')}><option value="">Choose…</option>{pick('categorical').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field>
                <Field as="select" label="Columns" name="y" value={f.y} onChange={set('y')}><option value="">Choose…</option>{pick('categorical').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field>
              </>
            )}
          </>
        ) : null}
        {spec.x ? <Field as="select" label={spec.x[0]} name="x" value={f.x} onChange={set('x')}><option value="">Choose…</option>{pick(spec.x[1]).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field> : null}
        {spec.y ? <Field as="select" label={spec.y[0]} name="y" value={f.y} onChange={set('y')}><option value="">{spec.y[2] ? 'Count rows' : 'Choose…'}</option>{pick(spec.y[1]).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field> : null}
        {(type === 'bar' || type === 'timeseries') && f.y ? <Field as="select" label="Aggregation" name="agg" value={f.agg} onChange={set('agg')}>{['mean', 'sum', 'median', 'min', 'max'].map((a) => <option key={a} value={a}>{a}</option>)}</Field> : null}
        {type === 'bar' ? <Field label="Show top" name="top_n" inputMode="numeric" value={f.top_n} onChange={set('top_n')} hint="1 to 100 categories." /> : null}
        {type === 'histogram' ? <Field label="Bins (optional)" name="bins" inputMode="numeric" value={f.bins} onChange={set('bins')} hint="2 to 200. Blank picks a rule-based number." /> : null}
        {type === 'scatter' ? <Field as="select" label="Colour by (optional)" name="color" value={f.color} onChange={set('color')}><option value="">None</option>{pick('categorical').map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}</Field> : null}
        {type === 'timeseries' ? (
          <>
            <Field as="select" label="Period" name="freq" value={f.freq} onChange={set('freq')}>{['auto', 'day', 'week', 'month', 'quarter', 'year'].map((a) => <option key={a} value={a}>{a}</option>)}</Field>
            <Field label="From (date)" name="from" type="date" value={range.from} onChange={(e) => setRange((s) => ({ ...s, from: e.target.value }))} />
            <Field label="To (date)" name="to" type="date" value={range.to} onChange={(e) => setRange((s) => ({ ...s, to: e.target.value }))} />
          </>
        ) : null}
        {type === 'timeseries' || type === 'scatter' ? <label className="check"><input type="checkbox" checked={f.trend_line} onChange={set('trend_line')} /> Show trend overlay</label> : null}
        {type === 'histogram' ? <label className="check"><input type="checkbox" checked={f.density} onChange={set('density')} /> Show density instead of counts</label> : null}
        <FilterBar columns={cols} filters={filters} onChange={setFilters} />
        {problem ? <p className="field-error" role="alert">{problem}</p> : null}
        <div><button type="submit" className="btn" disabled={r.busy}>{r.busy ? 'Drawing…' : 'Draw chart'}</button></div>
      </form>
      {r.error ? <p className="message message--error" role="alert">{r.error}</p> : null}
      {chart && chart.status === 'failed' ? <p className="message message--error" role="alert">{chart.error?.message}</p> : null}
      {chart && chart.status === 'not_run' ? <p className="message message--warn" role="status"><strong>Not enough evidence.</strong> {chart.warnings?.[0] || 'There is not enough data for this chart.'}</p> : null}
      {chart?.status === 'ok' ? (
        <>
          <ChartView chart={chart.data} selected={sel?.s} onSelect={(s, label) => select(s, label)} />
          <p className="muted">Hover for values. Drag on a scatter or time series to select a range, or click a bar, cell or bin, to see the rows behind it.</p>
          {chart.warnings?.length ? <ul className="warnings">{chart.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
          {sel ? <RowsPanel runId={r.run.id} select={sel.s} label={sel.label} onClose={() => setSel(null)} /> : null}
          <EvidencePanel run={r.run} onOpenRows={(s, l) => select(s, l)} onRerun={r.replace} onSaved={(s) => r.replace({ ...r.run, ...s })} />
        </>
      ) : null}
    </div>
  );
}
