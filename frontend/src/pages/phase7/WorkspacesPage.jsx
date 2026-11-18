import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { RESOURCE_LABEL, ROLE_HINT, ROLE_LABEL } from '../../features/phase7/labels.js';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

const SHAREABLE = {
  report: { label: 'A report', load: () => phase7.reports.list({ limit: 100 }).then((d) => d.items.map((r) => ({ id: r.id, label: r.title }))) },
  model: { label: 'A registered model', load: () => phase7.registry.models({ limit: 100 }).then((d) => d.items.map((m) => ({ id: m.id, label: m.name }))) },
  analysis: { label: 'A saved analysis', load: () => phase7.intelligence.runs({ limit: 100 }).then((d) => d.items.map((r) => ({ id: r.id, label: r.title }))) },
};

function Invitations({ onChanged }) {
  const res = useResource(() => phase7.workspaces.myInvitations(), []);
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState(null);
  async function answer(accept) {
    const out = await attempt(() => phase7.workspaces.respond(token.trim(), accept), setMsg, accept ? 'You joined the workspace.' : 'Invitation declined.');
    if (out) { setToken(''); onChanged(); res.reload({ quiet: true }); }
  }
  const items = res.data?.items ?? [];
  return (
    <section className="card" aria-label="Invitations">
      <h2 className="card__title">Join with an invitation</h2>
      {items.length ? <ul className="list">{items.map((i) => <li key={i.id} className="row"><div className="row__main"><span className="row__title">{i.name}</span><span className="row__meta">as {i.role}, valid until {when(i.expires_at)}</span></div></li>)}</ul> : <p className="row__meta">No pending invitations for your email address.</p>}
      <Field label="Invitation code" value={token} maxLength={200} onChange={(e) => setToken(e.target.value)} hint="Paste the code from the invitation link (the part after token=). It only works for the email address it was sent to." />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="btn-row"><button type="button" className="btn" disabled={!token.trim()} onClick={() => answer(true)}>Accept</button><button type="button" className="btn btn--ghost" disabled={!token.trim()} onClick={() => answer(false)}>Decline</button></div>
    </section>
  );
}

/** Workspaces: private groups you share chosen items with. Nothing is public, and joining shares nothing by itself. */
export function WorkspacesPage() {
  const res = useResource(() => phase7.workspaces.list(), []);
  const [name, setName] = useState('');
  const [msg, setMsg] = useState(null);
  async function create(e) {
    e.preventDefault();
    const out = await attempt(() => phase7.workspaces.create({ name }), setMsg, 'Workspace created. You are its admin.');
    if (out) { setName(''); res.reload({ quiet: true }); }
  }
  return (
    <article className="page">
      <PageHeader eyebrow="Collaboration" title="Workspaces" lead="Share specific reports, models and analyses with people you invite. Joining a workspace shares nothing by itself, and nothing here is public." />
      <Loaded res={res} what="your workspaces">
        {(d) => (
          <>
            {d.items.length === 0 ? <Empty title="You are not in a workspace yet"><p>Create one, or paste an invitation code below.</p></Empty> : (
              <ul className="list">{d.items.map((w) => <li key={w.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/workspaces/${encodeURIComponent(w.id)}`}>{w.name}</Link></span><span className="row__meta">{w.role} · {w.members} member(s)</span></div></li>)}</ul>
            )}
            <form className="card" onSubmit={create}>
              <h2 className="card__title">New workspace</h2>
              <Field label="Name" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} required />
              {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
              <div className="card__actions"><button className="btn" disabled={!name.trim()}>Create</button></div>
            </form>
            <Invitations onChanged={() => res.reload({ quiet: true })} />
          </>
        )}
      </Loaded>
    </article>
  );
}

function ShareForm({ workspace, onDone }) {
  const [type, setType] = useState('report');
  const options = useResource(() => SHAREABLE[type].load(), [type]);
  const [id, setId] = useState('');
  const [permission, setPermission] = useState('view');
  const [msg, setMsg] = useState(null);
  async function submit(e) {
    e.preventDefault();
    const out = await attempt(() => phase7.workspaces.share(workspace.id, { resource_type: type, resource_id: id, permission }), setMsg, 'Shared. You can stop sharing at any time.');
    if (out) { setId(''); onDone(); }
  }
  return (
    <form className="card" onSubmit={submit} aria-label="Share an item">
      <h3 className="card__title">Share something</h3>
      <Field as="select" label="What" value={type} onChange={(e) => { setType(e.target.value); setId(''); }}>{Object.entries(SHAREABLE).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</Field>
      <Field as="select" label="Which one" value={id} onChange={(e) => setId(e.target.value)}><option value="">Choose…</option>{(options.data ?? []).map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</Field>
      <Field as="select" label="Permission" value={permission} onChange={(e) => setPermission(e.target.value)}><option value="view">View only</option><option value="edit">Editors may edit</option></Field>
      <p className="row__meta">Viewers can only look, whatever permission you pick. Datasets and documents are shared by description only, never their content.</p>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={!id}>Share</button></div>
    </form>
  );
}

function Events({ workspaceId }) {
  const res = useResource(() => phase7.workspaces.events(workspaceId, { limit: 50 }), [workspaceId]);
  return <Loaded res={res} what="the event log">{(d) => d.items.length === 0 ? <p>No events.</p> : <ul className="list">{d.items.map((e) => <li key={e.id} className="row"><div className="row__main"><span className="row__title">{String(e.event).replace(/_/g, ' ')}</span><span className="row__meta">{when(e.created_at ?? e.at)}</span></div></li>)}</ul>}</Loaded>;
}

/** One workspace: members, invitations, shared items and (for admins) the event log. */
export function WorkspacePage() {
  const { workspaceId } = useParams('/workspaces/:workspaceId');
  const res = useResource(() => phase7.workspaces.get(workspaceId), [workspaceId]);
  const [msg, setMsg] = useState(null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('viewer');
  const [link, setLink] = useState(null);
  const reload = () => res.reload({ quiet: true });
  async function invite(e) {
    e.preventDefault();
    const out = await attempt(() => phase7.workspaces.invite(workspaceId, email.trim(), role), setMsg);
    if (out) { setLink(out.invite_link ?? null); setEmail(''); setMsg({ kind: 'success', text: out.delivery === 'sent' ? 'Invitation created and emailed.' : 'Invitation created but not emailed. Send them the link yourself.' }); reload(); }
  }
  return (
    <article className="page">
      <Loaded res={res} what="this workspace">
        {(w) => {
          const admin = w.your_role === 'admin';
          return (
            <>
              <PageHeader eyebrow={`Your role: ${w.your_role}`} title={w.name} back={{ to: '/workspaces', label: 'Workspaces' }} lead={w.public_sharing} />
              {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
              <section className="card" aria-label="Members"><h2 className="card__title">Members</h2>
                <ul className="list">{w.members.map((m) => (
                  <li key={m.user_id} className="row"><div className="row__main"><span className="row__title">{m.name || m.email}</span><span className="row__meta">{m.email} · {m.role}{m.expires_at ? ` · until ${when(m.expires_at)}` : ''}</span></div>
                    {admin ? <button type="button" className="btn btn--ghost" onClick={async () => { if (window.confirm(`Remove ${m.email}? Their access to shared items ends now.`)) { await attempt(() => phase7.workspaces.removeMember(w.id, m.user_id), setMsg, 'Member removed.'); reload(); } }}>Remove</button> : null}</li>))}</ul>
              </section>
              {admin ? (
                <section className="card" aria-label="Invite"><h2 className="card__title">Invite someone</h2>
                  <form onSubmit={invite}>
                    <Field label="Their email address" type="email" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} required />
                    <Field as="select" label="Role" value={role} onChange={(e) => setRole(e.target.value)} hint={ROLE_HINT[role]}>{Object.entries(ROLE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Field>
                    <div className="card__actions"><button className="btn" disabled={!email.trim()}>Create invitation</button></div>
                  </form>
                  {link ? <Notice kind="info" title="Send this link to them yourself">{link}<br />It is shown once, works once, and only for the email address you entered. Invitations are not emailed unless this server has email delivery set up.</Notice> : null}
                  {(w.invitations ?? []).filter((i) => i.status === 'pending').map((i) => <p key={i.id} className="row__meta">Pending: {i.email} as {i.role} <button type="button" className="link-button" onClick={async () => { await attempt(() => phase7.workspaces.cancelInvitation(w.id, i.id), setMsg, 'Invitation cancelled.'); reload(); }}>Cancel</button></p>)}
                </section>
              ) : null}
              <section className="card" aria-label="Shared items"><h2 className="card__title">Shared in this workspace</h2>
                {w.shares.length === 0 ? <p>Nothing shared yet.</p> : <ul className="list">{w.shares.map((s) => (
                  <li key={s.grant_id} className="row"><div className="row__main"><span className="row__title">{RESOURCE_LABEL[s.resource_type] ?? s.resource_type}{s.resource_type === 'report' ? <> · <Link to={`/reports/${encodeURIComponent(s.resource_id)}`}>open</Link></> : s.resource_type === 'model' ? <> · <Link to={`/models/${encodeURIComponent(s.resource_id)}`}>open</Link></> : null}</span><span className="row__meta">by {s.owner} · <Badge tone="muted">{s.permission}</Badge></span></div>
                    <button type="button" className="btn btn--ghost" onClick={async () => { await attempt(() => phase7.workspaces.revoke(s.grant_id), setMsg, 'Sharing stopped. Access ended immediately.'); reload(); }}>Stop sharing</button></li>))}</ul>}
              </section>
              {w.your_role !== 'viewer' ? <ShareForm workspace={w} onDone={reload} /> : <Notice kind="info">Viewers can look at what is shared but cannot share.</Notice>}
              {admin ? <section className="card" aria-label="Event log"><h2 className="card__title">Event log</h2><Events workspaceId={w.id} /></section> : null}
            </>
          );
        }}
      </Loaded>
    </article>
  );
}
