import { ReviewBox } from '../../components/reviews/ReviewBox.jsx';
import { useEffect, useRef, useState } from 'react';
import { analytics } from '../../api/index.js';
import { EvidencePanel } from '../../components/analytics/EvidencePanel.jsx';
import { RowsPanel } from '../../components/analytics/RowsPanel.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { EVIDENCE_LABEL } from '../../features/analytics/format.js';

const EXAMPLES = ['Which columns have missing values?', 'Are there outliers?', 'What is the average of each numeric column?', 'Which columns are most related?'];

function Verification({ v }) {
  if (!v) return null;
  const cls = v.status === 'PASSED' ? 'badge--ok' : v.status === 'FAILED' ? 'badge--danger' : 'badge--warn';
  return <p className="muted"><span className={`badge ${cls}`}>{v.status === 'PASSED' ? 'Figures re-checked' : v.status === 'FAILED' ? 'Check failed' : 'Not independently checked'}</span> {v.method}: {v.matched} of {v.checked} figures matched{v.not_checked ? `, ${v.not_checked} not checkable` : ''}.</p>;
}

function Answer({ turn, onChoose, onRows }) {
  const [run, setRun] = useState(null);
  const [open, setOpen] = useState(false);
  const r = turn.reply;
  if (r.status === 'needs_clarification') {
    return (
      <div className="msg msg--assistant">
        <p>{r.clarification.prompt}</p>
        {r.clarification.options.length ? <div className="chip-row">{r.clarification.options.map((o) => <button key={o.value} type="button" className="btn btn--ghost btn--sm" disabled={!turn.latest} onClick={() => onChoose(r, o.value)}>{o.label}</button>)}</div> : <p className="muted">Ask again with a bit more detail.</p>}
      </div>
    );
  }
  if (r.status === 'cannot_answer') return <div className="msg msg--assistant"><p>{r.message}</p></div>;
  const a = r.answer;
  async function showEvidence() {
    setOpen(!open);
    if (!run) { try { setRun(await analytics.getRun(r.run.id)); } catch { setRun(false); } }
  }
  return (
    <div className="msg msg--assistant">
      <p className="msg__text">{a.ai?.used ? a.ai.text : a.text}</p>
      {a.ai?.used ? <p className="muted">Wording by a local AI model; every figure was checked against the calculated results. <details><summary>Standard answer</summary>{a.text}</details></p> : null}
      {a.ai?.reason && !a.ai.used ? <p className="muted">{a.ai.reason}</p> : null}
      {a.caveats?.length ? <ul className="warnings">{a.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul> : null}
      <Verification v={a.verification} />
      <Explanation value={a.explanation} level={5} />
      <details>
        <summary>How I understood the question</summary>
        <ol>{r.interpretation.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        {r.interpretation.notes?.length ? <ul>{r.interpretation.notes.map((s, i) => <li key={i}>{s}</li>)}</ul> : null}
      </details>
      <div className="btn-row">
        <span className={`badge ${r.evidence_status === 'VERIFIED' ? 'badge--ok' : 'badge--warn'}`}>{EVIDENCE_LABEL[r.evidence_status]}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={showEvidence} aria-expanded={open}>{open ? 'Hide evidence' : 'Show evidence'}</button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onRows(r.run.id, { type: 'all' }, 'this answer')}>See the rows</button>
      </div>
      {open && run ? <EvidencePanel run={run} onOpenRows={(s, l) => onRows(r.run.id, s, l)} onRerun={setRun} onSaved={(s) => setRun({ ...run, ...s })} /> : null}
      {r.run?.id ? <ReviewBox key={r.run.id} kind="analysis" targetId={r.run.id} /> : null}
      {open && run === false ? <p className="message message--error" role="alert">Could not load the evidence.</p> : null}
    </div>
  );
}

export function AskTab({ schema }) {
  const [turns, setTurns] = useState([]);
  const [text, setText] = useState('');
  const [useAi, setUseAi] = useState(false);
  const [convId, setConvId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [rows, setRows] = useState(null);
  const end = useRef(null);
  const provider = useResource(() => analytics.provider(), []);
  const history = useResource(() => analytics.conversations(schema.asset_id), [schema.asset_id]);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'nearest' }); }, [turns.length]);
  const ai = provider.data;

  async function send(question, choices) {
    setBusy(true); setError('');
    setTurns((t) => [...t.map((x) => ({ ...x, latest: false })), ...(choices ? [] : [{ role: 'user', text: question }])]);
    try {
      const reply = await analytics.ask(schema.asset_id, { question, conversation_id: convId, choices, use_ai: useAi && !!ai?.available, version_id: schema.version_id });
      setConvId(reply.conversation_id);
      setTurns((t) => [...t, { role: 'assistant', reply, question, latest: true }]);
      history.reload({ quiet: true });
    } catch (e) { setError(describeError(e)); } finally { setBusy(false); }
  }
  async function open(id) {
    setError('');
    try {
      const c = await analytics.conversation(id);
      setConvId(id);
      setTurns(c.messages.map((m, i) => m.role === 'user' ? { role: 'user', text: m.content } : { role: 'saved', text: m.content, payload: m.payload, runId: m.run_id, key: i }));
    } catch (e) { setError(describeError(e)); }
  }
  function submit(e) { e.preventDefault(); const q = text.trim(); if (!q || busy) return; setText(''); send(q, undefined); }
  function choose(reply, value) { const last = [...turns].reverse().find((t) => t.role === 'user'); send(last?.text ?? '', { [reply.clarification.slot]: value }); }
  const convs = history.data?.items ?? [];

  return (
    <div className="stack">
      <p className="muted">Ask in plain words. Your question is turned into a fixed analysis plan, calculated by the analysis engine, and each figure is re-checked independently. If something is unclear I ask, rather than guess.</p>
      <div className="chat" role="log" aria-live="polite" aria-label="Conversation">
        {turns.length === 0 ? <div className="chat__empty"><p>Try one of these:</p><div className="chip-row">{EXAMPLES.map((q) => <button key={q} type="button" className="btn btn--ghost btn--sm" onClick={() => send(q)}>{q}</button>)}</div></div> : null}
        {turns.map((t, i) => t.role === 'user' ? <div key={i} className="msg msg--user"><p>{t.text}</p></div>
          : t.role === 'saved' ? <div key={i} className="msg msg--assistant"><p>{t.text}</p>{t.runId ? <div className="btn-row"><button type="button" className="btn btn--ghost btn--sm" onClick={() => setRows({ runId: t.runId, select: { type: 'all' }, label: 'this answer' })}>See the rows</button></div> : null}</div>
          : <Answer key={i} turn={t} onChoose={choose} onRows={(runId, select, label) => setRows({ runId, select, label })} />)}
        {busy ? <p className="muted" role="status">Working it out…</p> : null}
        <div ref={end} />
      </div>
      {error ? <p className="message message--error" role="alert">{error}</p> : null}
      <form className="askbar" onSubmit={submit}>
        <label className="sr-only" htmlFor="ask-input">Your question</label>
        <input id="ask-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} placeholder="e.g. What is the average of revenue for region = North?" disabled={busy} />
        <button type="submit" className="btn" disabled={busy || !text.trim()}>Ask</button>
      </form>
      <div className="aiopt">
        {ai?.available ? (
          <label className="check"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} /> Let the {ai.local ? 'local' : 'configured'} AI model ({ai.name}) reword answers. Only the already-verified figures and your question are sent{ai.local ? ' to a model running on this server\'s own machine' : ''}; never your rows.</label>
        ) : <p className="muted">No AI model is connected, so answers use the standard wording. Nothing is sent to any outside AI service.</p>}
      </div>
      {rows ? <RowsPanel runId={rows.runId} select={rows.select} label={rows.label} onClose={() => setRows(null)} /> : null}
      {convs.length ? (
        <details className="card"><summary>Earlier conversations ({convs.length})</summary>
          <ul className="list">{convs.map((c) => <li key={c.id}><button type="button" className="btn btn--ghost btn--sm" onClick={() => open(c.id)}>{c.title}</button> <span className="muted">{c.updated_at?.slice(0, 10)}</span></li>)}</ul>
        </details>
      ) : null}
    </div>
  );
}
