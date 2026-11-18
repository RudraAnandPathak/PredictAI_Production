import { useState } from 'react';
import { owner } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDateTime } from '../../features/identity/format.js';
import { useReauth } from '../../features/identity/ReauthContext.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { UserSummary } from './OwnerOpsTabs.jsx';

const STATUS_BADGE = { active: 'badge--ok', pending: 'badge--warn', blocked: 'badge--danger' };

function ManageUser({ summary, onClose, onChanged }) {
  const { user: me } = useAuth();
  const { withReauth } = useReauth();
  const detail = useResource(() => owner.user(summary.id).then((r) => r.data), [summary.id]);
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState(null);
  const [done, setDone] = useState(null);
  const [busy, setBusy] = useState(false);
  const u = detail.data ?? summary;
  const isOwnerRole = (u.roles ?? []).includes('owner');
  const isSelf = u.id === me.id;
  const special = (detail.data?.entitlements ?? []).find((e) => e.tier === 'special');

  async function act(label, fn) {
    setBusy(true);
    setFailure(null);
    setDone(null);
    try {
      await withReauth(fn);
      setDone(label);
      await detail.reload({ quiet: true });
      onChanged();
    } catch (err) {
      if (err?.code !== 'REAUTH_CANCELLED') setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={u.display_name || u.email} onClose={onClose}>
      <dl className="kv">
        <dt>Email</dt><dd>{u.email}</dd>
        <dt>Status</dt><dd>{u.status}{u.blocked_reason ? ` — ${u.blocked_reason}` : ''}</dd>
        <dt>Roles</dt><dd>{(u.roles ?? []).join(', ') || 'user'}</dd>
        <dt>Special access</dt><dd>{u.special_access ? 'Yes' : 'No'}</dd>
        <dt>Last sign-in</dt><dd>{formatDateTime(u.last_sign_in_at)}</dd>
      </dl>
      <UserSummary userId={u.id} />
      {detail.error ? <FormMessage kind="error">{describeError(detail.error)}</FormMessage> : null}
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {done ? <FormMessage kind="success" focusOnShow={false}>{done}</FormMessage> : null}
      {isSelf ? <p className="hint">This is your own account, so account-level changes are disabled here.</p> : null}
      <div className="stack">
        {u.status === 'blocked' ? (
          <div className="form__actions">
            <button type="button" className="btn btn--sm" disabled={busy} onClick={() => act('Account restored.', () => owner.unblock(u.id))}>Restore account</button>
          </div>
        ) : !isSelf && !isOwnerRole ? (
          <div className="form">
            <Field label="Reason for suspending (optional, kept in the audit log)" name="reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
            <div className="form__actions">
              <button type="button" className="btn btn--danger btn--sm" disabled={busy} onClick={() => act('Account suspended and signed out everywhere.', () => owner.block(u.id, reason))}>Suspend account</button>
            </div>
          </div>
        ) : null}
        {!isSelf ? (
          <div className="form__actions">
            {special ? (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => act('Special access revoked. It applies from the next request.', () => owner.revokeEntitlement(special.id, 'revoked by owner'))}>Revoke special access</button>
            ) : (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy || u.status !== 'active'} onClick={() => act('Special access granted.', () => owner.grantSpecial(u.id))}>Grant special access</button>
            )}
            {!isOwnerRole ? (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy || u.status !== 'active'} onClick={() => act('Owner role granted.', () => owner.promote(u.id))}>Make backup owner</button>
            ) : (
              <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => act('Owner role removed.', () => owner.demote(u.id))}>Remove owner role</button>
            )}
          </div>
        ) : null}
      </div>
      <div className="form__actions"><button type="button" className="btn btn--ghost" onClick={onClose}>Close</button></div>
    </Modal>
  );
}

export function UsersTab() {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [applied, setApplied] = useState({ q: '', status: '' });
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(null);
  const limit = 25;
  const users = useResource(() => owner.users({ q: applied.q, status: applied.status, limit, offset }).then((r) => r.data), [applied, offset]);
  const data = users.data;

  function search(event) {
    event.preventDefault();
    setOffset(0);
    setApplied({ q: query.trim(), status });
  }

  return (
    <div className="stack">
      <form className="toolbar" onSubmit={search} role="search" aria-label="Find accounts">
        <Field label="Search by email or name" name="q" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Field as="select" label="Status" name="status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any</option><option value="active">Active</option><option value="pending">Pending</option><option value="blocked">Suspended</option>
        </Field>
        <button type="submit" className="btn">Search</button>
      </form>
      {users.error ? <FormMessage kind="error">{describeError(users.error)}</FormMessage> : null}
      {users.loading && !data ? <PageLoading label="Loading accounts…" /> : (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">Accounts</caption>
            <thead><tr><th scope="col">Account</th><th scope="col">Status</th><th scope="col">Access</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {(data?.items ?? []).map((u) => (
                <tr key={u.id}>
                  <td><strong>{u.display_name || u.email}</strong>{u.display_name ? <div className="row__meta">{u.email}</div> : null}</td>
                  <td><span className={`badge ${STATUS_BADGE[u.status] ?? 'badge--muted'}`}>{u.status}</span></td>
                  <td>
                    <div className="pill-row">
                      {u.roles.includes('owner') ? <span className="badge badge--accent">Owner</span> : null}
                      {u.special_access ? <span className="badge badge--ok">Special</span> : null}
                      {!u.roles.includes('owner') && !u.special_access ? <span className="badge badge--muted">Member</span> : null}
                    </div>
                  </td>
                  <td>{formatDateTime(u.last_sign_in_at)}</td>
                  <td><button type="button" className="btn btn--ghost btn--sm" onClick={() => setSelected(u)}>Manage<span className="sr-only"> {u.email}</span></button></td>
                </tr>
              ))}
              {data && data.items.length === 0 ? <tr><td colSpan={5}>No accounts match.</td></tr> : null}
            </tbody>
          </table>
        </div>
      )}
      {data ? (
        <div className="card__row">
          <span className="hint">{data.total} account(s)</span>
          <div className="form__actions">
            <button type="button" className="btn btn--ghost btn--sm" disabled={offset === 0 || users.loading} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button>
            <button type="button" className="btn btn--ghost btn--sm" disabled={offset + limit >= data.total || users.loading} onClick={() => setOffset(offset + limit)}>
              {users.loading ? <Spinner label="Loading" /> : null} Next
            </button>
          </div>
        </div>
      ) : null}
      {selected ? <ManageUser summary={selected} onClose={() => setSelected(null)} onChanged={() => users.reload({ quiet: true })} /> : null}
    </div>
  );
}
