import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { consentLines } from '../../features/research/plan.js';
import { Empty } from './shared.jsx';
import { useProjects } from './useProjects.js';

/** Research dashboard: pick a project, see what is allowed to leave the server, and what is not built yet. */
export function ResearchPage() {
  const projects = useProjects();
  const settings = useResource(() => research.settings(), []);
  const exam = useResource(() => research.examCapabilities(), []);
  const lines = consentLines(settings.data);

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Research</p>
        <h1 className="page-title">Research and world intelligence</h1>
        <p className="lead">Ask a question and get an answer built from quoted passages, with the source and place of every quote. Research starts from your own documents; the web and news feeds are used only when you switch them on, and every item says where it came from.</p>
      </header>

      <section className="card stack" aria-label="What is allowed">
        <div className="card__row"><h2 className="card__title">What is allowed to leave this server</h2><Link to="/research/settings" className="btn btn--ghost btn--sm">Change settings</Link></div>
        {settings.loading && !settings.data ? <PageLoading label="Reading your settings…" /> : null}
        {settings.error ? <ErrorState title="Could not load your settings" message={describeError(settings.error)} onRetry={() => settings.reload()} /> : null}
        {lines.length ? <ul>{lines.map((l, i) => <li key={i}>{l}</li>)}</ul> : null}
        <p className="row__meta">Everything outside this server is off until you turn it on, and your own documents and datasets are never sent to a web search or a news feed.</p>
      </section>

      <section className="card stack" aria-label="Projects">
        <h2 className="card__title">Choose a project</h2>
        {projects.loading && !projects.data ? <PageLoading label="Finding your projects…" /> : null}
        {projects.error ? <ErrorState title="Could not load your projects" message={describeError(projects.error)} onRetry={() => projects.reload()} /> : null}
        {!projects.loading && !projects.error && projects.items.length === 0 ? (
          <Empty title="No projects yet"><p>Research belongs to a project, so that your documents, sources and findings stay together and apart from other projects.</p><Link to="/projects" className="btn">Go to projects</Link></Empty>
        ) : null}
        <ul className="list">
          {projects.items.map((p) => (
            <li key={p.id} className="row" data-project-id={p.id}>
              <div className="row__main">
                <span className="row__title"><Link to={`/research/p/${encodeURIComponent(p.id)}`}>{p.name}</Link></span>
                <span className="row__meta">{p.documents ?? 0} document{p.documents === 1 ? '' : 's'} · {p.datasets ?? 0} dataset{p.datasets === 1 ? '' : 's'}</span>
              </div>
              <Link to={`/research/p/${encodeURIComponent(p.id)}/new`} className="btn btn--sm">New research</Link>
            </li>
          ))}
        </ul>
      </section>

      {exam.data ? (
        <section className="card stack" aria-label="Not available yet">
          <h2 className="card__title">Not available yet</h2>
          <p className="muted">{exam.data.phase}</p>
          <ul>{exam.data.not_available.map((n) => <li key={n.id}><strong>{n.feature}</strong> ({n.id}). <span className="row__meta">{n.needs}</span></li>)}</ul>
        </section>
      ) : null}
    </article>
  );
}
