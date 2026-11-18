import { useEffect, useState } from 'react';
import { Field } from '../../components/forms/Field.jsx';
import { choosableProviders, diffConsent, formFromConsent } from '../../features/research/consent.js';
import { describeError } from '../../features/identity/validation.js';
import { Notice } from './shared.jsx';

/**
 * Consent for web research, news and outside AI. Everything starts off. `row` is the stored choice for this layer; `onSave(changes)` sends only what changed.
 * `locked` lists switches this layer cannot turn on (a project can only narrow what the person allowed).
 */
export function ConsentForm({ row, platform, onSave, locked = {}, legend }) {
  const [form, setForm] = useState(formFromConsent(row));
  const [errors, setErrors] = useState({});
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setForm(formFromConsent(row)); }, [row]);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const providers = choosableProviders(platform);
  const toggleProvider = (key) => set({ providers: form.providers.includes(key) ? form.providers.filter((k) => k !== key) : [...form.providers, key] });
  const webBlocked = platform && platform.web_enabled === false;

  async function submit(event) {
    event.preventDefault();
    const d = diffConsent(row, form);
    if (d.errors) { setErrors(d.errors); setMessage(null); return; }
    setErrors({});
    if (Object.keys(d.changes).length === 0) { setMessage({ kind: 'info', text: 'Nothing has changed.' }); return; }
    setBusy(true);
    try { await onSave(d.changes); setMessage({ kind: 'success', text: 'Saved.' }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); } finally { setBusy(false); }
  }

  return (
    <form className="stack" onSubmit={submit} noValidate aria-label={legend}>
      <fieldset className="card stack">
        <legend className="card__title">{legend}</legend>
        {webBlocked ? <Notice kind="info" title="Web research is switched off on this server">The platform owner has not enabled it, so it stays off whatever you choose here.</Notice> : null}
        <label className="check-field"><input type="checkbox" checked={form.webEnabled} disabled={Boolean(locked.web)} onChange={(e) => set({ webEnabled: e.target.checked })} /><span><span className="check-field__label">Allow web research</span><span className="field__hint">Short search phrases made from a question may be sent to the search sites you tick below. Your documents and datasets are never sent.</span>{locked.web ? <span className="field__hint">{locked.web}</span> : null}</span></label>
        <div className="stack" role="group" aria-label="Search sites">
          {providers.map((p) => (
            <label key={p.key} className="check-field"><input type="checkbox" checked={form.providers.includes(p.key)} onChange={() => toggleProvider(p.key)} /><span><span className="check-field__label">{p.label}</span><span className="field__hint">{p.description} Requests go to {p.host}.</span></span></label>
          ))}
          {(platform?.providers ?? []).filter((p) => !p.available).map((p) => <p key={p.key} className="row__meta">{p.label} is not available: {p.reason}</p>)}
        </div>
        <label className="check-field"><input type="checkbox" checked={form.newsEnabled} disabled={Boolean(locked.news)} onChange={(e) => set({ newsEnabled: e.target.checked })} /><span><span className="check-field__label">Allow reading news feeds</span><span className="field__hint">Fetches the feeds you add, only headlines and the summary each feed publishes.</span>{locked.news ? <span className="field__hint">{locked.news}</span> : null}</span></label>
        <label className="check-field"><input type="checkbox" checked={form.externalAi} disabled={Boolean(locked.ai)} onChange={(e) => set({ externalAi: e.target.checked })} /><span><span className="check-field__label">Allow an outside AI service to word summaries</span><span className="field__hint">Only if the platform owner has also enabled one. Text from your own documents and datasets is never sent to an outside service. Research works without any AI model.</span>{locked.ai ? <span className="field__hint">{locked.ai}</span> : null}</span></label>
        <Field label="Prefer pages newer than (days)" inputMode="numeric" value={form.freshnessDays} onChange={(e) => set({ freshnessDays: e.target.value })} error={errors.freshnessDays} hint="Older pages are marked as outdated, not hidden." />
        <Field label="Keep saved web pages for (days)" inputMode="numeric" value={form.cacheDays} onChange={(e) => set({ cacheDays: e.target.value })} error={errors.cacheDays} hint="Saved pages make repeat research faster and kinder to the sites. 1 to 90 days." />
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        <div className="btn-row"><button type="submit" className="btn" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button></div>
      </fieldset>
    </form>
  );
}
