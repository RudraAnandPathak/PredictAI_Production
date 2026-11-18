import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { formatDate, formatDateTime } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { DELETION_SCOPE } from '../../features/workspace/governance.js';
import { formatBytes, plural } from '../../features/workspace/format.js';

/** What the person has stored, what will be deleted soon, and proof that earlier deletions finished. */
export function StoragePanel() {
  const inv = useResource(() => workspace.inventory({ horizon_days: 30 }), []);
  const del = useResource(() => workspace.deletions(), []);
  if (inv.loading && !inv.data) return <PageLoading label="Loading your storage summary…" />;
  if (inv.error) return <ErrorState title="Could not load your storage summary" message={describeError(inv.error)} onRetry={() => inv.reload()} />;
  const d = inv.data;
  const history = del.data?.items ?? [];
  return (
    <section className="card" aria-labelledby="storage-h">
      <h2 className="card__title" id="storage-h">Your stored data</h2>
      <dl className="kv">
        <dt>Projects</dt><dd>{d.projects}</dd>
        <dt>Files</dt><dd>{plural(d.datasets, 'dataset')} and {plural(d.documents, 'document')}</dd>
        <dt>Space used</dt><dd>{formatBytes(d.stored_bytes)} ({formatBytes(d.original_bytes)} originals, {formatBytes(d.derived_bytes)} cleaned versions)</dd>
      </dl>
      <h3 className="card__subtitle">Deleting automatically in the next 30 days</h3>
      {d.upcoming_deletions.length === 0 ? <p className="muted">Nothing is scheduled. Set a date on a file’s Details tab if you want it removed automatically.</p> : (
        <ul className="rows">
          {d.upcoming_deletions.map((u) => (
            <li className="row" key={u.asset_id}>
              <div className="row__main">
                <Link to={`/projects/${encodeURIComponent(u.project_id)}/assets/${encodeURIComponent(u.asset_id)}?tab=details`}>{u.name}</Link>
                <span className="row__meta">after {formatDate(u.retain_until)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      <h3 className="card__subtitle">Deletion history</h3>
      {history.length === 0 ? <p className="muted">No deletions yet.</p> : (
        <ul className="rows">
          {history.slice(0, 10).map((h) => (
            <li className="row" key={h.id}>
              <div className="row__main">
                <span className="row__title">{DELETION_SCOPE[h.scope] ?? 'Deleted'}</span>
                <span className="row__meta">{h.status === 'completed' ? `Completed ${formatDateTime(h.completed_at)}` : 'Finishing — it will complete automatically'}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
