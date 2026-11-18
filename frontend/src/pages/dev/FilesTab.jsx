import { useState } from 'react';
import { dev } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { bytesText, cleanPath, groupByFolder } from '../../features/dev/labels.js';
import { Empty, Loaded, Notice, attempt, when } from './shared.jsx';

/** File tree and a plain text editor. Saves use the version you opened, so a change made elsewhere is reported instead of overwritten. */
export function FilesTab({ projectId }) {
  const tree = useResource(() => dev.files.list(projectId), [projectId]);
  const [open, setOpen] = useState(null);       // { path, content, version }
  const [draft, setDraft] = useState('');
  const [newPath, setNewPath] = useState('');
  const [msg, setMsg] = useState(null);
  const [history, setHistory] = useState(null);

  async function load(path, version) {
    const f = await attempt(() => dev.files.read(projectId, path, version), setMsg);
    if (f) { setOpen({ path, version: f.version, latest: version == null }); setDraft(f.content ?? ''); setMsg(null); setHistory(null); }
  }
  async function save() {
    const out = await attempt(() => dev.files.write(projectId, open.path, draft, open.version), setMsg, 'Saved.');
    if (out) { setOpen({ ...open, version: out.version }); tree.reload({ quiet: true }); }
  }
  async function create(e) {
    e.preventDefault();
    const p = cleanPath(newPath);
    if (!p) { setMsg({ kind: 'error', text: 'Use a relative path such as src/train.py (no “..”, no leading slash).' }); return; }
    const out = await attempt(() => dev.files.write(projectId, p, '', null), setMsg, `Created ${p}.`);
    if (out) { setNewPath(''); tree.reload({ quiet: true }); load(p); }
  }
  async function remove() {
    const out = await attempt(() => dev.files.remove(projectId, open.path, open.path), setMsg, 'Deleted. Earlier versions can still be restored from history.');
    if (out) { setOpen(null); tree.reload({ quiet: true }); }
  }
  async function showHistory() { const h = await attempt(() => dev.files.history(projectId, open.path), setMsg); if (h) setHistory(h.versions ?? []); }
  async function restore(v) { const out = await attempt(() => dev.files.restore(projectId, open.path, v), setMsg, `Version ${v} restored as a new version.`); if (out) load(open.path); }

  return (
    <div>
      <form className="card" onSubmit={create} aria-label="New file">
        <Field label="New file" value={newPath} onChange={(e) => setNewPath(e.target.value)} hint="Credential files (.env, keys) and anything that looks like a secret are refused." />
        <div className="card__actions"><button className="btn" disabled={!newPath.trim()}>Create</button></div>
      </form>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <Loaded res={tree} what="files">
        {(d) => (d.items.length === 0 ? <Empty title="No files yet"><p>Create a file above to start this workspace.</p></Empty> : (
          <div>
            {groupByFolder(d.items).map(({ folder, files }) => (
              <section key={folder || '(top)'}><h3>{folder || 'Top level'}</h3>
                <ul className="list">{files.map((f) => (
                  <li key={f.path} className="row"><div className="row__main"><span className="row__title"><button type="button" className="link" onClick={() => load(f.path)}>{f.path.split('/').pop()}</button></span>
                    <span className="row__meta">{bytesText(f.bytes)} · v{f.version} · {when(f.updated_at)}</span></div></li>))}</ul></section>))}
          </div>
        ))}
      </Loaded>
      {open ? (
        <section className="card" aria-label={`Editing ${open.path}`}>
          <h3 className="card__title">{open.path} {open.latest ? `(version ${open.version})` : `(older version ${open.version}, read only)`}</h3>
          <Field as="textarea" label="Contents" rows={18} value={draft} spellCheck={false} onChange={(e) => setDraft(e.target.value)} readOnly={!open.latest} style={{ fontFamily: 'monospace' }} />
          <div className="card__actions">
            {open.latest ? <button className="btn" type="button" onClick={save}>Save</button> : null}
            <button className="btn btn--ghost" type="button" onClick={showHistory}>Version history</button>
            {open.latest ? <button className="btn btn--ghost" type="button" onClick={remove}>Delete file</button> : null}
          </div>
          {history ? <ul className="list">{history.map((v) => <li key={v.version} className="row"><div className="row__main"><span className="row__title">Version {v.version}</span><span className="row__meta">{when(v.created_at)} · {bytesText(v.bytes)}</span></div>
            <button className="btn btn--ghost" type="button" onClick={() => load(open.path, v.version)}>View</button> <button className="btn btn--ghost" type="button" onClick={() => restore(v.version)}>Restore</button></li>)}</ul> : null}
        </section>
      ) : null}
    </div>
  );
}
