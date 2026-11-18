import { world } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatNumber } from '../../features/analytics/format.js';
import { influenceView, lagMeaning } from '../../features/research/influence.js';
import { Notice } from './shared.jsx';

/** One saved influence analysis. The first thing on the page is that it shows association over time, not cause and effect. */
export function InfluencePage() {
  const { analysisId } = useParams('/research/influence/:analysisId');
  const res = useResource(() => world.influence(analysisId), [analysisId]);
  if (res.loading && !res.data) return <PageLoading label="Opening the analysis…" />;
  if (res.error) {
    const missing = res.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Analysis not found' : 'Could not open the analysis'} message={missing ? 'This analysis does not exist, or it is not yours.' : describeError(res.error)} onRetry={missing ? null : () => res.reload()} />
        <p><Link to="/research" className="btn btn--ghost">Back to research</Link></p>
      </article>
    );
  }
  const a = res.data;
  const v = influenceView(a);
  const back = `/research/p/${encodeURIComponent(a.project_id)}?tab=influence`;
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to={back}>Influence analysis</Link></p>
        <h1 className="page-title">{a.label}</h1>
      </header>

      <Notice kind="warn" title="Association, not cause">
        <p>{v.banner}</p>
        <p className="row__meta">Causal status: <strong>{v.causalStatus}</strong>. Kind of evidence: {v.evidence}.</p>
      </Notice>
      {v.damaged ? <Notice kind="error" title="Fingerprint mismatch">The saved result no longer matches its fingerprint. Do not rely on it; run the analysis again.</Notice> : null}

      <section className="card stack" aria-label="Result">
        <h2 className="card__title">What was found</h2>
        {v.status !== 'ok' ? <p>The analysis could not reach a result: {v.status.replace(/_/g, ' ')}.</p> : null}
        {v.reading ? <p>{v.reading}</p> : null}
        {v.best ? (
          <dl className="kv">
            <div className="kv__pair"><dt>Strongest gap tested</dt><dd>{v.best.lag} day{Math.abs(v.best.lag) === 1 ? '' : 's'} — {lagMeaning(v.best.lag)}</dd></div>
            <div className="kv__pair"><dt>Rank correlation</dt><dd>{v.best.rho}</dd></div>
            <div className="kv__pair"><dt>Adjusted p-value</dt><dd>{v.best.p}</dd></div>
            <div className="kv__pair"><dt>Days compared</dt><dd>{v.best.n}</dd></div>
          </dl>
        ) : null}
        <dl className="kv">
          {v.coverageArticles != null ? <div className="kv__pair"><dt>Articles counted</dt><dd>{v.coverageArticles} over {v.coverageDays} days</dd></div> : null}
          {v.overlapDays != null ? <div className="kv__pair"><dt>Days both series cover</dt><dd>{v.overlapDays}</dd></div> : null}
          {v.dataset ? <div className="kv__pair"><dt>Your data</dt><dd>{v.dataset.name} · {v.dataset.value_column} by {v.dataset.date_column} ({v.dataset.aggregate})</dd></div> : null}
          <div className="kv__pair"><dt>Method</dt><dd>{v.method}</dd></div>
        </dl>
        {v.trend ? <p className="row__meta">{v.trend.strong ? 'A strong trend was found in at least one series, which can create an association by itself.' : 'No strong trend was found in either series.'} (coverage {formatNumber(v.trend.coverage, 2)}, your data {formatNumber(v.trend.value, 2)})</p> : null}
        {v.warnings.length ? <ul aria-label="Warnings">{v.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
      </section>

      {v.lags.length ? (
        <section className="card stack" aria-label="Every gap tested">
          <h2 className="card__title">Every gap tested</h2>
          <div className="table-wrap"><table className="table table--compact">
            <caption>Negative gaps mean your data moved first.</caption>
            <thead><tr><th scope="col">Gap (days)</th><th scope="col">Correlation</th><th scope="col">Days compared</th><th scope="col">p</th><th scope="col">Adjusted p</th></tr></thead>
            <tbody>{v.lags.map((l) => <tr key={l.lag}><th scope="row">{l.lag}</th><td>{formatNumber(l.rho, 3)}</td><td>{l.n}</td><td>{formatNumber(l.p, 3)}</td><td>{formatNumber(l.q, 3)}</td></tr>)}</tbody>
          </table></div>
        </section>
      ) : null}

      <section className="card stack" aria-label="Why this is not cause and effect">
        <h2 className="card__title">Why this is not cause and effect</h2>
        <ul>{v.whyNotCausal.map((w, i) => <li key={i}>{w}</li>)}</ul>
        {v.whatWouldHelp.length ? <><h3 className="sub">What would help show more</h3><ul>{v.whatWouldHelp.map((w, i) => <li key={i}>{w}</li>)}</ul></> : null}
      </section>

      <Explanation value={a.explanation} />
    </article>
  );
}
