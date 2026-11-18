import { useEffect } from 'react';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { satisfies } from '../../features/identity/permissions.js';
import { loginUrl } from '../../features/identity/safeNext.js';
import { AccessDeniedPage } from '../../pages/AccessDeniedPage.jsx';
import { ErrorState } from '../ui/ErrorState.jsx';
import { PageLoading } from '../ui/Spinner.jsx';

function RedirectToLogin() {
  const { navigate, pathname } = useRouter();
  useEffect(() => {
    navigate(loginUrl(pathname + window.location.search), { replace: true });
  }, [navigate, pathname]);
  return <PageLoading label="Taking you to sign in…" />;
}

/** Presentation guard. The backend independently enforces every permission on every request. */
export function RouteGate({ access = 'public', children }) {
  const auth = useAuth();
  if (access === 'public') return children;
  if (auth.status === 'loading') return <PageLoading label="Checking your session…" />;
  if (auth.status === 'unavailable') {
    return <ErrorState title="Can't reach PredictAI" message="We could not check your session because the server did not answer." onRetry={() => auth.refresh()} />;
  }
  if (auth.status === 'anonymous') return <RedirectToLogin />;
  if (!satisfies(auth.user, access)) return <AccessDeniedPage />;
  return children;
}
