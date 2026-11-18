import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api/index.js';
import { ApiError, isAbortError } from '../../api/errors.js';
import { fetchHealth } from '../../api/health.js';
import { config } from '../../app/config.js';

const HealthContext = createContext(null);

const INITIAL = { phase: 'checking', health: null, error: null, checkedAt: null, latencyMs: null };

/**
 * Owns the single connection check to GET /health: once on mount, on demand, when the tab
 * becomes visible again and on an interval (0 disables polling). Phases:
 * checking | connected | unreachable.
 */
export function HealthProvider({ children, client = api, pollIntervalMs = config.healthPollIntervalMs }) {
  const [state, setState] = useState(INITIAL);
  const inFlight = useRef(null);

  const refresh = useCallback(async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    // Keep showing "connected" during a quiet re-check to avoid flicker.
    setState((current) => (current.phase === 'connected' ? current : { ...current, phase: 'checking' }));
    const started = performance.now();
    try {
      const health = await fetchHealth(client, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setState({ phase: 'connected', health, error: null, checkedAt: new Date(), latencyMs: Math.round(performance.now() - started) });
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) return;
      const apiError = error instanceof ApiError
        ? error
        : new ApiError({ code: 'UNKNOWN', message: 'Something went wrong while checking the connection.' });
      setState({ phase: 'unreachable', health: null, error: apiError, checkedAt: new Date(), latencyMs: null });
    }
  }, [client]);

  useEffect(() => {
    refresh();
    const onVisible = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    const timer = pollIntervalMs > 0 ? setInterval(() => !document.hidden && refresh(), pollIntervalMs) : null;
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      if (timer) clearInterval(timer);
      inFlight.current?.abort();
    };
  }, [refresh, pollIntervalMs]);

  const value = useMemo(() => ({ ...state, pollIntervalMs, refresh }), [state, pollIntervalMs, refresh]);
  return <HealthContext.Provider value={value}>{children}</HealthContext.Provider>;
}

export function useHealth() {
  const context = useContext(HealthContext);
  if (!context) throw new Error('useHealth must be used inside <HealthProvider>.');
  return context;
}
