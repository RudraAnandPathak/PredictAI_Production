import { useEffect, useState } from 'react';
import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { describeError } from '../../features/identity/validation.js';
import { formatBytes, KIND_LABEL } from '../../features/workspace/format.js';
import { footprintLines } from '../../features/workspace/governance.js';

const STATUS_BADGE = { ready: ['badge--ok', 'Ready'], processing: ['badge--warn', 'Processing'], failed: ['badge--danger', 'Needs attention'] };

export function AssetTable({ assets, readOnly, onDeleted }) {
  const [target, setTarget] = useState(null);
  if (assets.length === 0) {
    return <p className="muted" role="status">No files yet. Add a dataset or a document above to get started.</p>;
  }
  return (
    <>
      <div className="table-wrap">
        <table className="table">
          <caption className="sr-only">Files in this project</caption>
          <thead>
            <tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Size</th><th scope="col">Contents</th><th scope="col">Status</th><th scope="col">Added</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {assets.map((a) => {
              const [cls, label] = STATUS_BADGE[a.status] ?? ['badge--muted', a.status];
              return (
                <tr key={a.id}>
                  <td><Link to={`/projects/${encodeURIComponent(a.project_id)}/assets/${encodeURIComponent(a.id)}`}>{a.name}</Link></td>
                  <td>{KIND_LABEL[a.kind]} · {a.format.toUpperCase()}</td>
                  <td>{formatBytes(a.size_bytes)}</td>
                  <td>{contents(a)}</td>
                  <td>
                    <span className={`badge ${cls}`}>{label}</span>
                    {a.status === 'failed' && a.status_detail ? <span className="row__meta">{a.status_detail}</span> : null}
                  </td>
                  <td>{formatDate(a.created_at)}</td>
                  <td>
                    <a className="btn btn--ghost btn--sm" href={workspace.assets.downloadUrl(a.id)} download>Download<span className="sr-only"> {a.name}</span></a>{' '}
                    {!readOnly ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => setTarget(a)}>Delete<span className="sr-only"> {a.name}</span></button> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {target ? <DeleteAssetDialog asset={target} onClose={() => setTarget(null)} onDeleted={() => { setTarget(null); onDeleted(); }} /> : null}
    </>
  );
}

export function contents(a) {
  if (a.kind === 'dataset' && a.row_count != null) return `${a.row_count.toLocaleString()} rows × ${a.column_count} columns`;
  if (a.kind === 'document' && a.page_count != null) return `${a.page_count}${a.page_count_estimated ? ' pages (estimated)' : a.page_count === 1 ? ' page' : ' pages'}`;
  return '—';
}

function DeleteAssetDialog({ asset, onClose, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const [fp, setFp] = useState(null);
  useEffect(() => {
    let live = true;
    workspace.assets.footprint(asset.id).then((r) => live && setFp(r)).catch(() => {});
    return () => { live = false; };
  }, [asset.id]);
  async function confirm() {
    setBusy(true);
    setFailure(null);
    try {
      const result = await workspace.assets.remove(asset.id);
      if (result.deleted === false) setFailure(result.message);
      else onDeleted();
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Delete this file?" onClose={onClose}>
      <p>This permanently deletes <strong>{asset.name}</strong>, including cleaned versions, extracted text and profiles made from it. This cannot be undone.</p>
      {fp ? (
        <>
          <p className="muted">This will remove {fp.objects} stored {fp.objects === 1 ? 'item' : 'items'} ({formatBytes(fp.bytes)}):</p>
          <ul className="plain">{footprintLines(fp, formatBytes).map((l) => <li key={l.kind}><strong>{l.label}</strong> — {l.text}</li>)}</ul>
        </>
      ) : null}
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" onClick={onClose} data-autofocus>Keep file</button>
        <button type="button" className="btn btn--danger" disabled={busy} onClick={confirm}>{busy ? <Spinner label="Deleting" /> : null} Delete permanently</button>
      </div>
    </Modal>
  );
}
