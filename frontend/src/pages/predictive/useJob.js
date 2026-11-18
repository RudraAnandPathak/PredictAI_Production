import { useCallback, useEffect, useRef, useState } from 'react';
import { predictive } from '../../api/index.js';
import { pollJob } from '../../features/predictive/jobs.js';
import { describeError } from '../../features/identity/validation.js';

/** Follows one training job until it finishes. Only the latest tracked job updates the screen; leaving the page stops the checks. */
export function useJobTracker() {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const token = useRef(0);

  const track = useCallback(async (jobId, initial = null) => {
    const mine = ++token.current;
    setError('');
    if (initial) setJob(initial);
    try {
      return await pollJob(jobId, {
        fetchJob: (id) => predictive.job(id),
        onUpdate: (next) => { if (mine === token.current) setJob(next); },
        isStopped: () => mine !== token.current,
      });
    } catch (e) {
      if (mine === token.current) setError(describeError(e));
      return null;
    }
  }, []);

  const clear = useCallback(() => { token.current += 1; setJob(null); setError(''); }, []);
  useEffect(() => () => { token.current += 1; }, []);
  return { job, error, track, clear, setJob };
}
