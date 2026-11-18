import { ROADMAP_PHASES } from '../app/navigation.js';
import { Link } from '../app/router/Link.jsx';
import { StatusBadge } from '../components/ui/StatusBadge.jsx';

const AUDIENCE_NOTE = {
  special: 'Available to accounts with Special access once this module is built.',
  owner: 'Intended for the platform owner only, behind step-up protection.',
};

export function ModulePlaceholderPage({ item }) {
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Planned module</p>
        <h1 className="page-title">{item.label}</h1>
        <p className="lead">{item.summary}</p>
      </header>
      <section className="card placeholder" aria-label={`${item.label} status`}>
        <StatusBadge status={item.status} />
        <p>
          <strong>Not built yet.</strong> Planned for Phase {item.phase}: {ROADMAP_PHASES[item.phase]}. Only the shell and accounts
          exist so far, so there is nothing to use here.
        </p>
        {AUDIENCE_NOTE[item.audience] ? <p className="muted">{AUDIENCE_NOTE[item.audience]}</p> : null}
        <Link to="/" className="btn btn--ghost">Back to overview</Link>
      </section>
    </article>
  );
}
