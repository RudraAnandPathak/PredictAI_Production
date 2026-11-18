import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Empty, Loaded, when } from '../../../pages/phase7/shared.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';

const wsPath = (id) => `/research-intelligence/${encodeURIComponent(id)}`;

function Chooser({ current, title, lead, suffix, verb }) {
  const res = useResource(() => ri.workspaces.list(), []);
  return (
    <ResearchFrame current={current} title={title} lead={lead}>
      <Loaded res={res} what="your workspaces">
        {(d) => (d.items.length === 0 ? <Empty title="No workspaces yet"><p><Link to="/research-intelligence/workspaces">Create a workspace</Link> first.</p></Empty> : (
          <ul className="list">{d.items.map((w) => <li key={w.id} className="row"><div className="row__main"><span className="row__title"><Link to={`${wsPath(w.id)}${suffix}`}>{verb} — {w.name}</Link></span><span className="row__meta">{w.counts.sources} source(s) · updated {when(w.updated_at)}</span></div></li>)}</ul>
        ))}
      </Loaded>
    </ResearchFrame>
  );
}

export const AssistantChooserPage = () => <Chooser current="assistant" title="Research Assistant" lead="Ask questions of one workspace. Answers quote your sources or say there is not enough evidence." suffix="" verb="Ask" />;
export const StudioChooserPage = () => <Chooser current="studio" title="Research Studio" lead="Compare sources, keep findings, build outlines and generate research questions from a finished analysis." suffix="/studio" verb="Open Studio" />;

export function ReportsIndexPage() {
  const res = useResource(() => ri.reports.all(), []);
  return (
    <ResearchFrame current="reports" title="Research Reports" lead="Reports are built from a finished analysis of your sources. Open a report to edit, regenerate or export it; to start a new one, open a workspace’s Studio.">
      <Loaded res={res} what="your reports">
        {(d) => (d.items.length === 0 ? <Empty title="No reports yet"><p>Run an analysis in <Link to="/research-intelligence/studio">Research Studio</Link>, then choose “Create report”.</p></Empty> : (
          <ul className="list">{d.items.map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title"><Link to={`${wsPath(r.workspace_id)}/reports/${encodeURIComponent(r.id)}`}>{r.title}</Link></span><span className="row__meta">{r.type_label} · {r.workspace_name} · {r.source_count} source(s) · version {r.version}{r.edited ? ' · edited by you' : ''} · updated {when(r.updated_at)}</span></div></li>)}</ul>
        ))}
      </Loaded>
    </ResearchFrame>
  );
}
