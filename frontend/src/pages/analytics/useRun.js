import { useCallback, useRef, useState } from 'react';
import { describeError } from '../../features/identity/validation.js';

/** Runs one analysis call at a time; keeps the latest result, and ignores answers that arrive after a newer request. */
export function useRun() {
  const [state, setState] = useState({ busy: false, run: null, error: '' });
  const ticket = useRef(0);
  const start = useCallback(async (call) => {
    const t = ++ticket.current;
    setState((s) => ({ ...s, busy: true, error: '' }));
    try {
      const run = await call();
      if (t === ticket.current) setState({ busy: false, run, error: '' });
      return run;
    } catch (error) {
      if (t === ticket.current) setState((s) => ({ ...s, busy: false, error: describeError(error) }));
      return null;
    }
  }, []);
  const replace = useCallback((run) => setState((s) => ({ ...s, run })), []);
  return { ...state, start, replace };
}
