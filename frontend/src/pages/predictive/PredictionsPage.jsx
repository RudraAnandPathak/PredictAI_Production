import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { contents } from '../projects/AssetTable.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { ModelRow, Stat } from './shared.jsx';
import { useDatasets } from './useDatasets.js';

const EXPLAIN_LABEL = { permutation_importance: 'Permutation importance', native_importance: 'Importance reported by the model', partial_dependence: 'Partial dependence charts', local_effects: 'Why this prediction (one-input-at-a-time)', shap: 'SHAP values' };

/** Interface 1: Predictive Analytics Dashboard. Where to start, what exists, and what this build can and cannot do. */
export function PredictionsPage() {
  const recent = useResource(() => predictive.models({ limit: 6 }), []);
  const catalog = useResource(() => predictive.catalog(), []);
  const datasets = useDatasets();
  const total = recent.data?.total ?? 0;
  const items = recent.data?.items ?? [];
  const datasetCount = datasets.groups.reduce((n, g) => n + g.assets.length, 0);
  const ex = catalog.data?.explainability ?? {};

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Predictions</p>
        <h1 className="page-title">Predictive analytics</h1>
        <p className="lead">Train models that predict a category or a number from your data, see how well they really work, understand what they rely on, and use them on new cases. Everything runs on your own data, on this server, with free open-source tools.</p>
      </header>

      <div className="stat-grid" aria-label="Summary">
        <Stat label="Models saved" value={recent.loading && !recent.data ? '…' : formatNumber(total, 0)} />
        <Stat label="Datasets you can model" value={datasets.loading ? '…' : formatNumber(datasetCount, 0)} />
        <Stat label="Algorithms available" value={catalog.data ? formatNumber(catalog.data.algorithms.length, 0) : '…'} />
      </div>

      <section className="card stack" aria-label="Start a model">
        <h2 className="card__title">Start with a dataset</h2>
        {datasets.loading ? <PageLoading label="Finding your datasets…" /> : null}
        {datasets.error ? <ErrorState title="Could not load your datasets" message={describeError(datasets.error)} onRetry={datasets.reload} /> : null}
        {!datasets.loading && !datasets.error && datasets.groups.length === 0 ? (
          <div className="state" role="status"><div className="state__body"><h3 className="state__title">No datasets yet</h3><p>Upload a CSV or Excel file in a project first.</p><Link to="/projects" className="btn">Go to projects</Link></div></div>
        ) : null}
        {datasets.groups.map(({ project, assets }) => (
          <div key={project.id} aria-label={project.name}>
            <h3 className="sub">{project.name}</h3>
            <ul className="list">
              {assets.map((a) => (
                <li key={a.id} className="row">
                  <div className="row__main">
                    <span className="row__title">{a.status === 'ready' ? <Link to={`/predictions/data/${encodeURIComponent(a.id)}`}>{a.name}</Link> : a.name}</span>
                    <span className="row__meta">{a.status === 'ready' ? contents(a) : 'Not ready for modelling'}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section className="card stack" aria-label="Recent models">
        <div className="card__row"><h2 className="card__title">Recent models</h2><Link to="/predictions/models" className="btn btn--ghost btn--sm">All models</Link></div>
        {recent.error ? <ErrorState title="Could not load your models" message={describeError(recent.error)} onRetry={() => recent.reload()} /> : null}
        {!recent.loading && !recent.error && items.length === 0 ? <p className="muted">No models yet. Choose a dataset above to train your first one.</p> : null}
        <ul className="list">{items.map((m) => <ModelRow key={m.id} model={m} />)}</ul>
      </section>

      <section className="card stack" aria-label="How it works">
        <h2 className="card__title">How it works</h2>
        <ol className="steps">
          <li><strong>Choose what to predict.</strong> PredictAI works out whether it is a yes/no question, a choice between categories, or a number.</li>
          <li><strong>Check the data.</strong> Missing values, repeated rows, columns that give the answer away, and imbalance are found and explained before anything is trained.</li>
          <li><strong>Train and compare.</strong> Several algorithms are tested the same way on rows they never saw, and ranked on cross-validation.</li>
          <li><strong>Understand and use.</strong> See what the model relies on, predict single cases or whole datasets, and download the results.</li>
        </ol>
        <p className="muted">A model finds patterns in past data. It does not show what causes an outcome, and it can be wrong when new cases differ from the data it learned from.</p>
      </section>

      {catalog.data ? (
        <section className="card stack" aria-label="What explanations are included">
          <h2 className="card__title">What this build can explain</h2>
          <ul>{Object.entries(EXPLAIN_LABEL).map(([k, label]) => <li key={k}>{label}: <strong>{ex[k] ? 'included' : 'not included'}</strong></li>)}</ul>
          {ex.shap_note ? <p className="row__meta">{ex.shap_note}</p> : null}
        </section>
      ) : null}
    </article>
  );
}
