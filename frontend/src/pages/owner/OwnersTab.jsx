import { useState } from 'react';
import { owner } from '../../api/index.js';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useReauth } from '../../features/identity/ReauthContext.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

export function OwnersTab() {
  const { user } = useAuth();
  const { withReauth } = useReauth();
  const owners = useResource(() => owner.owners().then((r) => r.data.items ?? []), []);
  const [failure, setFailure] = useState(null);

  async function demote(id) {
    setFailure(null);
    try {
      await withReauth(() => owner.demote(id));
      owners.reload({ quiet: true });
    } catch (err) {
      if (err?.code !== 'REAUTH_CANCELLED') setFailure(describeError(err));
    }
  }

  const list = owners.data ?? [];
  return (
    <section className="card" aria-labelledby="owners-h">
      <div className="card__head">
        <h2 className="card__title" id="owners-h">Owners</h2>
        <p className="card__desc">Keep at least one backup owner. To add one, open the person in the Accounts tab and choose “Make backup owner”. The last owner can never be removed.</p>
      </div>
      {failure || owners.error ? <FormMessage kind="error">{failure || describeError(owners.error)}</FormMessage> : null}
      {owners.loading && !owners.data ? <PageLoading label="Loading owners…" /> : (
        <ul className="rows">
          {list.map((o) => (
            <li className="row" key={o.id}>
              <div className="row__main">
                <span className="row__title">{o.display_name || o.email}{o.id === user.id ? ' (you)' : ''}</span>
                <span className="row__meta">{o.email} · since {formatDate(o.created_at)}</span>
              </div>
              {o.id !== user.id ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => demote(o.id)}>Remove owner role<span className="sr-only"> from {o.email}</span></button> : null}
            </li>
          ))}
        </ul>
      )}
      <p className="hint">If every owner is locked out, recovery is done on the server with the command-line tool, not in the browser. See the README.</p>
    </section>
  );
}
