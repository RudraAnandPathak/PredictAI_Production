import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Empty, Loaded, when } from '../../../pages/phase7/shared.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';

const wsLink = (id) => `/research-intelligence/${encodeURIComponent(id)}`;
const ACT = { analysis: 'Analysis', report: 'Report', generated: 'Studio output' };

/** Where to pick up: recent workspaces, sources, saved findings, reports and activity. */
export function ResearchHomePage() {
  const res = useResource(() => ri.home(), []);
  return (
    <ResearchFrame current="home" title="Research Home" lead="Pick up where you left off. Everything here comes from your own workspaces; nothing is shared.">
      <Loaded res={res} what="your research home">
        {(d) => (
          <div className="ri-home">
            <section className="card" aria-label="Recent workspaces">
              <h2 className="card__title">Recent workspaces</h2>
              {d.workspaces.length === 0 ? <Empty title="No workspaces yet"><p><Link to="/research-intelligence/workspaces">Create your first workspace</Link>, or <Link to="/research-intelligence/discover">discover papers</Link> first.</p></Empty> : (
                <ul className="list">{d.workspaces.map((w) => <li key={w.id} className="row"><div className="row__main"><span className="row__title"><Link to={wsLink(w.id)}>{w.name}</Link></span><span className="row__meta">{w.sources} source(s) · updated {when(w.updated_at)} · <Link to={`${wsLink(w.id)}/studio`}>Studio</Link></span></div></li>)}</ul>
              )}
            </section>
            <section className="card" aria-label="Recent activity">
              <h2 className="card__title">Recent activity</h2>
              {d.activity.length === 0 ? <p className="row__meta">No analyses, reports or studio outputs yet.</p> : (
                <ul className="list">{d.activity.map((a) => <li key={`${a.kind}-${a.id}`} className="row"><div className="row__main"><span className="row__title">{ACT[a.kind] ?? a.kind}: {a.kind === 'report' ? <Link to={`${wsLink(a.workspace_id)}/reports/${encodeURIComponent(a.id)}`}>{a.title}</Link> : <Link to={`${wsLink(a.workspace_id)}/studio`}>{a.title}</Link>}</span><span className="row__meta">{when(a.at)}</span></div></li>)}</ul>
              )}
            </section>
            <section className="card" aria-label="Recent sources">
              <h2 className="card__title">Recent sources</h2>
              {d.sources.length === 0 ? <p className="row__meta">No sources yet.</p> : <ul className="list">{d.sources.map((s) => <li key={s.id} className="row"><div className="row__main"><span className="row__title">{s.title}</span><span className="row__meta">{s.workspace_name} · {s.status}</span></div></li>)}</ul>}
            </section>
            <section className="card" aria-label="Saved findings">
              <h2 className="card__title">Saved findings</h2>
              {d.findings.length === 0 ? <p className="row__meta">Nothing saved yet. Open a citation in a workspace and choose “Save as evidence”.</p> : <ul className="list">{d.findings.map((f) => <li key={f.id} className="row"><div className="row__main"><span className="note__body">{f.text}</span><span className="row__meta">{f.source_title} · {f.workspace_name}</span></div></li>)}</ul>}
            </section>
            <section className="card" aria-label="Reports">
              <h2 className="card__title">Reports</h2>
              {d.reports.length === 0 ? <p className="row__meta">No reports yet. Run an analysis in a workspace’s Studio, then create a report.</p> : <ul className="list">{d.reports.map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title"><Link to={`${wsLink(r.workspace_id)}/reports/${encodeURIComponent(r.id)}`}>{r.title}</Link></span><span className="row__meta">{r.workspace_name} · updated {when(r.updated_at)}</span></div></li>)}</ul>}
            </section>
          </div>
        )}
      </Loaded>
    </ResearchFrame>
  );
}
