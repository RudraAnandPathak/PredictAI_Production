import { useEffect, useState } from 'react';
import { analytics } from '../../api/index.js';
import { describeError } from '../../features/identity/validation.js';
import { formatNumber } from '../../features/analytics/format.js';
import { downloadBlob } from './ChartFrame.jsx';

/** Drill-down: the actual rows behind a chart element or result, with export. Rows are fetched from the server under the same authorisation. */
export function RowsPanel({ runId, select, label, onClose }) {
  const [page, setPage] = useState(0);
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const size = 25;
  const key = JSON.stringify(select);

  useEffect(() => { setPage(0); }, [key, runId]);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    analytics.rows(runId, { select, offset: page * size, limit: size })
      .then((data) => { if (live) setState({ loading: false, data, error: null }); })
      .catch((error) => { if (live) setState({ loading: false, data: null, error }); });
    return () => { live = false; };
  }, [runId, key, page]);

  async function exportRows() {
    setExporting(true); setExportError('');
    try { const { blob, filename } = await analytics.exportFile(runId, { what: 'rows', select }); downloadBlob(blob, filename); } catch (e) { setExportError(describeError(e)); } finally { setExporting(false); }
  }
  const d = state.data;
  return (
    <section className="card rows-panel" aria-label="Underlying rows" aria-live="polite">
      <header className="rows-panel__head">
        <div><h3 className="result__title">Rows behind {label || 'this selection'}</h3>{d ? <p className="muted">{formatNumber(d.total)} rows. Row numbers count from the first data row of the source table.</p> : null}</div>
        <div className="btn-row">
          <button type="button" className="btn btn--ghost btn--sm" disabled={exporting || !d || d.total === 0} onClick={exportRows}>{exporting ? 'Preparing…' : 'Export rows (CSV)'}</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close</button>
        </div>
      </header>
      {state.error ? <p className="message message--error" role="alert">{describeError(state.error)}</p> : null}
      {exportError ? <p className="message message--error" role="alert">{exportError}</p> : null}
      {state.loading && !d ? <p className="muted">Loading rows…</p> : null}
      {d && d.items.length === 0 ? <p className="muted">No rows match this selection.</p> : null}
      {d && d.items.length > 0 ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Rows">
          <table className="table table--compact">
            <thead><tr><th scope="col">Row</th>{d.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
            <tbody>{d.items.map((r) => <tr key={r.row}><td>{r.row}</td>{r.values.map((v, i) => <td key={i}>{v === null ? <span className="muted">empty</span> : String(v)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : null}
      {d && d.total > size ? (
        <div className="btn-row">
          <button type="button" className="btn btn--ghost btn--sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span className="muted">Page {page + 1} of {Math.ceil(d.total / size)}</span>
          <button type="button" className="btn btn--ghost btn--sm" disabled={(page + 1) * size >= d.total} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      ) : null}
    </section>
  );
}
