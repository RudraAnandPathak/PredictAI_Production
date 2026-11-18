import { useState } from 'react';
import { owner } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { formatDateTime } from '../../features/identity/format.js';
import { useReauth } from '../../features/identity/ReauthContext.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

const STATE_BADGE = { unused: 'badge--ok', used: 'badge--muted', revoked: 'badge--danger', expired: 'badge--warn' };

export function CodesTab() {
  const { withReauth } = useReauth();
  const codes = useResource(() => owner.codes().then((r) => r.data.items ?? []), []);
  const [label, setLabel] = useState('');
  const [days, setDays] = useState('30');
  const [error, setError] = useState(null);
  const [failure, setFailure] = useState(null);
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  async function create(event) {
    event.preventDefault();
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || n > 90) return setError('Choose a whole number of days from 1 to 90.');
    setError(null);
    setFailure(null);
    setCreated(null);
    setCopied(false);
    setBusy(true);
    try {
      const result = await withReauth(() => owner.createCode({ label: label.trim(), expiresInDays: n }));
      setCreated(result.data);
      setLabel('');
      codes.reload({ quiet: true });
    } catch (err) {
      if (err?.code !== 'REAUTH_CANCELLED') setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id) {
    setFailure(null);
    try {
      await withReauth(() => owner.revokeCode(id));
      codes.reload({ quiet: true });
    } catch (err) {
      if (err?.code !== 'REAUTH_CANCELLED') setFailure(describeError(err));
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.code);
      setCopied(true);
    } catch {
      setCopied(false); // clipboard unavailable: the code is selectable text
    }
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="newcode-h">
        <div className="card__head">
          <h2 className="card__title" id="newcode-h">Create a code</h2>
          <p className="card__desc">Each code is personal, works once and can be revoked until it is used. Only a hash is stored, so the code is shown just this once.</p>
        </div>
        {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
        <form className="form" onSubmit={create} noValidate>
          <div className="form__row">
            <Field label="Label (who it is for)" name="label" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={200} />
            <Field label="Valid for (days)" name="days" type="number" min="1" max="90" inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value)} error={error} />
          </div>
          <div className="form__actions"><button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Creating" /> : null} Create code</button></div>
        </form>
        {created ? (
          <div className="code-reveal" role="status">
            <strong>Copy this code now — it will not be shown again.</strong>
            <span className="code-reveal__value" data-testid="new-code">{created.code}</span>
            <div className="form__actions">
              <button type="button" className="btn btn--sm" onClick={copy}>{copied ? 'Copied' : 'Copy code'}</button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setCreated(null)}>I have saved it</button>
            </div>
          </div>
        ) : null}
      </section>
      <section className="card" aria-labelledby="codes-h">
        <h2 className="card__title" id="codes-h">Codes</h2>
        {codes.error ? <FormMessage kind="error">{describeError(codes.error)}</FormMessage> : null}
        {codes.loading && !codes.data ? <PageLoading label="Loading codes…" /> : (
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">Special-access codes</caption>
              <thead><tr><th scope="col">Label</th><th scope="col">State</th><th scope="col">Created</th><th scope="col">Expires</th><th scope="col">Used by</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {(codes.data ?? []).map((c) => (
                  <tr key={c.id}>
                    <td>{c.label || '—'}</td>
                    <td><span className={`badge ${STATE_BADGE[c.state] ?? 'badge--muted'}`}>{c.state}</span></td>
                    <td>{formatDateTime(c.created_at)}</td>
                    <td>{formatDateTime(c.expires_at)}</td>
                    <td>{c.used_by_email || '—'}</td>
                    <td>{c.state === 'unused' ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => revoke(c.id)}>Revoke<span className="sr-only"> code {c.label || c.id}</span></button> : null}</td>
                  </tr>
                ))}
                {codes.data && codes.data.length === 0 ? <tr><td colSpan={6}>No codes yet.</td></tr> : null}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
