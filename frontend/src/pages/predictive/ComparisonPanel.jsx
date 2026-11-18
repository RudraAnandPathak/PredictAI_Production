import { Explanation } from '../../components/ui/Explanation.jsx';
import { ModelComparisonBars } from '../../components/predictive/MlCharts.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { OVERFIT_LABEL, OVERFIT_TONE, formatScore, formatSeconds } from '../../features/predictive/format.js';
import { Badge, Notice } from './shared.jsx';

const bytes = (n) => (n === null || n === undefined ? '—' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** Interface 5 (within one training run): every candidate, ranked by cross-validation, with the reason for the order. */
export function ComparisonPanel({ report }) {
  const ranking = report.ranking ?? [];
  const best = report.best ?? {};
  const ex = report.explanations ?? {};
  const label = best.primary_label ?? 'Score';
  const skipped = (report.candidates ?? []).filter((c) => c.status && c.status !== 'ok' && c.status !== 'trained');
  return (
    <section className="stack" aria-label="Model comparison">
      <div className="card stack">
        <h2 className="card__title">How the candidates compare</h2>
        <p>Every algorithm was trained and tested the same way. They are ranked by <strong>{label}</strong> in cross-validation on the training rows{best.lower_is_better ? ' (lower is better)' : ''}. The held-out rows were not used to choose.</p>
        <Explanation value={ex.ranking} />
      </div>
      <ModelComparisonBars ranking={ranking} metricLabel={label} lowerIsBetter={Boolean(best.lower_is_better)} />
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Candidate ranking">
        <table className="table table--compact">
          <thead>
            <tr><th scope="col">Rank</th><th scope="col">Model</th><th scope="col">{label} (cross-validation)</th><th scope="col">Spread</th><th scope="col">{label} (held-out)</th><th scope="col">Overfitting</th><th scope="col">Training time</th><th scope="col">Size</th></tr>
          </thead>
          <tbody>
            {ranking.map((r) => (
              <tr key={r.id} className={r.id === best.id ? 'is-best' : undefined}>
                <td>{r.rank}</td>
                <td>{r.label}{r.id === best.id ? <> <Badge tone="ok">chosen</Badge></> : null}{r.within_one_se_of_best && r.id !== best.id ? <> <Badge tone="muted" title="Within one standard error of the best">effectively tied</Badge></> : null}</td>
                <td>{formatScore(r.cv_primary_shown, 4)}</td>
                <td>± {formatScore(r.cv_std, 3)}</td>
                <td>{formatScore(r.test_primary_shown, 4)}</td>
                <td><Badge tone={OVERFIT_TONE[r.overfitting] ?? 'muted'}>{OVERFIT_LABEL[r.overfitting] ?? r.overfitting}</Badge></td>
                <td>{formatSeconds(r.fit_s)}</td>
                <td>{bytes(r.model_bytes)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {best.simpler_alternative ? <Notice kind="info" title="A simpler option performs about as well">The “{ranking.find((r) => r.id === best.simpler_alternative)?.label ?? best.simpler_alternative}” model is within normal variation of the chosen one and is easier to understand and run.</Notice> : null}
      {skipped.length ? (
        <div className="card stack"><h3 className="card__title">Not trained</h3>
          <ul>{skipped.map((c) => <li key={c.id}><strong>{c.label}</strong>: {c.reason || c.status}</li>)}</ul></div>
      ) : null}
      {report.algorithms?.skipped?.length ? (
        <div className="card stack"><h3 className="card__title">Algorithms left out</h3>
          <ul>{report.algorithms.skipped.map((a, i) => <li key={i}><strong>{a.label ?? a.key}</strong>: {a.reason}</li>)}</ul></div>
      ) : null}
      {report.hpo ? (
        <div className="card stack" aria-label="Tuning">
          <h3 className="card__title">Tuning</h3>
          {report.hpo.algorithms?.map((a) => (
            <div key={a.algorithm}>
              <p><strong>{a.label}</strong>: {a.trials_run} of {a.trials_planned} settings tried{a.stopped_early ? ' (stopped early by the time limit)' : ''}; {formatScore(a.baseline_score, 4)} with the defaults{a.best_score !== undefined ? `, ${formatScore(a.best_score, 4)} with the best settings found` : ''}.</p>
              {a.best_params && Object.keys(a.best_params).length ? <p className="row__meta">Best settings: {Object.entries(a.best_params).map(([k, v]) => `${k} = ${typeof v === 'number' ? formatNumber(v, 4) : String(v)}`).join(', ')}</p> : null}
            </div>
          ))}
          {report.hpo.caution ? <p className="row__meta">{report.hpo.caution}</p> : null}
          <Explanation value={ex.hpo} />
        </div>
      ) : null}
    </section>
  );
}
