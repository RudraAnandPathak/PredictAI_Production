import { useCallback, useEffect, useRef, useState } from 'react';
import { isAbortError } from '../../api/errors.js';

/** Load data from a function (ignored if the component unmounts or the inputs change mid-flight). */
export function useResource(load, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const counter = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);

  const reload = useCallback(async ({ quiet = false } = {}) => {
    const ticket = ++counter.current;
    if (!quiet) setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await run();
      if (ticket === counter.current) setState({ loading: false, data, error: null });
    } catch (error) {
      if (ticket === counter.current && !isAbortError(error)) setState((s) => ({ loading: false, data: s.data, error }));
    }
  }, [run]);

  useEffect(() => {
    reload();
    return () => { counter.current += 1; };
  }, [reload]);

  return { ...state, reload };
}
