import { useCallback, useEffect, useRef, useState } from 'react';
import { research } from '../../api/index.js';
import { pollJob } from '../../features/research/jobs.js';
import { describeError } from '../../features/identity/validation.js';

/**
 * Follows one background job (a research run, a news read or an influence analysis) until it ends. Only the latest followed job updates the screen and
 * leaving the page stops the checks; the job itself keeps running on the server.
 */
export function useFollowJob({ onFinished } = {}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const token = useRef(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  const follow = useCallback(async (jobId, initial = null) => {
    const mine = ++token.current;
    setError('');
    if (initial) setJob(initial);
    try {
      const last = await pollJob(jobId, {
        fetchJob: (id) => research.job(id),
        onUpdate: (next) => { if (mine === token.current) setJob(next); },
        isStopped: () => mine !== token.current,
      });
      if (last && mine === token.current) finished.current?.(last);
      return last;
    } catch (e) {
      if (mine === token.current) setError(describeError(e));
      return null;
    }
  }, []);

  const clear = useCallback(() => { token.current += 1; setJob(null); setError(''); }, []);
  useEffect(() => () => { token.current += 1; }, []);
  return { job, error, follow, clear, setJob };
}
