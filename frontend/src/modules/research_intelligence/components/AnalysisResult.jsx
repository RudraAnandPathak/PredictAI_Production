import { gradeLabel } from '../logic.js';

const KIND = { agreement: 'Several sources agree', possible_contradiction: 'Possible contradiction (check the quotes)' };

/** A finished analysis: counts computed from the sources, themes with their quotes, evidence grades. Nothing here is a verdict. */
export function AnalysisResult({ result }) {
  if (!result) return null;
  const sy = result.synthesis ?? {};
  const grades = result.evidence?.sources ?? [];
  return (
    <div className="ri-result">
      {sy.overview?.statement ? <p className="note__body">{sy.overview.statement}</p> : null}
      {result.generated?.mode ? <p className="row__meta">{result.generated.mode === 'model' ? 'A model wrote a narrative from the quotes below; each sentence was checked against them.' : 'No AI model wrote any of this. Quotes and counts come straight from your sources.'}</p> : null}
      <h4>Themes</h4>
      {(sy.themes ?? []).length === 0 ? <p className="row__meta">No shared themes were found across these sources.</p> : (
        <ul className="list">{sy.themes.map((t, i) => (
          <li key={i} className="row"><div className="row__main">
            <span className="row__title">{KIND[t.kind] ?? t.kind} — sources {t.sources.map((l) => `[${l}]`).join(', ')}</span>
            {t.confidence ? <span className="row__meta">Agreement level: {t.confidence}</span> : null}
            {t.quotes.map((q, j) => <blockquote key={j} className="citation__quote">{q.text}<span className="row__meta"> [{q.label}]{q.locator ? ` · ${q.locator}` : ''}</span></blockquote>)}
          </div></li>
        ))}</ul>
      )}
      <h4>Evidence reading</h4>
      {grades.length === 0 ? <p className="row__meta">No design information was found in the sources.</p> : (
        <ul className="list">{grades.map((g) => <li key={g.label} className="row__meta">[{g.label}] {gradeLabel(g.grade)}{g.design ? ` · ${g.design}` : ''}{g.sample_size ? ` · n = ${g.sample_size}` : ''}</li>)}</ul>
      )}
      {result.evidence?.note ? <p className="row__meta">{result.evidence.note}</p> : null}
      {(result.gaps?.stated_by_authors ?? []).length ? <><h4>Gaps the authors state</h4><ul className="list">{result.gaps.stated_by_authors.slice(0, 8).map((q, i) => <li key={i} className="row__meta">“{q.text}” [{q.label}]</li>)}</ul></> : null}
    </div>
  );
}
