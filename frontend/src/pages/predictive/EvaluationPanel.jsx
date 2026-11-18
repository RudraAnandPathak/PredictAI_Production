import { Explanation } from '../../components/ui/Explanation.jsx';
import { ActualVsPredicted, ConfusionMatrix, LearningCurve, PrChart, ResidualHistogram, ResidualPlot, RocChart } from '../../components/predictive/MlCharts.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { OVERFIT_LABEL, OVERFIT_TONE, PROBLEM_LABEL, formatScore, formatShare, problemTypeOf } from '../../features/predictive/format.js';
import { Badge, Notice } from './shared.jsx';

const METRIC_LABEL = {
  accuracy: 'Accuracy', balanced_accuracy: 'Balanced accuracy', precision: 'Precision', recall: 'Recall', f1: 'F1', specificity: 'Specificity', precision_macro: 'Precision (average of classes)',
  recall_macro: 'Recall (average of classes)', f1_macro: 'F1 (average of classes)', precision_weighted: 'Precision (weighted)', recall_weighted: 'Recall (weighted)', f1_weighted: 'F1 (weighted)',
  roc_auc: 'ROC-AUC', roc_auc_ovr: 'ROC-AUC (one class against the rest)', average_precision: 'Average precision (PR-AUC)', brier_score: 'Brier score (lower is better)', log_loss: 'Log loss (lower is better)',
  mae: 'Mean absolute error', mse: 'Mean squared error', rmse: 'Root mean squared error', r2: 'R²', adjusted_r2: 'Adjusted R²', median_absolute_error: 'Median absolute error', max_error: 'Largest error',
  bias: 'Average error (bias)', mape: 'Mean absolute percentage error',
};
const labelOf = (k) => METRIC_LABEL[k] ?? k.replace(/_/g, ' ');

export function MetricsTable({ metrics }) {
  const entries = Object.entries(metrics ?? {}).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return null;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Scores on held-out rows">
      <table className="table table--compact">
        <thead><tr><th scope="col">Measure</th><th scope="col">Score</th></tr></thead>
        <tbody>{entries.map(([k, v]) => <tr key={k}><td>{labelOf(k)}</td><td>{formatScore(v, 4)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function ClassReport({ report }) {
  if (!report) return null;
  const rows = Object.entries(report).filter(([, v]) => v && typeof v === 'object');
  if (!rows.length) return null;
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Scores for each outcome">
      <table className="table table--compact">
        <thead><tr><th scope="col">Outcome</th><th scope="col">Precision</th><th scope="col">Recall</th><th scope="col">F1</th><th scope="col">Rows</th></tr></thead>
        <tbody>{rows.map(([k, v]) => <tr key={k}><td>{k}</td><td>{formatScore(v.precision, 3)}</td><td>{formatScore(v.recall, 3)}</td><td>{formatScore(v['f1-score'], 3)}</td><td>{formatNumber(v.support, 0)}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function Calibration({ cal }) {
  if (!cal?.available) return cal ? <p className="muted">Calibration is not available: {cal.reason ?? 'there are too few held-out rows to judge it.'}</p> : null;
  return (
    <div className="card stack" aria-label="Calibration">
      <h3 className="card__title">Are the probabilities trustworthy?</h3>
      <p>On average the stated probability and the real rate differ by {formatShare(cal.mean_gap, 1)} ({cal.verdict}). A well-calibrated model that says “80%” is right about 8 times in 10.</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Calibration table">
        <table className="table table--compact">
          <thead><tr><th scope="col">Group of rows</th><th scope="col">Probability the model gave</th><th scope="col">How often it really happened</th></tr></thead>
          <tbody>{cal.mean_predicted.map((m, i) => <tr key={i}><td>{i + 1} of {cal.mean_predicted.length}</td><td>{formatShare(m, 1)}</td><td>{formatShare(cal.observed_rate[i], 1)}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

/** Interface 6: Model Evaluation. Everything here comes from rows the model never saw while it was learning. */
export function EvaluationPanel({ report, model }) {
  const ev = report.evaluation;
  const ex = report.explanations ?? {};
  const best = report.best ?? {};
  const problemType = problemTypeOf(report, model);
  const classification = problemType && problemType !== 'regression';
  const bestCandidate = (report.candidates ?? []).find((c) => c.id === best.id);
  const over = bestCandidate?.overfitting;
  return (
    <section className="stack" aria-label="Model evaluation">
      <div className="card stack">
        <h2 className="card__title">How good is this model?</h2>
        <p>{PROBLEM_LABEL[problemType] ?? 'Model'}. Scores below are on {formatNumber(ev.n, 0)} rows kept aside and never used to train, choose or tune the model.</p>
        {ev.basis_note ? <p className="row__meta">{ev.basis_note}</p> : null}
        <MetricsTable metrics={ev.metrics} />
        {ev.not_computed?.length ? <p className="row__meta">Not calculated: {ev.not_computed.map((n) => (typeof n === 'string' ? n : `${n.metric ?? n.name}: ${n.reason}`)).join('; ')}</p> : null}
        <Explanation value={ex.evaluation} />
      </div>

      {report.baseline ? (
        <div className="card stack" aria-label="Baseline">
          <h3 className="card__title">Compared with guessing</h3>
          <p>{report.baseline.description}</p>
          <p>The baseline scores {formatScore(report.baseline.cv_primary_shown, 3)} in cross-validation; this model scores {formatScore(best.cv_primary_shown, 3)} ({best.primary_label}{best.lower_is_better ? ', lower is better' : ''}).</p>
          <Explanation value={ex.baseline} />
        </div>
      ) : null}

      {classification ? (
        <>
          <ConfusionMatrix matrix={ev.confusion_matrix} />
          <ClassReport report={ev.classification_report} />
          <div className="chart-grid">
            <RocChart curves={ev.curves?.roc} basisNote={ev.curves?.roc?.length > 1 ? 'Each curve treats one outcome as positive and the rest as negative.' : ''} />
            <PrChart curves={ev.curves?.pr} />
          </div>
          <Calibration cal={ev.calibration} />
        </>
      ) : (
        <>
          <div className="card stack" aria-label="Typical error">
            <h3 className="card__title">How far off are the predictions?</h3>
            {ev.typical_error ? (
              <p>80% of predictions were off by no more than {formatScore(ev.typical_error.within_80, 3)}, and 95% by no more than {formatScore(ev.typical_error.within_95, 3)} (measured on held-out rows). The target itself ranges from {formatScore(ev.target_range?.min, 2)} to {formatScore(ev.target_range?.max, 2)}.</p>
            ) : <p className="muted">Typical error is not available.</p>}
          </div>
          <ActualVsPredicted scatter={ev.scatter} targetName={model?.target} />
          <div className="chart-grid">
            <ResidualPlot scatter={ev.scatter} />
            <ResidualHistogram residuals={ev.residuals} />
          </div>
        </>
      )}

      <div className="card stack" aria-label="Overfitting">
        <h3 className="card__title">Does it generalise?</h3>
        {over ? <p><Badge tone={OVERFIT_TONE[over.level]}>{OVERFIT_LABEL[over.level] ?? over.level}</Badge> {over.message}</p> : <p className="muted">No overfitting check is available for this model.</p>}
        <Explanation value={ex.overfitting} />
        <LearningCurve curve={report.learning_curve} metricLabel={best.primary_label} lowerIsBetter={Boolean(best.lower_is_better)} />
        {report.learning_curve && !report.learning_curve.available ? <p className="muted">The learning curve is not available: {report.learning_curve.reason ?? 'there are too few rows to draw one.'}</p> : null}
        <Explanation value={ex.learning_curve} />
      </div>

      {report.warnings?.length ? <Notice kind="warn" title="Cautions"><ul>{report.warnings.map((w, i) => <li key={i}>{typeof w === 'string' ? w : w.message}</li>)}</ul></Notice> : null}
      {report.limitations?.length ? <details><summary>Limits of these results</summary><ul>{report.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></details> : null}
    </section>
  );
}
