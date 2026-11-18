import { useEffect, useState } from 'react';
import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { PROJECT_STATUS_LABEL, plural } from '../../features/workspace/format.js';
import { CreateProjectModal } from './CreateProjectModal.jsx';
import { StoragePanel } from './StoragePanel.jsx';

export function ProjectsPage({ pickerFor = null }) {
  const { navigate } = useRouter();
  const [status, setStatus] = useState('active');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const projects = useResource(() => workspace.projects.list({ status, q: query, limit: 100 }), [status, query]);
  const items = projects.data?.items ?? [];
  const nothingYet = !projects.loading && !projects.error && items.length === 0 && !query && status === 'active';

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">{pickerFor ? 'Uploads' : 'Workspace'}</p>
        <h1 className="page-title">{pickerFor ? 'Choose a project to add files to' : 'Projects'}</h1>
        <p className="lead">
          {pickerFor
            ? 'Every file belongs to a project. Pick one, then use its Data tab to upload datasets and documents.'
            : 'A project keeps related datasets and documents together and private to you.'}
        </p>
      </header>

      <section className="card" aria-label="Your projects">
        <div className="toolbar">
          <Field label="Search projects" name="search" type="search" value={search} onChange={(e) => setSearch(e.target.value)} />
          <Field as="select" label="Show" name="status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="active">Active projects</option>
            <option value="archived">Archived projects</option>
          </Field>
          <button type="button" className="btn" onClick={() => setCreating(true)}>New project</button>
        </div>

        {projects.loading && !projects.data ? <PageLoading label="Loading your projects…" /> : null}
        {projects.error ? <ErrorState title="Could not load projects" message={describeError(projects.error)} onRetry={() => projects.reload()} /> : null}

        {nothingYet ? (
          <div className="state" role="status">
            <div className="state__body">
              <h2 className="state__title">No projects yet</h2>
              <p>Create your first project to start uploading datasets and documents. You can rename, archive or delete it at any time.</p>
              <button type="button" className="btn" onClick={() => setCreating(true)}>Create your first project</button>
            </div>
          </div>
        ) : null}
        {!projects.loading && !projects.error && items.length === 0 && !nothingYet ? (
          <p className="muted" role="status">{query ? `No projects match “${query}”.` : 'No archived projects.'}</p>
        ) : null}

        {items.length > 0 ? (
          <ul className="rows">
            {items.map((p) => (
              <li className="row" key={p.id}>
                <div className="row__main">
                  <span className="row__title">
                    <Link to={`/projects/${encodeURIComponent(p.id)}${pickerFor ? '?tab=data' : ''}`}>{p.name}</Link>{' '}
                    {p.status === 'archived' ? <span className="badge badge--muted">{PROJECT_STATUS_LABEL.archived}</span> : null}
                  </span>
                  {p.description ? <span className="row__meta">{p.description}</span> : null}
                  <span className="row__meta">
                    {plural(p.datasets, 'dataset')} · {plural(p.documents, 'document')} · updated {formatDate(p.updated_at)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {!pickerFor ? <StoragePanel /> : null}

      {creating ? (
        <CreateProjectModal
          onClose={() => setCreating(false)}
          onCreated={(project) => { setCreating(false); navigate(`/projects/${encodeURIComponent(project.id)}${pickerFor ? '?tab=data' : ''}`); }}
        />
      ) : null}
    </article>
  );
}
