import { useEffect, useState } from 'react';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { isOwner, hasSpecial } from '../../features/identity/permissions.js';
import { AccessSection } from './AccessSection.jsx';
import { ConnectedAccountsSection } from './ConnectedAccountsSection.jsx';
import { DeleteSection } from './DeleteSection.jsx';
import { PasswordSection } from './PasswordSection.jsx';
import { ProfileSection } from './ProfileSection.jsx';
import { SessionsSection } from './SessionsSection.jsx';

const LABELS = { google: 'Google', github: 'GitHub' };

export function AccountPage() {
  const { user } = useAuth();
  // After the provider sends the browser back we get ?oauth=linked|error&provider=…; show it once, then tidy the URL.
  const [outcome] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get('oauth');
    if (status !== 'linked' && status !== 'error' && status !== 'reauthed') return null;
    const provider = params.get('provider') ?? '';
    return { status, label: LABELS[provider] ?? 'The account' };
  });
  useEffect(() => {
    if (outcome) window.history.replaceState(null, '', window.location.pathname);
  }, [outcome]);

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Account</p>
        <h1 className="page-title">{user.displayName || 'Your account'}</h1>
        <div className="pill-row" aria-label="Your access">
          <span className="badge badge--muted">Member</span>
          {hasSpecial(user) ? <span className="badge badge--ok">Special access</span> : null}
          {isOwner(user) ? <span className="badge badge--accent">Owner</span> : null}
        </div>
      </header>
      <div className="stack">
        <ProfileSection />
        <PasswordSection />
        <AccessSection />
        <SessionsSection />
        <ConnectedAccountsSection outcome={outcome} />
        <DeleteSection />
      </div>
    </article>
  );
}
