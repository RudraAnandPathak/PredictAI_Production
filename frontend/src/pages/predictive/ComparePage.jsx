import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { ModelComparisonBars } from '../../components/predictive/MlCharts.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { OVERFIT_LABEL, formatScore, formatSeconds, formatWhen, modelTitle } from '../../features/predictive/format.js';
import { Notice } from './shared.jsx';

const bytes = (n) => (n === null || n === undefined ? '—' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const idsFromUrl = () => (new URLSearchParams(window.location.search).get('ids') ?? '').split(',').map((s) => s.trim()).filter(Boolean);

/** Interface 5: Model Comparison across saved models that predict the same target from the same dataset. */
export function ComparePage() {
  const ids = idsFromUrl();
  const key = ids.join(',');
  const result = useResource(() => (ids.length >= 2 ? predictive.compare(ids) : Promise.resolve(null)), [key]);
  const c = result.data;
  const bars = c && c.same_primary_metric ? [...c.models]
    .sort((a, b) => (a.lower_is_better ? a.cv_score - b.cv_score : b.cv_score - a.cv_score))
    .map((m, i) => ({ id: m.id, label: modelTitle(m), rank: i + 1, cv_primary_shown: m.cv_score, cv_std: 0, test_primary_shown: m.test_score, overfitting: m.overfitting, fit_s: m.fit_s, model_bytes: m.model_bytes })) : [];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/predictions">Predictions</Link> / <Link to="/predictions/models">Models</Link> / Compare</p>
        <h1 className="page-title">Compare models</h1>
      </header>
      {ids.length < 2 ? <Notice kind="warn" title="Choose models to compare">Tick two or more models on the <Link to="/predictions/models">models page</Link>.</Notice> : null}
      {ids.length >= 2 && result.loading && !c ? <PageLoading label="Comparing models…" /> : null}
      {ids.length >= 2 && result.error ? <ErrorState title="These models cannot be compared" message={describeError(result.error)} onRetry={() => result.reload()} /> : null}
      {c ? (
        <>
          <p className="lead">All of these predict “{c.target}” from the same dataset.</p>
          {c.notes?.length ? <Notice kind="warn" title="Read with care"><ul>{c.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></Notice> : null}
          <Explanation value={c.explanation} level={2} />
          {bars.length ? <ModelComparisonBars ranking={bars} metricLabel={c.models[0].primary_label} lowerIsBetter={Boolean(c.models[0].lower_is_better)} /> : null}
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Side-by-side comparison">
            <table className="table table--compact">
              <thead><tr><th scope="col">Measure</th>{c.models.map((m) => <th key={m.id} scope="col"><Link to={`/predictions/models/${encodeURIComponent(m.id)}`}>{modelTitle(m)}</Link></th>)}</tr></thead>
              <tbody>
                <tr><th scope="row">Algorithm</th>{c.models.map((m) => <td key={m.id}>{m.algorithm_label}{m.tuned ? ' (tuned)' : ''}</td>)}</tr>
                <tr><th scope="row">Ranked by</th>{c.models.map((m) => <td key={m.id}>{m.primary_label}{m.lower_is_better ? ' (lower is better)' : ''}</td>)}</tr>
                <tr><th scope="row">Cross-validation score</th>{c.models.map((m) => <td key={m.id}>{formatScore(m.cv_score, 4)}</td>)}</tr>
                <tr><th scope="row">Held-out score</th>{c.models.map((m) => <td key={m.id}>{formatScore(m.test_score, 4)}</td>)}</tr>
                {c.shared_metrics.map((k) => <tr key={k}><th scope="row">{k.replace(/_/g, ' ')}</th>{c.models.map((m) => <td key={m.id}>{formatScore(m.metrics[k], 4)}</td>)}</tr>)}
                <tr><th scope="row">Overfitting</th>{c.models.map((m) => <td key={m.id}>{OVERFIT_LABEL[m.overfitting] ?? m.overfitting ?? '—'}</td>)}</tr>
                <tr><th scope="row">Inputs used</th>{c.models.map((m) => <td key={m.id}>{m.n_features}</td>)}</tr>
                <tr><th scope="row">Training rows / test rows</th>{c.models.map((m) => <td key={m.id}>{m.train_rows} / {m.test_rows}</td>)}</tr>
                <tr><th scope="row">Training time</th>{c.models.map((m) => <td key={m.id}>{formatSeconds(m.fit_s)}</td>)}</tr>
                <tr><th scope="row">Size</th>{c.models.map((m) => <td key={m.id}>{bytes(m.model_bytes)}</td>)}</tr>
                <tr><th scope="row">Random seed</th>{c.models.map((m) => <td key={m.id}>{m.seed}</td>)}</tr>
                <tr><th scope="row">Trained</th>{c.models.map((m) => <td key={m.id}>{formatWhen(m.trained_at)}</td>)}</tr>
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </article>
  );
}
