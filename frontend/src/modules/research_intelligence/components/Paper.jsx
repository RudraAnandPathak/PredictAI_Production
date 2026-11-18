import { accessLabel, authorLine, citationCountLabel } from '../logic.js';

/** One search result or library entry. Only what the provider reported is shown; nothing is filled in. */
export function Paper({ rec, selected, onSelect, actions, showAbstract = true }) {
  const providers = Object.keys(rec.providers ?? {});
  return (
    <li className="row ri-paper">
      <div className="row__main">
        {onSelect ? (
          <label className="ri-paper__pick"><input type="checkbox" checked={Boolean(selected)} onChange={() => onSelect(rec)} /> <span className="sr-only">Select for comparison: </span></label>
        ) : null}
        <span className="row__title">{rec.url ? <a href={rec.url} target="_blank" rel="noopener noreferrer">{rec.title}</a> : rec.title}</span>
        <span className="row__meta">{authorLine(rec)}{rec.year ? ` · ${rec.year}` : ''}{rec.venue ? ` · ${rec.venue}` : ''}{rec.doi ? ` · DOI ${rec.doi}` : ''}</span>
        <span className="row__meta">{citationCountLabel(rec)} · {accessLabel(rec)}{providers.length ? ` · found by ${providers.join(', ')}` : ''}</span>
        {rec.why?.length ? <span className="row__meta">Why it matched: {rec.why.join('; ')}</span> : null}
        {showAbstract && rec.abstract ? <details><summary>Abstract</summary><p className="note__body">{rec.abstract}</p></details> : null}
        {showAbstract && !rec.abstract ? <span className="row__meta">No abstract reported.</span> : null}
        {rec.citation?.apa ? <details><summary>Citation text</summary><p className="note__body">{rec.citation.apa}</p></details> : null}
        {actions ? <div className="btn-row">{actions}</div> : null}
      </div>
    </li>
  );
}
