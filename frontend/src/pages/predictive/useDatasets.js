import { useEffect, useState } from 'react';
import { workspace } from '../../api/index.js';

/** Every dataset the person owns across their active projects, grouped by project. Authorisation is the same as everywhere else: only their own. */
export function useDatasets() {
  const [state, setState] = useState({ loading: true, error: null, groups: [] });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const projects = (await workspace.projects.list({ status: 'active', limit: 100 })).items ?? [];
        const groups = [];
        for (const p of projects) {
          if (!p.datasets) continue;
          const assets = (await workspace.assets.list(p.id, { kind: 'dataset', limit: 100 })).items ?? [];
          if (assets.length) groups.push({ project: p, assets });
        }
        if (live) setState({ loading: false, error: null, groups });
      } catch (error) { if (live) setState({ loading: false, error, groups: [] }); }
    })();
    return () => { live = false; };
  }, [tick]);
  const reload = () => { setState((s) => ({ ...s, loading: true, error: null })); setTick((n) => n + 1); };
  return { ...state, reload };
}
