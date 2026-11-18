import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { describeVersion } from '../../features/workspace/cleaning.js';

export function HistoryPanel({ asset, readOnly, refreshKey, onChanged }) {
  const history = useResource(() => workspace.assets.history(asset.id), [asset.id, asset.current_version_id, refreshKey]);
  const [target, setTarget] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  if (history.loading && !history.data) return <PageLoading label="Loading history…" />;
  if (history.error) return <ErrorState title="Could not load the history" message={describeError(history.error)} onRetry={() => history.reload()} />;
  const { versions, items } = history.data;

  async function rollback() {
    setBusy(true);
    setFailure(null);
    try {
      await workspace.assets.rollback(asset.id, target.id);
      setTarget(null);
      onChanged();
    } catch (error) {
      setTarget(null);
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Version history" className="stack">
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      <div className="table-wrap" tabIndex={0}>
        <table className="table">
          <caption>Versions of this file. Switching versions never deletes anything.</caption>
          <thead><tr><th scope="col">Version</th><th scope="col">Rows × columns</th><th scope="col">Made</th><th scope="col">By</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {[...versions].reverse().map((v) => (
              <tr key={v.id}>
                <th scope="row">{describeVersion(v)}{v.current ? <> <span className="badge badge--ok">Current</span></> : null}</th>
                <td>{v.rows != null ? `${v.rows.toLocaleString()} × ${v.columns}` : '—'}</td>
                <td>{formatDate(v.created_at)}</td>
                <td>{v.created_by?.name ?? '—'}</td>
                <td>{!v.current && !readOnly ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => setTarget(v)}>Make current<span className="sr-only"> version {v.number}</span></button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 className="card__title">What was done</h2>
      {items.length === 0 ? <p className="muted">No cleaning yet. This file is exactly as it was uploaded.</p> : (
        <ol className="timeline">
          {[...items].reverse().map((t) => (
            <li key={t.id} className="card">
              <p><strong>{t.kind === 'rollback' ? `Switched back to version ${t.to_version}` : `Cleaned: version ${t.from_version} → ${t.to_version}`}</strong>
                <span className="row__meta"> {formatDate(t.at)} · by {t.performed_by?.name ?? 'unknown'}</span></p>
              <ul>{t.explanation.map((e, i) => <li key={i}>{e}</li>)}</ul>
              {t.kind === 'clean' && t.summary?.rows_before != null ? <p className="row__meta">{t.summary.rows_before.toLocaleString()} → {t.summary.rows_after.toLocaleString()} rows · {t.summary.cells_changed.toLocaleString()} cells changed</p> : null}
            </li>
          ))}
        </ol>
      )}
      {target ? (
        <Modal title={`Make version ${target.number} current?`} onClose={() => setTarget(null)}>
          <p>The data view, profile and further cleaning will use version {target.number}. Nothing is deleted: you can switch to any other version afterwards.</p>
          <div className="form__actions">
            <button type="button" className="btn btn--ghost" onClick={() => setTarget(null)} data-autofocus>Cancel</button>
            <button type="button" className="btn btn--primary" disabled={busy} onClick={rollback}>{busy ? <Spinner label="Switching" /> : null} Make version {target.number} current</button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}
