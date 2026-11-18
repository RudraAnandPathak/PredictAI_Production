import { useEffect } from 'react';
import { AppShell } from '../components/layout/AppShell.jsx';
import { AuthLayout } from '../components/layout/AuthLayout.jsx';
import { ErrorBoundary } from '../components/ui/ErrorBoundary.jsx';
import { AuthProvider, useAuth } from '../features/identity/AuthContext.jsx';
import { ReauthProvider } from '../features/identity/ReauthContext.jsx';
import { HealthProvider } from '../features/system/HealthContext.jsx';
import { notFound, routes } from './routes.jsx';
import { matchRoute } from './router/matchPath.js';
import { RouterProvider, useRouter } from './router/RouterProvider.jsx';

function Frame() {
  const { pathname, navigate } = useRouter();
  const auth = useAuth();
  const match = matchRoute(routes, pathname);
  const route = match?.route;
  const redirectHome = route?.guestOnly && auth.status === 'authenticated';

  useEffect(() => {
    if (redirectHome) navigate('/', { replace: true });
  }, [redirectHome, navigate]);

  if (route?.layout === 'auth') {
    return (
      <AuthLayout title={route.title}>
        <ErrorBoundary key={pathname}>{redirectHome ? null : route.element}</ErrorBoundary>
      </AuthLayout>
    );
  }
  return <AppShell routes={routes} notFound={notFound} />;
}

export function App() {
  return (
    <ErrorBoundary>
      <RouterProvider>
        <AuthProvider>
          <ReauthProvider>
            <HealthProvider>
              <Frame />
            </HealthProvider>
          </ReauthProvider>
        </AuthProvider>
      </RouterProvider>
    </ErrorBoundary>
  );
}
