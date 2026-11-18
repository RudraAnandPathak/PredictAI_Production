import { useEffect, useState } from 'react';
import { ri } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Notice, attempt, when } from '../../../pages/phase7/shared.jsx';
import { analysisActive, analysisBody, analysisState, toggleId } from '../logic.js';
import { AnalysisResult } from './AnalysisResult.jsx';

const POLL_MS = 3000;

/** Start an analysis of chosen sources (runs in the background), watch it, read it. */
export function StudioAnalyses({ wid, sources, selected, onSelect, analysisId, onPick }) {
  const list = useResource(() => ri.analyses.list(wid), [wid]);
  const [detail, setDetail] = useState(null);
  const [title, setTitle] = useState('');
  const [question, setQuestion] = useState('');
  const [useModel, setUseModel] = useState(false);
  const [msg, setMsg] = useState(null);
  const items = list.data?.items ?? [];
  const active = items.some(analysisActive);
  const ready = sources.filter((s) => s.status === 'ready' && s.included && s.chunk_count > 0);
  const readyIds = ready.map((s) => s.id);

  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => list.reload({ quiet: true }), POLL_MS);
    return () => clearInterval(t);
  }, [active, list.reload]);
  useEffect(() => {
    if (!analysisId) { setDetail(null); return undefined; }
    let live = true;
    ri.analyses.get(wid, analysisId).then((d) => live && setDetail(d)).catch(() => live && setDetail(null));
    return () => { live = false; };
  }, [wid, analysisId, items.find((a) => a.id === analysisId)?.status]);

  async function start(e) {
    e.preventDefault();
    const { body, errors } = analysisBody({ title, question, sourceIds: selected, useModel }, readyIds);
    if (errors.length) { setMsg({ kind: 'error', text: errors.join(' ') }); return; }
    const out = await attempt(() => ri.analyses.start(wid, body), setMsg, 'Analysis started. It runs in the background and you can leave this page.');
    if (out) { list.reload({ quiet: true }); onPick(out.id); }
  }
  return (
    <section className="card" aria-label="Analyses">
      <h2 className="card__title">1. Compare your sources</h2>
      <form onSubmit={start}>
        <fieldset className="ri-fieldset"><legend>Sources to compare (at least two)</legend>
          {ready.length < 2 ? <Notice kind="info">Add and finish indexing at least two sources in this workspace first.</Notice> : null}
          {ready.map((s) => <label key={s.id} className="ri-check"><input type="checkbox" checked={selected.includes(s.id)} onChange={() => onSelect(toggleId(selected, s.id))} /> {s.title}</label>)}
          {ready.length > 0 ? <button type="button" className="link-button" onClick={() => onSelect(selected.length === ready.length ? [] : readyIds)}>{selected.length === ready.length ? 'Clear selection' : 'Select all'}</button> : null}
        </fieldset>
        <Field label="Focus question (optional)" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} hint="Narrows the comparison to passages about this topic." />
        <Field label="Title (optional)" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
        <label className="ri-check"><input type="checkbox" checked={useModel} onChange={(e) => setUseModel(e.target.checked)} /> Also ask an AI model for a short narrative (allowed only by your workspace’s AI setting; every sentence is checked against the quotes)</label>
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <button className="btn" disabled={selected.length < 2}>Start analysis</button>
      </form>
      {list.error && !list.data ? <Notice kind="error">Analyses could not be loaded.</Notice> : null}
      <ul className="list">{items.map((a) => (
        <li key={a.id} className="row"><div className="row__main">
          <span className="row__title"><button type="button" className="link-button" aria-pressed={a.id === analysisId} onClick={() => onPick(a.id)}>{a.title}</button></span>
          <span className="row__meta">{analysisState(a.status)}{analysisActive(a) ? ` · ${a.stage ?? ''} ${Math.round(a.progress ?? 0)}%` : ''}{a.status === 'failed' ? ` · ${a.error_message ?? 'it did not finish'}` : ''} · {a.source_ids.length} source(s) · {when(a.created_at)}</span>
          <span className="btn-row">
            {analysisActive(a) ? <button type="button" className="btn btn--ghost" onClick={() => attempt(() => ri.analyses.cancel(wid, a.id), setMsg).then(() => list.reload({ quiet: true }))}>Cancel</button> : <button type="button" className="btn btn--ghost" onClick={() => attempt(() => ri.analyses.remove(wid, a.id), setMsg).then((o) => { if (o) { if (a.id === analysisId) onPick(null); list.reload({ quiet: true }); } })}>Delete</button>}
          </span>
        </div></li>
      ))}</ul>
      {detail?.status === 'succeeded' ? <AnalysisResult result={detail.result} /> : null}
      {detail?.status === 'queued' ? <Notice kind="info">Waiting for the background worker. If this does not start, ask the platform owner to run the research worker.</Notice> : null}
    </section>
  );
}
