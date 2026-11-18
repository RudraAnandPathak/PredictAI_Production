import { useEffect, useState } from 'react';
import { useResource } from '../../../features/identity/useResource.js';
import { ri } from '../../../api/index.js';
import { Badge, Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { answerNote, answerSegments, citationWhere, readyCount } from '../labels.js';

function Answer({ message, onCite }) {
  const segs = answerSegments(message.body, message.citations);
  const note = answerNote(message.answer);
  return (
    <div className="ri-msg ri-msg--assistant">
      <p className="ri-msg__body">
        {segs.map((s, i) => s.kind === 'text' ? <span key={i}>{s.text}</span>
          : <button key={i} type="button" className="link-button" aria-label={`Citation ${s.label}: ${citationWhere(s.citation)}`} onClick={() => onCite(s.citation)}>[{s.label}]</button>)}
      </p>
      {note ? <p className="row__meta">{note}</p> : null}
      {message.answer?.mode === 'insufficient_evidence' ? <Badge tone="planned">No answer given</Badge> : null}
    </div>
  );
}

/** Conversation: ask, read the answer, click a citation to see the exact stored passage. */
export function AssistantPanel({ wid, sources, onCite, scope = [] }) {
  const history = useResource(() => ri.conversations.list(wid), [wid]);
  const [conv, setConv] = useState(null);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const ready = readyCount(sources);

  useEffect(() => { setConv(null); setMessages([]); }, [wid]);

  async function openConversation(id) {
    if (!id) { setConv(null); setMessages([]); return; }
    const out = await attempt(() => ri.conversations.get(wid, id), setMsg);
    if (out) { setConv(out.conversation); setMessages(out.messages); setMsg(null); }
  }
  async function removeConversation() {
    if (!conv || !window.confirm('Delete this conversation and its citations? Saved evidence and notes are kept.')) return;
    const out = await attempt(() => ri.conversations.remove(wid, conv.id), setMsg);
    if (out) { setConv(null); setMessages([]); history.reload({ quiet: true }); }
  }

  async function ask(e) {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy) return;
    setBusy(true); setMsg(null);
    setMessages((m) => [...m, { id: `pending-${m.length}`, role: 'user', body: q }]);
    setQuestion('');
    const out = await attempt(() => ri.ask(wid, q, conv?.id, scope), setMsg);
    setBusy(false);
    if (out) { history.reload({ quiet: true }); setConv(out.conversation); setMessages((m) => [...m.filter((x) => !String(x.id).startsWith('pending-')), { id: `u-${out.message.id}`, role: 'user', body: q }, out.message]); }
    else setMessages((m) => m.filter((x) => !String(x.id).startsWith('pending-')));
  }

  return (
    <section className="card ri-panel" aria-label="Ask your sources">
      <h2 className="card__title">Ask your sources</h2>
      <div className="field">
        <label className="field__label" htmlFor="ri-conv">Conversation</label>
        <select id="ri-conv" className="field__input" value={conv?.id ?? ''} onChange={(e) => openConversation(e.target.value)}>
          <option value="">New conversation</option>
          {(history.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.title || 'Untitled'} ({c.messages})</option>)}
        </select>
        {conv ? <button type="button" className="link-button" onClick={removeConversation}>Delete this conversation</button> : null}
      </div>
      <p className="row__meta" role="status">{scope.length ? `Searching only the ${scope.length} source(s) you selected.` : 'Searching all included sources.'}</p>
      {ready === 0 ? <Notice kind="info">Add and finish indexing at least one source to ask questions. Answers only use the sources you choose.</Notice> : null}
      <div className="ri-thread" aria-live="polite">
        {messages.map((m) => m.role === 'user' ? <p key={m.id} className="ri-msg ri-msg--user">{m.body}</p> : <Answer key={m.id} message={m} onCite={onCite} />)}
        {busy ? <p className="row__meta">Searching your sources…</p> : null}
      </div>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <form onSubmit={ask}>
        <div className="field">
          <label className="field__label" htmlFor="ri-q">Question</label>
          <textarea id="ri-q" className="field__input" rows={3} maxLength={2500} value={question} disabled={ready === 0} onChange={(e) => setQuestion(e.target.value)} />
          <p className="field__hint">Answers cite exact passages. If your sources do not contain the answer, you will be told so instead of getting a guess.</p>
        </div>
        <button className="btn" disabled={busy || ready === 0 || !question.trim()}>{busy ? 'Working…' : 'Ask'}</button>
      </form>
    </section>
  );
}
