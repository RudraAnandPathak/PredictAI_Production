import { useEffect, useState } from 'react';
import { workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { contents } from '../projects/AssetTable.jsx';

/** Picker: every ready dataset across the person's active projects. Authorisation is the same as the Projects pages (only their own). */
export function AnalyticsPage() {
  const [state, setState] = useState({ loading: true, error: null, groups: [] });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const projects = (await workspace.projects.list({ status: 'active', limit: 100 })).items ?? [];
        const groups = [];
        for (const p of projects) {
          if (!p.datasets) continue;
          const assets = (await workspace.assets.list(p.id, { kind: 'dataset', limit: 100 })).items ?? [];
          if (assets.length) groups.push({ project: p, assets });
        }
        if (live) setState({ loading: false, error: null, groups });
      } catch (error) { if (live) setState({ loading: false, error, groups: [] }); }
    })();
    return () => { live = false; };
  }, [tick]);
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Analytics</p>
        <h1 className="page-title">Analyse a dataset</h1>
        <p className="lead">Pick a dataset to explore it, test a hypothesis, draw charts or ask questions. Every number is calculated on the server, and each result shows the data and method behind it.</p>
      </header>
      {state.loading ? <PageLoading label="Finding your datasets…" /> : null}
      {state.error ? <ErrorState title="Could not load your datasets" message={describeError(state.error)} onRetry={() => { setState({ loading: true, error: null, groups: [] }); setTick((n) => n + 1); }} /> : null}
      {!state.loading && !state.error && state.groups.length === 0 ? (
        <div className="state" role="status"><div className="state__body"><h2 className="state__title">No datasets yet</h2><p>Upload a CSV or Excel file in a project first.</p><Link to="/projects" className="btn">Go to projects</Link></div></div>
      ) : null}
      {state.groups.map(({ project, assets }) => (
        <section key={project.id} className="card" aria-label={project.name}>
          <h2 className="card__title">{project.name}</h2>
          <ul className="list">
            {assets.map((a) => (
              <li key={a.id} className="row">
                <div className="row__main">
                  <span className="row__title">{a.status === 'ready' ? <Link to={`/analytics/${encodeURIComponent(a.id)}`}>{a.name}</Link> : a.name}</span>
                  <span className="row__meta">{a.status === 'ready' ? `${contents(a)}` : 'Not ready for analysis'}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </article>
  );
}
