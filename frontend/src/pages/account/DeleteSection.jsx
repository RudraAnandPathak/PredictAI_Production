import { useState } from 'react';
import { identity } from '../../api/index.js';
import { ProviderReauth } from '../../components/auth/ProviderReauth.jsx';
import { PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { isOwner } from '../../features/identity/permissions.js';
import { describeError } from '../../features/identity/validation.js';

export function DeleteSection() {
  const { user, markSignedOut } = useAuth();
  const { navigate } = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [sure, setSure] = useState(false);
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const [needsReauth, setNeedsReauth] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (user.hasPassword && !password) return setFailure('Enter your password to confirm.');
    if (!sure) return setFailure('Tick the box to confirm you understand.');
    setBusy(true);
    setFailure(null);
    try {
      await identity.deleteAccount(user.hasPassword ? password : null);
      markSignedOut(null);
      navigate('/login?deleted=1', { replace: true });
    } catch (err) {
      if (err?.code === 'REAUTH_REQUIRED') setNeedsReauth(true);
      else setFailure(err?.code === 'INVALID_PASSWORD' ? err.message : describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card danger-zone" aria-labelledby="delete-h">
      <div className="card__head">
        <h2 className="card__title" id="delete-h">Delete account</h2>
        <p className="card__desc">
          Permanently removes your sign-in and anonymises your personal details. Security records are kept without your email address. This cannot be undone.
        </p>
      </div>
      {isOwner(user) ? (
        <FormMessage kind="info" focusOnShow={false}>Owners cannot delete their account while they are the last owner. Add a backup owner or remove the owner role first.</FormMessage>
      ) : null}
      <div className="card__actions">
        <button type="button" className="btn btn--danger" onClick={() => { setOpen(true); setPassword(''); setSure(false); setFailure(null); setNeedsReauth(false); }}>Delete my account…</button>
      </div>
      {open ? (
        <Modal title="Delete your account?" onClose={() => setOpen(false)}>
          <form className="form" onSubmit={submit} noValidate>
            <p className="muted">This signs you out everywhere and removes your personal data. You cannot recover the account afterwards.</p>
            {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
            {user.hasPassword ? <PasswordField label="Password" name="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} data-autofocus /> : null}
            {needsReauth ? <ProviderReauth providers={user.connectedProviders} reason="confirm deleting your account" /> : null}
            <label className="check">
              <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} />
              <span>I understand this permanently deletes my account.</span>
            </label>
            <div className="form__actions">
              <button type="submit" className="btn btn--danger" disabled={busy}>{busy ? <Spinner label="Deleting" /> : null} Delete account</button>
              <button type="button" className="btn btn--ghost" onClick={() => setOpen(false)}>Cancel</button>
            </div>
          </form>
        </Modal>
      ) : null}
    </section>
  );
}
