import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Empty, Loaded, Notice, PageHeader, attempt, when } from '../../../pages/phase7/shared.jsx';
import { ResearchNav } from '../components/ResearchNav.jsx';
import { limitLines, nearLimit } from '../labels.js';

/** List of research workspaces with the limits the server enforces. */
export function ResearchIntelligencePage() {
  const res = useResource(() => Promise.all([ri.workspaces.list(), ri.limits()]).then(([w, l]) => ({ ...w, ri: l })), []);
  const [name, setName] = useState('');
  const [msg, setMsg] = useState(null);
  async function create(e) {
    e.preventDefault();
    const out = await attempt(() => ri.workspaces.create(name.trim()), setMsg, 'Workspace created.');
    if (out) { setName(''); res.reload({ quiet: true }); }
  }
  return (
    <article className="page">
      <ResearchNav current="workspaces" />
      <PageHeader eyebrow="Research" title="My Workspaces" lead="Bring your own papers, articles and data. Ask questions and get answers that quote your sources, or an honest “not enough evidence”." />
      <Loaded res={res} what="your research workspaces">
        {(d) => {
          const l = d.ri;
          const full = l && l.workspaces_used >= l.max_workspaces;
          return (
            <>
              {d.items.length === 0 ? <Empty title="No research workspaces yet"><p>Create one below, then add documents to it.</p></Empty> : (
                <ul className="list">{d.items.map((w) => (
                  <li key={w.id} className="row"><div className="row__main">
                    <span className="row__title"><Link to={`/research-intelligence/${encodeURIComponent(w.id)}`}>{w.name}</Link></span>
                    <span className="row__meta">{w.counts.sources} source(s) · {w.counts.conversations} conversation(s) · {w.counts.notes} note(s) · updated {when(w.updated_at)}</span>
                  </div></li>
                ))}</ul>
              )}
              <form className="card" onSubmit={create}>
                <h2 className="card__title">New workspace</h2>
                {full ? <Notice kind="warning">You have reached your limit of {l.max_workspaces} workspaces. Delete one, or activate Special access for a higher limit.</Notice> : null}
                <Field label="Name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required disabled={full} />
                {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
                <div className="card__actions"><button className="btn" disabled={!name.trim() || full}>Create</button></div>
              </form>
              {l ? (
                <section className="card" aria-label="Your limits">
                  <h2 className="card__title">Your limits</h2>
                  <ul className="list">{limitLines(l).map((t) => <li key={t} className="row__meta">{t}</li>)}</ul>
                  {nearLimit(l.ai_requests_today, l.ai_requests_per_day) || nearLimit(l.imports_today, l.imports_per_day) ? <Notice kind="info">You are close to a daily limit. It resets tomorrow.</Notice> : null}
                </section>
              ) : null}
            </>
          );
        }}
      </Loaded>
    </article>
  );
}
