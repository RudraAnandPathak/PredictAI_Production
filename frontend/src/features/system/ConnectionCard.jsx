import { Icon } from '../../components/ui/Icon.jsx';
import { Link } from '../../app/router/Link.jsx';
import { useHealth } from './HealthContext.jsx';

const COPY = {
  checking: ['Checking the connection…', 'Contacting the PredictAI API.'],
  connected: ['Connected to the API', 'The frontend reached the backend health endpoint.'],
  unreachable: ['Cannot reach the API', 'The shell is running, but the backend did not answer.'],
};

export function ConnectionCard() {
  const { phase, health, error, refresh } = useHealth();
  const [title, text] = COPY[phase];
  return (
    <section className={`card connection connection--${phase}`} aria-labelledby="connection-title">
      <p className="eyebrow">API connection</p>
      <h2 id="connection-title" className="card__title">{title}</h2>
      <p className="muted">{text}</p>
      {phase === 'connected' && health ? (
        <p className="connection__facts">
          <span>{health.service}</span>
          <span>v{health.version}</span>
          <span>{health.environment}</span>
        </p>
      ) : null}
      {phase === 'unreachable' && error ? <p className="state__detail">{error.message}</p> : null}
      <div className="card__actions">
        {phase === 'unreachable' ? (
          <button type="button" className="btn" onClick={refresh}>
            <Icon name="refresh" size={16} /> Retry
          </button>
        ) : null}
        <Link to="/system/status" className="btn btn--ghost">
          System status <Icon name="arrow" size={16} />
        </Link>
      </div>
    </section>
  );
}
