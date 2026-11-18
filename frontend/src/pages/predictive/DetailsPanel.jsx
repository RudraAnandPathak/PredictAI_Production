import { useState } from 'react';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Link } from '../../app/router/Link.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { describeConfig } from '../../features/predictive/config.js';
import { PROBLEM_LABEL, formatMs, formatScore, formatWhen, headline } from '../../features/predictive/format.js';
import { DeleteModelModal, RenameModal } from './ModelActions.jsx';
import { JobBadge, KeyFacts } from './shared.jsx';

const bytes = (n) => (n === null || n === undefined ? '—' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

/** Interface 10 (detail part): the model's metadata, version history and training history, plus rename and delete. */
export function DetailsPanel({ model, report, onChanged }) {
  const { navigate } = useRouter();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const ex = report?.explanations ?? {};
  const params = model.hyperparameters && Object.keys(model.hyperparameters).length ? Object.entries(model.hyperparameters).map(([k, v]) => `${k} = ${typeof v === 'number' ? formatNumber(v, 4) : String(v)}`).join(', ') : 'default settings';
  return (
    <section className="stack" aria-label="Model details">
      <div className="card stack">
        <h2 className="card__title">About this model</h2>
        <KeyFacts items={[
          ['Name', model.name], ['Algorithm', `${model.algorithm_label}${model.tuned ? ' (tuned)' : ''}`], ['Predicts', `${model.target} — ${PROBLEM_LABEL[model.problem_type] ?? model.problem_type}`],
          ['Version', `${model.model_version}`], ['Headline score', headline(model)], ['Cross-validation score', `${model.primary_label} ${formatScore(model.cv_score, 4)}`],
          ['Held-out score', model.test_score === null || model.test_score === undefined ? 'none (no rows were held back)' : `${model.primary_label} ${formatScore(model.test_score, 4)}`],
          ['Trained', formatWhen(model.trained_at)], ['Training time', formatMs(model.duration_ms)], ['Size', bytes(model.model_bytes)], ['Random seed', String(model.seed)],
          ['Settings', params], ['Inputs used', (model.features?.used ?? []).join(', ')], ['Inputs left out', `${model.features?.dropped ?? 0}`],
          ['Trained on', model.dataset ? `${model.dataset.name ?? 'dataset'}${model.dataset.version_number ? `, version ${model.dataset.version_number}` : ''}${model.dataset.rows ? `, ${formatNumber(model.dataset.rows, 0)} rows` : ''}` : undefined],
          ['Model fingerprint (SHA-256)', model.model_sha256], ['Engine version', `${model.ml_version}`],
        ]} />
        <div className="btn-row">
          <button type="button" className="btn btn--ghost" onClick={() => setRenaming(true)}>Rename</button>
          <button type="button" className="btn btn--ghost" onClick={() => setDeleting(true)}>Delete</button>
          <Link className="btn btn--ghost" to={`/predictions/data/${encodeURIComponent(model.asset_id)}?tab=train`}>Train again on this dataset</Link>
        </div>
      </div>

      {model.config ? (
        <div className="card stack" aria-label="Training settings">
          <h3 className="card__title">Settings used</h3>
          <ul>{describeConfig({ ...model.config, hpo: model.config.hpo?.method ?? model.config.hpo, test_size: model.config.validation?.test_size, cv_folds: model.config.validation?.cv_folds }).map((l, i) => <li key={i}>{l}</li>)}</ul>
          <p className="row__meta">The full configuration, library versions and seed are stored with the model, so the same run can be reproduced.</p>
        </div>
      ) : null}

      {report?.features?.used?.length ? (
        <div className="card stack" aria-label="How inputs were prepared">
          <h3 className="card__title">How the inputs were prepared</h3>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Input preparation">
            <table className="table table--compact">
              <thead><tr><th scope="col">Input</th><th scope="col">Kind</th><th scope="col">Why it is used</th></tr></thead>
              <tbody>{report.features.used.map((f) => <tr key={f.name}><td>{f.name}</td><td>{f.role}</td><td>{f.why}</td></tr>)}</tbody>
            </table>
          </div>
          {report.features.dropped?.length ? <><h4 className="sub">Left out</h4><ul>{report.features.dropped.map((f) => <li key={f.name}><strong>{f.name}</strong>: {f.why ?? f.reason}</li>)}</ul></> : null}
          <Explanation value={ex.features} />
        </div>
      ) : null}

      {model.versions?.length > 1 ? (
        <div className="card stack" aria-label="Versions">
          <h3 className="card__title">Versions on this dataset and target</h3>
          <ul className="list">
            {model.versions.map((v) => (
              <li key={v.id} className="row">
                <div className="row__main">
                  <span className="row__title">{v.id === model.id ? <strong>Version {v.model_version} (this one)</strong> : <Link to={`/predictions/models/${encodeURIComponent(v.id)}`}>Version {v.model_version}</Link>} — {v.algorithm_label}</span>
                  <span className="row__meta">Trained {formatWhen(v.trained_at)} · held-out {formatScore(v.test_score, 3)} · cross-validation {formatScore(v.cv_score, 3)}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card stack" aria-label="Training history">
        <h3 className="card__title">Training history</h3>
        {model.training_history?.length ? (
          <ul className="list">
            {model.training_history.map((j) => (
              <li key={j.id} className="row">
                <div className="row__main"><span className="row__title">{j.label || 'Training run'} <JobBadge status={j.status} /></span><span className="row__meta">{formatWhen(j.created_at)}{j.duration_ms ? ` · ${formatMs(j.duration_ms)}` : ''}</span></div>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No training runs are recorded.</p>}
      </div>
      {renaming ? <RenameModal model={model} onClose={() => setRenaming(false)} onDone={() => { setRenaming(false); onChanged?.(); }} /> : null}
      {deleting ? <DeleteModelModal model={model} onClose={() => setDeleting(false)} onDone={() => navigate('/predictions/models')} /> : null}
    </section>
  );
}
