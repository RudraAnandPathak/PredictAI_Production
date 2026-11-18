import { useEffect, useState } from 'react';
import { identity } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { Field, PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { SocialButtons } from '../../components/auth/SocialButtons.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { readOAuthError } from '../../features/identity/oauthMessages.js';
import { checkDisplayName, checkEmail, checkPassword, describeError, fieldErrors, PASSWORD_MIN } from '../../features/identity/validation.js';

export function RegisterPage() {
  const [form, setForm] = useState({ email: '', displayName: '', password: '', confirm: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(null);
  const [providerProblem] = useState(() => readOAuthError(window.location.search));
  useEffect(() => {
    if (providerProblem) window.history.replaceState(null, '', window.location.pathname);
  }, [providerProblem]);
  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    const found = {};
    const email = checkEmail(form.email);
    if (email) found.email = email;
    const name = checkDisplayName(form.displayName);
    if (name) found.displayName = name;
    const password = checkPassword(form.password, { email: form.email });
    if (password) found.password = password;
    if (!form.confirm) found.confirm = 'Type your password again.';
    else if (form.confirm !== form.password) found.confirm = 'The two passwords do not match.';
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const result = await identity.register({ email: form.email.trim(), password: form.password, displayName: form.displayName.trim() });
      setDone({ verificationRequired: Boolean(result.data?.verification_required) });
    } catch (error) {
      const server = fieldErrors(error);
      if (Object.keys(server).length) setErrors({ email: server.email, password: server.password, displayName: server.display_name });
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    // The server answers identically whether or not the address was already registered,
    // so this screen must not say which one happened.
    return (
      <section className="auth__card" aria-labelledby="reg-done">
        <div className="card__head">
          <p className="eyebrow">Almost there</p>
          <h1 className="auth__title" id="reg-done">{done.verificationRequired ? 'Check your email' : 'Request received'}</h1>
        </div>
        <FormMessage kind="success" focusOnShow={false}>
          {done.verificationRequired
            ? 'If this address can be used, we have sent a confirmation link. Open it to activate your account, then sign in.'
            : 'If this address can be used, your account is ready. You can sign in now.'}
        </FormMessage>
        <p className="muted">Already have an account with this address? Signing in or resetting your password will work as usual.</p>
        <Link to="/login" className="btn">Go to sign in</Link>
      </section>
    );
  }

  return (
    <section className="auth__card" aria-labelledby="reg-title">
      <div className="card__head">
        <p className="eyebrow">Get started</p>
        <h1 className="auth__title" id="reg-title">Create account</h1>
      </div>
      {providerProblem ? <FormMessage kind="error" title="Could not create the account">{providerProblem}</FormMessage> : null}
      {failure ? <FormMessage kind="error" title="Could not create the account">{failure}</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <Field label="Email" name="email" type="email" autoComplete="email" inputMode="email" value={form.email} onChange={set('email')} error={errors.email} required />
        <Field label="Name (optional)" name="display_name" autoComplete="name" value={form.displayName} onChange={set('displayName')} error={errors.displayName} hint="Shown on your account. You can change it later." />
        <PasswordField label="Password" name="password" autoComplete="new-password" value={form.password} onChange={set('password')} error={errors.password}
          hint={`At least ${PASSWORD_MIN} characters. A few random words make a strong password.`} required />
        <PasswordField label="Confirm password" name="confirm" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} error={errors.confirm} required />
        <button type="submit" className="btn btn--block" disabled={busy}>{busy ? <Spinner label="Creating account" /> : null} Create account</button>
      </form>
      <SocialButtons intent="register" />
      <p className="auth__foot"><span>Already registered? <Link to="/login">Sign in</Link></span></p>
    </section>
  );
}
