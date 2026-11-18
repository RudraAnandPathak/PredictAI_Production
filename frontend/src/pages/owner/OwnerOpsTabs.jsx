import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useReauth } from '../../features/identity/ReauthContext.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { bytesText } from '../../features/phase7/labels.js';
import { Badge, Loaded, Notice, Stat, StateBadge, attempt, when } from '../phase7/shared.jsx';

/** Platform counts. Counts, sizes and states only: titles, document text, report content and conversations are never shown to owners here. */
export function OverviewTab() {
  const res = useResource(() => phase7.ownerOps.overview(), []);
  return (
    <Loaded res={res} what="the overview">
      {(o) => (
        <>
          {o.maintenance.active ? <Notice kind="warn" title="Maintenance mode is on">{o.maintenance.notice}</Notice> : null}
          <section className="card"><h2 className="card__title">Accounts</h2>
            <div className="stats"><Stat label="Total" value={o.accounts.total} />{Object.entries(o.accounts.by_status).map(([k, v]) => <Stat key={k} label={k} value={v} />)}<Stat label="With Special access" value={o.accounts.special_access_active} /></div></section>
          <section className="card"><h2 className="card__title">Content</h2>
            <div className="stats"><Stat label="Projects" value={o.content.projects} /><Stat label="Datasets" value={o.content.datasets} /><Stat label="Documents" value={o.content.documents} /><Stat label="Stored" value={bytesText(o.content.stored_bytes)} />
              <Stat label="Reports" value={o.content.reports} /><Stat label="Exports" value={o.content.report_exports} /><Stat label="Registered models" value={o.content.registered_models} /><Stat label="Workspaces" value={o.content.workspaces} /><Stat label="Active schedules" value={o.content.schedules_active} /></div></section>
          <section className="card"><h2 className="card__title">Work in progress</h2>
            <div className="stats"><Stat label="Training jobs running" value={o.work.training_jobs_running} /><Stat label="Research jobs running" value={o.work.research_jobs_running} /><Stat label="Training jobs failed (7 days)" value={o.work.jobs_failed_last_7_days} /></div></section>
          <p className="row__meta">{o.boundary} Updated {when(o.generated_at)}.</p>
        </>
      )}
    </Loaded>
  );
}

/** Health. A check that cannot be measured says so; it is never shown as healthy. */
export function MonitoringTab() {
  const res = useResource(() => phase7.ownerOps.monitoring(), []);
  return (
    <Loaded res={res} what="monitoring">
      {(m) => (
        <>
          <div className="card__row"><h2 className="card__title">Overall: <StateBadge state={m.overall} /></h2><button type="button" className="btn btn--ghost" onClick={() => res.reload()}>Check again</button></div>
          <Notice kind="info">{m.note}</Notice>
          <ul className="list">{m.checks.map((c) => <li key={c.name} className="row"><div className="row__main"><span className="row__title">{c.name.replace(/_/g, ' ')} <StateBadge state={c.state} /></span><span className="row__meta">{c.detail}</span></div></li>)}</ul>
          <p className="row__meta">Checked {when(m.generated_at)}.</p>
        </>
      )}
    </Loaded>
  );
}

/** Maintenance mode: pauses changes for everyone except owners. Needs a recent password confirmation and an explicit confirmation here. */
export function MaintenanceTab() {
  const { withReauth } = useReauth();
  const res = useResource(() => phase7.ownerOps.maintenance(), []);
  const [notice, setNotice] = useState('');
  const [confirm, setConfirm] = useState(false);
  const [msg, setMsg] = useState(null);
  async function set(active) {
    const out = await attempt(() => withReauth(() => phase7.ownerOps.setMaintenance(active, notice, true)), setMsg, active ? 'Maintenance mode is on. Owners can still work.' : 'Maintenance mode is off.');
    if (out) { setConfirm(false); res.reload({ quiet: true }); }
  }
  return (
    <Loaded res={res} what="maintenance state">
      {(m) => (
        <section className="card">
          <h2 className="card__title">Maintenance mode: {m.active ? <Badge tone="warn">On</Badge> : <Badge tone="ok">Off</Badge>}</h2>
          <p>While it is on, other accounts can still sign in and read their work, but cannot change anything, and scheduled deliveries are paused. Owner access is not affected.</p>
          {m.active ? <p className="row__meta">Notice shown to people: {m.notice || '(default)'} · changed {when(m.changed_at)}</p> : <Field label="Notice shown to people (optional)" value={notice} maxLength={300} onChange={(e) => setNotice(e.target.value)} />}
          {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
          {m.active ? <button type="button" className="btn" onClick={() => set(false)}>Turn maintenance off</button> : confirm ? (
            <div className="btn-row"><button type="button" className="btn" onClick={() => set(true)}>Yes, pause changes for everyone else</button><button type="button" className="btn btn--ghost" onClick={() => setConfirm(false)}>Cancel</button></div>
          ) : <button type="button" className="btn" onClick={() => setConfirm(true)}>Turn maintenance on…</button>}
        </section>
      )}
    </Loaded>
  );
}

function QuotaRow({ item, onSaved }) {
  const { withReauth } = useReauth();
  const [value, setValue] = useState(String(item.effective));
  const [msg, setMsg] = useState(null);
  const label = item.key === 'standard_max_documents' ? 'Documents per standard account' : 'Pages per document (standard account)';
  async function save() {
    const n = Number(value);
    if (!Number.isInteger(n)) { setMsg({ kind: 'error', text: 'Use a whole number.' }); return; }
    const out = await attempt(() => withReauth(() => phase7.ownerOps.setQuota(item.key, n)), setMsg, 'Saved.');
    if (out) onSaved();
  }
  async function reset() { const out = await attempt(() => withReauth(() => phase7.ownerOps.resetQuota(item.key)), setMsg, 'Back to the default.'); if (out) onSaved(); }
  return (
    <section className="card" aria-label={label}>
      <h3 className="card__title">{label}</h3>
      <p className="row__meta">Default {item.default}{item.override !== null ? ` · overridden to ${item.override}` : ''} · allowed {item.min} to {item.max}</p>
      <Field label="Limit" inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, ''))} />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="btn-row"><button type="button" className="btn" onClick={save}>Save</button>{item.override !== null ? <button type="button" className="btn btn--ghost" onClick={reset}>Use default</button> : null}</div>
    </section>
  );
}

/** Limits for standard accounts. Special access lifts them. Lowering a limit never deletes anything. */
export function QuotasTab() {
  const res = useResource(() => phase7.ownerOps.quotaPolicy(), []);
  return <Loaded res={res} what="quota policy">{(q) => <><Notice kind="info">{q.note}</Notice>{q.items.map((i) => <QuotaRow key={`${i.key}-${i.effective}`} item={i} onSaved={() => res.reload({ quiet: true })} />)}</>}</Loaded>;
}

/** Everything an owner changed in these areas. */
export function OwnerEventsTab() {
  const res = useResource(() => phase7.ownerOps.events({ limit: 50 }), []);
  return <Loaded res={res} what="owner actions">{(d) => d.items.length === 0 ? <p>No owner actions recorded yet.</p> : <ul className="list">{d.items.map((e) => <li key={e.id} className="row"><div className="row__main"><span className="row__title">{e.action.replace(/_/g, ' ')}{e.target ? `: ${e.target}` : ''}</span><span className="row__meta">{e.outcome} · {when(e.created_at)}</span></div></li>)}</ul>}</Loaded>;
}

/** Metadata for one account (shown inside the account dialog). Counts only. */
export function UserSummary({ userId }) {
  const res = useResource(() => phase7.ownerOps.userSummary(userId), [userId]);
  return (
    <Loaded res={res} what="usage">
      {(s) => (
        <section aria-label="Usage">
          <h3>Usage (counts only)</h3>
          <p className="row__meta">{s.projects} project(s), {s.datasets} dataset(s), {s.documents} document(s), {bytesText(s.stored_bytes)} stored, {s.reports} report(s), {s.models} model(s), {s.schedules} schedule(s), in {s.workspaces_member_of} workspace(s).</p>
          <p className="row__meta">{s.boundary}</p>
        </section>
      )}
    </Loaded>
  );
}
