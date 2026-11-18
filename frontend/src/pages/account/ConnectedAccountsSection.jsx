import { useState } from 'react';
import { identity } from '../../api/index.js';
import { ProviderReauth } from '../../components/auth/ProviderReauth.jsx';
import { PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

export function ConnectedAccountsSection({ outcome }) {
  const { user } = useAuth();
  const [needsReauth, setNeedsReauth] = useState(false);
  const providers = useResource(() => identity.providers().then((r) => r.data.items ?? []), []);
  const [failure, setFailure] = useState(null);
  const [working, setWorking] = useState(null);
  const [unlinking, setUnlinking] = useState(null);
  const [password, setPassword] = useState('');
  const [modalError, setModalError] = useState(null);

  async function connect(provider) {
    setWorking(provider.id);
    setFailure(null);
    try {
      const result = await identity.startOAuth(provider.id);
      window.location.assign(result.data.authorization_url); // leaves the app for the provider's consent screen
    } catch (err) {
      setFailure(describeError(err));
      setWorking(null);
    }
  }

  async function confirmUnlink(event) {
    event.preventDefault();
    if (user.hasPassword && !password) return setModalError('Enter your password to confirm.');
    setWorking(unlinking.id);
    setModalError(null);
    try {
      await identity.unlinkOAuth(unlinking.id, user.hasPassword ? password : null);
      setUnlinking(null);
      setPassword('');
      await providers.reload({ quiet: true });
    } catch (err) {
      if (err?.code === 'REAUTH_REQUIRED') setNeedsReauth(true);
      else setModalError(err?.code === 'INVALID_PASSWORD' || err?.code === 'LAST_SIGN_IN_METHOD' ? err.message : describeError(err));
    } finally {
      setWorking(null);
    }
  }

  const items = providers.data ?? [];
  const noneConfigured = !providers.loading && items.length > 0 && items.every((p) => !p.configured && !p.linked);

  return (
    <section className="card" aria-labelledby="oauth-h">
      <div className="card__head">
        <h2 className="card__title" id="oauth-h">Connected accounts</h2>
        <p className="card__desc">Link a Google or GitHub account to your PredictAI account. Linking never creates a separate account.</p>
      </div>
      {outcome?.status === 'linked' ? <FormMessage kind="success" focusOnShow={false}>{outcome.label} connected.</FormMessage> : null}
      {outcome?.status === 'reauthed' ? <FormMessage kind="success" focusOnShow={false}>Identity confirmed with {outcome.label}. You can now repeat the action you were doing.</FormMessage> : null}
      {outcome?.status === 'error' ? <FormMessage kind="error">We could not connect {outcome.label}. Nothing was changed. Please try again.</FormMessage> : null}
      {failure || providers.error ? <FormMessage kind="error">{failure || describeError(providers.error)}</FormMessage> : null}
      {providers.loading && !providers.data ? <PageLoading label="Loading providers…" /> : (
        <ul className="rows">
          {items.map((p) => (
            <li className="row" key={p.id}>
              <div className="row__main">
                <span className="row__title">{p.label}</span>
                <span className="row__meta">
                  {p.linked ? `Connected ${formatDate(p.linked_at)}` : p.configured ? 'Not connected' : 'Not set up on this server yet'}
                </span>
              </div>
              {p.linked ? (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setUnlinking(p); setPassword(''); setModalError(null); setNeedsReauth(false); }}>
                  Disconnect<span className="sr-only"> {p.label}</span>
                </button>
              ) : (
                <button type="button" className="btn btn--ghost btn--sm" disabled={!p.configured || working === p.id} onClick={() => connect(p)}>
                  Connect<span className="sr-only"> {p.label}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {noneConfigured ? <p className="hint">The site owner has not added provider credentials yet, so connecting is switched off.</p> : null}
      {unlinking ? (
        <Modal title={`Disconnect ${unlinking.label}?`} onClose={() => setUnlinking(null)}>
          <form className="form" onSubmit={confirmUnlink} noValidate>
            <p className="muted">{user.hasPassword ? 'You will still be able to sign in with your email and password. Enter your password to confirm.'
              : 'You can only disconnect a sign-in method if another one remains (a password or another connected account).'}</p>
            {modalError ? <FormMessage kind="error">{modalError}</FormMessage> : null}
            {user.hasPassword ? <PasswordField label="Password" name="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} data-autofocus /> : null}
            {needsReauth ? <ProviderReauth providers={user.connectedProviders} reason="confirm disconnecting" /> : null}
            <div className="form__actions">
              <button type="submit" className="btn btn--danger" disabled={working === unlinking.id}>{working === unlinking.id ? <Spinner label="Disconnecting" /> : null} Disconnect</button>
              <button type="button" className="btn btn--ghost" onClick={() => setUnlinking(null)}>Cancel</button>
            </div>
          </form>
        </Modal>
      ) : null}
    </section>
  );
}
