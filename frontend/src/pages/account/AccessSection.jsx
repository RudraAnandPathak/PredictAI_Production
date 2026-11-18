import { useState } from 'react';
import { identity } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { formatDate, normaliseAccessCode } from '../../features/identity/format.js';
import { hasSpecial, isOwner } from '../../features/identity/permissions.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';

export function AccessSection() {
  const { user, refresh } = useAuth();
  const special = hasSpecial(user);
  const ownerRole = isOwner(user);
  const entitlements = useResource(() => identity.entitlements().then((r) => r.data.items ?? []), [user.id, special]);
  const [code, setCode] = useState('');
  const [error, setError] = useState(null);
  const [failure, setFailure] = useState(null);
  const [granted, setGranted] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    const value = normaliseAccessCode(code);
    setFailure(null);
    setGranted(false);
    if (!value) return setError('Enter the access code you were given.');
    setError(null);
    setBusy(true);
    try {
      await identity.activateCode(value);
      setCode('');
      setGranted(true);
      await refresh({ quiet: true }); // the profile now lists the new tier; the server is the source of truth
      entitlements.reload({ quiet: true });
    } catch (err) {
      setFailure(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const items = entitlements.data ?? [];
  return (
    <section className="card" aria-labelledby="access-h">
      <div className="card__head">
        <h2 className="card__title" id="access-h">Special access</h2>
        <p className="card__desc">
          Special access unlocks professional tools as they are released. It is tied to your account, not to this browser, so it follows you to every device.
        </p>
      </div>
      <p>
        <span className={`badge ${special ? 'badge--ok' : 'badge--muted'}`}>{special ? 'Active' : 'Not active'}</span>
        {ownerRole ? <span className="hint"> Included with the owner role.</span> : null}
      </p>
      {items.length > 0 ? (
        <ul className="rows" aria-label="Your entitlements">
          {items.map((e) => (
            <li className="row" key={e.id}>
              <div className="row__main">
                <span className="row__title">{e.tier === 'special' ? 'Special access' : e.tier}</span>
                <span className="row__meta">Since {formatDate(e.activated_at)}</span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {!special ? (
        <>
          <p className="muted">Have a code? Codes are personal, work once and are issued by the owner.</p>
          {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
          {granted ? <FormMessage kind="success" title="Special access activated">It applies immediately and stays on your account.</FormMessage> : null}
          <form className="form" onSubmit={submit} noValidate>
            <Field label="Access code" name="access_code" value={code} onChange={(e) => setCode(e.target.value)} error={error}
              autoComplete="off" autoCapitalize="characters" spellCheck={false} placeholder="PAI-XXXXX-XXXXX-XXXXX-XXXXX" />
            <div className="form__actions">
              <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Activating" /> : null} Activate code</button>
            </div>
          </form>
        </>
      ) : granted ? <FormMessage kind="success" title="Special access activated">It applies immediately and stays on your account.</FormMessage> : null}
    </section>
  );
}
