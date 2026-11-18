import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { CATEGORY_LABEL, describeFilter, EVIDENCE_LABEL, formatNumber } from '../../features/analytics/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

const STATES = [['', 'All'], ['new', 'New'], ['saved', 'Saved'], ['dismissed', 'Dismissed']];

function InsightCard({ insight: i, onState, onRows }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = async (state) => { setBusy(true); setError(''); try { await onState(i.id, state); } catch (e) { setError(describeError(e)); } finally { setBusy(false); } };
  const ok = i.evidence_status === 'VERIFIED';
  const unc = i.uncertainty;
  return (
    <li className={`insight insight--${i.state}`}>
      <p className="insight__head">
        <span className="badge badge--muted">{CATEGORY_LABEL[i.category] ?? i.category}</span>{' '}
        <span className={`badge ${ok ? 'badge--ok' : 'badge--warn'}`}>{EVIDENCE_LABEL[i.evidence_status]}</span>
        {i.state !== 'new' ? <> <span className="badge badge--muted">{i.state}</span></> : null}
      </p>
      <p className="insight__statement">{i.statement}</p>
      <Explanation value={i.explanation} level={5} />
      <details>
        <summary>Evidence, method and limits</summary>
        <dl className="kv">
          <dt>Columns</dt><dd>{i.affected.columns.join(', ') || 'whole dataset'}</dd>
          <dt>Rows</dt><dd>{i.affected.rows_affected !== null && i.affected.rows_affected !== undefined ? `${formatNumber(i.affected.rows_affected)} affected of ` : ''}{formatNumber(i.affected.rows_in_scope)} in scope</dd>
          <dt>Method</dt><dd>{i.method || '—'}</dd>
          <dt>Uncertainty</dt><dd>{unc ? (unc.available === false ? unc.reason : JSON.stringify(unc)) : '—'}</dd>
          <dt>Filters</dt><dd>{i.filters?.length ? i.filters.map(describeFilter).join('; ') : 'none'}</dd>
          <dt>Dataset version</dt><dd>{i.dataset_version.version_id.slice(0, 8)}… · {i.dataset_version.checksum_sha256.slice(0, 12)}…</dd>
          <dt>Lineage</dt><dd>analysis {i.lineage.run_id.slice(0, 8)}… · plan {i.lineage.plan_hash.slice(0, 12)}… · {i.lineage.calc_version}</dd>
        </dl>
        {i.limitations?.length ? <><h4 className="sub">Limitations</h4><ul>{i.limitations.map((l, k) => <li key={k}>{l}</li>)}</ul></> : null}
      </details>
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      <div className="btn-row">
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onRows(i.run_id, i.affected.row_numbers?.length ? { type: 'rows', rows: i.affected.row_numbers } : { type: 'all' }, 'this insight')}>See the rows</button>
        {i.state !== 'saved' ? <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => set('saved')}>Save</button> : null}
        {i.state !== 'dismissed' ? <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => set('dismissed')}>Dismiss</button> : null}
        {i.state !== 'new' ? <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => set('new')}>{i.state === 'dismissed' ? 'Restore' : 'Unsave'}</button> : null}
      </div>
    </li>
  );
}

export function InsightsTab({ schema }) {
  const [state, setState] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [rows, setRows] = useState(null);
  const list = useResource(() => analytics.insights(schema.asset_id, { state: state || undefined, version_id: schema.version_id, limit: 100 }), [schema.asset_id, schema.version_id, state]);

  async function generate() {
    setBusy(true); setMessage(null);
    try {
      const res = await analytics.generateInsights(schema.asset_id, { version_id: schema.version_id });
      setMessage({ kind: 'ok', text: `${res.count} findings are up to date. Ones you saved or dismissed keep their state.${res.message ? ` ${res.message}` : ''}` });
      list.reload({ quiet: true });
    } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }
  async function onState(id, next) { await analytics.setInsightState(id, next); list.reload({ quiet: true }); }
  const items = list.data?.items ?? [];
  return (
    <div className="stack">
      <p className="muted">Findings are produced by the same analysis engine as everything else, each with its method, uncertainty, limits and the exact data it came from. Relationships are never described as causes.</p>
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={generate}>{busy ? 'Looking for findings…' : items.length ? 'Refresh findings' : 'Find insights'}</button>
        <div className="seg" role="group" aria-label="Show">
          {STATES.map(([v, l]) => <button key={v} type="button" className={`seg__btn${state === v ? ' is-active' : ''}`} aria-pressed={state === v} onClick={() => setState(v)}>{l}</button>)}
        </div>
      </div>
      {message ? <p className={`message ${message.kind === 'ok' ? 'message--ok' : 'message--error'}`} role="status">{message.text}</p> : null}
      {list.error ? <ErrorState title="Could not load insights" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
      {!list.loading && !list.error && items.length === 0 ? <p className="muted">{state ? 'Nothing here yet.' : 'No insights yet. Choose “Find insights” to analyse this dataset.'}</p> : null}
      <ul className="insights">{items.map((i) => <InsightCard key={i.id} insight={i} onState={onState} onRows={(runId, select, label) => setRows({ runId, select, label })} />)}</ul>
      {rows ? <RowsPanel runId={rows.runId} select={rows.select} label={rows.label} onClose={() => setRows(null)} /> : null}
    </div>
  );
}
