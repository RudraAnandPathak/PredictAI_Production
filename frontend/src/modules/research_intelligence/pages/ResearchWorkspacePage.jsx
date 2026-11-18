import { useEffect, useState } from 'react';
import { ri } from '../../../api/index.js';
import { useParams } from '../../../app/router/useParams.js';
import { useRouter } from '../../../app/router/RouterProvider.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Link } from '../../../app/router/Link.jsx';
import { Loaded, Notice, PageHeader, attempt } from '../../../pages/phase7/shared.jsx';
import { AssistantPanel } from '../components/AssistantPanel.jsx';
import { EvidencePanel } from '../components/EvidencePanel.jsx';
import { ResearchNav } from '../components/ResearchNav.jsx';
import { PolicyControl } from '../components/PolicyControl.jsx';
import { SourcesPanel } from '../components/SourcesPanel.jsx';
import { Field } from '../../../components/forms/Field.jsx';
import { hasActive } from '../labels.js';
import { toggleId } from '../logic.js';

const POLL_MS = 4000;

/** Choose which sources a question may use (none chosen = all included sources). The server re-checks every id. */
function ScopePicker({ sources, scope, onScope, filter, onFilter }) {
  const ready = sources.filter((s) => s.status === 'ready' && s.included);
  if (ready.length < 2) return null;
  const shown = ready.filter((s) => !filter.trim() || s.title.toLowerCase().includes(filter.trim().toLowerCase()));
  return (
    <details className="card ri-scope">
      <summary>Choose which sources to ask ({scope.length ? `${scope.length} selected` : 'all'})</summary>
      <Field label="Filter sources by title" value={filter} onChange={(e) => onFilter(e.target.value)} />
      <ul className="list">{shown.map((s) => <li key={s.id}><label className="ri-check"><input type="checkbox" checked={scope.includes(s.id)} onChange={() => onScope(toggleId(scope, s.id))} /> {s.title}</label></li>)}</ul>
      <div className="btn-row"><button type="button" className="btn btn--ghost" onClick={() => onScope(ready.map((s) => s.id))}>Select all</button><button type="button" className="btn btn--ghost" onClick={() => onScope([])}>Use all sources</button></div>
    </details>
  );
}

/** Three panels: sources | questions | evidence. Sources that are still being processed are refreshed until they settle. */
export function ResearchWorkspacePage() {
  const { workspaceId } = useParams('/research-intelligence/:workspaceId');
  const { navigate } = useRouter();
  const ws = useResource(() => ri.workspaces.overview(workspaceId), [workspaceId]);
  const src = useResource(() => ri.sources.list(workspaceId), [workspaceId]);
  const lim = useResource(() => ri.limits(), []);
  const [target, setTarget] = useState(null);
  const [msg, setMsg] = useState(null);
  const [scope, setScope] = useState([]);
  const [filter, setFilter] = useState('');
  const sources = src.data?.items ?? [];
  const active = hasActive(sources);

  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => src.reload({ quiet: true }), POLL_MS);
    return () => clearInterval(t);
  }, [active, src.reload]);

  const refresh = () => { src.reload({ quiet: true }); ws.reload({ quiet: true }); };
  async function openSource(source) {
    const out = await attempt(() => ri.sources.chunks(workspaceId, source.id), setMsg);
    const first = out?.items?.[0];
    if (first) setTarget({ chunk_id: first.id ?? first.chunk_id, source_id: source.id, source_title: source.title, locator: first.locator, page: first.page });
  }
  async function remove() {
    if (!window.confirm('Delete this workspace with all its sources, passages, conversations and notes? This cannot be undone.')) return;
    const out = await attempt(() => ri.workspaces.remove(workspaceId), setMsg);
    if (out) navigate('/research-intelligence/workspaces');
  }
  return (
    <article className="page ri-page">
      <ResearchNav current="workspaces" />
      <Loaded res={ws} what="this workspace">
        {(d) => (
          <>
            <PageHeader back={{ to: '/research-intelligence/workspaces', label: 'My Workspaces' }} eyebrow="Workspace" title={d.workspace.name} lead={d.workspace.description || undefined} />
            <p className="btn-row"><Link to={`/research-intelligence/${encodeURIComponent(workspaceId)}/studio`} className="btn btn--ghost">Open Research Studio</Link> <Link to="/research-intelligence/discover" className="btn btn--ghost">Find more papers</Link></p>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            {src.error && !src.data ? <Notice kind="error">Sources could not be loaded.</Notice> : null}
            <ScopePicker sources={sources} scope={scope} onScope={setScope} filter={filter} onFilter={setFilter} />
            <div className="ri-grid">
              <SourcesPanel wid={workspaceId} sources={sources} limits={lim.data} onChanged={refresh} onOpen={openSource} />
              <AssistantPanel wid={workspaceId} sources={sources} onCite={setTarget} scope={scope} />
              <EvidencePanel wid={workspaceId} target={target} onClose={() => setTarget(null)} />
            </div>
            <PolicyControl wid={workspaceId} policy={d.settings?.ai_data_policy ?? 'local_only'} onChanged={refresh} />
            <div className="btn-row"><button type="button" className="btn btn--ghost" onClick={remove}>Delete this workspace</button></div>
          </>
        )}
      </Loaded>
    </article>
  );
}
