import { useResource } from '../../features/identity/useResource.js';
import { workspace } from '../../api/index.js';

/** The person's active projects (the same list every other module uses; only their own are returned). */
export function useProjects() {
  const r = useResource(() => workspace.projects.list({ status: 'active', limit: 100 }), []);
  return { ...r, items: r.data?.items ?? [] };
}
