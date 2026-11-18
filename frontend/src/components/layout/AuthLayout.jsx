import { useEffect, useRef } from 'react';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Logo } from '../ui/Logo.jsx';
import { ThemeToggle } from './ThemeToggle.jsx';

/** Frame for signed-out screens (login, register, recovery): brand, theme toggle and one card. */
export function AuthLayout({ title, children }) {
  const { pathname } = useRouter();
  const mainRef = useRef(null);
  const lastPath = useRef(pathname);

  useEffect(() => {
    document.title = `${title} · PredictAI`;
  }, [title]);

  useEffect(() => {
    if (lastPath.current === pathname) return;
    lastPath.current = pathname;
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="auth">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="auth__bar">
        <Link to="/" className="auth__brand" aria-label="PredictAI overview"><Logo /></Link>
        <ThemeToggle />
      </header>
      <main id="main" ref={mainRef} className="auth__main" tabIndex={-1}>{children}</main>
      <p className="auth__legal">PredictAI never asks for your password by email.</p>
    </div>
  );
}
