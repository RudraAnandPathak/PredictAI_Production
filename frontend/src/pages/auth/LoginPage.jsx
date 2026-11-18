import { useEffect, useState } from 'react';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Field, PasswordField } from '../../components/forms/Field.jsx';
import { SocialButtons } from '../../components/auth/SocialButtons.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { safeNext } from '../../features/identity/safeNext.js';
import { readOAuthError } from '../../features/identity/oauthMessages.js';
import { checkEmail, describeError } from '../../features/identity/validation.js';

export function LoginPage() {
  const { navigate } = useRouter();
  const auth = useAuth();
  const params = new URLSearchParams(window.location.search);
  const next = safeNext(params.get('next'));
  const notice = params.get('expired') ? 'expired' : params.get('reset') ? 'reset' : params.get('deleted') ? 'deleted' : params.get('verified') ? 'verified' : null;

  const [form, setForm] = useState({ email: '', password: '', rememberMe: true });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [providerProblem] = useState(() => readOAuthError(window.location.search));
  useEffect(() => {
    if (providerProblem) window.history.replaceState(null, '', window.location.pathname);
  }, [providerProblem]);

  // Already signed in (for example, opened /login in a second tab): go where they were headed.
  useEffect(() => {
    if (auth.status === 'authenticated') navigate(next, { replace: true });
  }, [auth.status, navigate, next]);

  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.type === 'checkbox' ? event.target.checked : event.target.value }));

  async function submit(event) {
    event.preventDefault();
    const found = {};
    const emailProblem = checkEmail(form.email);
    if (emailProblem) found.email = emailProblem;
    if (!form.password) found.password = 'Enter your password.';
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      await auth.signIn({ email: form.email.trim(), password: form.password, rememberMe: form.rememberMe });
      navigate(next, { replace: true });
    } catch (error) {
      setFailure(describeError(error));
      setForm((f) => ({ ...f, password: '' }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="auth__card" aria-labelledby="login-title">
      <div className="card__head">
        <p className="eyebrow">Welcome back</p>
        <h1 className="auth__title" id="login-title">Sign in</h1>
      </div>
      {notice === 'expired' ? <FormMessage kind="info" title="Your session has ended">Please sign in again to continue.</FormMessage> : null}
      {notice === 'reset' ? <FormMessage kind="success" title="Password updated">Sign in with your new password. Any other devices were signed out.</FormMessage> : null}
      {notice === 'deleted' ? <FormMessage kind="success" title="Account deleted">Your account and personal data have been removed.</FormMessage> : null}
      {notice === 'verified' ? <FormMessage kind="success" title="Email confirmed">You can sign in now.</FormMessage> : null}
      {providerProblem ? <FormMessage kind="error" title="Could not sign in">{providerProblem}</FormMessage> : null}
      {failure ? <FormMessage kind="error" title="Could not sign in">{failure}</FormMessage> : null}
      {auth.status === 'unavailable' ? <FormMessage kind="warn" focusOnShow={false}>PredictAI could not be reached. You can still try to sign in.</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <Field label="Email" name="email" type="email" autoComplete="username" inputMode="email" value={form.email} onChange={set('email')} error={errors.email} required />
        <PasswordField label="Password" name="password" autoComplete="current-password" value={form.password} onChange={set('password')} error={errors.password} required />
        <label className="check">
          <input type="checkbox" checked={form.rememberMe} onChange={set('rememberMe')} />
          <span>Keep me signed in on this device. Leave unticked on a shared computer.</span>
        </label>
        <button type="submit" className="btn btn--block" disabled={busy}>{busy ? <Spinner label="Signing in" /> : null} Sign in</button>
      </form>
      <SocialButtons intent="login" />
      <p className="auth__foot">
        <Link to="/forgot-password">Forgot your password?</Link> · <Link to="/magic-link/request">Email me a sign-in link</Link>
        <span>New here? <Link to="/register">Create an account</Link></span>
      </p>
    </section>
  );
}
