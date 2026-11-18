import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Loaded, Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { useState } from 'react';
import { ResearchFrame } from '../components/ResearchNav.jsx';
import { POLICY_TEXT, limitLines } from '../labels.js';
import { providerState } from '../logic.js';

/** Limits, search providers, history and how each workspace treats external AI. Keys are never shown: only whether one is set. */
export function ResearchSettingsPage() {
  const res = useResource(() => Promise.all([ri.limits(), ri.discovery.providers(), ri.workspaces.list(), ri.discovery.searches()]).then(([limits, providers, ws, searches]) => ({ limits, providers, ws, searches })), []);
  const [msg, setMsg] = useState(null);
  return (
    <ResearchFrame current="settings" title="Research Settings" lead="What you can do, which search providers are on, and where your documents are allowed to go.">
      <Loaded res={res} what="research settings">
        {(d) => (
          <>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <section className="card" aria-label="Your limits"><h2 className="card__title">Your limits</h2><ul className="list">{limitLines(d.limits).map((t) => <li key={t} className="row__meta">{t}</li>)}</ul></section>
            <section className="card" aria-label="Search providers">
              <h2 className="card__title">Search providers</h2>
              <p className="row__meta">{d.providers.note}</p>
              <ul className="list">{d.providers.items.map((p) => (
                <li key={p.key} className="row"><div className="row__main"><span className="row__title">{p.label} <span className="row__meta">({p.host})</span></span>
                  <span className="row__meta">{p.description}</span>
                  <span className="row__meta">{p.enabled ? (p.available ? 'On and usable' : `On, but not usable: ${p.reason ?? 'unknown reason'}`) : 'Switched off by the platform owner'}{p.key_configured === true ? ' · an API key is configured' : p.key_configured === false ? ' · no API key (the free tier is used)' : ''}</span></div></li>
              ))}</ul>
              <p className="row__meta">Providers are turned on or off, and keys are set, in the server’s environment file by the platform owner. Keys never appear in this app.</p>
            </section>
            <section className="card" aria-label="AI and your documents">
              <h2 className="card__title">AI and your documents</h2>
              <p className="row__meta">Each workspace has its own setting. {POLICY_TEXT.local_only}</p>
              <ul className="list">{d.ws.items.map((w) => <li key={w.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/research-intelligence/${encodeURIComponent(w.id)}`}>{w.name}</Link></span><span className="row__meta">Change this at the bottom of the workspace page.</span></div></li>)}</ul>
            </section>
            <section className="card" aria-label="Search history">
              <h2 className="card__title">Discovery search history</h2>
              {d.searches.items.length === 0 ? <p className="row__meta">No stored searches.</p> : <ul className="list">{d.searches.items.map((s) => <li key={s.id} className="row"><div className="row__main"><span className="row__title">{s.query.query || s.query.title || s.query.author || s.query.doi}</span><span className="row__meta">{s.total} result(s) · {s.providers.map((p) => `${p.provider}: ${providerState(p.status)}`).join(', ')}</span></div></li>)}</ul>}
              <div className="btn-row"><button type="button" className="btn btn--ghost" disabled={d.searches.items.length === 0} onClick={() => attempt(() => ri.discovery.clearHistory(), setMsg, 'Search history cleared.').then((o) => o && res.reload({ quiet: true }))}>Clear search history</button></div>
            </section>
          </>
        )}
      </Loaded>
    </ResearchFrame>
  );
}
