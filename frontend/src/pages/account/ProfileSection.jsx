import { useState } from 'react';
import { identity } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDate } from '../../features/identity/format.js';
import { checkDisplayName, describeError, DISPLAY_NAME_MAX } from '../../features/identity/validation.js';

export function ProfileSection() {
  const { user, applyProfile } = useAuth();
  const [name, setName] = useState(user.displayName);
  const [error, setError] = useState(null);
  const [failure, setFailure] = useState(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resent, setResent] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const problem = checkDisplayName(name);
    setError(problem);
    setFailure(null);
    setSaved(false);
    if (problem) return;
    setBusy(true);
    try {
      applyProfile(await identity.updateProfile(name.trim()));
      setSaved(true);
    } catch (err) {
      setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    try {
      await identity.resendVerification(user.email);
      setResent(true);
    } catch (err) {
      setFailure(describeError(err));
    }
  }

  return (
    <section className="card" aria-labelledby="profile-h">
      <div className="card__head">
        <h2 className="card__title" id="profile-h">Profile</h2>
        <p className="card__desc">The only personal details PredictAI keeps are your email and an optional name.</p>
      </div>
      <dl className="kv">
        <dt>Email</dt><dd>{user.email}</dd>
        <dt>Member since</dt><dd>{formatDate(user.createdAt)}</dd>
        <dt>Email status</dt>
        <dd>{user.emailVerified ? 'Confirmed' : 'Not confirmed'}</dd>
      </dl>
      {!user.emailVerified ? (
        <FormMessage kind="warn" focusOnShow={false}>
          Your email address is not confirmed yet.{' '}
          {resent ? 'We sent a new link.' : <button type="button" className="link-btn" onClick={resend}>Send the confirmation link again</button>}
        </FormMessage>
      ) : null}
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {saved ? <FormMessage kind="success" focusOnShow={false}>Profile saved.</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <Field label="Name" name="display_name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} error={error}
          hint={`Optional, up to ${DISPLAY_NAME_MAX} characters.`} />
        <div className="form__actions">
          <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Saving" /> : null} Save profile</button>
        </div>
      </form>
    </section>
  );
}
