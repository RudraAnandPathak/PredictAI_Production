import { citeText, isEditable, isLocked, originLabel } from '../logic.js';

function Cites({ cite, onCite }) {
  if (!cite?.length) return null;
  return <span className="ri-cites">{cite.map((c) => <button key={c} type="button" className="link-button" aria-label={`Show source ${c}`} onClick={() => onCite(c)}>[{c}]</button>)}</span>;
}

function Origin({ block }) {
  return <span className={`ri-origin ri-origin--${block.origin}`}>{originLabel(block.origin)}{block.edited_from ? ` (edited; was ${block.edited_from})` : ''}</span>;
}

/** Read-only view of one block. */
export function BlockView({ block, onCite }) {
  if (block.type === 'quote') return <blockquote className="citation__quote">{block.text}<br /><Cites cite={block.cite} onCite={onCite} /> <Origin block={block} /></blockquote>;
  if (block.type === 'bullets') return <div><ul>{block.items.map((i, k) => <li key={k}>{i}</li>)}</ul><Cites cite={block.cite} onCite={onCite} /> <Origin block={block} /></div>;
  if (block.type === 'table') return (
    <div className="ri-scroll"><table className="ri-table"><thead><tr>{block.columns.map((c) => <th scope="col" key={c}>{c}</th>)}</tr></thead><tbody>{block.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table><Cites cite={block.cite} onCite={onCite} /> <Origin block={block} /></div>
  );
  if (!block.text && block.hint) return <p className="row__meta">Your turn — {block.hint}</p>;
  return <p className="note__body">{block.text} <Cites cite={block.cite} onCite={onCite} /> <Origin block={block} /></p>;
}

/** Editable view of one block. Quotes and tables are evidence: they can only be removed. */
export function BlockEdit({ block, labels, onText, onBullets, onCites, onRemove }) {
  return (
    <div className="ri-block-edit">
      {isEditable(block) ? (block.type === 'text' ? (
        <>
          <label className="field__label" htmlFor={`t-${block.id}`}>Text {block.hint && !block.text ? `— ${block.hint}` : ''}</label>
          <textarea id={`t-${block.id}`} className="field__input" rows={4} value={block.text} onChange={(e) => onText(e.target.value)} />
        </>
      ) : (
        <>
          <label className="field__label" htmlFor={`b-${block.id}`}>Bullets (one per line)</label>
          <textarea id={`b-${block.id}`} className="field__input" rows={4} defaultValue={block.items.join('\n')} onChange={(e) => onBullets(e.target.value)} />
        </>
      )) : <BlockView block={block} onCite={() => {}} />}
      {isEditable(block) ? (
        <fieldset className="ri-fieldset"><legend>Cited sources</legend>
          {labels.map((l) => <label key={l} className="ri-check"><input type="checkbox" checked={block.cite.includes(l)} onChange={() => onCites(block.cite.includes(l) ? block.cite.filter((x) => x !== l) : [...block.cite, l])} /> [{l}]</label>)}
        </fieldset>
      ) : null}
      {isLocked(block) ? <p className="row__meta">Quoted evidence and computed tables cannot be edited. {citeText(block.cite) ? `Cites ${citeText(block.cite)}.` : ''}</p> : null}
      <button type="button" className="link-button" onClick={onRemove}>Remove this block</button>
    </div>
  );
}
