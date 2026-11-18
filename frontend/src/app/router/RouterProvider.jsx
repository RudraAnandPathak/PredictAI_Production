import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from 'react';

// A deliberately small History-API router. Phase 1 only needs flat routes; the route
// table in app/routes.jsx is plain data, so adopting a full router library later only
// touches this folder.

const NAVIGATE_EVENT = 'predictai:navigate';
const RouterContext = createContext(null);

function subscribe(callback) {
  window.addEventListener('popstate', callback);
  window.addEventListener(NAVIGATE_EVENT, callback);
  return () => {
    window.removeEventListener('popstate', callback);
    window.removeEventListener(NAVIGATE_EVENT, callback);
  };
}

const getPathname = () => window.location.pathname;

export function RouterProvider({ children }) {
  const pathname = useSyncExternalStore(subscribe, getPathname, () => '/');

  const navigate = useCallback((to, { replace = false } = {}) => {
    const url = new URL(to, window.location.href);
    if (url.origin !== window.location.origin) {
      window.location.assign(url.href);
      return;
    }
    const next = url.pathname + url.search + url.hash;
    const current = window.location.pathname + window.location.search + window.location.hash;
    if (next === current) return;
    window.history[replace ? 'replaceState' : 'pushState'](null, '', next);
    window.dispatchEvent(new Event(NAVIGATE_EVENT));
  }, []);

  const value = useMemo(() => ({ pathname, navigate }), [pathname, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter() {
  const context = useContext(RouterContext);
  if (!context) throw new Error('useRouter must be used inside <RouterProvider>.');
  return context;
}
