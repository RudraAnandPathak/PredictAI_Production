import { useState } from 'react';
import { identity } from '../../api/index.js';
import { PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { checkPassword, describeError, PASSWORD_MIN } from '../../features/identity/validation.js';

const EMPTY = { current: '', next: '', confirm: '' };

export function PasswordSection() {
  const { user, setCsrf } = useAuth();
  if (!user.hasPassword) return <SetPasswordSection email={user.email} />;
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (name) => (event) => setForm((f) => ({ ...f, [name]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    const found = {};
    if (!form.current) found.current = 'Enter your current password.';
    const strength = checkPassword(form.next, { email: user.email });
    if (strength) found.next = strength;
    else if (form.next === form.current) found.next = 'Choose a password you have not used here before.';
    if (!form.confirm) found.confirm = 'Type your new password again.';
    else if (form.confirm !== form.next) found.confirm = 'The two passwords do not match.';
    setErrors(found);
    setFailure(null);
    setDone(false);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const result = await identity.changePassword({ currentPassword: form.current, newPassword: form.next });
      setCsrf(result.data?.csrf_token); // the server rotates this session, so the old token is no longer valid
      setForm(EMPTY);
      setDone(true);
    } catch (err) {
      if (err?.code === 'INVALID_PASSWORD') setErrors({ current: err.message });
      else setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="password-h">
      <div className="card__head">
        <h2 className="card__title" id="password-h">Password</h2>
        <p className="card__desc">Changing your password signs you out of all your other devices.</p>
      </div>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {done ? <FormMessage kind="success" title="Password changed">Your other devices have been signed out and we emailed you a security notice.</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <PasswordField label="Current password" name="current_password" autoComplete="current-password" value={form.current} onChange={set('current')} error={errors.current} required />
        <PasswordField label="New password" name="new_password" autoComplete="new-password" value={form.next} onChange={set('next')} error={errors.next} hint={`At least ${PASSWORD_MIN} characters.`} required />
        <PasswordField label="Confirm new password" name="confirm_password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} error={errors.confirm} required />
        <div className="form__actions">
          <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Changing password" /> : null} Change password</button>
        </div>
      </form>
    </section>
  );
}

/** Accounts created with Google/GitHub have no password. Setting one uses the same emailed, single-use link as a reset. */
function SetPasswordSection({ email }) {
  const [state, setState] = useState('idle');
  const [failure, setFailure] = useState(null);
  async function send() {
    setState('sending');
    setFailure(null);
    try {
      await identity.requestRecovery(email);
      setState('sent');
    } catch (err) {
      setFailure(describeError(err));
      setState('idle');
    }
  }
  return (
    <section className="card" aria-labelledby="password-h">
      <div className="card__head">
        <h2 className="card__title" id="password-h">Password</h2>
        <p className="card__desc">You sign in with a connected account, so this account has no password. You can add one to also sign in with your email.</p>
      </div>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {state === 'sent' ? <FormMessage kind="success" title="Check your email" focusOnShow={false}>We sent a link to {email}. Open it to choose a password. It works once and expires in 30 minutes.</FormMessage> : null}
      <div className="card__actions">
        <button type="button" className="btn" disabled={state === 'sending'} onClick={send}>{state === 'sending' ? <Spinner label="Sending" /> : null} Email me a link to set a password</button>
      </div>
    </section>
  );
}
