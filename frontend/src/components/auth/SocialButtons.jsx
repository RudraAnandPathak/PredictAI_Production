import { useEffect, useState } from 'react';
import { identity } from '../../api/index.js';
import { FormMessage } from '../forms/FormMessage.jsx';
import { Spinner } from '../ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';

/** "Sign in with / Sign up with Google or GitHub". `intent` is 'login' or 'register'. Secrets never reach the browser: it only receives the provider's authorization URL. */
export function SocialButtons({ intent }) {
  const [providers, setProviders] = useState(null);
  const [working, setWorking] = useState(null);
  const [failure, setFailure] = useState(null);

  useEffect(() => {
    let live = true;
    identity.oauthAvailable().then((items) => live && setProviders(items)).catch(() => live && setProviders([]));
    return () => { live = false; };
  }, []);

  async function go(provider) {
    setWorking(provider.id);
    setFailure(null);
    try {
      window.location.assign(await identity.startOAuthSignIn(provider.id, intent)); // leaves the app for the provider
    } catch (error) {
      setFailure(describeError(error));
      setWorking(null);
    }
  }

  if (providers === null || providers.length === 0) return null;
  const verb = intent === 'register' ? 'Sign up with' : 'Sign in with';
  const unavailable = providers.filter((p) => !p.configured);
  return (
    <div className="social" role="group" aria-label="Continue with another account">
      <p className="social__or" aria-hidden="true"><span>or continue with</span></p>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {providers.map((p) => (
        <button key={p.id} type="button" className="btn btn--ghost btn--block" disabled={!p.configured || working !== null} onClick={() => go(p)}>
          {working === p.id ? <Spinner label={`Opening ${p.label}`} /> : null} {verb} {p.label}
        </button>
      ))}
      {unavailable.length > 0 ? (
        <p className="hint">{unavailable.map((p) => p.label).join(' and ')} {unavailable.length > 1 ? 'are' : 'is'} not set up on this server yet. The server administrator can check with <code>python -m app.cli oauth-status</code>.</p>
      ) : null}
    </div>
  );
}
