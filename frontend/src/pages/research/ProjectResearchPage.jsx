import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { DocumentsPanel } from './DocumentsPanel.jsx';
import { HistoryPanel } from './HistoryPanel.jsx';
import { InfluencePanel } from './InfluencePanel.jsx';
import { NewsPanel } from './NewsPanel.jsx';
import { PrivacyPanel } from './PrivacyPanel.jsx';
import { SearchToolsPanel } from './SearchToolsPanel.jsx';
import { SourcesPanel } from './SourcesPanel.jsx';

const TAB_IDS = ['history', 'documents', 'sources', 'tools', 'news', 'influence', 'privacy'];

/** One project's research: history, the source browser, news and events, influence analysis, and this project's privacy choices. */
export function ProjectResearchPage() {
  const { projectId } = useParams('/research/p/:projectId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(TAB_IDS.includes(initial) ? initial : 'history');
  const project = useResource(() => workspace.projects.get(projectId), [projectId]);

  if (project.loading && !project.data) return <PageLoading label="Opening project…" />;
  if (project.error) {
    const missing = project.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Project not found' : 'Could not open the project'} message={missing ? 'This project does not exist, or it is not yours.' : describeError(project.error)} onRetry={missing ? null : () => project.reload()} />
        <p><Link to="/research" className="btn btn--ghost">Back to research</Link></p>
      </article>
    );
  }
  const p = project.data;
  const tabs = [
    { id: 'history', label: 'Research', panel: <HistoryPanel key="h" projectId={projectId} /> },
    { id: 'documents', label: 'Search documents', panel: <DocumentsPanel key="d" projectId={projectId} /> },
    { id: 'sources', label: 'Sources and evidence', panel: <SourcesPanel key="s" projectId={projectId} /> },
    { id: 'tools', label: 'Search tools', panel: <SearchToolsPanel key="t" projectId={projectId} /> },
    { id: 'news', label: 'News and events', panel: <NewsPanel key="n" projectId={projectId} /> },
    { id: 'influence', label: 'Influence analysis', panel: <InfluencePanel key="i" projectId={projectId} /> },
    { id: 'privacy', label: 'Privacy for this project', panel: <PrivacyPanel key="p" projectId={projectId} /> },
  ];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/research">Research</Link></p>
        <h1 className="page-title">{p.name}</h1>
        <div className="btn-row"><Link to={`/research/p/${encodeURIComponent(projectId)}/new`} className="btn">New research</Link><Link to={`/projects/${encodeURIComponent(projectId)}`} className="btn btn--ghost">Open project files</Link></div>
      </header>
      <Tabs label="Project research" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}
