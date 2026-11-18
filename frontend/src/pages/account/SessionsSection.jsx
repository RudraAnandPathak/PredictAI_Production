import { useState } from 'react';
import { identity } from '../../api/index.js';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDateTime } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';

export function SessionsSection() {
  const { signOut } = useAuth();
  const { navigate } = useRouter();
  const sessions = useResource(() => identity.sessions().then((r) => r.data.items ?? []), []);
  const [failure, setFailure] = useState(null);
  const [working, setWorking] = useState(null);

  async function revoke(id) {
    setWorking(id);
    setFailure(null);
    try {
      await identity.revokeSession(id);
      await sessions.reload({ quiet: true });
    } catch (err) {
      setFailure(describeError(err));
    } finally {
      setWorking(null);
    }
  }

  async function everywhere() {
    await signOut({ everywhere: true });
    navigate('/login', { replace: true });
  }

  return (
    <section className="card" aria-labelledby="sessions-h">
      <div className="card__head">
        <h2 className="card__title" id="sessions-h">Signed-in devices</h2>
        <p className="card__desc">Only a rough device label and when it was last used are kept. Sign out any you do not recognise.</p>
      </div>
      {failure || sessions.error ? <FormMessage kind="error">{failure || describeError(sessions.error)}</FormMessage> : null}
      {sessions.loading && !sessions.data ? <PageLoading label="Loading devices…" /> : (
        <ul className="rows">
          {(sessions.data ?? []).map((s) => (
            <li className="row" key={s.id}>
              <div className="row__main">
                <span className="row__title">{s.device} {s.current ? <span className="badge badge--accent">This device</span> : null}</span>
                <span className="row__meta">Signed in {formatDateTime(s.issued_at)} · last active {formatDateTime(s.last_seen_at)}</span>
              </div>
              {!s.current ? (
                <button type="button" className="btn btn--ghost btn--sm" disabled={working === s.id} onClick={() => revoke(s.id)}>
                  Sign out<span className="sr-only"> {s.device}</span>
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <div className="card__actions">
        <button type="button" className="btn btn--ghost" onClick={everywhere}>Sign out of every device</button>
      </div>
    </section>
  );
}
