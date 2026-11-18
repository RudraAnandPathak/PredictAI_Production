import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { EvidencePanel } from '../../components/analytics/EvidencePanel.jsx';
import { FilterBar } from '../../components/analytics/FilterBar.jsx';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { RunResults } from '../../components/analytics/ResultViews.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { useRun } from './useRun.js';

const KINDS = [
  ['compare_groups', 'Do groups differ? (a number by a category)'],
  ['association', 'Are two categories related?'],
  ['correlation', 'Are two numbers related?'],
  ['paired', 'Did the same rows change between two numbers?'],
];

/** Hypothesis testing: the user states a question, the server chooses and checks the test, and shows assumptions, effect size and uncertainty. */
export function TestsTab({ schema }) {
  const cols = schema.columns;
  const numeric = cols.filter((c) => c.role === 'numeric');
  const cats = cols.filter((c) => c.role === 'categorical');
  const [kind, setKind] = useState('compare_groups');
  const [f, setF] = useState({ outcome: '', group: '', second: '', alpha: '0.05', alternative: 'two-sided', prefer: 'auto', reference_group: '' });
  const [filters, setFilters] = useState([]);
  const [rows, setRows] = useState(null);
  const [problem, setProblem] = useState('');
  const r = useRun();
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const needs = {
    compare_groups: { outcome: ['Number to compare', numeric], group: ['Compare across', cats] },
    association: { outcome: ['First category', cats], group: ['Second category', cats] },
    correlation: { outcome: ['First number', numeric], second: ['Second number', numeric] },
    paired: { outcome: ['Before / first number', numeric], second: ['After / second number', numeric] },
  }[kind];

  function submit(execute) {
    setProblem('');
    for (const key of Object.keys(needs)) if (!f[key]) { setProblem(`Choose ${needs[key][0].toLowerCase()}.`); return; }
    const alpha = Number(f.alpha);
    if (!(alpha >= 0.001 && alpha <= 0.2)) { setProblem('Significance level must be between 0.001 and 0.2.'); return; }
    setRows(null);
    const body = { kind, alpha, alternative: f.alternative, prefer: f.prefer, execute, filters, version_id: schema.version_id };
    for (const key of Object.keys(needs)) body[key] = f[key];
    if (kind === 'compare_groups' && f.reference_group.trim()) body.reference_group = f.reference_group.trim();
    r.start(() => analytics.hypothesis(schema.asset_id, body));
  }

  return (
    <div className="stack">
      <p className="muted">State what you want to know. The test is chosen from the data, its assumptions are checked, and the result is reported with an effect size and a confidence interval, not just a p-value.</p>
      <form className="card form-grid" onSubmit={(e) => { e.preventDefault(); submit(true); }} aria-label="Hypothesis test">
        <Field as="select" label="Question" name="kind" value={kind} onChange={(e) => { setKind(e.target.value); setF((s) => ({ ...s, outcome: '', group: '', second: '' })); }}>
          {KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Field>
        {Object.entries(needs).map(([key, [label, list]]) => (
          <Field key={key} as="select" label={label} name={key} value={f[key]} onChange={set(key)} hint={list.length === 0 ? 'No suitable column in this dataset.' : undefined}>
            <option value="">Choose…</option>{list.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Field>
        ))}
        <Field as="select" label="Direction" name="alternative" value={f.alternative} onChange={set('alternative')}>
          <option value="two-sided">Any difference (two-sided)</option><option value="greater">First is greater</option><option value="less">First is smaller</option>
        </Field>
        {kind !== 'association' ? (
          <Field as="select" label="Method" name="prefer" value={f.prefer} onChange={set('prefer')}>
            <option value="auto">Choose from the assumption checks</option><option value="parametric">Parametric</option><option value="nonparametric">Non-parametric</option>
          </Field>
        ) : null}
        <Field label="Significance level (alpha)" name="alpha" inputMode="decimal" value={f.alpha} onChange={set('alpha')} />
        {kind === 'compare_groups' ? <Field label="Reference group (optional)" name="reference_group" value={f.reference_group} onChange={set('reference_group')} hint="Used for pairwise comparisons." /> : null}
        <FilterBar columns={cols} filters={filters} onChange={setFilters} />
        {problem ? <p className="field-error" role="alert">{problem}</p> : null}
        <div className="btn-row">
          <button type="submit" className="btn" disabled={r.busy}>{r.busy ? 'Testing…' : 'Run test'}</button>
          <button type="button" className="btn btn--ghost" disabled={r.busy} onClick={() => submit(false)}>Show the plan only</button>
        </div>
      </form>
      {r.error ? <p className="message message--error" role="alert">{r.error}</p> : null}
      {r.run?.result ? (
        <>
          <RunResults run={r.run} titles={{ hypothesis: 'Hypothesis test' }} />
          <EvidencePanel run={r.run} onOpenRows={(sel, label) => setRows({ sel, label })} onRerun={r.replace} onSaved={(s) => r.replace({ ...r.run, ...s })} />
          {rows ? <RowsPanel runId={r.run.id} select={rows.sel} label={rows.label} onClose={() => setRows(null)} /> : null}
        </>
      ) : null}
    </div>
  );
}
