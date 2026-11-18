import { useEffect, useRef, useState } from 'react';
import { identity } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { tokenFromHash } from '../../features/identity/safeNext.js';
import { checkEmail, describeError } from '../../features/identity/validation.js';

export function VerifyEmailPage() {
  const [token] = useState(() => tokenFromHash(window.location.hash));
  const [state, setState] = useState(token ? 'verifying' : 'invalid');
  const [message, setMessage] = useState(null);
  const started = useRef(false);

  const [email, setEmail] = useState('');
  const [emailError, setEmailError] = useState(null);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
    if (!token || started.current) return;
    started.current = true; // the link is single-use: never submit it twice (React StrictMode runs effects twice)
    identity.verifyEmail(token)
      .then(() => setState('verified'))
      .catch((error) => {
        setMessage(describeError(error));
        setState('invalid');
      });
  }, [token]);

  async function resend(event) {
    event.preventDefault();
    const problem = checkEmail(email);
    setEmailError(problem);
    if (problem) return;
    setBusy(true);
    try {
      await identity.resendVerification(email.trim());
      setResent(true);
    } catch (error) {
      setMessage(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  if (state === 'verifying') return <section className="auth__card"><PageLoading label="Confirming your email…" /></section>;

  if (state === 'verified') {
    return (
      <section className="auth__card" aria-labelledby="verify-title">
        <div className="card__head"><p className="eyebrow">Email</p><h1 className="auth__title" id="verify-title">Email confirmed</h1></div>
        <FormMessage kind="success" focusOnShow={false}>Thank you. Your email address is confirmed and your account is active.</FormMessage>
        <Link to="/login?verified=1" className="btn">Sign in</Link>
      </section>
    );
  }

  return (
    <section className="auth__card" aria-labelledby="verify-title">
      <div className="card__head"><p className="eyebrow">Email</p><h1 className="auth__title" id="verify-title">Link not valid</h1></div>
      <FormMessage kind="error" focusOnShow={false}>{message || 'This confirmation link is invalid or has expired.'}</FormMessage>
      {resent ? (
        <FormMessage kind="success" focusOnShow={false}>If that address needs confirming, we have sent a new link.</FormMessage>
      ) : (
        <form className="form" onSubmit={resend} noValidate>
          <Field label="Send me a new link" name="email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} error={emailError} required />
          <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Sending" /> : null} Send new link</button>
        </form>
      )}
      <p className="auth__foot"><Link to="/login">Back to sign in</Link></p>
    </section>
  );
}
