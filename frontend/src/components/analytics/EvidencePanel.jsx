import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { describeError } from '../../features/identity/validation.js';
import { describeFilter, EVIDENCE_LABEL, formatNumber } from '../../features/analytics/format.js';
import { downloadBlob } from './ChartFrame.jsx';

/** Everything needed to inspect and reproduce a result: source, calculation, inputs, interpretation, and how to reach the data. */
export function EvidencePanel({ run, onOpenRows, onRerun, onSaved }) {
  const ev = run?.result?.evidence;
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState(null);
  if (!ev) return null;
  const verified = ev.status === 'VERIFIED';

  async function act(name, fn) {
    setBusy(name); setMessage(null);
    try { await fn(); } catch (error) { setMessage({ kind: 'error', text: describeError(error) }); } finally { setBusy(''); }
  }
  const exportResult = () => act('export', async () => {
    const { blob, filename } = await analytics.exportFile(run.id, { what: 'result' });
    downloadBlob(blob, filename);
  });
  const rerun = () => act('rerun', async () => {
    const fresh = await analytics.rerun(run.id);
    setMessage({ kind: fresh.reproduction.reproduced ? 'ok' : 'error', text: fresh.reproduction.reproduced ? 'Re-run complete: the results are identical (same result fingerprint).' : `Re-run complete: results differ. ${fresh.reproduction.explanation}` });
    onRerun?.(fresh);
  });
  const toggleSave = () => act('save', async () => { const r = await analytics.saveRun(run.id, { saved: !run.saved }); onSaved?.(r); });

  return (
    <details className="evidence" open={!verified}>
      <summary>
        <span className={`badge ${verified ? 'badge--ok' : 'badge--warn'}`}>{EVIDENCE_LABEL[ev.status]}</span>
        {' '}Evidence, calculation and reproducibility
      </summary>
      {!verified ? <p className="message message--warn" role="status">This result is not presented as verified. Missing: {ev.missing.join(', ') || 'a completed analysis'}{ev.incomplete_reason ? `. ${ev.incomplete_reason}` : ''}</p> : null}
      <div className="evidence__grid">
        <section aria-label="Source data">
          <h4 className="sub">Source data</h4>
          <dl className="kv">
            <dt>File</dt><dd>{ev.source.source_file}</dd>
            <dt>Version</dt><dd>{ev.source.version_number} ({ev.source.version_kind}) · {ev.source.format}</dd>
            <dt>Checksum</dt><dd className="mono">{ev.source.checksum_sha256.slice(0, 16)}…</dd>
            <dt>Rows</dt><dd>{formatNumber(ev.source.rows_in_scope)} in scope of {formatNumber(ev.source.rows_in_dataset)}</dd>
            <dt>Filters</dt><dd>{ev.source.row_snapshot.filters.length ? ev.source.row_snapshot.filters.map(describeFilter).join('; ') : 'none'}</dd>
            {ev.source.row_snapshot.sample ? <><dt>Sample</dt><dd>{JSON.stringify(ev.source.row_snapshot.sample)}</dd></> : null}
          </dl>
        </section>
        <section aria-label="Calculation">
          <h4 className="sub">Calculation</h4>
          <dl className="kv">
            <dt>Calc version</dt><dd>{ev.calculation.calc_version}</dd>
            <dt>Plan fingerprint</dt><dd className="mono">{ev.calculation.plan_hash.slice(0, 16)}…</dd>
            <dt>Result fingerprint</dt><dd className="mono">{run.result_hash?.slice(0, 16)}…</dd>
            <dt>Run at</dt><dd>{ev.calculation.executed_at}</dd>
            <dt>Libraries</dt><dd>{Object.entries(ev.calculation.libraries).map(([k, v]) => `${k} ${v}`).join(', ')}</dd>
            <dt>Isolation</dt><dd>{ev.calculation.isolation.mode}{ev.calculation.isolation.limits_applied ? ` (${ev.calculation.isolation.limits_applied.length} limits)` : ''}</dd>
          </dl>
          <ul className="ops">{ev.calculation.operations.map((o) => <li key={o.index}><strong>{o.title}</strong> — {o.method || o.op} <span className="muted">[{o.status}]</span></li>)}</ul>
        </section>
        <section aria-label="Inputs">
          <h4 className="sub">Inputs</h4>
          <dl className="kv">
            <dt>Columns</dt><dd>{ev.inputs.selected_columns.join(', ') || '—'}</dd>
            <dt>Missing data</dt><dd>{ev.inputs.null_policy}</dd>
            <dt>Random seed</dt><dd>{ev.inputs.seed}</dd>
            <dt>Lineage</dt><dd>{ev.inputs.transformation_lineage.map((l) => `v${l.version_number} ${l.kind}${l.transformation ? ` (${l.transformation.operations.join(', ')})` : ''}`).join(' → ')}</dd>
          </dl>
        </section>
      </div>
      {ev.access.warnings?.length ? <ul className="warnings">{ev.access.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
      <div className="btn-row">
        {onOpenRows ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => onOpenRows({ type: 'all' }, 'all rows in this analysis')}>See the rows used</button> : null}
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy === 'rerun'} onClick={rerun}>{busy === 'rerun' ? 'Re-running…' : 'Re-run and compare'}</button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy === 'export'} onClick={exportResult}>Download evidence (JSON)</button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy === 'save'} onClick={toggleSave}>{run.saved ? 'Remove from saved' : 'Save this analysis'}</button>
      </div>
      {message ? <p className={`message ${message.kind === 'ok' ? 'message--ok' : 'message--error'}`} role="status">{message.text}</p> : null}
    </details>
  );
}
