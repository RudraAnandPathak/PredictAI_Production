import { dev } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { accessSummary, executionSummary } from '../../features/dev/labels.js';
import { Badge, Empty, JobStatus, Loaded, Notice, PageHeader, when } from './shared.jsx';
import { Stat as StatBox } from '../predictive/shared.jsx';

/** Landing page. Every number comes from the server; a person without Special access sees why, and nothing else. */
export function DevelopmentPage() {
  const res = useResource(() => dev.overview(), []);
  return (
    <article className="page">
      <PageHeader eyebrow="Professional" title="Development workspace" lead="Notebooks, code, environments, jobs, experiments and Git, scoped to your own projects." />
      <Loaded res={res} what="the development workspace">
        {(d) => {
          const access = accessSummary(d);
          if (access.state !== 'open') {
            return <Notice kind="warn" title="Locked">{access.text} <Link to="/account">Open your account</Link></Notice>;
          }
          const run = executionSummary(d.execution);
          return (
            <>
              <Notice kind={run.runs ? 'info' : 'warn'} title="Running code">{run.text}</Notice>
              <div className="stats">
                {Object.entries({ Workspaces: d.counts.workspaces, Files: d.counts.files, Environments: d.counts.environments, Experiments: d.counts.experiments, Artifacts: d.counts.artifacts, Pipelines: d.counts.pipelines, 'Active jobs': d.jobs.active, 'Queued jobs': d.jobs.queued })
                  .map(([k, v]) => <StatBox key={k} label={k} value={v} />)}
              </div>
              {d.counts.suspended_workspaces ? <Notice kind="warn">{d.counts.suspended_workspaces} workspace(s) are suspended because access was removed. Files are kept and return when access does.</Notice> : null}
              <h2>Your projects</h2>
              {d.projects.length === 0 ? <Empty title="No projects yet"><p>Create a project first, then open it here. <Link to="/projects">Go to projects</Link></p></Empty> : (
                <ul className="list">{d.projects.map((p) => (
                  <li key={p.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/development/${encodeURIComponent(p.id)}`}>{p.name}</Link></span>
                    <span className="row__meta">{p.has_workspace ? 'Workspace created' : 'No workspace yet — it is created when you first save a file'}</span></div></li>))}</ul>
              )}
              <h2>Recent jobs</h2>
              {d.jobs.recent.length === 0 ? <p className="row__meta">No jobs have run yet.</p> : (
                <ul className="list">{d.jobs.recent.map((j) => (
                  <li key={j.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/development/${encodeURIComponent(j.project_id)}?tab=jobs`}>{j.entry || j.kind}</Link></span>
                    <span className="row__meta"><JobStatus status={j.status} /> · {when(j.created_at)}</span></div></li>))}</ul>
              )}
              <h2>Recent activity</h2>
              {d.history.length === 0 ? <p className="row__meta">Nothing recorded yet.</p> : <ul className="list">{d.history.map((e) => <li key={e.id} className="row"><div className="row__main"><span className="row__title">{e.action}</span><span className="row__meta">{e.outcome} · {when(e.created_at)}</span></div></li>)}</ul>}
              <Notice kind="info" title="What is not available">
                {Object.entries(d.capabilities ?? {}).map(([k, v]) => <p key={k} className="row__meta"><Badge tone="muted">{k.replace(/_/g, ' ')}</Badge> {String(v)}</p>)}
              </Notice>
            </>
          );
        }}
      </Loaded>
    </article>
  );
}
