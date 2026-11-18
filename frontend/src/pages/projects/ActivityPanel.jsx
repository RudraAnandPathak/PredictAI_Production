import { workspace } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { formatDateTime } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { describeEvent } from '../../features/workspace/format.js';

export function ActivityPanel({ projectId, refreshKey }) {
  const activity = useResource(() => workspace.projects.activity(projectId, { limit: 50 }), [projectId, refreshKey]);
  return (
    <section className="card" aria-labelledby="activity-h">
      <div className="card__head">
        <h2 className="card__title" id="activity-h">Activity</h2>
        <p className="card__desc">What happened in this project, newest first. Only actions are listed, never file contents.</p>
      </div>
      {activity.loading && !activity.data ? <PageLoading label="Loading activity…" /> : null}
      {activity.error ? <ErrorState title="Could not load activity" message={describeError(activity.error)} onRetry={() => activity.reload()} /> : null}
      {activity.data && activity.data.items.length === 0 ? <p className="muted">Nothing has happened yet.</p> : null}
      <ul className="rows">
        {(activity.data?.items ?? []).map((e) => (
          <li className="row" key={e.id}>
            <div className="row__main">
              <span className="row__title">{describeEvent(e.event_type)}</span>
              <span className="row__meta">{formatDateTime(e.occurred_at)}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
