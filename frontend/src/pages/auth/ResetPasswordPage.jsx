import { useEffect, useState } from 'react';
import { identity } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { tokenFromHash } from '../../features/identity/safeNext.js';
import { checkPassword, describeError, PASSWORD_MIN } from '../../features/identity/validation.js';

export function ResetPasswordPage() {
  const { navigate } = useRouter();
  const { markSignedOut } = useAuth();
  // The token arrives in the URL fragment (never sent to a server or logged). Keep it in memory
  // and remove it from the address bar and history straight away.
  const [token] = useState(() => tokenFromHash(window.location.hash));
  const [form, setForm] = useState({ password: '', confirm: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, '', window.location.pathname);
  }, []);

  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    const found = {};
    const password = checkPassword(form.password);
    if (password) found.password = password;
    if (!form.confirm) found.confirm = 'Type your new password again.';
    else if (form.confirm !== form.password) found.confirm = 'The two passwords do not match.';
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      await identity.completeRecovery({ token, newPassword: form.password });
      markSignedOut(null); // the server ended every session (including one in this browser), so do not keep showing a stale one
      navigate('/login?reset=1', { replace: true });
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <section className="auth__card" aria-labelledby="reset-title">
        <div className="card__head">
          <p className="eyebrow">Account recovery</p>
          <h1 className="auth__title" id="reset-title">Link not valid</h1>
        </div>
        <FormMessage kind="error" focusOnShow={false}>This reset link is missing or incomplete. Request a new one to continue.</FormMessage>
        <Link to="/forgot-password" className="btn">Request a new link</Link>
      </section>
    );
  }

  return (
    <section className="auth__card" aria-labelledby="reset-title">
      <div className="card__head">
        <p className="eyebrow">Account recovery</p>
        <h1 className="auth__title" id="reset-title">Choose a new password</h1>
      </div>
      {failure ? (
        <FormMessage kind="error" title="Could not reset the password">
          {failure} <Link to="/forgot-password">Request a new link</Link>
        </FormMessage>
      ) : null}
      <form className="form" onSubmit={submit} noValidate>
        <PasswordField label="New password" name="password" autoComplete="new-password" value={form.password} onChange={set('password')} error={errors.password}
          hint={`At least ${PASSWORD_MIN} characters. You will be signed out on all devices.`} required />
        <PasswordField label="Confirm new password" name="confirm" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} error={errors.confirm} required />
        <button type="submit" className="btn btn--block" disabled={busy}>{busy ? <Spinner label="Saving" /> : null} Set new password</button>
      </form>
    </section>
  );
}
