import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, identity } from '../../api/index.js';
import { ApiError } from '../../api/errors.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { loginUrl } from './safeNext.js';

const AuthContext = createContext(null);
const REVALIDATE_AFTER_MS = 60_000;

/**
 * Owns "who is signed in". The session itself is an HttpOnly cookie the page cannot read; this
 * provider only keeps the profile returned by GET /me and the CSRF token for state-changing calls.
 *
 * status: loading | authenticated | anonymous | unavailable (server unreachable: we do not
 * pretend to know). `endedReason` is set when a session ended while the page was open.
 */
export function AuthProvider({ children, client = api, service = identity }) {
  const { navigate, pathname } = useRouter();
  const [state, setState] = useState({ status: 'loading', user: null, error: null, endedReason: null });
  const csrf = useRef(null);
  const lastChecked = useRef(0);
  const pathRef = useRef(pathname);
  pathRef.current = pathname;
  const statusRef = useRef('loading');
  statusRef.current = state.status;

  const apply = useCallback(({ user, csrfToken }) => {
    csrf.current = csrfToken;
    lastChecked.current = Date.now();
    setState({ status: 'authenticated', user, error: null, endedReason: null });
  }, []);

  const clear = useCallback((endedReason = null) => {
    csrf.current = null;
    setState({ status: 'anonymous', user: null, error: null, endedReason });
  }, []);

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    try {
      apply(await service.me({ silent: true }));
    } catch (error) {
      if (error instanceof ApiError && error.isConnectionProblem) {
        // Keep an existing signed-in view during a blip; only a first load reports "unavailable".
        if (!quiet || statusRef.current === 'loading') setState((s) => ({ ...s, status: s.user ? s.status : 'unavailable', error }));
        return;
      }
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        const hadSession = statusRef.current === 'authenticated';
        clear(hadSession ? (error.code === 'SESSION_EXPIRED' ? 'expired' : 'ended') : null);
        if (hadSession) navigate(loginUrl(pathRef.current, { expired: true }), { replace: true });
        return;
      }
      setState((s) => ({ ...s, status: s.user ? s.status : 'unavailable', error: error instanceof ApiError ? error : null }));
    }
  }, [apply, clear, navigate, service]);

  // Wire the shared client: CSRF header source, and global handling of an expired session.
  useEffect(() => {
    client.configure({
      getCsrfToken: () => csrf.current,
      onError: (error, { path }) => {
        if (error.code === 'SESSION_EXPIRED' || (error.status === 401 && statusRef.current === 'authenticated' && !path.startsWith('/auth/login'))) {
          clear('expired');
          navigate(loginUrl(pathRef.current, { expired: true }), { replace: true });
        }
      },
    });
    return () => client.configure({ getCsrfToken: null, onError: null });
  }, [client, clear, navigate]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Coming back to the tab re-checks the session, so revoked or expired sessions are noticed promptly.
  useEffect(() => {
    const onVisible = () => {
      if (document.hidden || statusRef.current !== 'authenticated') return;
      if (Date.now() - lastChecked.current > REVALIDATE_AFTER_MS) refresh({ quiet: true });
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [refresh]);

  const signIn = useCallback(async (credentials) => {
    apply(await service.login(credentials));
  }, [apply, service]);

  const signInWithMagicLink = useCallback(async (token) => {
    apply(await service.consumeMagicLink({ token }));
  }, [apply, service]);

  const signOut = useCallback(async ({ everywhere = false } = {}) => {
    try {
      await service.logout({ everywhere });
    } catch {
      // Even if the call failed we leave the signed-in view; the server session expires on its own.
    }
    clear(null);
  }, [clear, service]);

  const setCsrf = useCallback((token) => {
    if (token) csrf.current = token;
  }, []);

  const value = useMemo(() => ({
    ...state, signIn, signInWithMagicLink, signOut, refresh, setCsrf, applyProfile: apply, markSignedOut: clear,
  }), [state, signIn, signInWithMagicLink, signOut, refresh, setCsrf, apply, clear]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>.');
  return context;
}
