import { useState } from 'react';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { useParams } from '../../app/router/useParams.js';
import { PageHeader } from './shared.jsx';
import { EnvsTab } from './EnvsTab.jsx';
import { FilesTab } from './FilesTab.jsx';
import { JobsTab } from './JobsTab.jsx';
import { ArtifactsTab, ExperimentsTab, GitTab, HistoryTab, NotebooksTab, PipelinesTab } from './MoreTabs.jsx';
import { ComputePanel } from './ComputePage.jsx';

const TAB_IDS = ['files', 'notebooks', 'envs', 'jobs', 'experiments', 'git', 'pipelines', 'artifacts', 'compute', 'history'];

/** One project's workspace. The server decides what is allowed on every request; this page only arranges it. */
export function DevProjectPage() {
  const { projectId } = useParams('/development/:projectId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [active, setActive] = useState(TAB_IDS.includes(initial) ? initial : 'files');
  const tabs = [
    { id: 'files', label: 'Code', panel: <FilesTab projectId={projectId} /> },
    { id: 'notebooks', label: 'Notebooks', panel: <NotebooksTab projectId={projectId} /> },
    { id: 'envs', label: 'Environments', panel: <EnvsTab projectId={projectId} /> },
    { id: 'jobs', label: 'Jobs', panel: <JobsTab projectId={projectId} /> },
    { id: 'experiments', label: 'Experiments', panel: <ExperimentsTab projectId={projectId} /> },
    { id: 'git', label: 'Git', panel: <GitTab projectId={projectId} /> },
    { id: 'pipelines', label: 'Pipelines', panel: <PipelinesTab projectId={projectId} /> },
    { id: 'artifacts', label: 'Artifacts', panel: <ArtifactsTab projectId={projectId} /> },
    { id: 'compute', label: 'Compute', panel: <ComputePanel projectId={projectId} /> },
    { id: 'history', label: 'History', panel: <HistoryTab projectId={projectId} /> },
  ];
  return (
    <article className="page">
      <PageHeader eyebrow="Development" title="Project workspace" back={{ to: '/development', label: 'Development' }} />
      <Tabs label="Workspace sections" tabs={tabs} active={active} onChange={setActive} />
    </article>
  );
}
