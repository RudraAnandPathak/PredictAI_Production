import { useId, useState } from 'react';

/** Labelled input with hint and error text wired up for screen readers. */
export function Field({ label, error, hint, id: idProp, className = '', as = 'input', children, ...props }) {
  const auto = useId();
  const id = idProp ?? auto;
  const describedBy = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(' ') || undefined;
  const Control = as;
  return (
    <div className={`field${error ? ' field--invalid' : ''} ${className}`.trim()}>
      <label className="field__label" htmlFor={id}>{label}</label>
      <Control id={id} className="field__input" aria-invalid={error ? 'true' : undefined} aria-describedby={describedBy} {...props}>
        {children}
      </Control>
      {hint ? <p className="field__hint" id={`${id}-hint`}>{hint}</p> : null}
      {error ? <p className="field__error" id={`${id}-error`}>{error}</p> : null}
    </div>
  );
}

export function PasswordField({ label = 'Password', ...props }) {
  const [shown, setShown] = useState(false);
  const auto = useId();
  const id = props.id ?? auto;
  return (
    <div className="field-password">
      <Field {...props} id={id} label={label} type={shown ? 'text' : 'password'} autoCapitalize="none" spellCheck={false} />
      <button
        type="button"
        className="field-password__toggle"
        onClick={() => setShown((v) => !v)}
        aria-pressed={shown}
        aria-controls={id}
      >
        {shown ? 'Hide' : 'Show'}<span className="sr-only"> {label.toLowerCase()}</span>
      </button>
    </div>
  );
}
