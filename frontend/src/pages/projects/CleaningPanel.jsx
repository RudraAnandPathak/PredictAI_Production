import { useMemo, useState } from 'react';
import { workspace } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import {
  CASES, columnsBefore, CONVERT, DATE_FORMATS, defaultStep, FILL, FILTERS, fromOperation, MULTI, OP_ORDER, OPS, OPTIONAL_MULTI, stepProblem, toOperation,
} from '../../features/workspace/cleaning.js';

export function CleaningPanel({ asset, readOnly, onApplied }) {
  const ext = useResource(() => workspace.assets.extraction(asset.id), [asset.id, asset.current_version_id]);
  const tips = useResource(() => workspace.assets.cleaningSuggestions(asset.id), [asset.id, asset.current_version_id]);
  const [steps, setSteps] = useState([]);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const [problems, setProblems] = useState({});
  const [confirming, setConfirming] = useState(false);
  const base = useMemo(() => (ext.data?.summary?.columns ?? []).map((c) => c.name), [ext.data]);

  if (readOnly) return <p className="muted" role="status">This project is archived, so files are read-only. Restore the project to clean data.</p>;
  if (ext.loading && !ext.data) return <PageLoading label="Loading columns…" />;
  if (ext.error) return <ErrorState title="Could not load the columns" message={describeError(ext.error)} onRetry={() => ext.reload()} />;

  const update = (i, patch) => { setSteps((s) => s.map((st, j) => (j === i ? { ...st, ...patch } : st))); setPreview(null); };
  const remove = (i) => { setSteps((s) => s.filter((_, j) => j !== i)); setPreview(null); };
  const add = (op) => { setSteps((s) => [...s, defaultStep(op)]); setPreview(null); };
  const load = (operations) => { setSteps(operations.map(fromOperation)); setPreview(null); setFailure(null); };

  async function runPreview() {
    const found = {};
    steps.forEach((st, i) => { const p = stepProblem(st, columnsBefore(steps, base, i)); if (p) found[i] = p; });
    setProblems(found);
    setFailure(null);
    if (!steps.length) { setFailure('Add at least one step.'); return; }
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      setPreview(await workspace.assets.previewCleaning(asset.id, steps.map(toOperation)));
    } catch (error) {
      setPreview(null);
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    setFailure(null);
    try {
      const result = await workspace.assets.confirmCleaning(preview.id);
      setConfirming(false);
      setPreview(null);
      setSteps([]);
      onApplied(result);
    } catch (error) {
      setConfirming(false);
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    const id = preview.id;
    setPreview(null);
    try { await workspace.assets.discardCleaning(id); } catch { /* the preview expires on its own */ }
  }

  return (
    <section aria-label="Clean data" className="stack">
      <p className="muted">Choose steps, then preview exactly what would change. Nothing is saved until you confirm, and your original file is never modified: the result becomes a new version you can undo.</p>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {tips.data?.items?.length ? (
        <details className="card">
          <summary>Suggestions ({tips.data.items.length}) <span className="badge badge--warn">Rule of thumb</span></summary>
          <ul className="plain">
            {tips.data.items.map((t) => (
              <li key={t.id}>
                <strong>{t.title}.</strong> {t.reason}{' '}
                {t.operations.length ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => load(t.operations)}>Use this step<span className="sr-only">: {t.title}</span></button> : null}
              </li>
            ))}
          </ul>
          {tips.data.needs_profile ? <p className="row__meta">Create a profile (Profile tab) to get more suggestions, such as duplicates and extra spaces.</p> : null}
        </details>
      ) : null}
      <ol className="steps">
        {steps.map((step, i) => (
          <li key={i} className="card step">
            <div className="step__head">
              <strong>Step {i + 1}: {OPS[step.op].label}</strong>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove(i)}>Remove<span className="sr-only"> step {i + 1}</span></button>
            </div>
            <StepFields step={step} index={i} columns={columnsBefore(steps, base, i)} onChange={(patch) => update(i, patch)} />
            {problems[i] ? <p className="field__error" role="alert">{problems[i]}</p> : null}
          </li>
        ))}
      </ol>
      <div className="form__actions">
        <Field as="select" label="Add a step" value="" onChange={(e) => e.target.value && add(e.target.value)}>
          <option value="">Choose a step…</option>
          {OP_ORDER.map((op) => <option key={op} value={op}>{OPS[op].label}</option>)}
        </Field>
        <button type="button" className="btn btn--primary" disabled={busy || !steps.length} onClick={runPreview}>{busy && !confirming ? <Spinner label="Previewing" /> : null} Preview changes</button>
      </div>
      {preview ? <PreviewView preview={preview} onApply={() => setConfirming(true)} onDiscard={discard} /> : null}
      {confirming ? (
        <Modal title="Apply these changes?" onClose={() => setConfirming(false)}>
          <p>This creates <strong>a new version</strong> with {preview.summary.rows_after.toLocaleString()} rows and {preview.summary.columns_after} columns. Your original file and earlier versions stay exactly as they are, and you can switch back at any time from History.</p>
          <div className="form__actions">
            <button type="button" className="btn btn--ghost" onClick={() => setConfirming(false)} data-autofocus>Not yet</button>
            <button type="button" className="btn btn--primary" disabled={busy} onClick={apply}>{busy ? <Spinner label="Applying" /> : null} Apply as a new version</button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

function ColumnChecks({ label, columns, value, onChange, hint }) {
  const toggle = (name) => onChange(value.includes(name) ? value.filter((n) => n !== name) : [...value, name]);
  return (
    <fieldset className="checks">
      <legend className="field__label">{label}</legend>
      {hint ? <p className="field__hint">{hint}</p> : null}
      <div className="checks__grid">
        {columns.map((c) => <label key={c} className="check"><input type="checkbox" checked={value.includes(c)} onChange={() => toggle(c)} /> <span>{c}</span></label>)}
      </div>
    </fieldset>
  );
}

function ColumnSelect({ columns, value, onChange, label = 'Column' }) {
  return (
    <Field as="select" label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Choose…</option>
      {columns.map((c) => <option key={c} value={c}>{c}</option>)}
    </Field>
  );
}

function StepFields({ step, columns, onChange }) {
  const sel = (key, options, label) => (
    <Field as="select" label={label} value={step[key]} onChange={(e) => onChange({ [key]: e.target.value })}>
      {Object.entries(options).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
    </Field>
  );
  const multi = MULTI.has(step.op) || OPTIONAL_MULTI.has(step.op);
  return (
    <div className="form__row">
      {step.op !== 'drop_duplicates' && !multi ? <ColumnSelect columns={columns} value={step.column} onChange={(column) => onChange({ column })} /> : null}
      {step.op === 'fill_missing' ? <>{sel('strategy', FILL, 'Fill with')}{step.strategy === 'constant' ? <Field label="Value" value={step.value} maxLength={500} onChange={(e) => onChange({ value: e.target.value })} /> : null}</> : null}
      {step.op === 'convert_type' ? <>{sel('to', CONVERT, 'Convert to')}{step.to === 'date' ? sel('date_format', DATE_FORMATS, 'Dates are written as') : null}{sel('on_invalid', { empty: 'Leave it empty', keep: 'Keep it as it is' }, 'If a value cannot be converted')}</> : null}
      {step.op === 'rename_column' ? <Field label="New name" value={step.new_name} maxLength={200} onChange={(e) => onChange({ new_name: e.target.value })} /> : null}
      {step.op === 'filter_rows' ? <>{sel('action', { keep: 'Keep only rows where', remove: 'Remove rows where' }, 'Action')}{sel('filter', FILTERS, 'Condition')}{!['is_empty', 'not_empty'].includes(step.filter) ? <Field label="Value" value={step.value} maxLength={500} onChange={(e) => onChange({ value: e.target.value })} /> : null}</> : null}
      {step.op === 'drop_missing' ? sel('how', { any: 'Any chosen column is empty', all: 'All chosen columns are empty' }, 'Remove a row when') : null}
      {step.op === 'change_case' ? sel('case', CASES, 'Change to') : null}
      {multi ? <ColumnChecks label={OPTIONAL_MULTI.has(step.op) ? 'Columns (none selected means every column)' : 'Columns'} columns={columns} value={step.columns} onChange={(v) => onChange({ columns: v })} /> : null}
    </div>
  );
}

const cell = (v, changed, key) => <td key={key} className={`cell-clip${changed ? ' cell--changed' : ''}`} title={v}>{v === '' ? <span className="row__meta">empty</span> : v}</td>;

function PreviewView({ preview, onApply, onDiscard }) {
  const s = preview.summary;
  return (
    <section aria-label="Preview of changes" className="stack preview">
      <h2 className="card__title">Preview — nothing has been saved yet</h2>
      <div className="stat-grid">
        <div className="stat"><span className="stat__value">{s.rows_before.toLocaleString()} → {s.rows_after.toLocaleString()}</span><span className="stat__label">Rows</span></div>
        <div className="stat"><span className="stat__value">{s.columns_before} → {s.columns_after}</span><span className="stat__label">Columns</span></div>
        <div className="stat"><span className="stat__value">{s.rows_removed.toLocaleString()}</span><span className="stat__label">Rows removed</span></div>
        <div className="stat"><span className="stat__value">{s.cells_changed.toLocaleString()}</span><span className="stat__label">Cells changed</span></div>
      </div>
      <ol>{s.steps.map((st) => <li key={st.number}>{st.explanation} <span className="row__meta">{[st.rows_removed ? `${st.rows_removed.toLocaleString()} rows removed` : null, st.cells_changed ? `${st.cells_changed.toLocaleString()} cells changed` : null, st.unconvertible ? `${st.unconvertible.toLocaleString()} could not be converted` : null, st.fill_value !== undefined ? `fill value ${st.fill_value}` : null].filter(Boolean).join(' · ') || 'no effect on its own'}</span></li>)}</ol>
      {preview.warnings.map((w) => <FormMessage key={w.code + w.message} kind="info" focusOnShow={false}>{w.message}</FormMessage>)}
      {preview.blocked_reason ? <FormMessage kind="error" focusOnShow={false}>{preview.blocked_reason}</FormMessage> : null}
      {preview.changed_rows.length ? (
        <div className="table-wrap" tabIndex={0}>
          <table className="table">
            <caption>Examples of changed rows (first {preview.changed_rows.length}); highlighted cells changed</caption>
            <thead><tr><th scope="col">Row</th><th scope="col">Version</th>{preview.after.columns.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {preview.changed_rows.flatMap((r) => [
                <tr key={`${r.row}b`}><th scope="row" rowSpan={2}>{r.row}</th><td className="row__meta">Before</td>{r.before.map((v, j) => cell(v, r.changed_columns.includes(j), j))}</tr>,
                <tr key={`${r.row}a`}><td className="row__meta">After</td>{r.after.map((v, j) => cell(v, r.changed_columns.includes(j), j))}</tr>,
              ])}
            </tbody>
          </table>
        </div>
      ) : null}
      {preview.removed_rows.length ? (
        <div className="table-wrap" tabIndex={0}>
          <table className="table">
            <caption>Examples of rows that would be removed (first {preview.removed_rows.length})</caption>
            <thead><tr><th scope="col">Row</th>{preview.before.columns.map((c) => <th scope="col" key={c}>{c}</th>)}<th scope="col">Why</th></tr></thead>
            <tbody>{preview.removed_rows.map((r) => <tr key={r.row}><th scope="row">{r.row}</th>{r.cells.map((v, j) => cell(v, false, j))}<td>{r.reason}</td></tr>)}</tbody>
          </table>
        </div>
      ) : null}
      {preview.columns_truncated ? <p className="row__meta">Only the first columns are shown in the examples.</p> : null}
      <p className="row__meta">This preview expires if you do not confirm it soon.</p>
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" onClick={onDiscard}>Discard preview</button>
        <button type="button" className="btn btn--primary" disabled={!s.can_apply} onClick={onApply}>Apply as a new version…</button>
      </div>
    </section>
  );
}
