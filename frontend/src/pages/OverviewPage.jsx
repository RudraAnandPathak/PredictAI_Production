import { NAV_ITEMS, ROADMAP_PHASES } from '../app/navigation.js';
import { Link } from '../app/router/Link.jsx';
import { Icon } from '../components/ui/Icon.jsx';
import { StatusBadge } from '../components/ui/StatusBadge.jsx';
import { ConnectionCard } from '../features/system/ConnectionCard.jsx';

const FOUNDATION = [
  'Responsive application shell with accessible navigation',
  'Versioned API with a standard response envelope and safe errors',
  'Validated configuration for local, staging and production',
  'Request IDs and structured logging end to end',
  'Accounts: sign-in, sessions, password recovery, roles and individual special-access codes',
  'Owner controls with an audit log, protected by the server',
];

export function OverviewPage() {
  const planned = NAV_ITEMS.filter((item) => item.status !== 'existing');
  return (
    <>
      <section className="hero">
        <p className="eyebrow">PredictAI · Phase 2 · Identity &amp; security</p>
        <h1 className="display">Intelligence you can explain.</h1>
        <p className="lead">
          Analytics, prediction, document intelligence and research in one privacy-aware, provider-independent
          platform. This is the secure application shell. The modules below arrive phase by phase.
        </p>
      </section>

      <div className="grid grid--2">
        <ConnectionCard />
        <section className="card" aria-labelledby="foundation-title">
          <p className="eyebrow">Delivered so far</p>
          <h2 id="foundation-title" className="card__title">What exists today</h2>
          <ul className="checklist">
            {FOUNDATION.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      </div>

      <section aria-labelledby="modules-title" className="section">
        <h2 id="modules-title" className="section-title">Module map</h2>
        <p className="muted section__intro">
          Nothing below is built yet. Status labels and phase numbers follow the engineering roadmap.
        </p>
        <ul className="module-grid">
          {planned.map((item) => (
            <li key={item.id}>
              <Link to={item.path} className="module-card">
                <span className="module-card__icon"><Icon name={item.icon} size={20} /></span>
                <h3 className="module-card__title">{item.label}</h3>
                <p>{item.summary}</p>
                <span className="module-card__meta">
                  <StatusBadge status={item.status} />
                  <span>Phase {item.phase} · {ROADMAP_PHASES[item.phase]}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
