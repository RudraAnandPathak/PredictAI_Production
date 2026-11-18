import { useState } from 'react';
import { identity } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { checkEmail, describeError } from '../../features/identity/validation.js';

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState(null);
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const problem = checkEmail(email);
    setError(problem);
    setFailure(null);
    if (problem) return;
    setBusy(true);
    try {
      await identity.requestRecovery(email.trim());
      setSent(true);
    } catch (err) {
      setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth__card" aria-labelledby="forgot-title">
      <div className="card__head">
        <p className="eyebrow">Account recovery</p>
        <h1 className="auth__title" id="forgot-title">Reset password</h1>
      </div>
      {sent ? (
        <>
          <FormMessage kind="success" focusOnShow={false}>
            If an account exists for that address, we have emailed a reset link. It works once and expires in 30 minutes.
          </FormMessage>
          <Link to="/login" className="btn btn--ghost">Back to sign in</Link>
        </>
      ) : (
        <>
          <p className="muted">Enter the email you registered with and we will send you a link to choose a new password.</p>
          {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
          <form className="form" onSubmit={submit} noValidate>
            <Field label="Email" name="email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} error={error} required />
            <button type="submit" className="btn btn--block" disabled={busy}>{busy ? <Spinner label="Sending" /> : null} Send reset link</button>
          </form>
          <p className="auth__foot"><Link to="/login">Back to sign in</Link></p>
        </>
      )}
    </section>
  );
}
