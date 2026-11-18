import { useCallback, useMemo } from 'react';
import { workspace } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { useUploads } from '../../features/workspace/useUploads.js';
import { formatBytes } from '../../features/workspace/format.js';
import { AssetTable } from './AssetTable.jsx';
import { UploadZone } from './UploadZone.jsx';

export function DataPanel({ project, onChanged }) {
  const archived = project.status === 'archived';
  const assets = useResource(() => workspace.assets.list(project.id), [project.id]);
  const quota = useResource(() => workspace.quota(), []);
  const interrupted = useResource(() => workspace.uploads.list(project.id), [project.id]);
  const limits = useMemo(() => ({ maxBytes: quota.data ? quota.data.max_file_mb * 1024 * 1024 : null }), [quota.data]);
  const refresh = useCallback(() => { assets.reload({ quiet: true }); quota.reload({ quiet: true }); interrupted.reload({ quiet: true }); onChanged?.(); }, [assets, quota, interrupted, onChanged]);
  const uploads = useUploads({ projectId: project.id, limits, onSettled: refresh });

  async function discard(id) {
    await workspace.uploads.cancel(id).catch(() => {});
    interrupted.reload({ quiet: true });
  }

  const pending = (interrupted.data?.items ?? []).filter((u) => !uploads.items.some((it) => it.name === u.filename && it.size === u.size_bytes));
  return (
    <div className="stack">
      {archived ? (
        <section className="card"><p className="muted">This project is archived, so files can be viewed and downloaded but not added or deleted. Restore it in Settings to make changes.</p></section>
      ) : (
        <UploadZone uploads={uploads} limits={limits} quota={quota.data} />
      )}

      {!archived && pending.length > 0 ? (
        <section className="card" aria-labelledby="resume-h">
          <div className="card__head">
            <h2 className="card__title" id="resume-h">Unfinished uploads</h2>
            <p className="card__desc">These were interrupted. Choose the same file again and it continues from where it stopped, or discard it.</p>
          </div>
          <ul className="rows">
            {pending.map((u) => (
              <li className="row" key={u.id}>
                <div className="row__main">
                  <span className="row__title">{u.filename}</span>
                  <span className="row__meta">{formatBytes(u.received_bytes)} of {formatBytes(u.size_bytes)} received</span>
                </div>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => discard(u.id)}>Discard<span className="sr-only"> {u.filename}</span></button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="card" aria-labelledby="files-h">
        <div className="card__head">
          <h2 className="card__title" id="files-h">Files</h2>
        </div>
        {assets.loading && !assets.data ? <PageLoading label="Loading files…" /> : null}
        {assets.error ? <ErrorState title="Could not load files" message={describeError(assets.error)} onRetry={() => assets.reload()} /> : null}
        {assets.data ? <AssetTable assets={assets.data.items} readOnly={archived} onDeleted={refresh} /> : null}
      </section>
    </div>
  );
}
