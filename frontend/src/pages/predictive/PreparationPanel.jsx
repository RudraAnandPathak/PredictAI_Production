import { Explanation } from '../../components/ui/Explanation.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { ACTION_LABEL, SUITABILITY_LABEL, SUITABILITY_TONE, formatShare } from '../../features/predictive/format.js';
import { CheckField, ChoiceField, NumberField } from './OptionField.jsx';
import { Badge, Notice, Stat } from './shared.jsx';

const ROLE_CHOICES = [['default', 'Automatic'], ['use', 'Use as an input'], ['numeric', 'Treat as a number'], ['categorical', 'Treat as a category'], ['drop', 'Leave out']];
const SEVERITY_TONE = { high: 'danger', medium: 'warn', low: 'muted' };

function Suitability({ s }) {
  if (!s) return null;
  return (
    <div className={`card stack suitability suitability--${s.verdict}`}>
      <p><Badge tone={SUITABILITY_TONE[s.verdict]}>{SUITABILITY_LABEL[s.verdict] ?? s.verdict}</Badge></p>
      <p>{s.summary}</p>
      {s.blockers?.length ? <div><h3 className="sub">Must be fixed first</h3><ul>{s.blockers.map((b, i) => <li key={i}>{b}</li>)}</ul></div> : null}
      {s.cautions?.length ? <div><h3 className="sub">Worth knowing</h3><ul>{s.cautions.map((b, i) => <li key={i}>{b}</li>)}</ul></div> : null}
    </div>
  );
}

function ColumnTable({ ws, columns }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="How each column will be used">
      <table className="table table--compact">
        <thead><tr><th scope="col">Column</th><th scope="col">Empty</th><th scope="col">Decision</th><th scope="col">Why</th><th scope="col">Your choice</th></tr></thead>
        <tbody>
          {columns.map((c) => {
            const d = c.decision ?? {};
            const chosen = ws.form.roles[c.name] ?? 'default';
            return (
              <tr key={c.name}>
                <td><strong>{c.name}</strong><br /><span className="row__meta">{c.declared_type}{c.distinct !== undefined ? ` · ${formatNumber(c.distinct, 0)} different values` : ''}</span></td>
                <td>{c.missing ? `${formatNumber(c.missing, 0)} (${formatNumber(c.missing_pct, 1)}%)` : 'none'}</td>
                <td><Badge tone={d.action === 'drop' ? 'warn' : d.action === 'target' ? 'muted' : 'ok'}>{ACTION_LABEL[d.action] ?? d.action}</Badge>{d.role && d.action === 'use' ? <><br /><span className="row__meta">as {d.role}</span></> : null}</td>
                <td>
                  {d.reason}
                  {d.notes?.length ? <ul className="warnings">{d.notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
                  {c.flags?.length ? <p className="row__meta">{c.flags.join(' · ')}</p> : null}
                  {c.association && d.action !== 'target' ? <p className="row__meta">Link to the target: {c.association.measure} = {formatNumber(c.association.value, 3)} (n = {formatNumber(c.association.n, 0)}). A link is not a cause.</p> : null}
                </td>
                <td>
                  {d.action === 'target' ? <span className="muted">—</span> : (
                    <select className="field__input field__input--sm" aria-label={`How to treat ${c.name}`} value={chosen}
                      onChange={(e) => ws.setRole(c.name, e.target.value)}>
                      {ROLE_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Interface 2: Dataset Preparation. Shows what the checks found and exactly how each column will be prepared. The original dataset is never changed. */
export function PreparationPanel({ ws }) {
  const { assessment, form, errors } = ws;
  const r = assessment.data;
  const pre = form.preprocessing;
  const set = (key) => (value) => ws.setPre({ [key]: value });

  if (!r) {
    return (
      <section className="stack" aria-label="Dataset preparation">
        <div className="state" role="status"><div className="state__body">
          <h2 className="state__title">Nothing to prepare yet</h2>
          <p>{form.target ? 'Run the checks to see how this dataset would be prepared for a model.' : 'Choose the column to predict first, then run the checks.'}</p>
          <div className="btn-row">
            {form.target ? <button type="button" className="btn" disabled={assessment.busy} onClick={() => ws.runAssess()}>{assessment.busy ? <Spinner label="Checking" /> : null} Check the dataset</button>
              : <button type="button" className="btn" onClick={() => ws.setTab('objective')}>Choose what to predict</button>}
          </div>
          {assessment.error ? <Notice kind="error">{assessment.error}</Notice> : null}
        </div></div>
      </section>
    );
  }

  const pp = r.preprocessing ?? {};
  return (
    <section className="stack" aria-label="Dataset preparation">
      <p className="muted">{pp.principle} Your original dataset is not changed; preparation happens inside the model, separately for each training run.</p>
      {assessment.stale ? <Notice kind="warn">These results are for earlier settings. <button type="button" className="btn btn--ghost btn--sm" onClick={() => ws.runAssess()}>Check again</button></Notice> : null}
      <Suitability s={r.suitability} />

      <div className="stat-grid" aria-label="Preparation summary">
        <Stat label="Rows" value={formatNumber(r.dataset?.rows, 0)} hint={`${formatNumber(r.dataset?.labelled_rows, 0)} with a value to learn from`} />
        <Stat label="Inputs used" value={formatNumber(pp.used, 0)} hint={`${formatNumber(pp.dropped, 0)} left out`} />
        <Stat label="Model columns after encoding" value={`about ${formatNumber(pp.encoded_features_estimate, 0)}`} />
        <Stat label="Repeated rows" value={formatNumber(r.duplicates?.rows ?? 0, 0)} hint={r.duplicates?.share ? formatShare(r.duplicates.share, 1) : undefined} />
      </div>

      <div className="card stack">
        <h2 className="card__title">Columns</h2>
        <ColumnTable ws={ws} columns={r.columns ?? []} />
        <p className="row__meta">Change a choice, then press “Check again”. Date columns become year, month, day and weekday parts. Numbers are filled and scaled using the training rows only.</p>
      </div>

      {r.leakage?.length ? (
        <div className="card stack" aria-label="Possible leakage">
          <h2 className="card__title">Possible information leaks</h2>
          <p className="muted">A column that gives away the answer would make the model look far better than it will be on new cases. These are excluded unless you override them.</p>
          <ul>{r.leakage.map((l) => <li key={l.column}><Badge tone={SEVERITY_TONE[l.severity] ?? 'muted'}>{l.severity}</Badge> <strong>{l.column}</strong>: {l.reason}</li>)}</ul>
        </div>
      ) : null}

      {r.imbalance ? (
        <div className="card stack" aria-label="Class balance">
          <h2 className="card__title">Balance of outcomes</h2>
          <p>{r.imbalance.message}</p>
        </div>
      ) : null}

      {r.duplicates?.rows ? <Notice kind="warn" title={`${formatNumber(r.duplicates.rows, 0)} repeated rows`}>{r.duplicates.why_it_matters}</Notice> : null}
      {r.issues?.length ? <ul className="warnings">{r.issues.map((i, k) => <li key={k}>{typeof i === 'string' ? i : i.message}</li>)}</ul> : null}

      <div className="card stack">
        <h2 className="card__title">Preparation settings</h2>
        <p className="muted">The defaults suit most data. Each choice is explained in the results above and recorded with the model.</p>
        <div className="form-grid">
          <ChoiceField group="numeric_impute" label="Fill empty numbers with" value={pre.numeric_impute} onChange={set('numeric_impute')} />
          <ChoiceField group="categorical_impute" label="Fill empty categories with" value={pre.categorical_impute} onChange={set('categorical_impute')} />
          <ChoiceField group="scaling" label="Scale numbers" value={pre.scaling} onChange={set('scaling')} />
          <ChoiceField group="encoding" label="Turn categories into numbers" value={pre.encoding} onChange={set('encoding')} />
          <ChoiceField group="high_cardinality" label="Categories with very many values" value={pre.high_cardinality} onChange={set('high_cardinality')} />
          <ChoiceField group="outliers" label="Extreme values" value={pre.outliers} onChange={set('outliers')} />
          <ChoiceField group="log_transform" label="Very skewed numbers" value={pre.log_transform} onChange={set('log_transform')} />
          <NumberField label="Leave out columns with more empty values than (%)" value={pre.max_missing_pct} min={5} max={100} onChange={set('max_missing_pct')} error={errors.max_missing_pct} />
          <NumberField label="Most categories to give their own column" value={pre.max_onehot_levels} min={2} max={100} onChange={set('max_onehot_levels')} error={errors.max_onehot_levels} />
          <NumberField label="Group categories rarer than (share)" value={pre.rare_category_min_frequency} min={0} max={0.2} step={0.005} onChange={set('rare_category_min_frequency')} error={errors.rare_category_min_frequency} />
        </div>
        <div className="check-grid">
          <CheckField label="Add a “was empty” column" checked={pre.missing_indicator} onChange={set('missing_indicator')} hint="Lets the model use the fact that a value was missing." />
          <CheckField label="Split dates into year, month, day and weekday" checked={pre.datetime_features} onChange={set('datetime_features')} />
          <CheckField label="Remove repeated rows" checked={pre.drop_duplicates} onChange={set('drop_duplicates')} hint="Stops the same row appearing in both training and testing." />
        </div>
        <div className="btn-row">
          <button type="button" className="btn" disabled={assessment.busy} onClick={() => ws.runAssess()}>{assessment.busy ? <><Spinner label="Checking" /> Checking…</> : 'Check again with these settings'}</button>
          <button type="button" className="btn btn--ghost" onClick={() => ws.setTab('train')}>Continue to training</button>
        </div>
      </div>
      <Explanation value={r.explanation} level={3} />
    </section>
  );
}
