import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { plural } from '../../features/workspace/format.js';
import { ActivityPanel } from './ActivityPanel.jsx';
import { DataPanel } from './DataPanel.jsx';
import { SettingsPanel } from './SettingsPanel.jsx';

const TAB_IDS = ['data', 'activity', 'settings'];

export function ProjectPage() {
  const { projectId } = useParams('/projects/:projectId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(TAB_IDS.includes(initial) ? initial : 'data');
  const [version, setVersion] = useState(0);
  const project = useResource(() => workspace.projects.get(projectId), [projectId]);

  if (project.loading && !project.data) return <PageLoading label="Opening project…" />;
  if (project.error) {
    const missing = project.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Project not found' : 'Could not open the project'}
          message={missing ? 'This project does not exist, or it is not yours. Check the link or go back to your projects.' : describeError(project.error)}
          onRetry={missing ? null : () => project.reload()} />
        <p><Link to="/projects" className="btn btn--ghost">Back to projects</Link></p>
      </article>
    );
  }
  const p = project.data;
  const changed = (next) => { project.reload({ quiet: true }); setVersion((v) => v + 1); return next; };
  const tabs = [
    { id: 'data', label: 'Data', panel: <DataPanel project={p} onChanged={changed} /> },
    { id: 'activity', label: 'Activity', panel: <ActivityPanel projectId={p.id} refreshKey={version} /> },
    { id: 'settings', label: 'Settings', panel: <SettingsPanel project={p} onChanged={changed} /> },
  ];

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/projects">Projects</Link> / Workspace</p>
        <h1 className="page-title">{p.name}</h1>
        {p.description ? <p className="lead">{p.description}</p> : null}
        <div className="pill-row" aria-label="Project summary">
          <span className={`badge ${p.status === 'archived' ? 'badge--muted' : 'badge--ok'}`}>{p.status === 'archived' ? 'Archived (read-only)' : 'Active'}</span>
          <span className="badge badge--muted">{plural(p.datasets, 'dataset')}</span>
          <span className="badge badge--muted">{plural(p.documents, 'document')}</span>
          <span className="badge badge--muted">Created {formatDate(p.created_at)}</span>
        </div>
      </header>
      <Tabs label="Project sections" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}
