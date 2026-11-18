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
import { formatBytes, KIND_LABEL } from '../../features/workspace/format.js';
import { contents } from './AssetTable.jsx';
import { DatasetPreview } from './DatasetPreview.jsx';
import { DocumentViewer } from './DocumentViewer.jsx';
import { CleaningPanel } from './CleaningPanel.jsx';
import { HistoryPanel } from './HistoryPanel.jsx';
import { ExtractionDetails } from './ExtractionDetails.jsx';
import { ProfilePanel } from './ProfilePanel.jsx';

const STATUS = { ready: ['badge--ok', 'Ready'], processing: ['badge--warn', 'Processing'], failed: ['badge--danger', 'Needs attention'] };

export function AssetPage() {
  const { projectId, assetId } = useParams('/projects/:projectId/assets/:assetId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(['details', 'profile', 'clean', 'history'].includes(initial) ? initial : 'view');
  const asset = useResource(() => workspace.assets.get(assetId), [assetId]);
  const project = useResource(() => workspace.projects.get(projectId), [projectId]);
  const archived = project.data?.status === 'archived';
  const [refresh, setRefresh] = useState(0);
  const changed = () => { asset.reload({ quiet: true }); setRefresh((n) => n + 1); };

  const back = <p><Link to={`/projects/${encodeURIComponent(projectId)}`} className="btn btn--ghost">Back to project</Link></p>;
  if (asset.loading && !asset.data) return <PageLoading label="Opening file…" />;
  if (asset.error) {
    const missing = asset.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'File not found' : 'Could not open the file'}
          message={missing ? 'This file does not exist, or it is not yours. Check the link or go back to the project.' : describeError(asset.error)}
          onRetry={missing ? null : () => asset.reload()} />
        {back}
      </article>
    );
  }
  const a = asset.data;
  const [cls, label] = STATUS[a.status] ?? ['badge--muted', a.status];
  const ready = a.status === 'ready';
  const tabs = [
    { id: 'view', label: a.kind === 'dataset' ? 'Data' : 'Text', panel: ready ? (a.kind === 'dataset' ? <DatasetPreview key={`${a.current_version_id}-${refresh}`} asset={a} /> : <DocumentViewer asset={a} />) : <Unavailable asset={a} /> },
    ...(ready && a.kind === 'dataset' ? [
      { id: 'clean', label: 'Clean', panel: <CleaningPanel asset={a} readOnly={archived} onApplied={() => { changed(); setTab('history'); }} /> },
      { id: 'history', label: 'History', panel: <HistoryPanel asset={a} readOnly={archived} refreshKey={refresh} onChanged={() => { changed(); setTab('view'); }} /> },
    ] : []),
    ...(ready ? [{ id: 'profile', label: 'Profile', panel: <ProfilePanel key={a.current_version_id} asset={a} readOnly={archived} /> }] : []),
    { id: 'details', label: 'Details', panel: <ExtractionDetails asset={a} ready={ready} readOnly={archived} onChanged={() => asset.reload({ quiet: true })} /> },
  ];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/projects">Projects</Link> / <Link to={`/projects/${encodeURIComponent(a.project_id)}`}>Workspace</Link> / File</p>
        <h1 className="page-title">{a.name}</h1>
        <div className="pill-row" aria-label="File summary">
          <span className={`badge ${cls}`}>{label}</span>
          <span className="badge badge--muted">{KIND_LABEL[a.kind]} · {a.format.toUpperCase()}</span>
          <span className="badge badge--muted">{formatBytes(a.size_bytes)}</span>
          <span className="badge badge--muted">{contents(a)}</span>
          <span className="badge badge--muted">Added {formatDate(a.created_at)}</span>
        </div>
        {archived ? <p className="muted">This project is archived, so files are read-only. Restore it to profile or change data.</p> : null}
        <p><a className="btn btn--ghost btn--sm" href={workspace.assets.downloadUrl(a.id)} download>Download original</a>{ready && a.kind === 'dataset' ? <> <Link to={`/analytics/${encodeURIComponent(a.id)}`} className="btn btn--sm">Analyse this dataset</Link></> : null}</p>
      </header>
      <Tabs label="File sections" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}

function Unavailable({ asset }) {
  return (
    <div className="message message--error" role="status">
      <p className="message__title">This file could not be read</p>
      <div className="message__body">{asset.status_detail || 'It is still being processed. Refresh in a moment.'} The original is kept; you can download or delete it.</div>
    </div>
  );
}
