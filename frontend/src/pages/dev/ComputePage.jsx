import { dev } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { COMPUTE_LIMIT_LABEL, accessSummary, bytesText } from '../../features/dev/labels.js';
import { Badge, Empty, Loaded, Meter, Notice, PageHeader } from './shared.jsx';

/** Usage, quotas and the honest list of what this installation can and cannot do, for one project. */
export function ComputePanel({ projectId }) {
  const usage = useResource(() => dev.compute.usage(projectId), [projectId]);
  const caps = useResource(() => dev.compute.capabilities(), []);
  return (
    <div>
      <Loaded res={usage} what="usage">
        {(d) => (
          <>
            {d.warnings.map((w) => <Notice key={w} kind="warn">{w}</Notice>)}
            <section><h3>Usage</h3>
              <Meter label="Processor time today" used={d.today.user_cpu_seconds} limit={d.today.user_cpu_seconds_limit} format={(n) => `${n} s`} />
              <Meter label="Your running jobs" used={d.concurrency.user_running} limit={d.concurrency.user_limit} />
              <Meter label="This project’s running jobs" used={d.concurrency.project_running} limit={d.concurrency.project_limit} />
              <Meter label="Queue" used={d.queue.waiting_total} limit={d.queue.capacity} />
              <Meter label="Project storage" used={d.storage.files_bytes + d.storage.artifact_bytes} limit={d.storage.limit_bytes} format={bytesText} />
            </section>
            <section><h3>Quotas</h3>
              <ul className="list">{Object.entries(d.limits).map(([k, v]) => <li key={k} className="row"><div className="row__main"><span className="row__title">{COMPUTE_LIMIT_LABEL[k] ?? k}</span><span className="row__meta">{v}</span></div></li>)}</ul>
              <p className="row__meta">Limits can be lowered per job but never raised. Only the platform owner can change a quota, with a recorded reason.</p>
            </section>
            <section><h3>Jobs by state</h3>{Object.keys(d.jobs_by_status).length === 0 ? <p className="row__meta">No jobs yet.</p> : <p>{Object.entries(d.jobs_by_status).map(([s, n]) => <Badge key={s} tone="muted">{s}: {n}</Badge>)}</p>}</section>
          </>
        )}
      </Loaded>
      <Loaded res={caps} what="resource status">
        {(d) => (
          <section><h3>Resource status</h3>
            <ul className="list">{Object.entries(d.capabilities ?? {}).map(([k, v]) => <li key={k} className="row"><div className="row__main"><span className="row__title">{k.replace(/_/g, ' ')}</span><span className="row__meta">{typeof v === 'string' ? v : JSON.stringify(v)}</span></div></li>)}</ul>
            <p className="row__meta">GPU: {d.gpu?.available ? 'available' : d.gpu?.reason}</p>
            <p className="row__meta">Execution mode: {d.execution_mode}</p>
          </section>
        )}
      </Loaded>
    </div>
  );
}

/** The sidebar's Compute entry: shows the person's own projects and sends them to the project's Compute tab. */
export function ComputePage() {
  const res = useResource(() => dev.overview(), []);
  return (
    <article className="page">
      <PageHeader eyebrow="Professional" title="Compute" lead="Usage, quotas, resource status and job controls, per project." />
      <Loaded res={res} what="compute">
        {(d) => {
          const access = accessSummary(d);
          if (access.state !== 'open') return <Notice kind="warn" title="Locked">{access.text}</Notice>;
          return d.projects.length === 0 ? <Empty title="No projects yet"><p><Link to="/projects">Create a project</Link> first.</p></Empty> : (
            <ul className="list">{d.projects.map((p) => <li key={p.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/development/${encodeURIComponent(p.id)}?tab=compute`}>{p.name}</Link></span></div></li>)}</ul>
          );
        }}
      </Loaded>
    </article>
  );
}
