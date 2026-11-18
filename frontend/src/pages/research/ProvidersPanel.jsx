import { useState } from 'react';
import { ai } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { AI_TASK_LABEL, PRIVACY_CLASS_LABEL, PRIVACY_CLASS_TONE, PROVIDER_KIND_LABEL, labelOf } from '../../features/research/labels.js';
import { PROVIDER_KINDS, TASKS, buildProviderBody, enableChecks, probeLine, usageErrors, usageRows } from '../../features/research/providers.js';
import { Badge, Notice } from './shared.jsx';

const BLANK = { key: '', kind: 'ollama', label: '', baseUrl: 'http://127.0.0.1:11434', model: '', embedModel: '', tasks: ['plan', 'summarize', 'synthesize'], secretEnv: '', timeoutS: '', maxRetries: '', priority: '', paid: false };

function ProviderRow({ p, listing, onChange, setMessage }) {
  const checks = enableChecks(p, listing);
  const act = async (fn, ok) => { setMessage(null); try { const r = await fn(); if (ok) setMessage({ kind: 'info', text: typeof ok === 'function' ? ok(r) : ok }); onChange(); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } };

  async function toggle() {
    if (!p.enabled) {
      if (checks.blocked) { setMessage({ kind: 'error', text: checks.blocked }); return; }
      let confirmPaid = false;
      if (checks.needsPaidConfirm) {
        if (!window.confirm(`“${p.label}” is marked as paid. Switching it on may cost money, and PredictAI cannot see or limit what the provider bills you. Switch it on?`)) return;
        confirmPaid = true;
      } else if (checks.notes.length && !window.confirm(`${checks.notes.join('\n')}\n\nSwitch “${p.label}” on?`)) return;
      await act(() => ai.setEnabled(p.key, true, confirmPaid), `${p.label} switched on.`);
    } else await act(() => ai.setEnabled(p.key, false), `${p.label} switched off.`);
  }

  return (
    <li className="row provider" data-provider-key={p.key}>
      <div className="row__main">
        <span className="row__title">{p.label} <span className="row__meta">({labelOf(PROVIDER_KIND_LABEL, p.kind)}{p.model ? `, ${p.model}` : ''})</span></span>
        <span className="row__meta"><Badge tone={PRIVACY_CLASS_TONE[p.privacy_class] ?? 'muted'}>{labelOf(PRIVACY_CLASS_LABEL, p.privacy_class)}</Badge> {p.paid ? <Badge tone="warn">Paid</Badge> : null} {p.enabled ? <Badge tone="ok">On</Badge> : <Badge tone="muted">Off</Badge>}</span>
        <span className="row__meta">Used for: {(p.tasks ?? []).map((t) => labelOf(AI_TASK_LABEL, t)).join(', ') || 'nothing'}{` · ${probeLine(p.last_probe)}`}</span>
        {listing.can_manage ? <span className="row__meta">{p.base_url ? `${p.base_url} · ` : ''}timeout {p.timeout_s}s · {p.max_retries} retr{p.max_retries === 1 ? 'y' : 'ies'}{p.secret_env ? ` · key from ${p.secret_env} (${p.has_secret ? 'set' : 'not set'})` : ''}</span> : null}
      </div>
      {listing.can_manage && p.kind !== 'builtin' ? (
        <div className="btn-row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={toggle}>{p.enabled ? 'Switch off' : 'Switch on'}</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => act(() => ai.probe(p.key), (r) => (r.ok ? 'The provider answered.' : `The provider did not answer${r.message ? `: ${r.message}` : ''}.`))}>Test</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => { if (window.confirm(`Remove “${p.label}”?`)) act(() => ai.remove(p.key), 'Provider removed.'); }}>Remove</button>
        </div>
      ) : null}
    </li>
  );
}

/** AI providers. PredictAI works with none of them. Only the platform owner can add, switch on or remove one; everyone can see what is in use. */
export function ProvidersPanel() {
  const providers = useResource(() => ai.providers(), []);
  const usage = useResource(() => ai.usage(30), []);
  const [form, setForm] = useState(BLANK);
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const listing = providers.data;
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  async function add(event) {
    event.preventDefault();
    const built = buildProviderBody(form);
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({});
    setBusy(true);
    try { await ai.create(built.body); setForm(BLANK); setMessage({ kind: 'success', text: 'Provider added. It is switched off until you switch it on.' }); providers.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }

  if (providers.loading && !listing) return <PageLoading label="Loading AI providers…" />;
  if (providers.error) return <ErrorState title="Could not load the AI providers" message={describeError(providers.error)} onRetry={() => providers.reload()} />;

  return (
    <section className="stack" aria-label="AI providers">
      <div className="card stack">
        <h2 className="card__title">AI providers</h2>
        <p>{listing.notice}</p>
        <ul className="list">{listing.items.map((p) => <ProviderRow key={p.key} p={p} listing={listing} onChange={() => { providers.reload({ quiet: true }); usage.reload({ quiet: true }); }} setMessage={setMessage} />)}</ul>
        {!listing.can_manage ? <p className="row__meta">Only the platform owner can add, switch on or remove providers.</p> : null}
        {listing.can_manage ? <p className="row__meta">{listing.paid_allowed ? 'Paid providers are allowed on this server; each still needs your confirmation.' : 'Paid providers are not allowed on this server.'} {listing.remote_allowed ? 'Providers on other machines are allowed.' : 'Only providers on this machine or your own network can be added.'}</p> : null}
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
      </div>

      {listing.can_manage ? (
        <form className="card form-grid" onSubmit={add} noValidate aria-label="Add a provider">
          <h3 className="card__title">Add a provider</h3>
          <Field label="Type" as="select" value={form.kind} onChange={(e) => set({ kind: e.target.value })}>{PROVIDER_KINDS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Field>
          <Field label="Short name" value={form.key} onChange={(e) => set({ key: e.target.value })} error={errors.key} hint="Lowercase letters, digits, - or _." />
          <Field label="Label" value={form.label} onChange={(e) => set({ label: e.target.value })} error={errors.label} maxLength={80} />
          <Field label="Address" type="url" value={form.baseUrl} onChange={(e) => set({ baseUrl: e.target.value })} error={errors.baseUrl} />
          <Field label="Model" value={form.model} onChange={(e) => set({ model: e.target.value })} error={errors.model} />
          <Field label="Embedding model (optional)" value={form.embedModel} onChange={(e) => set({ embedModel: e.target.value })} />
          <fieldset className="stack" aria-label="Tasks"><legend className="field__label">Use it for</legend>
            {TASKS.map((t) => <label key={t} className="check-field"><input type="checkbox" checked={form.tasks.includes(t)} onChange={() => set({ tasks: form.tasks.includes(t) ? form.tasks.filter((x) => x !== t) : [...form.tasks, t] })} /><span className="check-field__label">{labelOf(AI_TASK_LABEL, t)}</span></label>)}
          </fieldset>
          <Field label="Name of the environment variable that holds its key (optional)" value={form.secretEnv} onChange={(e) => set({ secretEnv: e.target.value })} error={errors.secretEnv} placeholder="PREDICTAI_AI_KEY_MYHOST" hint="Never paste a key here. Set the variable on the server; PredictAI reads it from there and never shows it." autoComplete="off" />
          <Field label="Timeout (seconds)" inputMode="numeric" value={form.timeoutS} onChange={(e) => set({ timeoutS: e.target.value })} error={errors.timeoutS} />
          <Field label="Retries" inputMode="numeric" value={form.maxRetries} onChange={(e) => set({ maxRetries: e.target.value })} error={errors.maxRetries} />
          <Field label="Priority (lower is tried first)" inputMode="numeric" value={form.priority} onChange={(e) => set({ priority: e.target.value })} error={errors.priority} />
          <label className="check-field"><input type="checkbox" checked={form.paid} onChange={(e) => set({ paid: e.target.checked })} /><span><span className="check-field__label">This provider charges for use</span><span className="field__hint">Paid providers are never switched on automatically.</span></span></label>
          <div className="btn-row"><button type="submit" className="btn" disabled={busy}>{busy ? 'Adding…' : 'Add provider'}</button></div>
        </form>
      ) : null}

      {usage.data ? (
        <div className="card stack" aria-label="Usage">
          <h3 className="card__title">Recent use — {usage.data.scope}, last 30 days</h3>
          {usageRows(usage.data).length === 0 ? <p className="muted">No requests yet.</p> : (
            <div className="table-wrap"><table className="table table--compact">
              <thead><tr><th scope="col">Provider</th><th scope="col">Task</th><th scope="col">Result</th><th scope="col">Calls</th><th scope="col">Tokens in / out</th><th scope="col">Average time</th></tr></thead>
              <tbody>{usageRows(usage.data).map((r) => <tr key={r.key}><th scope="row">{r.provider}</th><td>{labelOf(AI_TASK_LABEL, r.task)}</td><td>{r.outcome}</td><td>{r.calls}</td><td>{r.unitsIn} / {r.unitsOut}</td><td>{r.avgMs} ms</td></tr>)}</tbody>
            </table></div>
          )}
          {usageErrors(usage.data).length ? <div><h4 className="sub">Errors</h4><ul>{usageErrors(usage.data).map((e) => <li key={e.key}>{e.provider}: {e.code} × {e.count}</li>)}</ul></div> : null}
          {usage.data.cost_note ? <p className="row__meta">{usage.data.cost_note}</p> : null}
        </div>
      ) : null}
    </section>
  );
}
