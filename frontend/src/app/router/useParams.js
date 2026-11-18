import { matchRoute } from './matchPath.js';
import { useRouter } from './RouterProvider.jsx';

/** Parameters of the current URL for a pattern such as '/projects/:projectId' ({} when it does not match). */
export function useParams(pattern) {
  const { pathname } = useRouter();
  return matchRoute([{ path: pattern }], pathname)?.params ?? {};
}
