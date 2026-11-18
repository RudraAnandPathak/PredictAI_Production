import { Field } from '../../components/forms/Field.jsx';
import { OPTION_LABELS } from '../../features/predictive/config.js';

/** A choice from the server's closed list, labelled in plain words. `group` is the key in OPTION_LABELS. */
export function ChoiceField({ group, label, value, onChange, hint, error, allowed }) {
  const labels = OPTION_LABELS[group] ?? {};
  const keys = (allowed ?? Object.keys(labels)).filter((k) => k in labels);
  return (
    <Field as="select" label={label} value={value} hint={hint} error={error} onChange={(e) => onChange(e.target.value)}>
      {keys.map((k) => <option key={k} value={k}>{labels[k]}</option>)}
    </Field>
  );
}

export function NumberField({ label, value, onChange, hint, error, min, max, step }) {
  return <Field label={label} type="number" inputMode="decimal" value={value} min={min} max={max} step={step} hint={hint} error={error} onChange={(e) => onChange(e.target.value === '' ? '' : e.target.value)} />;
}

export function CheckField({ label, checked, onChange, hint }) {
  return (
    <label className="check-field">
      <input type="checkbox" checked={Boolean(checked)} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="check-field__label">{label}</span>{hint ? <span className="field__hint">{hint}</span> : null}</span>
    </label>
  );
}
