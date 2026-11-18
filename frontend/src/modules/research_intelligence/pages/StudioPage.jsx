import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { useRouter } from '../../../app/router/RouterProvider.jsx';
import { useParams } from '../../../app/router/useParams.js';
import { useResource } from '../../../features/identity/useResource.js';
import { Loaded, Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { Field } from '../../../components/forms/Field.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';
import { StudioAnalyses } from '../components/StudioAnalyses.jsx';
import { StudioEvidence } from '../components/StudioEvidence.jsx';
import { StudioTools } from '../components/StudioTools.jsx';

function ReportMaker({ wid, analysisId, selected }) {
  const types = useResource(() => ri.reports.types(), []);
  const { navigate } = useRouter();
  const [type, setType] = useState('literature_review');
  const [title, setTitle] = useState('');
  const [msg, setMsg] = useState(null);
  const list = useResource(() => ri.reports.list(wid), [wid]);
  async function make(e) {
    e.preventDefault();
    const body = { analysis_id: analysisId, report_type: type, ...(title.trim() ? { title: title.trim() } : {}), ...(selected.length ? { source_ids: selected } : {}) };
    const out = await attempt(() => ri.reports.create(wid, body), setMsg);
    if (out) navigate(`/research-intelligence/${encodeURIComponent(wid)}/reports/${encodeURIComponent(out.id)}`);
  }
  return (
    <section className="card" aria-label="Create a report">
      <h2 className="card__title">4. Create a report</h2>
      <p className="row__meta">Reports are built only from a finished analysis. Quotes are copied exactly; references come from the stored source details and nothing is invented.</p>
      <form onSubmit={make}>
        <Field as="select" label="Report type" value={type} onChange={(e) => setType(e.target.value)}>{(types.data?.items ?? []).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</Field>
        <Field label="Title (optional)" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <button className="btn" disabled={!analysisId}>{analysisId ? 'Create report' : 'Choose a finished analysis first'}</button>
      </form>
      <ul className="list">{(list.data?.items ?? []).map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/research-intelligence/${encodeURIComponent(wid)}/reports/${encodeURIComponent(r.id)}`}>{r.title}</Link></span><span className="row__meta">{r.type_label} · version {r.version}{r.edited ? ' · edited' : ''}</span></div></li>)}</ul>
    </section>
  );
}

/** Research Studio for one workspace: analyse, keep findings, outline, generate, report. */
export function StudioPage() {
  const { workspaceId } = useParams('/research-intelligence/:workspaceId/studio');
  const ws = useResource(() => ri.workspaces.overview(workspaceId), [workspaceId]);
  const src = useResource(() => ri.sources.list(workspaceId), [workspaceId]);
  const [selected, setSelected] = useState([]);
  const [analysisId, setAnalysisId] = useState(null);
  return (
    <ResearchFrame current="studio" title={ws.data ? `Studio — ${ws.data.workspace.name}` : 'Research Studio'} back={{ to: `/research-intelligence/${encodeURIComponent(workspaceId)}`, label: '← Back to the workspace' }}
      lead="Everything here is built from your own sources. Nothing is validated or peer reviewed by this tool.">
      <Loaded res={ws} what="this workspace">
        {() => (
          <div className="ri-studio">
            <StudioAnalyses wid={workspaceId} sources={src.data?.items ?? []} selected={selected} onSelect={setSelected} analysisId={analysisId} onPick={setAnalysisId} />
            <StudioEvidence wid={workspaceId} />
            <StudioTools wid={workspaceId} analysisId={analysisId} selected={selected} />
            <ReportMaker wid={workspaceId} analysisId={analysisId} selected={selected} />
          </div>
        )}
      </Loaded>
    </ResearchFrame>
  );
}
