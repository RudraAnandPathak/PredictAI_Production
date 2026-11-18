import { explanationView } from '../../features/explanation/explanation.js';

/**
 * The application's universal "Explanation" section. Every result that can be explained shows this, titled exactly "Explanation".
 * It only renders text the server produced (each number in it is checked against the result on the server). When the server has no reliable
 * explanation, or sent none, it says so and why; it never fills the gap with wording of its own.
 */
export function Explanation({ value, level = 4 }) {
  const v = explanationView(value);
  const Heading = `h${Math.min(6, Math.max(2, level))}`;
  return (
    <section className={`explanation${v.available ? '' : ' explanation--unavailable'}`} aria-label={v.title} data-explanation={v.available ? 'available' : 'unavailable'}>
      <Heading className="explanation__title">{v.title}</Heading>
      {v.available ? (
        <>
          <p className="explanation__summary">{v.summary}</p>
          {v.points.length ? <ul className="explanation__points">{v.points.map((p, i) => <li key={i}>{p}</li>)}</ul> : null}
          {v.cautions.length ? (
            <ul className="explanation__cautions" aria-label="Cautions">{v.cautions.map((c, i) => <li key={i}>{c}</li>)}</ul>
          ) : null}
          {v.terms.length ? (
            <details className="explanation__terms">
              <summary>Terms used</summary>
              <dl className="explanation__defs">{v.terms.map((t) => <div key={t.term} className="explanation__term"><dt>{t.term}</dt><dd>{t.meaning}</dd></div>)}</dl>
            </details>
          ) : null}
        </>
      ) : (
        <p className="explanation__none"><strong>Unavailable.</strong> {v.reason}</p>
      )}
    </section>
  );
}
