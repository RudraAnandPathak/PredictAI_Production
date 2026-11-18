import { useHealth } from './HealthContext.jsx';

const LABEL = { checking: 'Checking API…', connected: 'API connected', unreachable: 'API unreachable' };

export function ConnectionStatus() {
  const { phase, error, latencyMs } = useHealth();
  return (
    <div className={`conn conn--${phase}`} role="status" title={phase === 'unreachable' ? error?.message : undefined}>
      <span className="conn__dot" aria-hidden="true" />
      <span className="conn__label">{LABEL[phase]}</span>
      {phase === 'connected' && latencyMs != null ? <span className="conn__meta">{latencyMs} ms</span> : null}
    </div>
  );
}
