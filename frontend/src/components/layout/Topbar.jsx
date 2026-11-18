import { Link } from '../../app/router/Link.jsx';
import { ConnectionStatus } from '../../features/system/ConnectionStatus.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { Icon } from '../ui/Icon.jsx';
import { LogoMark } from '../ui/Logo.jsx';
import { ThemeToggle } from './ThemeToggle.jsx';

export function Topbar({ title, menuOpen, onMenu, menuButtonRef }) {
  const auth = useAuth();
  return (
    <header className="topbar">
      <button
        type="button"
        ref={menuButtonRef}
        className="icon-btn topbar__menu"
        onClick={onMenu}
        aria-label="Open navigation"
        aria-expanded={menuOpen}
        aria-controls="primary-navigation"
      >
        <Icon name="menu" />
      </button>
      <span className="topbar__mark" aria-hidden="true">
        <LogoMark size={24} />
      </span>
      <p className="topbar__page">{title}</p>
      <div className="topbar__actions">
        <ConnectionStatus />
        {auth.status === 'authenticated' ? (
          <Link to="/account" className="account-chip" aria-label={`Your account, ${auth.user.displayName || auth.user.email}`}>
            <Icon name="user" size={16} />
            <span className="account-chip__name">{auth.user.displayName || auth.user.email}</span>
          </Link>
        ) : auth.status === 'anonymous' ? (
          <span className="signin-actions">
            <Link to="/login" className="btn btn--sm">Sign in</Link>
            <Link to="/register" className="btn btn--ghost btn--sm">Create account</Link>
          </span>
        ) : null}
        <ThemeToggle />
      </div>
    </header>
  );
}
