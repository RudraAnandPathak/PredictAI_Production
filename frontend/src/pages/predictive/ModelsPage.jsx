import { useState } from 'react';
import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { ModelsList } from './ModelsList.jsx';

const PAGE = 25;

/** Interface 10: Model Management. Every saved model with rename, delete and comparison. */
export function ModelsPage() {
  const [offset, setOffset] = useState(0);
  const list = useResource(() => predictive.models({ limit: PAGE, offset }), [offset]);
  const items = list.data?.items ?? [];
  const total = list.data?.total ?? 0;
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/predictions">Predictions</Link> / Models</p>
        <h1 className="page-title">Your models</h1>
        <p className="lead">Every model you have trained. Open one to see how well it works and use it, or tick several that predict the same thing to compare them side by side. To continue a trend over time, use <Link to="/predictions/forecast">Forecast</Link>.</p>
      </header>
      {list.loading && !list.data ? <PageLoading label="Loading models…" /> : null}
      {list.error ? <ErrorState title="Could not load your models" message={describeError(list.error)} onRetry={() => list.reload()} /> : null}
      {!list.loading && !list.error && total === 0 ? (
        <div className="state" role="status"><div className="state__body"><h2 className="state__title">No models yet</h2><p>Train a model from one of your datasets and it will be listed here.</p><Link to="/predictions" className="btn">Choose a dataset</Link></div></div>
      ) : null}
      {items.length ? <ModelsList items={items} onChanged={() => { if (items.length === 1 && offset > 0) setOffset(Math.max(0, offset - PAGE)); else list.reload({ quiet: true }); }} /> : null}
      {total > PAGE ? (
        <nav className="btn-row" aria-label="Pages">
          <button type="button" className="btn btn--ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span className="row__meta">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <button type="button" className="btn btn--ghost" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </nav>
      ) : null}
    </article>
  );
}
