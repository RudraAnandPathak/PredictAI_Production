import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { EvidencePanel } from '../../components/analytics/EvidencePanel.jsx';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { RunResults } from '../../components/analytics/ResultViews.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

const KINDS = { dashboard: 'Overview', relationships: 'Relationships', hypothesis: 'Hypothesis test', chart: 'Chart', question: 'Question', insights: 'Insights', descriptive: 'Descriptive', compare: 'Comparison' };

export function SavedTab({ schema }) {
  const [onlySaved, setOnlySaved] = useState(true);
  const [openId, setOpenId] = useState(null);
  const [run, setRun] = useState(null);
  const [error, setError] = useState('');
  const [rows, setRows] = useState(null);
  const list = useResource(() => analytics.runs(schema.asset_id, { saved: onlySaved, limit: 50 }), [schema.asset_id, onlySaved]);
  async function open(id) {
    setOpenId(id); setRun(null); setError(''); setRows(null);
    try { setRun(await analytics.getRun(id)); } catch (e) { setError(describeError(e)); }
  }
  const items = list.data?.items ?? [];
  return (
    <div className="stack">
      <p className="muted">Revisit earlier analyses exactly as they were calculated, re-run them to confirm the numbers reproduce, or re-run after the data changes.</p>
      <label className="check"><input type="checkbox" checked={onlySaved} onChange={(e) => setOnlySaved(e.target.checked)} /> Saved only</label>
      {list.error ? <ErrorState title="Could not load analyses" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
      {!list.loading && items.length === 0 ? <p className="muted">{onlySaved ? 'Nothing saved yet. Open any analysis and choose “Save this analysis”.' : 'No analyses yet.'}</p> : null}
      <ul className="list">
        {items.map((r) => (
          <li key={r.id} className="row">
            <div className="row__main">
              <span className="row__title"><button type="button" className="linklike" onClick={() => open(r.id)} aria-expanded={openId === r.id}>{r.label || KINDS[r.kind] || r.kind}</button> {r.saved ? <span className="badge badge--muted">saved</span> : null} <span className={`badge ${r.status === 'failed' ? 'badge--danger' : r.evidence_status === 'VERIFIED' ? 'badge--ok' : 'badge--warn'}`}>{r.status === 'failed' ? 'failed' : r.evidence_status === 'VERIFIED' ? 'Verified' : 'Incomplete'}</span></span>
              <span className="row__meta">{r.created_at?.slice(0, 16).replace('T', ' ')} · {r.rows_used ?? 0} rows · fingerprint {r.result_hash?.slice(0, 10) ?? '—'}</span>
            </div>
          </li>
        ))}
      </ul>
      {error ? <p className="message message--error" role="alert">{error}</p> : null}
      {run?.result ? (
        <section className="card" aria-label="Saved analysis">
          <RunResults run={run} />
          <EvidencePanel run={run} onOpenRows={(s, l) => setRows({ s, l })} onRerun={(fresh) => { setRun(fresh); list.reload({ quiet: true }); }} onSaved={(s) => { setRun({ ...run, ...s }); list.reload({ quiet: true }); }} />
          {rows ? <RowsPanel runId={run.id} select={rows.s} label={rows.l} onClose={() => setRows(null)} /> : null}
        </section>
      ) : null}
      {run && !run.result ? <p className="message message--error" role="status">This analysis did not complete{run.error_code ? ` (${run.error_code})` : ''}, so there are no results to show.</p> : null}
    </div>
  );
}
