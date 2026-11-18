import { useEffect, useState } from 'react';
import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { algorithmsFor, buildConfig, hasErrors, kindOfProblem } from '../../features/predictive/config.js';
import { isFinal } from '../../features/predictive/jobs.js';
import { SUITABILITY_LABEL, formatMs, formatWhen } from '../../features/predictive/format.js';
import { JobPanel } from './JobPanel.jsx';
import { CheckField, ChoiceField, NumberField } from './OptionField.jsx';
import { JobBadge, Notice } from './shared.jsx';

function AlgorithmPicker({ ws, kind }) {
  const { catalog, form } = ws;
  const list = algorithmsFor(catalog, kind);
  const auto = form.algorithms === 'auto';
  const chosen = auto ? [] : form.algorithms;
  const toggle = (key) => ws.update({ algorithms: chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key] });
  return (
    <fieldset className="card stack" aria-label="Algorithms">
      <legend className="card__title">Which algorithms to try</legend>
      <label className="check-field">
        <input type="radio" name="algos" checked={auto} onChange={() => ws.update({ algorithms: 'auto' })} />
        <span><span className="check-field__label">Choose automatically (recommended)</span><span className="field__hint">Tries a range from simple to powerful that suits the size of your data, and ranks them fairly.</span></span>
      </label>
      <label className="check-field">
        <input type="radio" name="algos" checked={!auto} onChange={() => ws.update({ algorithms: list.slice(0, 4).map((a) => a.key) })} />
        <span><span className="check-field__label">I will choose</span></span>
      </label>
      {!auto ? (
        <ul className="algo-list">
          {list.map((a) => (
            <li key={a.key}>
              <label className="check-field">
                <input type="checkbox" checked={chosen.includes(a.key)} onChange={() => toggle(a.key)} />
                <span><span className="check-field__label">{a.label}</span><span className="field__hint">{a.description}</span></span>
              </label>
            </li>
          ))}
        </ul>
      ) : null}
      {!auto && chosen.length === 0 ? <p className="field__error">Choose at least one algorithm.</p> : null}
    </fieldset>
  );
}

/** Interface 4: AutoML Training. Settings, start, live progress, cancel and past runs. */
export function TrainingPanel({ ws }) {
  const { schema, catalog, form, errors, assessment, tracker } = ws;
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const [cancelling, setCancelling] = useState(false);
  const [past, setPast] = useState({ loading: true, items: [], error: '' });
  const kind = kindOfProblem(form.problemType !== 'auto' ? form.problemType : (assessment.data?.problem?.chosen ?? assessment.data?.problem?.recommended ?? null));
  const columns = schema.columns ?? [];
  const timeCols = columns.filter((c) => (c.role === 'datetime' || c.role === 'numeric') && c.name !== form.target);
  const live = tracker.job && !isFinal(tracker.job.status);
  const algoMissing = form.algorithms !== 'auto' && form.algorithms.length === 0;
  const verdict = assessment.data?.suitability?.verdict;
  const blocked = verdict === 'blocked' && !assessment.stale;
  const cannotStart = hasErrors(errors) || algoMissing || busy || live || blocked;

  useEffect(() => {
    let alive = true;
    predictive.jobs(schema.asset_id, { limit: 10 })
      .then((r) => { if (alive) { setPast({ loading: false, items: r.items ?? [], error: '' }); const running = (r.items ?? []).find((j) => !isFinal(j.status)); if (running && !tracker.job) tracker.track(running.id, running); } })
      .catch((e) => { if (alive) setPast({ loading: false, items: [], error: describeError(e) }); });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schema.asset_id, tracker.job?.status]);

  async function start() {
    setBusy(true); setFailure('');
    try {
      const job = await predictive.startTraining(schema.asset_id, buildConfig(form), { versionId: schema.version_id, label: label.trim() });
      tracker.track(job.id, job);
    } catch (e) { setFailure(describeError(e)); } finally { setBusy(false); }
  }
  async function cancel() {
    if (!tracker.job) return;
    setCancelling(true);
    try { tracker.setJob(await predictive.cancelJob(tracker.job.id)); } catch (e) { setFailure(describeError(e)); } finally { setCancelling(false); }
  }

  return (
    <section className="stack" aria-label="AutoML training">
      <p className="muted">PredictAI trains several well-known algorithms on your data, compares them fairly on rows they never saw while learning, and keeps the best one. Training runs in the background, takes from seconds to a few minutes, and can be cancelled.</p>
      {!form.target ? <Notice kind="warn">Choose what to predict first. <button type="button" className="btn btn--ghost btn--sm" onClick={() => ws.setTab('objective')}>Go to the objective</button></Notice> : null}
      {blocked ? <Notice kind="error" title={SUITABILITY_LABEL.blocked}>The checks found a problem that stops training. <button type="button" className="btn btn--ghost btn--sm" onClick={() => ws.setTab('prepare')}>See what to fix</button></Notice> : null}

      <AlgorithmPicker ws={ws} kind={kind} />

      <div className="card stack">
        <h2 className="card__title">How the models are tested</h2>
        <div className="form-grid">
          <NumberField label="Rows held back for the final test (share)" value={form.testSize} min={0} max={0.5} step={0.05} error={errors.testSize} onChange={(v) => ws.update({ testSize: v === '' ? '' : Number(v) })} hint="These rows are never used to choose or tune a model. 0.2 means 20%." />
          <ChoiceField group="cv_strategy" label="Cross-validation" value={form.cvStrategy} error={errors.cvStrategy} onChange={(v) => ws.update({ cvStrategy: v })} hint="Each model is trained and tested several times on different slices of the training rows." />
          <NumberField label="Folds" value={form.cvFolds} min={2} max={catalog.limits?.max_cv_folds ?? 5} error={errors.cvFolds} onChange={(v) => ws.update({ cvFolds: v === '' ? '' : Number(v) })} />
          {form.cvStrategy === 'repeated_kfold' ? <NumberField label="Repeats" value={form.cvRepeats} min={1} max={catalog.limits?.max_cv_repeats ?? 2} error={errors.cvRepeats} onChange={(v) => ws.update({ cvRepeats: v === '' ? '' : Number(v) })} /> : null}
          {form.cvStrategy === 'time_series' ? (
            <Field as="select" label="Column that gives the time order" value={form.timeColumn} error={errors.timeColumn} onChange={(e) => ws.update({ timeColumn: e.target.value, shuffle: false })}>
              <option value="">Choose a column…</option>
              {timeCols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </Field>
          ) : null}
        </div>
        <CheckField label="Shuffle rows before splitting" checked={form.shuffle} onChange={(v) => ws.update({ shuffle: v })} hint={errors.shuffle || 'Recommended unless the rows are in time order.'} />
      </div>

      <div className="card stack">
        <h2 className="card__title">Tuning</h2>
        <div className="form-grid">
          <ChoiceField group="hpo_method" label="Search for better settings" value={form.hpoMethod} onChange={(v) => ws.update({ hpoMethod: v })} hint="Tries different settings for the best algorithms. Bounded in time and number of tries." />
          {form.hpoMethod !== 'none' ? (
            <>
              <NumberField label={form.hpoMethod === 'grid' ? 'Most combinations to try' : 'Settings to try'} value={form.hpoIter} min={1} max={catalog.limits?.max_hpo_trials ?? 30} error={errors.hpoIter} onChange={(v) => ws.update({ hpoIter: v === '' ? '' : Number(v) })} />
              <NumberField label="Best algorithms to tune" value={form.hpoTopK} min={1} max={catalog.limits?.max_hpo_algorithms ?? 2} error={errors.hpoTopK} onChange={(v) => ws.update({ hpoTopK: v === '' ? '' : Number(v) })} />
              <NumberField label="Folds while tuning" value={form.hpoFolds} min={2} max={catalog.limits?.max_cv_folds ?? 5} error={errors.hpoFolds} onChange={(v) => ws.update({ hpoFolds: v === '' ? '' : Number(v) })} />
            </>
          ) : null}
        </div>
      </div>

      <details className="card stack">
        <summary className="card__title">More settings</summary>
        <div className="form-grid">
          <ChoiceField group="class_weight" label="Rare outcomes" value={form.classWeight} onChange={(v) => ws.update({ classWeight: v })} />
          <ChoiceField group="rare_classes" label="Outcomes with very few rows" value={form.rareClasses} onChange={(v) => ws.update({ rareClasses: v })} />
          <NumberField label="Random seed" value={form.seed} min={0} error={errors.seed} onChange={(v) => ws.update({ seed: v === '' ? '' : Number(v) })} hint="The same seed and settings give the same result." />
          <NumberField label={`Time limit in seconds (up to ${catalog.limits?.time_budget_s ?? 120})`} value={form.timeBudget} min={10} error={errors.timeBudget} onChange={(v) => ws.update({ timeBudget: v === '' ? '' : Number(v) })} hint="Leave empty to use the default." />
          <Field label="Name for this run (optional)" value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
        </div>
      </details>

      {hasErrors(errors) ? <Notice kind="warn" title="Some settings need attention"><ul>{Object.values(errors).map((m, i) => <li key={i}>{m}</li>)}</ul></Notice> : null}
      {failure ? <Notice kind="error" title="Training could not be started">{failure}</Notice> : null}
      <div className="btn-row">
        <button type="button" className="btn btn--primary" disabled={cannotStart} onClick={start}>{busy ? <><Spinner label="Starting" /> Starting…</> : live ? 'Training in progress…' : 'Start training'}</button>
        <span className="row__meta">{describeConfigForm(form).join(' · ')}</span>
      </div>

      <JobPanel job={tracker.job} error={tracker.error} cancelling={cancelling} onCancel={cancel} onDismiss={tracker.clear} />

      <div className="card stack" aria-label="Earlier runs">
        <h2 className="card__title">Earlier runs on this dataset</h2>
        {past.loading ? <p className="muted">Loading…</p> : null}
        {past.error ? <Notice kind="error">{past.error}</Notice> : null}
        {!past.loading && !past.error && past.items.length === 0 ? <p className="muted">No training runs yet.</p> : null}
        <ul className="list">
          {past.items.map((j) => (
            <li key={j.id} className="row">
              <div className="row__main">
                <span className="row__title">{j.label || `Predicting “${j.config?.target}”`} <JobBadge status={j.status} /></span>
                <span className="row__meta">{formatWhen(j.created_at)}{j.duration_ms ? ` · ${formatMs(j.duration_ms)}` : ''}{j.error?.message ? ` · ${j.error.message}` : ''}</span>
              </div>
              {j.model_id ? <Link className="btn btn--ghost btn--sm" to={`/predictions/models/${encodeURIComponent(j.model_id)}`}>Open model</Link> : null}
              {!isFinal(j.status) && (!tracker.job || tracker.job.id !== j.id) ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => tracker.track(j.id, j)}>Follow</button> : null}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function describeConfigForm(form) {
  const c = buildConfig(form);
  const lines = [];
  lines.push(form.target ? `Predicting “${form.target}”` : 'No target chosen');
  lines.push(form.algorithms === 'auto' ? 'algorithms chosen automatically' : `${form.algorithms.length} algorithm${form.algorithms.length === 1 ? '' : 's'}`);
  if (c.hpo) lines.push(`tuning: ${c.hpo.method}`);
  return lines;
}
