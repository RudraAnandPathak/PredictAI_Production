import { useState } from 'react';
import { nb } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { MAX_DEEP_SOURCES, deepProblem } from '../logic.js';

/** Deep Research: plan searches, search the web and paper databases, and add the best results to this notebook as sources. */
export function DeepResearchPanel({ wid, onAdded }) {
  const [question, setQuestion] = useState('');
  const [count, setCount] = useState('5');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [result, setResult] = useState(null);
  async function run(e) {
    e.preventDefault();
    const problem = deepProblem({ question, maxSources: count });
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(true);
    const out = await attempt(() => nb.deepResearch(wid, question.trim(), Number(count)), setMsg);
    setBusy(false);
    if (out) { setResult(out.meta); setMsg(null); onAdded?.(); }
  }
  return (
    <form className="card" aria-label="Deep Research" onSubmit={run}>
      <h2 className="card__title">Deep Research</h2>
      <p className="row__meta">Searches the web and paper databases the server owner enabled, then adds the best results here as sources. Your question is sent to the search providers.</p>
      <Field label="What do you want to find out?" value={question} onChange={(e) => setQuestion(e.target.value)} />
      <Field label={`Sources to add (1 to ${MAX_DEEP_SOURCES})`} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value.replace(/\D/g, '').slice(0, 1))} />
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={busy}>{busy ? 'Searching…' : 'Find sources'}</button></div>
      {result ? (
        <div role="status">
          <p className="row__meta">Searched: {result.queries.join(' · ')}</p>
          <p><strong>Added {result.added.length} source(s).</strong> They are read in the background; when they show as ready, use the Studio.</p>
          <ul className="list">{result.added.map((a, i) => <li key={i} className="row__meta">{a.title}{a.year ? ` (${a.year})` : ''}</li>)}</ul>
          {result.failures.map((f, i) => <Notice key={i} kind="warn">Search failed: {f}</Notice>)}
          {result.skipped.slice(0, 5).map((s, i) => <p key={i} className="row__meta">Not added: {s.title} ({s.reason})</p>)}
        </div>
      ) : null}
    </form>
  );
}
