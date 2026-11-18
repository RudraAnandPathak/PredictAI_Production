import { useEffect, useRef, useState } from 'react';
import { conversations } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { MAX_QUESTION, TARGET_NOUN, answerNote, canStart, groupByDay, kindLabel, previewOf, questionProblem, stepsOf } from '../../features/conversations/format.js';
import { saveBlob } from '../research/shared.jsx';
import { Badge, Empty, Notice, PageHeader, attempt, when } from '../phase7/shared.jsx';

const PATH = '/conversations/:kind/:conversationId';

function Citations({ items }) {
  if (!items?.length) return null;
  return (
    <ul className="conv-cites" aria-label="Sources cited in this answer">
      {items.map((c) => (
        <li key={c.id} className="conv-cite">
          <strong>[{c.label}]</strong> {c.source_title}{c.page ? `, page ${c.page}` : ''}
          {!c.verified ? <> <Badge tone="warn">Not verified</Badge></> : null}
          <blockquote>{c.quote}</blockquote>
        </li>
      ))}
    </ul>
  );
}

function Message({ m }) {
  if (m.role === 'user') return <p className="conv-msg conv-msg--user">{m.text}</p>;
  const steps = stepsOf(m);
  const note = answerNote(m);
  return (
    <div className="conv-msg conv-msg--assistant">
      <p>{m.text}</p>
      {note ? <p className="row__meta">{note}</p> : null}
      <Citations items={m.citations} />
      {steps.length ? <details><summary>How this was worked out</summary><ol>{steps.map((s) => <li key={s}>{s}</li>)}</ol></details> : null}
    </div>
  );
}

function NewConversation({ targets, onCreated }) {
  const [kind, setKind] = useState('data');
  const [targetId, setTargetId] = useState('');
  const [title, setTitle] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const options = kind === 'data' ? (targets.datasets ?? []).map((d) => ({ id: d.id, label: `${d.name} (${d.project})` })) : (targets.workspaces ?? []).map((w) => ({ id: w.id, label: w.name }));
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    const out = await attempt(() => conversations.create(kind, targetId, title.trim() || undefined), setMsg);
    setBusy(false);
    if (out) { setTitle(''); onCreated(out); }
  }
  return (
    <form className="card" onSubmit={submit} aria-label="New conversation">
      <h2 className="card__title">New conversation</h2>
      <Field as="select" label="About" value={kind} onChange={(e) => { setKind(e.target.value); setTargetId(''); }}>
        <option value="data">A dataset</option>
        <option value="research">A research workspace</option>
      </Field>
      <Field as="select" label={`Which ${TARGET_NOUN[kind]}`} value={targetId} onChange={(e) => setTargetId(e.target.value)} required>
        <option value="">Choose…</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </Field>
      {!options.length ? <p className="row__meta">{kind === 'data' ? 'Upload a dataset in Projects first.' : 'Create a research workspace and add sources first.'}</p> : null}
      <Field label="Title (optional)" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={busy || !canStart(kind, targetId)}>{busy ? 'Starting…' : 'Start'}</button></div>
    </form>
  );
}

function Chat({ kind, conversationId, onChanged, onGone }) {
  const res = useResource(() => conversations.get(kind, conversationId), [kind, conversationId]);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [pending, setPending] = useState(null);
  const [renaming, setRenaming] = useState(null);
  const [local, setLocal] = useState(null);
  const end = useRef(null);
  const conv = local && local.id === conversationId && local.kind === kind ? local : res.data;
  useEffect(() => { setLocal(null); setMsg(null); setPending(null); setText(''); }, [kind, conversationId]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [conv?.messages?.length, pending]);

  if (res.loading && !conv) return <p role="status">Loading conversation…</p>;
  if (res.error && !conv) return <Notice kind="error">This conversation could not be opened. It may have been deleted. <Link to="/conversations">Back to the list</Link></Notice>;
  if (!conv) return null;

  async function send(e) {
    e.preventDefault();
    const problem = questionProblem(text);
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    const asked = text.trim();
    setBusy(true); setMsg(null); setPending(asked);
    const out = await attempt(() => conversations.send(kind, conversationId, asked), setMsg);
    setBusy(false); setPending(null);
    if (out) { setLocal(out.conversation); setText(''); onChanged(out.conversation); }
  }
  async function rename(e) {
    e.preventDefault();
    const out = await attempt(() => conversations.rename(kind, conversationId, renaming), setMsg);
    if (out) { setLocal(out); setRenaming(null); onChanged(out); }
  }
  async function remove() {
    if (!window.confirm('Delete this conversation? The dataset, workspace, sources and saved results are kept.')) return;
    const out = await attempt(() => conversations.remove(kind, conversationId), setMsg);
    if (out) onGone();
  }
  async function exportIt() {
    try { saveBlob(await conversations.exportMarkdown(kind, conversationId)); } catch (e) { setMsg({ kind: 'error', text: 'The export could not be downloaded. Try again.' }); }
  }
  const target = conv.target?.type === 'dataset' ? `/analytics/${encodeURIComponent(conv.target.id)}` : `/research-intelligence/workspaces/${encodeURIComponent(conv.target?.id ?? '')}`;

  return (
    <section className="card conv-chat" aria-label={`Conversation: ${conv.title}`}>
      <div className="conv-chat__head">
        {renaming === null ? (
          <>
            <h2 className="card__title">{conv.title}</h2>
            <Badge tone="muted">{kindLabel(kind)}</Badge>
          </>
        ) : (
          <form onSubmit={rename} className="conv-rename">
            <Field label="Title" value={renaming} maxLength={200} onChange={(e) => setRenaming(e.target.value)} autoFocus />
            <button className="btn" disabled={!renaming.trim()}>Save</button>
            <button type="button" className="btn btn--ghost" onClick={() => setRenaming(null)}>Cancel</button>
          </form>
        )}
        <div className="conv-chat__actions">
          <Link className="link-button" to={target}>Open {conv.target?.type === 'dataset' ? 'dataset' : 'workspace'}</Link>
          <button type="button" className="link-button" onClick={() => setRenaming(conv.title)}>Rename</button>
          <button type="button" className="link-button" onClick={exportIt}>Export</button>
          <button type="button" className="link-button" onClick={remove}>Delete</button>
        </div>
      </div>
      <div className="conv-thread" role="log" aria-live="polite" aria-label="Messages">
        {!conv.messages.length && !pending ? <p className="row__meta">{kind === 'data' ? 'Ask about this dataset in plain language, for example “What is the average of a column?”. Figures are calculated and checked, not guessed.' : 'Ask about your sources. Answers quote the passages they rely on, and say so when the sources do not contain the answer.'}</p> : null}
        {conv.messages.map((m) => <Message key={m.id} m={m} />)}
        {pending ? <><p className="conv-msg conv-msg--user">{pending}</p><p className="row__meta" role="status">Working…</p></> : null}
        <div ref={end} />
      </div>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <form onSubmit={send}>
        <div className="field">
          <label className="field__label" htmlFor="conv-q">Question</label>
          <textarea id="conv-q" className="field__input" rows={3} value={text} disabled={busy} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(e); }} />
          <p className="field__hint">{text.length}/{MAX_QUESTION}. Press Ctrl or Cmd + Enter to send.</p>
        </div>
        <button className="btn" disabled={busy || !text.trim()}>{busy ? 'Working…' : 'Ask'}</button>
      </form>
    </section>
  );
}

/** Every conversation in one place: a list with search on the left, the open conversation on the right. */
export function ConversationsPage() {
  const { navigate } = useRouter();
  const { kind: openKind, conversationId: openId } = useParams(PATH);
  const [kind, setKind] = useState('');
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => { const t = setTimeout(() => setQuery(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const list = useResource(() => conversations.list({ ...(query ? { q: query } : {}), ...(kind ? { kind } : {}), limit: 100 }), [query, kind]);
  const targets = useResource(() => conversations.targets(), []);
  const open = (c) => navigate(`/conversations/${encodeURIComponent(c.kind)}/${encodeURIComponent(c.id)}`);
  const groups = groupByDay(list.data?.items ?? []);

  return (
    <>
      <PageHeader eyebrow="Workspace" title="Conversations" lead="Ask about a dataset or a research workspace. Your history is kept here, searchable, and every answer shows how it was produced." />
      <div className="conv">
        <aside className="conv__side" aria-label="Your conversations">
          <div className="conv__filters">
            <Field type="search" label="Search conversations" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} hint="Searches titles and messages." />
            <Field as="select" label="Show" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">All</option><option value="data">Datasets</option><option value="research">Research</option>
            </Field>
          </div>
          {list.error && !list.data ? <Notice kind="error">Conversations could not be loaded. <button type="button" className="link-button" onClick={() => list.reload()}>Try again</button></Notice> : null}
          {list.data && !groups.length ? <Empty title={query ? 'No match' : 'No conversations yet'}>{query ? <p>Nothing matches “{query}”.</p> : <p>Start one below.</p>}</Empty> : null}
          {groups.map((g) => (
            <section key={g.label} aria-label={g.label}>
              <h2 className="conv__group">{g.label}</h2>
              <ul className="conv__list">
                {g.items.map((c) => {
                  const current = c.kind === openKind && c.id === openId;
                  return (
                    <li key={`${c.kind}:${c.id}`}>
                      <button type="button" className={`conv__item${current ? ' is-current' : ''}`} aria-current={current ? 'true' : undefined} onClick={() => open(c)}>
                        <span className="conv__title">{c.title}</span>
                        <span className="row__meta">{kindLabel(c.kind)} · {c.target} · {c.messages} message{c.messages === 1 ? '' : 's'}</span>
                        {c.preview ? <span className="conv__preview">{previewOf(c.preview)}</span> : null}
                        <span className="row__meta">{when(c.updated_at)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
          {targets.data ? <NewConversation targets={targets.data} onCreated={(c) => { list.reload({ quiet: true }); open({ kind: c.kind, id: c.id }); }} /> : null}
        </aside>
        <div className="conv__main">
          {openKind && openId
            ? <Chat key={`${openKind}:${openId}`} kind={openKind} conversationId={openId} onChanged={() => list.reload({ quiet: true })} onGone={() => { list.reload({ quiet: true }); navigate('/conversations'); }} />
            : <Empty title="Choose a conversation"><p>Open one from the list, or start a new one about a dataset or a research workspace.</p></Empty>}
        </div>
      </div>
    </>
  );
}
