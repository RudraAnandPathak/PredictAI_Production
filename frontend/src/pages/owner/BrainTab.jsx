import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { geminiSetupProblem, setupBody, usageLine } from '../../features/phase7/predict.js';
import { Badge, Loaded, Notice, attempt } from '../phase7/shared.jsx';

/** Owner screen for the AI Brain (Gemini via Google AI Studio). The key is never typed here: it is an environment variable on the server. */
export function BrainTab() {
  const status = useResource(() => phase7.brain.status(), []);
  const [form, setForm] = useState({ model: '', daily: '15', perMinute: '4' });
  const [msg, setMsg] = useState(null);
  const [models, setModels] = useState(null);
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  async function save(e) {
    e.preventDefault();
    const problem = geminiSetupProblem(form);
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(true);
    const out = await attempt(() => phase7.brain.setupGemini(setupBody(form)), setMsg, 'Saved.');
    setBusy(false);
    if (out) status.reload({ quiet: true });
  }
  async function listModels() { const out = await attempt(() => phase7.brain.models(), setMsg); if (out) setModels(out); }
  async function runTest() { setBusy(true); const out = await attempt(() => phase7.brain.test(), setMsg); setBusy(false); if (out) { setTest(out); status.reload({ quiet: true }); } }
  return (
    <section aria-label="AI Brain">
      <Loaded res={status} what="the AI Brain status">
        {(s) => (
          <>
            <div className="card">
              <h2 className="card__title">AI Brain <Badge tone={s.ready ? 'ok' : 'warn'}>{s.ready ? 'Ready' : 'Not ready'}</Badge></h2>
              <Notice kind="info">{s.data_notice}</Notice>
              <p className="row__meta">{s.cost_notice}</p>
              {s.blocked_by.map((b) => <Notice key={b} kind="warn">{b}</Notice>)}
              {s.provider ? <p className="row__meta">Model: {s.provider.model}. {usageLine(s.usage)}</p> : null}
              <p className="row__meta">{s.fallback.built_in}</p>
              <ul className="list">{s.features.map((f) => <li key={f.feature} className="row__meta">{f.feature}: {f.uses_brain ? `uses it; needs ${f.needs}` : f.needs}</li>)}</ul>
            </div>
            {s.can_manage ? (
              <form className="card" onSubmit={save}>
                <h3 className="card__title">Set up Gemini</h3>
                <p className="row__meta">Create the key in Google AI Studio, then set it on the server as <code>{s.provider?.secret_env ?? 'PREDICTAI_AI_KEY_GEMINI'}</code> and restart the API. Do not paste the key here.</p>
                <Field label="Model name" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
                <div className="grid grid--2">
                  <Field label="Requests per day (your own cap)" inputMode="numeric" value={form.daily} onChange={(e) => setForm({ ...form, daily: e.target.value.replace(/\D/g, '') })} />
                  <Field label="Requests per minute" inputMode="numeric" value={form.perMinute} onChange={(e) => setForm({ ...form, perMinute: e.target.value.replace(/\D/g, '') })} />
                </div>
                {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
                <div className="btn-row">
                  <button className="btn" disabled={busy}>Save</button>
                  <button type="button" className="btn btn--ghost" onClick={listModels}>List models</button>
                  <button type="button" className="btn btn--ghost" disabled={busy || !s.ready} onClick={runTest}>Send a test request</button>
                </div>
                {models && !models.ok ? <Notice kind="warn">{models.message}</Notice> : null}
                {models?.note ? <p className="row__meta">{models.note}</p> : null}
                {models ? <ul className="list">{(models.models ?? []).map((m) => <li key={m} className="row__meta">{m}</li>)}</ul> : null}
                {test ? <Notice kind={test.ok && test.answered_by === 'gemini' ? 'success' : 'warn'}>{test.ok ? `${test.note} (${test.answered_by}, ${test.model})` : [test.note, ...(test.reasons ?? [])].join(' ')}</Notice> : null}
              </form>
            ) : null}
          </>
        )}
      </Loaded>
    </section>
  );
}
