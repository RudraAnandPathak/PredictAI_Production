import { config } from '../app/config.js';
import { Icon } from '../components/ui/Icon.jsx';
import { ErrorState } from '../components/ui/ErrorState.jsx';
import { useHealth } from '../features/system/HealthContext.jsx';

function formatUptime(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h ? `${h}h` : null, h || m ? `${m}m` : null, `${s}s`].filter(Boolean).join(' ');
}

const formatTime = (value) => (value instanceof Date ? value.toLocaleTimeString() : '—');

export function SystemStatusPage() {
  const { phase, health, error, latencyMs, checkedAt, pollIntervalMs, refresh } = useHealth();

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Platform</p>
        <h1 className="page-title">System status</h1>
        <p className="lead">The live connection between this application and the PredictAI API.</p>
      </header>

      {phase === 'unreachable' && error ? (
        <ErrorState
          title="The API is not reachable"
          message={error.message}
          detail={[
            `Code: ${error.code}`,
            error.status ? `HTTP ${error.status}` : null,
            error.requestId ? `Request ID: ${error.requestId}` : null,
          ].filter(Boolean).join(' · ')}
          onRetry={refresh}
          retryLabel="Check again"
        />
      ) : null}
      {phase === 'unreachable' && config.isDevelopment ? (
        <p className="hint">Development hint: start the backend from the backend folder with <code>python -m app</code> (see the README).</p>
      ) : null}

      <section className="card" aria-labelledby="details-title">
        <div className="card__row">
          <h2 id="details-title" className="card__title">Health endpoint</h2>
          <button type="button" className="btn btn--ghost" onClick={refresh} disabled={phase === 'checking'}>
            <Icon name="refresh" size={16} /> Refresh
          </button>
        </div>
        <dl className="kv">
          <dt>Connection</dt>
          <dd>{phase === 'connected' ? 'Connected' : phase === 'checking' ? 'Checking…' : 'Unreachable'}</dd>
          <dt>Service</dt>
          <dd>{health?.service ?? '—'}</dd>
          <dt>Version</dt>
          <dd>{health?.version ?? '—'}</dd>
          <dt>Environment</dt>
          <dd>{health?.environment ?? '—'}</dd>
          <dt>Server time (UTC)</dt>
          <dd>{health?.timestamp ?? '—'}</dd>
          <dt>Uptime</dt>
          <dd>{health ? formatUptime(health.uptimeSeconds) : '—'}</dd>
          <dt>Round trip</dt>
          <dd>{latencyMs != null ? `${latencyMs} ms` : '—'}</dd>
          <dt>Request ID</dt>
          <dd>{health?.requestId ?? error?.requestId ?? '—'}</dd>
          <dt>Last checked</dt>
          <dd>{formatTime(checkedAt)}</dd>
          <dt>Auto-refresh</dt>
          <dd>{pollIntervalMs > 0 ? `every ${Math.round(pollIntervalMs / 1000)} s` : 'off'}</dd>
        </dl>
      </section>
    </article>
  );
}
