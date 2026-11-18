import { useEffect, useRef, useState } from 'react';
import { matchRoute } from '../../app/router/matchPath.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { ErrorBoundary } from '../ui/ErrorBoundary.jsx';
import { RouteGate } from './RouteGate.jsx';
import { Sidebar } from './Sidebar.jsx';
import { Topbar } from './Topbar.jsx';

const DESKTOP = '(min-width: 960px)';

export function AppShell({ routes, notFound }) {
  const { pathname } = useRouter();
  const [navOpen, setNavOpen] = useState(false);
  const mainRef = useRef(null);
  const menuButtonRef = useRef(null);
  const lastPath = useRef(pathname);
  const restoreFocus = useRef(false);

  function closeDrawer() {
    restoreFocus.current = true;
    setNavOpen(false);
  }

  const match = matchRoute(routes, pathname);
  const title = match ? match.route.title : 'Page not found';

  // After in-app navigation: close the drawer, reset scroll and move focus to the content.
  // Comparing with the previous path (not a "first render" flag) keeps this a no-op on the
  // initial mount, including the double effect run of React StrictMode in development.
  useEffect(() => {
    if (lastPath.current === pathname) return;
    lastPath.current = pathname;
    setNavOpen(false);
    window.scrollTo(0, 0);
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  useEffect(() => {
    document.title = match ? `${title} · PredictAI` : 'Page not found · PredictAI';
  }, [match, title]);

  // Drawer behaviour (small screens): Escape closes it, focus moves in and returns, and
  // widening the window to desktop size closes it.
  useEffect(() => {
    if (!navOpen) return undefined;
    document.querySelector('#primary-navigation a, #primary-navigation button')?.focus();
    const onKeyDown = (event) => {
      if (event.key === 'Escape') closeDrawer();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [navOpen]);

  // Return focus to the menu button only after the drawer has closed and the page behind it
  // is no longer inert (an inert element cannot take focus).
  useEffect(() => {
    if (!navOpen && restoreFocus.current) {
      restoreFocus.current = false;
      menuButtonRef.current?.focus();
    }
  }, [navOpen]);

  useEffect(() => {
    const query = window.matchMedia(DESKTOP);
    const onChange = (event) => event.matches && setNavOpen(false);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);


  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <Sidebar open={navOpen} onNavigate={() => setNavOpen(false)} onClose={closeDrawer} />
      {navOpen ? <button type="button" className="scrim" tabIndex={-1} aria-label="Close navigation" onClick={closeDrawer} /> : null}
      <div className="app-shell__main" inert={navOpen}>
        <Topbar title={title} menuOpen={navOpen} onMenu={() => setNavOpen(true)} menuButtonRef={menuButtonRef} />
        <main id="main" ref={mainRef} className="main" tabIndex={-1}>
          <ErrorBoundary key={pathname}>
            {match ? <RouteGate access={match.route.access}>{match.route.element}</RouteGate> : notFound}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
