import { useState } from 'react';
import { nb } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Badge, Loaded, Notice, attempt, when } from '../../../pages/phase7/shared.jsx';
import { downloadBlob } from '../../../components/analytics/ChartFrame.jsx';
import { EXPORT_LABEL, blocks, citeTarget, modeNote, readySourceCount, studioProblem, warnings } from '../logic.js';
import { StructuredView, hasStructuredView } from './StructuredView.jsx';
import { ReviewBox } from '../../../components/reviews/ReviewBox.jsx';

function Parts({ parts, output, onCite }) {
  return parts.map((p, i) => {
    if (p.type === 'cite') {
      const target = citeTarget(output, p.value);
      return target ? <button key={i} type="button" className="btn btn--ghost nb-cite" aria-label={`Open the passage for source mark ${p.value}`} onClick={() => onCite(target)}>[{p.value}]</button> : null;
    }
    return p.type === 'bold' ? <strong key={i}>{p.value}</strong> : <span key={i}>{p.value}</span>;
  });
}

function Output({ output, onCite, exportsFor, setMsg }) {
  const items = blocks(output.content);
  async function download(fmt) {
    const out = await attempt(() => nb.download(output.id, fmt), setMsg);
    if (out) downloadBlob(out.blob, out.filename);
  }
  return (
    <div className="card nb-output" role="region" aria-label={output.title}>
      <h3 className="card__title">{output.title} <Badge tone={output.mode === 'model' ? 'ok' : 'warn'}>{output.mode === 'model' ? 'AI-written' : 'Extract'}</Badge></h3>
      <p className="row__meta">{modeNote(output)}</p>
      {warnings(output).map((w, i) => <Notice key={i} kind="warn">{w}</Notice>)}
      <div className="btn-row">{(exportsFor(output.kind)).map((f) => <button key={f} type="button" className="btn btn--ghost" onClick={() => download(f)}>Download {EXPORT_LABEL[f]}</button>)}</div>
      {hasStructuredView(output.kind) ? <StructuredView output={output} onCite={onCite} /> : items.map((b, i) => (b.type === 'heading' ? <h4 key={i}><Parts parts={b.parts} output={output} onCite={onCite} /></h4>
        : b.type === 'item' ? <p key={i} className="nb-item">• <Parts parts={b.parts} output={output} onCite={onCite} /></p>
          : <p key={i} className={b.note ? 'row__meta' : undefined}><Parts parts={b.parts} output={output} onCite={onCite} /></p>))}
      <ReviewBox key={output.id} kind="notebook_output" targetId={output.id} />
    </div>
  );
}

/** Studio: written outputs, study material, slides, an infographic and an audio overview, all from the notebook's sources. Each item cites passages you can open. */
export function StudioPanel({ wid, sources, onCite }) {
  const kinds = useResource(() => nb.kinds(), []);
  const list = useResource(() => nb.list(wid), [wid]);
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);
  const [open, setOpen] = useState(null);
  const ready = readySourceCount(sources);
  async function make(kind) {
    const problem = studioProblem({ kind, instructions, readySources: ready });
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(kind);
    const out = await attempt(() => nb.generate(wid, kind, instructions.trim()), setMsg);
    setBusy(null);
    if (out) { setOpen(out); setMsg(null); list.reload({ quiet: true }); }
  }
  async function show(id) { const out = await attempt(() => nb.get(id), setMsg); if (out) setOpen(out); }
  async function remove(id) {
    const out = await attempt(() => nb.remove(id), setMsg);
    if (out) { if (open?.id === id) setOpen(null); list.reload({ quiet: true }); }
  }
  return (
    <section className="card" aria-label="Studio">
      <h2 className="card__title">Studio</h2>
      <Loaded res={kinds} what="the Studio options">
        {(k) => (
          <>
            <p className="row__meta">Written outputs</p>
            <div className="btn-row">{k.items.filter((x) => !x.structured).map((x) => (
              <button key={x.kind} type="button" className="btn btn--ghost" title={x.blurb} disabled={busy !== null} onClick={() => make(x.kind)}>{busy === x.kind ? 'Writing…' : x.title}</button>))}</div>
            <p className="row__meta">Study and present (need an AI model)</p>
            <div className="btn-row">{k.items.filter((x) => x.structured).map((x) => (
              <button key={x.kind} type="button" className="btn btn--ghost" title={x.blurb} disabled={busy !== null} onClick={() => make(x.kind)}>{busy === x.kind ? 'Making…' : x.title}</button>))}</div>
          </>
        )}
      </Loaded>
      <Field label="Anything specific? (optional)" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      {ready < 1 ? <p className="row__meta">Add a source and wait until it has been read to use the Studio.</p> : <p className="row__meta">Uses {ready} source(s). Each output is saved below.</p>}
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      {open ? <Output output={open} onCite={onCite} setMsg={setMsg} exportsFor={(k) => kinds.data?.items?.find((x) => x.kind === k)?.exports ?? ['markdown']} /> : null}
      <Loaded res={list} what="saved outputs">
        {(d) => d.items.length === 0 ? null : (
          <ul className="list">{d.items.map((o) => (
            <li key={o.id} className="row"><div className="row__main"><span className="row__title">{o.title}</span><span className="row__meta">{o.mode === 'model' ? 'AI-written' : 'Extract'} · {when(o.created_at)}</span></div>
              <div className="btn-row"><button type="button" className="btn btn--ghost" onClick={() => show(o.id)}>Open</button><button type="button" className="btn btn--ghost" onClick={() => remove(o.id)}>Delete</button></div></li>))}</ul>
        )}
      </Loaded>
    </section>
  );
}
