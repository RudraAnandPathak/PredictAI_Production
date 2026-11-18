import { useState } from 'react';
import { identity } from '../../api/index.js';
import { PROVIDER_LABEL } from '../../features/identity/oauthMessages.js';
import { describeError } from '../../features/identity/validation.js';
import { FormMessage } from '../forms/FormMessage.jsx';

/** For accounts without a password: confirm it is really you by signing in again with a connected provider. */
export function ProviderReauth({ providers, reason = 'confirm this action' }) {
  const [failure, setFailure] = useState(null);
  const [working, setWorking] = useState(null);
  async function go(id) {
    setWorking(id);
    setFailure(null);
    try {
      window.location.assign(await identity.startOAuthReauth(id));
    } catch (error) {
      setFailure(describeError(error));
      setWorking(null);
    }
  }
  if (!providers?.length) return <p className="muted">Set a password first, or connect Google or GitHub, so we can {reason}.</p>;
  return (
    <div className="stack">
      <p className="muted">Your account has no password. Sign in again with a connected account to {reason}; you will come back to your Account page afterwards.</p>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {providers.map((id) => (
        <button key={id} type="button" className="btn" disabled={working !== null} onClick={() => go(id)}>Confirm with {PROVIDER_LABEL[id] ?? id}</button>
      ))}
    </div>
  );
}
