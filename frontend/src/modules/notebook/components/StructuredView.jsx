import { useEffect, useRef, useState } from 'react';
import { Badge } from '../../../pages/phase7/shared.jsx';
import { LETTERS, citeTarget, mindMapRows, nextCard, pickVoices, quizScore, speechLines } from '../logic.js';

/** Source marks for one item; each opens the stored passage. */
function Marks({ sources, output, onCite }) {
  return (sources ?? []).map((s) => {
    const t = citeTarget(output, s);
    return t ? <button key={s} type="button" className="btn btn--ghost nb-cite" aria-label={`Open the passage for source mark ${s}`} onClick={() => onCite(t)}>[{s}]</button> : null;
  });
}

function Flashcards({ data, output, onCite }) {
  const [i, setI] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const cards = data.cards ?? [];
  if (!cards.length) return null;
  const c = cards[i];
  const go = (step) => { setI(nextCard(i, step, cards.length)); setFlipped(false); };
  return (
    <div>
      <p className="row__meta">Card {i + 1} of {cards.length}</p>
      <button type="button" className="card nb-flashcard" aria-live="polite" onClick={() => setFlipped(!flipped)}>
        {flipped ? <span>{c.back}</span> : <strong>{c.front}</strong>}
        <span className="row__meta"> ({flipped ? 'answer' : 'tap to turn'})</span>
      </button>
      {flipped ? <p><Marks sources={c.sources} output={output} onCite={onCite} /></p> : null}
      <div className="btn-row"><button type="button" className="btn btn--ghost" onClick={() => go(-1)}>Previous</button><button type="button" className="btn" onClick={() => go(1)}>Next</button></div>
    </div>
  );
}

function Quiz({ data, output, onCite }) {
  const [answers, setAnswers] = useState({});
  const [done, setDone] = useState(false);
  const qs = data.questions ?? [];
  const score = quizScore(qs, answers);
  return (
    <div>
      <ol>{qs.map((q, qi) => (
        <li key={qi}><p><strong>{q.question}</strong></p>
          <fieldset><legend className="sr-only">Answers for question {qi + 1}</legend>
            {q.options.map((o, oi) => {
              const mark = done ? (oi === q.answer ? ' ✓' : answers[qi] === oi ? ' ✗' : '') : '';
              return <label key={oi} className="row__check"><input type="radio" name={`q-${output.id}-${qi}`} disabled={done} checked={answers[qi] === oi} onChange={() => setAnswers({ ...answers, [qi]: oi })} /> {LETTERS[oi]}. {o}{mark}</label>;
            })}
          </fieldset>
          {done ? <p className="row__meta">{q.explanation} <Marks sources={q.sources} output={output} onCite={onCite} /></p> : null}</li>))}</ol>
      {done ? <p role="status"><strong>{score.right} of {score.total} right ({score.percent}%).</strong></p> : null}
      <div className="btn-row">{done ? <button type="button" className="btn btn--ghost" onClick={() => { setAnswers({}); setDone(false); }}>Try again</button>
        : <button type="button" className="btn" onClick={() => setDone(true)}>Check answers ({score.answered} of {score.total} answered)</button>}</div>
    </div>
  );
}

function MindMap({ data, output, onCite }) {
  const [folded, setFolded] = useState(new Set());
  const toggle = (p) => { const n = new Set(folded); if (n.has(p)) n.delete(p); else n.add(p); setFolded(n); };
  return (
    <div className="nb-mindmap" role="tree" aria-label={data.root}>
      <p><strong>{data.root}</strong></p>
      {mindMapRows(data, folded).map((r) => (
        <p key={r.path} role="treeitem" aria-level={r.depth} aria-expanded={r.hasChildren ? !r.folded : undefined} style={{ marginInlineStart: `${r.depth * 1.25}rem` }}>
          {r.hasChildren ? <button type="button" className="btn btn--ghost" aria-label={`${r.folded ? 'Open' : 'Fold'} ${r.label}`} onClick={() => toggle(r.path)}>{r.folded ? '+' : '−'}</button> : '• '}
          {r.label} <Marks sources={r.sources} output={output} onCite={onCite} />
        </p>))}
    </div>
  );
}

function Table({ data, output, onCite }) {
  return (
    <div className="table-wrap">
      {data.title ? <p><strong>{data.title}</strong></p> : null}
      <table className="table"><thead><tr>{data.columns.map((c) => <th key={c} scope="col">{c}</th>)}<th scope="col">Sources</th></tr></thead>
        <tbody>{data.rows.map((r, i) => <tr key={i}>{r.cells.map((c, j) => <td key={j}>{c || '—'}</td>)}<td><Marks sources={r.sources} output={output} onCite={onCite} /></td></tr>)}</tbody></table>
    </div>
  );
}

function Slides({ data, output, onCite }) {
  const [i, setI] = useState(0);
  const s = data.slides?.[i];
  if (!s) return null;
  return (
    <div>
      <div className="card nb-slide" aria-live="polite">
        <p className="row__meta">{data.title} · slide {i + 1} of {data.slides.length}</p>
        <h4>{s.title}</h4>
        <ul>{s.bullets.map((b, k) => <li key={k}>{b}</li>)}</ul>
        {s.notes ? <p className="row__meta">Notes: {s.notes}</p> : null}
        <p><Marks sources={s.sources} output={output} onCite={onCite} /></p>
      </div>
      <div className="btn-row"><button type="button" className="btn btn--ghost" disabled={i === 0} onClick={() => setI(i - 1)}>Previous</button>
        <button type="button" className="btn" disabled={i === data.slides.length - 1} onClick={() => setI(i + 1)}>Next</button></div>
    </div>
  );
}

function Infographic({ data, output, onCite }) {
  return (
    <div className="nb-infographic">
      <h4>{data.title}</h4>
      {data.subtitle ? <p className="lead">{data.subtitle}</p> : null}
      {data.stats.length ? <div className="grid grid--2">{data.stats.map((s, i) => (
        <div key={i} className="card"><p className="nb-stat">{s.value}</p><p>{s.label} <Marks sources={s.sources} output={output} onCite={onCite} /></p></div>))}</div> : null}
      <ul>{data.points.map((p, i) => <li key={i}>{p.text} <Marks sources={p.sources} output={output} onCite={onCite} /></li>)}</ul>
    </div>
  );
}

/** Reads the script aloud with the browser's own speech voices (free, on this device; nothing is uploaded). */
function Audio({ data, output, onCite }) {
  const synth = typeof window !== 'undefined' ? window.speechSynthesis : undefined;
  const [playing, setPlaying] = useState(false);
  const [line, setLine] = useState(-1);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; synth?.cancel(); }, [synth]);
  const lines = speechLines(data);
  function play(from = 0) {
    if (!synth) return;
    synth.cancel();
    stop.current = false;
    const v = pickVoices(synth.getVoices());
    setPlaying(true);
    const say = (i) => {
      if (stop.current || i >= lines.length) { setPlaying(false); setLine(-1); return; }
      const u = new SpeechSynthesisUtterance(lines[i].text);
      const voice = v[lines[i].speaker];
      if (voice) u.voice = voice;
      if (v.samePitchShift && lines[i].speaker === 'B') u.pitch = 0.8;
      u.onend = () => say(i + 1);
      u.onerror = () => { setPlaying(false); setLine(-1); };
      setLine(i);
      synth.speak(u);
    };
    say(from);
  }
  function halt() { stop.current = true; synth?.cancel(); setPlaying(false); setLine(-1); }
  return (
    <div>
      {synth ? <div className="btn-row">{playing ? <button type="button" className="btn" onClick={halt}>Stop</button> : <button type="button" className="btn" onClick={() => play(0)}>Play</button>}</div>
        : <p className="row__meta">This browser cannot read text aloud. You can still read the script.</p>}
      <p className="row__meta">Voices come from your device, so they sound different on each browser. No audio file is made.</p>
      <ol>{(data.lines ?? []).map((l, i) => (
        <li key={i} className={i === line ? 'nb-speaking' : undefined}><Badge tone={l.speaker === 'A' ? 'accent' : 'ok'}>Host {l.speaker}</Badge> {l.text} <Marks sources={l.sources} output={output} onCite={onCite} /></li>))}</ol>
    </div>
  );
}

const VIEWS = { flashcards: Flashcards, quiz: Quiz, mind_map: MindMap, data_table: Table, slides: Slides, infographic: Infographic, audio_script: Audio };

export function hasStructuredView(kind) { return Boolean(VIEWS[kind]); }

export function StructuredView({ output, onCite }) {
  const View = VIEWS[output.kind];
  const data = output.meta?.data;
  return View && data ? <View data={data} output={output} onCite={onCite} /> : null;
}
