import { useEffect, useState } from 'react';
import { ri } from '../../../api/index.js';
import { useParams } from '../../../app/router/useParams.js';
import { useResource } from '../../../features/identity/useResource.js';
import { Loaded, Notice, PageHeader, attempt } from '../../../pages/phase7/shared.jsx';
import { AssistantPanel } from '../../research_intelligence/components/AssistantPanel.jsx';
import { EvidencePanel } from '../../research_intelligence/components/EvidencePanel.jsx';
import { SourcesPanel } from '../../research_intelligence/components/SourcesPanel.jsx';
import { hasActive } from '../../research_intelligence/labels.js';
import { DeepResearchPanel } from '../components/DeepResearchPanel.jsx';
import { StudioPanel } from '../components/StudioPanel.jsx';

const POLL_MS = 4000;

/** One notebook: sources on the left, cited chat in the middle, Studio and the opened passage on the right. */
export function NotebookPage() {
  const fromNotebook = useParams('/notebook/:notebookId').notebookId;
  const fromOld = useParams('/research-intelligence/:notebookId').notebookId;
  const notebookId = fromNotebook ?? fromOld;
  const ws = useResource(() => ri.workspaces.overview(notebookId), [notebookId]);
  const src = useResource(() => ri.sources.list(notebookId), [notebookId]);
  const lim = useResource(() => ri.limits(), []);
  const [target, setTarget] = useState(null);
  const [msg, setMsg] = useState(null);
  const sources = src.data?.items ?? [];
  const active = hasActive(sources);
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => src.reload({ quiet: true }), POLL_MS);
    return () => clearInterval(t);
  }, [active, src.reload]);
  const refresh = () => { src.reload({ quiet: true }); ws.reload({ quiet: true }); };
  async function openSource(source) {
    const out = await attempt(() => ri.sources.chunks(notebookId, source.id), setMsg);
    const first = out?.items?.[0];
    if (first) setTarget({ chunk_id: first.id ?? first.chunk_id, source_id: source.id, source_title: source.title, locator: first.locator, page: first.page });
  }
  return (
    <article className="page ri-page">
      <Loaded res={ws} what="this notebook">
        {(d) => (
          <>
            <PageHeader back={{ to: '/notebook', label: 'Notebooks' }} eyebrow="Notebook" title={d.workspace.name} lead={d.workspace.description || undefined} />
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <div className="ri-grid">
              <div>
                <SourcesPanel wid={notebookId} sources={sources} limits={lim.data} onChanged={refresh} onOpen={openSource} />
                <DeepResearchPanel wid={notebookId} onAdded={refresh} />
              </div>
              <AssistantPanel wid={notebookId} sources={sources} onCite={setTarget} scope={[]} />
              <div>
                <StudioPanel wid={notebookId} sources={sources} onCite={setTarget} />
                <EvidencePanel wid={notebookId} target={target} onClose={() => setTarget(null)} />
              </div>
            </div>
          </>
        )}
      </Loaded>
    </article>
  );
}
