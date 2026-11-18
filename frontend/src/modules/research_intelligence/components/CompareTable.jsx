/** Side-by-side comparison of saved papers: bibliographic data and distinguishing words only. */
export function CompareTable({ data }) {
  const items = data.items ?? [];
  const row = (label, fn) => (<tr key={label}><th scope="row">{label}</th>{items.map((i) => <td key={i.library_id}>{fn(i)}</td>)}</tr>);
  return (
    <section className="card" aria-label="Comparison">
      <h2 className="card__title">Comparison</h2>
      <div className="ri-scroll">
        <table className="ri-table">
          <caption className="sr-only">Saved papers compared</caption>
          <thead><tr><th scope="col">&nbsp;</th>{items.map((i) => <th scope="col" key={i.library_id}>{i.title}</th>)}</tr></thead>
          <tbody>
            {row('Authors', (i) => (i.authors?.length ? i.authors.join(', ') : 'Not reported'))}
            {row('Year', (i) => i.year ?? 'Not reported')}
            {row('Venue', (i) => i.venue ?? 'Not reported')}
            {row('Type', (i) => i.type ?? '—')}
            {row('Citations', (i) => (i.citation_count == null ? 'Not reported' : `${i.citation_count} (${i.citation_source ?? 'provider'})`))}
            {row('Abstract', (i) => (i.has_abstract ? 'Yes' : 'No'))}
            {row('Words only this paper uses', (i) => (i.distinct_terms?.length ? i.distinct_terms.join(', ') : '—'))}
          </tbody>
        </table>
      </div>
      {data.shared_terms?.length ? <p className="row__meta">Words in all of them: {data.shared_terms.join(', ')}</p> : null}
      {data.year_span ? <p className="row__meta">Years: {data.year_span[0]}–{data.year_span[1]}</p> : null}
      {data.note ? <p className="row__meta">{data.note}</p> : null}
    </section>
  );
}
