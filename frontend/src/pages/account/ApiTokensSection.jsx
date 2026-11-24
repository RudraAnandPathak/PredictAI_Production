import { useEffect, useState } from 'react';
import { identity } from '../../api/index.js';
import { Field, PasswordField } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { describeError } from '../../features/identity/validation.js';
import { EXPIRY_CHOICES, STATE_LABEL, STATE_TONE, activeCount, tokenBody, tokenProblem, usedLine } from '../../features/apitokens/logic.js';

/** Personal API tokens: let your own scripts and AI agents (for example the PredictAI agent skill) upload, analyse and model data for you. */
export function ApiTokensSection() {
  const { user } = useAuth();
  const [items, setItems] = useState(null);
  const [allowed, setAllowed] = useState('');
  const [form, setForm] = useState({ name: '', password: '', days: 90 });
  const [created, setCreated] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await identity.apiTokens();
      setItems(r.data.items);
      setAllowed(r.data.allowed);
    } catch (err) {
      setMsg({ kind: 'error', text: describeError(err) });
    }
  }
  useEffect(() => { load(); }, []);

  async function create(event) {
    event.preventDefault();
    const problem = tokenProblem({ ...form, hasPassword: user.hasPassword });
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(true);
    try {
      const r = await identity.createApiToken(tokenBody(form));
      setCreated(r.data);
      setForm({ name: '', password: '', days: 90 });
      setMsg(null);
      load();
    } catch (err) {
      setMsg({ kind: 'error', text: describeError(err) });
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id) {
    try {
      await identity.revokeApiToken(id);
      if (created?.id === id) setCreated(null);
      load();
    } catch (err) {
      setMsg({ kind: 'error', text: describeError(err) });
    }
  }

  return (
    <section className="card" aria-labelledby="tokens-h">
      <div className="card__head">
        <h2 className="card__title" id="tokens-h">API tokens for AI agents</h2>
        <p className="card__desc">A token lets your own scripts or an AI agent (for example the PredictAI agent skill) work with your data. {allowed}</p>
      </div>
      {msg ? <FormMessage kind={msg.kind}>{msg.text}</FormMessage> : null}
      {created ? (
        <FormMessage kind="success" title="Copy your token now">
          <p>{created.notice}</p>
          <p><code className="token-value" aria-label="Your new API token">{created.token}</code></p>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigator.clipboard?.writeText(created.token)}>Copy</button>
        </FormMessage>
      ) : null}
      <form className="form" onSubmit={create} noValidate>
        <Field label="Name" value={form.name} maxLength={80} onChange={(e) => setForm({ ...form, name: e.target.value })} hint="Where it will be used, so you know which to revoke." />
        <Field label="Expires after" as="select" value={form.days} onChange={(e) => setForm({ ...form, days: Number(e.target.value) })}>
          {EXPIRY_CHOICES.map((d) => <option key={d} value={d}>{d} days</option>)}
        </Field>
        {user.hasPassword ? <PasswordField label="Your password" autoComplete="current-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /> : null}
        <div className="form__actions"><button className="btn" disabled={busy}>{busy ? 'Creating…' : 'Create token'}</button></div>
      </form>
      {items === null ? null : items.length === 0 ? <p className="row__meta">No tokens yet.</p> : (
        <ul className="list" aria-label="Your API tokens">
          {items.map((t) => (
            <li key={t.id} className="row">
              <div className="row__main">
                <span className="row__title">{t.name} <span className={`badge badge--${STATE_TONE[t.state]}`}>{STATE_LABEL[t.state]}</span></span>
                <span className="row__meta">{t.prefix}… · created {t.created_at.slice(0, 10)} · expires {t.expires_at.slice(0, 10)} · {usedLine(t)}</span>
              </div>
              {t.state === 'active' ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => revoke(t.id)}>Revoke</button> : null}
            </li>
          ))}
        </ul>
      )}
      {items ? <p className="row__meta">{activeCount(items)} of 10 active. Changing your password revokes all tokens.</p> : null}
    </section>
  );
}
