import { useEffect, useRef, useState } from 'react';
import { identity } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { tokenFromHash } from '../../features/identity/safeNext.js';
import { checkEmail, describeError } from '../../features/identity/validation.js';

/** The page the emailed link opens. The token is in the URL fragment (never sent to a server or a log), is removed from the address bar at once, and is submitted exactly once. */
export function MagicLinkPage() {
  const { navigate } = useRouter();
  const auth = useAuth();
  const [token] = useState(() => tokenFromHash(window.location.hash));
  const [state, setState] = useState(token ? 'working' : 'invalid');
  const [message, setMessage] = useState(null);
  const started = useRef(false);

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
    if (!token || started.current) return;
    started.current = true; // single-use link: never submit it twice (React StrictMode runs effects twice)
    auth.signInWithMagicLink(token)
      .then(() => navigate('/', { replace: true }))
      .catch((error) => { setMessage(describeError(error)); setState('invalid'); });
  }, [token, auth, navigate]);

  if (state === 'working') return <section className="auth__card"><PageLoading label="Signing you in…" /></section>;
  return (
    <section className="auth__card" aria-labelledby="magic-title">
      <div className="card__head"><p className="eyebrow">Sign in</p><h1 className="auth__title" id="magic-title">This link did not work</h1></div>
      <FormMessage kind="error">{message ?? 'This link is invalid or has expired. Links work once and last 15 minutes.'}</FormMessage>
      <Link to="/magic-link/request" className="btn">Email me a new link</Link>
      <p className="auth__foot"><Link to="/login">Back to sign in</Link></p>
    </section>
  );
}

export function MagicLinkRequestPage() {
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
      await identity.requestMagicLink(email.trim());
      setSent(true);
    } catch (err) {
      setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth__card" aria-labelledby="magic-request-title">
      <div className="card__head"><p className="eyebrow">Sign in</p><h1 className="auth__title" id="magic-request-title">Email me a sign-in link</h1></div>
      {sent ? (
        <>
          <FormMessage kind="success" focusOnShow={false}>If that address belongs to an account with a confirmed email, we have sent a link. It works once and expires in 15 minutes.</FormMessage>
          <Link to="/login" className="btn btn--ghost">Back to sign in</Link>
        </>
      ) : (
        <>
          <p className="muted">No password needed. We email you a one-time link. This only works for accounts whose email address has been confirmed.</p>
          {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
          <form className="form" onSubmit={submit} noValidate>
            <Field label="Email" name="email" type="email" autoComplete="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)} error={error} required />
            <button type="submit" className="btn btn--block" disabled={busy}>{busy ? <Spinner label="Sending" /> : null} Send sign-in link</button>
          </form>
          <p className="auth__foot"><Link to="/login">Back to sign in</Link></p>
        </>
      )}
    </section>
  );
}
