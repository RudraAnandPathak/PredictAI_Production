import { useState } from 'react';
import { research } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { Notice } from './shared.jsx';

const MAX = 20000;

/** Your own notes on a piece of research. They are stored as written by you and never mixed into the evidence. */
export function NotesPanel({ sessionId }) {
  const notes = useResource(() => research.notes(sessionId), [sessionId]);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState(null);
  const items = notes.data?.items ?? [];

  async function run(action, ok) {
    setMessage(null);
    try { await action(); if (ok) setMessage({ kind: 'info', text: ok }); notes.reload({ quiet: true }); return true; } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); return false; }
  }
  async function add(event) {
    event.preventDefault();
    if (!draft.trim()) { setMessage({ kind: 'error', text: 'Write something first.' }); return; }
    if (await run(() => research.addNote(sessionId, draft.trim()), 'Note added.')) setDraft('');
  }
  async function save() {
    if (!editing.body.trim()) { setMessage({ kind: 'error', text: 'A note cannot be empty. Delete it instead.' }); return; }
    if (await run(() => research.updateNote(editing.id, editing.body.trim()), 'Note saved.')) setEditing(null);
  }

  return (
    <section className="card stack" aria-label="Notes">
      <h2 className="card__title">Your notes</h2>
      <p className="row__meta">Notes are yours. They are kept separate from the evidence and are deleted with this research.</p>
      <ul className="list">
        {items.map((n) => (
          <li key={n.id} className="row note" data-note-id={n.id}>
            {editing?.id === n.id ? (
              <div className="stack" style={{ width: '100%' }}>
                <Field label="Edit note" as="textarea" rows={3} maxLength={MAX} value={editing.body} onChange={(e) => setEditing({ ...editing, body: e.target.value })} />
                <div className="btn-row"><button type="button" className="btn btn--sm" onClick={save}>Save</button><button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(null)}>Cancel</button></div>
              </div>
            ) : (
              <>
                <div className="row__main"><span className="note__body">{n.body}</span><span className="row__meta">Written by you · {formatWhen(n.updated_at)}{n.target_type !== 'session' ? ` · about a ${n.target_type}` : ''}</span></div>
                <div className="btn-row">
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing({ id: n.id, body: n.body })}>Edit</button>
                  <button type="button" className="btn btn--ghost btn--sm" onClick={() => { if (window.confirm('Delete this note?')) run(() => research.deleteNote(n.id), 'Note deleted.'); }}>Delete</button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
      {!notes.loading && items.length === 0 ? <p className="muted">No notes yet.</p> : null}
      <form className="stack" onSubmit={add} aria-label="Add a note">
        <Field label="Add a note" as="textarea" rows={2} maxLength={MAX} value={draft} onChange={(e) => setDraft(e.target.value)} />
        <div className="btn-row"><button type="submit" className="btn btn--sm">Add note</button></div>
      </form>
      {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
      {notes.error ? <Notice kind="error">{describeError(notes.error)}</Notice> : null}
    </section>
  );
}
