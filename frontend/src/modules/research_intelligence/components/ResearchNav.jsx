import { Link } from '../../../app/router/Link.jsx';
import { RESEARCH_NAV } from '../logic.js';

/** The eight research destinations, shown at the top of every research page. */
export function ResearchNav({ current }) {
  return (
    <nav className="ri-nav" aria-label="Research sections">
      <ul className="ri-nav__list">
        {RESEARCH_NAV.map((n) => (
          <li key={n.key}><Link to={n.to} className={`ri-nav__link${n.key === current ? ' is-current' : ''}`} aria-current={n.key === current ? 'page' : undefined}>{n.label}</Link></li>
        ))}
      </ul>
    </nav>
  );
}

/** Standard page frame: navigation, header and content. */
export function ResearchFrame({ current, eyebrow = 'Research', title, lead, back, children }) {
  return (
    <article className="page ri-page">
      <ResearchNav current={current} />
      <header className="page__header">
        <p className="eyebrow">{eyebrow}</p>
        <h1 className="page-title">{title}</h1>
        {lead ? <p className="lead">{lead}</p> : null}
        {back ? <p><Link to={back.to}>{back.label}</Link></p> : null}
      </header>
      {children}
    </article>
  );
}
