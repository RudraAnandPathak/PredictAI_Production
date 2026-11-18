import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Notice, attempt, when } from '../../../pages/phase7/shared.jsx';
import { GENERATORS, addOutlineItem, generatorLabel, moveOutlineItem, removeOutlineItem } from '../logic.js';

function OutlineEditor({ wid, outline, onDone }) {
  const [title, setTitle] = useState(outline.title);
  const [items, setItems] = useState(outline.items ?? []);
  const [text, setText] = useState('');
  const [parent, setParent] = useState('');
  const [msg, setMsg] = useState(null);
  const add = () => {
    const r = addOutlineItem(items, text, parent === '' ? null : Number(parent));
    if (r.error) setMsg({ kind: 'error', text: r.error }); else { setItems(r.items); setText(''); setMsg(null); }
  };
  return (
    <div className="card" role="group" aria-label={`Outline ${outline.title}`}>
      <Field label="Outline title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      <ol className="ri-outline">{items.map((n, i) => (
        <li key={n.id ?? i}>{n.heading}
          <span className="btn-row">
            <button type="button" className="link-button" onClick={() => setItems(moveOutlineItem(items, i, -1))} aria-label={`Move ${n.heading} up`}>Up</button>
            <button type="button" className="link-button" onClick={() => setItems(moveOutlineItem(items, i, 1))} aria-label={`Move ${n.heading} down`}>Down</button>
            <button type="button" className="link-button" onClick={() => setItems(removeOutlineItem(items, i))} aria-label={`Remove ${n.heading}`}>Remove</button>
          </span>
          {n.children?.length ? <ul>{n.children.map((c, j) => <li key={c.id ?? j}>{c.heading}</li>)}</ul> : null}
        </li>
      ))}</ol>
      <Field label="New heading" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
      <Field as="select" label="Place under" value={parent} onChange={(e) => setParent(e.target.value)}><option value="">Top level</option>{items.map((n, i) => <option key={i} value={i}>{n.heading}</option>)}</Field>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="btn-row">
        <button type="button" className="btn btn--ghost" onClick={add}>Add heading</button>
        <button type="button" className="btn" onClick={async () => { const o = await attempt(() => ri.studio.updateOutline(wid, outline.id, { title, items }), setMsg, 'Outline saved.'); if (o) onDone(); }}>Save outline</button>
        <button type="button" className="btn btn--ghost" onClick={onDone}>Close</button>
      </div>
    </div>
  );
}

/** Outlines, and research questions / hypotheses / a literature-review draft generated from a finished analysis. */
export function StudioTools({ wid, analysisId, selected }) {
  const outlines = useResource(() => ri.studio.outlines(wid), [wid]);
  const gen = useResource(() => ri.studio.generated(wid), [wid]);
  const [open, setOpen] = useState(null);
  const [shown, setShown] = useState(null);
  const [title, setTitle] = useState('');
  const [msg, setMsg] = useState(null);
  const createOutline = async (e) => {
    e.preventDefault();
    const o = await attempt(() => ri.studio.createOutline(wid, title.trim(), []), setMsg);
    if (o) { setTitle(''); outlines.reload({ quiet: true }); setOpen(o); }
  };
  const run = async (kind) => {
    const o = await attempt(() => ri.studio.generate(wid, kind, analysisId, selected.length ? selected : null), setMsg);
    if (o) { gen.reload({ quiet: true }); setShown(o); }
  };
  return (
    <section className="card" aria-label="Outlines and generated ideas">
      <h2 className="card__title">3. Outlines and ideas</h2>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <h3 className="card__title">Generate from the selected analysis</h3>
      {!analysisId ? <p className="row__meta">Choose a finished analysis above first.</p> : null}
      <ul className="list">{GENERATORS.map(([k, label, hint]) => <li key={k} className="row"><div className="row__main"><span className="row__title">{label}</span><span className="row__meta">{hint}</span><button type="button" className="btn btn--ghost" disabled={!analysisId} onClick={() => run(k)}>Generate</button></div></li>)}</ul>
      {shown ? <Generated item={shown} /> : null}
      <h3 className="card__title">Saved outputs</h3>
      <ul className="list">{(gen.data?.items ?? []).map((g) => <li key={g.id} className="row"><div className="row__main"><span className="row__title"><button type="button" className="link-button" onClick={async () => { const o = await attempt(() => ri.studio.generatedItem(wid, g.id), setMsg); if (o) setShown(o); }}>{g.title}</button></span><span className="row__meta">{generatorLabel(g.kind)} · {when(g.created_at)}</span><button type="button" className="link-button" onClick={() => attempt(() => ri.studio.removeGenerated(wid, g.id), setMsg).then((o) => { if (o) { gen.reload({ quiet: true }); setShown(null); } })}>Delete</button></div></li>)}</ul>
      <h3 className="card__title">Outlines</h3>
      <form onSubmit={createOutline}><Field label="New outline title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /><button className="btn btn--ghost" disabled={!title.trim()}>Create outline</button></form>
      <ul className="list">{(outlines.data?.items ?? []).map((o) => <li key={o.id} className="row"><div className="row__main"><span className="row__title"><button type="button" className="link-button" onClick={async () => { const d = await attempt(() => ri.studio.outline(wid, o.id), setMsg); if (d) setOpen(d); }}>{o.title}</button></span><span className="row__meta">{o.entries} heading(s) · {when(o.updated_at)}</span><button type="button" className="link-button" onClick={() => attempt(() => ri.studio.removeOutline(wid, o.id), setMsg).then((x) => { if (x) { outlines.reload({ quiet: true }); if (open?.id === o.id) setOpen(null); } })}>Delete</button></div></li>)}</ul>
      {open ? <OutlineEditor key={open.id} wid={wid} outline={open} onDone={() => { setOpen(null); outlines.reload({ quiet: true }); }} /> : null}
    </section>
  );
}

function Generated({ item }) {
  const c = item.content ?? {};
  return (
    <div className="card" role="region" aria-label={item.title}>
      <h4>{item.title}</h4>
      <p className="row__meta">{item.label ?? c.note}</p>
      {c.note && item.label !== c.note ? <p className="row__meta">{c.note}</p> : null}
      {Array.isArray(c.items) ? <ul className="list">{c.items.map((x) => (
        <li key={x.id} className="row"><div className="row__main">
          <span className="note__body">{x.text ?? x.statement}</span>
          {x.how_to_test ? <span className="row__meta">How it could be tested: {x.how_to_test}</span> : null}
          {x.status ? <span className="row__meta">Status: {x.status}</span> : null}
          {(x.basis ?? []).map((b, i) => <blockquote key={i} className="citation__quote">{b.text}<span className="row__meta"> [{b.label}]</span></blockquote>)}
        </div></li>
      ))}</ul> : null}
      {c.sections ? <p className="row__meta">This draft has {c.sections.length} sections. Create a “Literature review” report from the analysis to edit and export it.</p> : null}
    </div>
  );
}
