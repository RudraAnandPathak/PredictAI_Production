import { useEffect } from 'react';
import { analytics } from '../../api/index.js';
import { EvidencePanel } from '../../components/analytics/EvidencePanel.jsx';
import { FilterBar } from '../../components/analytics/FilterBar.jsx';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { RunResults } from '../../components/analytics/ResultViews.jsx';
import { useState } from 'react';
import { useRun } from './useRun.js';

const TITLES = { dataset_summary: 'Dataset summary', data_quality: 'Data quality', missing_values: 'Missing values', column_stats: 'Column statistics', frequency: 'Value counts' };

/** Descriptive statistics dashboard, and the relationship explorer, share this layout (preset = 'dashboard' | 'relationships'). */
export function PresetTab({ schema, preset, titles = TITLES, intro }) {
  const [filters, setFilters] = useState([]);
  const [rows, setRows] = useState(null);
  const r = useRun();
  const go = () => { setRows(null); return r.start(() => analytics.preset(schema.asset_id, preset, { filters, version_id: schema.version_id })); };
  useEffect(() => { go(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [preset, JSON.stringify(filters)]);
  return (
    <div className="stack">
      {intro ? <p className="muted">{intro}</p> : null}
      <FilterBar columns={schema.columns} filters={filters} onChange={setFilters} />
      {r.busy && !r.run ? <p className="muted" role="status">Calculating…</p> : null}
      {r.error ? <p className="message message--error" role="alert">{r.error} <button type="button" className="btn btn--ghost btn--sm" onClick={go}>Try again</button></p> : null}
      {r.run?.result ? (
        <div aria-busy={r.busy}>
          <RunResults run={r.run} titles={titles} />
          <EvidencePanel run={r.run} onOpenRows={(sel, label) => setRows({ sel, label })} onRerun={r.replace} onSaved={(s) => r.replace({ ...r.run, ...s })} />
          {rows ? <RowsPanel runId={r.run.id} select={rows.sel} label={rows.label} onClose={() => setRows(null)} /> : null}
        </div>
      ) : null}
    </div>
  );
}
