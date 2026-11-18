import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Link } from '../../../app/router/Link.jsx';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Empty, Loaded, Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { CompareTable } from '../components/CompareTable.jsx';
import { Paper } from '../components/Paper.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';
import { canCompare, toggleId } from '../logic.js';

/** Saved papers: compare, add to a workspace (full text only where the provider reported it), remove. */
export function LibraryPage() {
  const res = useResource(() => Promise.all([ri.discovery.library(), ri.workspaces.list()]).then(([l, w]) => ({ library: l, workspaces: w.items })), []);
  const [picked, setPicked] = useState([]);
  const [compare, setCompare] = useState(null);
  const [target, setTarget] = useState('');
  const [fullText, setFullText] = useState(false);
  const [msg, setMsg] = useState(null);
  const reload = () => res.reload({ quiet: true });
  const importOne = async (rec) => {
    if (!target) { setMsg({ kind: 'error', text: 'Choose a workspace first.' }); return; }
    const out = await attempt(() => ri.discovery.importTo(target, rec.library_id, fullText), setMsg);
    if (out) { setMsg({ kind: 'success', text: out.message ?? 'Added to the workspace. It will be indexed shortly.' }); reload(); }
  };
  return (
    <ResearchFrame current="library" title="Source Library" lead="Papers you saved from Discover. Adding one to a workspace imports its record and abstract; full text is fetched only if you ask and the provider reported an open copy.">
      <Loaded res={res} what="your library">
        {(d) => (
          <>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <div className="card ri-filters">
              <Field as="select" label="Add to workspace" value={target} onChange={(e) => setTarget(e.target.value)}>
                <option value="">Choose a workspace…</option>
                {d.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
              </Field>
              <label className="ri-check"><input type="checkbox" checked={fullText} onChange={(e) => setFullText(e.target.checked)} /> Also fetch open full text when available</label>
              <div className="btn-row"><button type="button" className="btn" disabled={!canCompare(picked)} onClick={async () => { const o = await attempt(() => ri.discovery.compare(picked), setMsg); if (o) setCompare(o); }}>Compare {picked.length} selected</button></div>
            </div>
            {compare ? <CompareTable data={compare} /> : null}
            {d.library.items.length === 0 ? <Empty title="Your library is empty"><p>Search in <Link to="/research-intelligence/discover">Discover Research</Link> and save papers here.</p></Empty> : (
              <ul className="list">{d.library.items.map((rec) => (
                <Paper key={rec.library_id} rec={rec} showAbstract={false} selected={picked.includes(rec.library_id)} onSelect={() => setPicked((p) => toggleId(p, rec.library_id, 6))}
                  actions={(
                    <>
                      <button type="button" className="btn btn--ghost" onClick={() => importOne(rec)}>Add to workspace</button>
                      <button type="button" className="btn btn--ghost" onClick={() => attempt(() => ri.discovery.removeLibrary(rec.library_id), setMsg).then((o) => { if (o) { setPicked((p) => p.filter((x) => x !== rec.library_id)); reload(); } })}>Remove</button>
                      {rec.in_workspaces?.length ? <span className="row__meta">In: {rec.in_workspaces.map((w) => w.workspace_name).join(', ')}</span> : null}
                    </>
                  )} />
              ))}</ul>
            )}
          </>
        )}
      </Loaded>
    </ResearchFrame>
  );
}
