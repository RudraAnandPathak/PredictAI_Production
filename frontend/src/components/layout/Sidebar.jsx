import { NAV_GROUPS, ROADMAP_PHASES, STATUS_LABEL, visibleGroups } from '../../app/navigation.js';
import { Link, NavLink } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { useAuth } from '../../features/identity/AuthContext.jsx';
import { satisfies } from '../../features/identity/permissions.js';
import { Icon } from '../ui/Icon.jsx';
import { Logo } from '../ui/Logo.jsx';

export function Sidebar({ open, onNavigate, onClose }) {
  const auth = useAuth();
  const { navigate } = useRouter();
  const groups = visibleGroups((access) => satisfies(auth.user, access), NAV_GROUPS);

  async function signOut() {
    onNavigate?.();
    await auth.signOut();
    navigate('/login', { replace: true });
  }

  return (
    <div id="primary-navigation" className={`sidebar${open ? ' is-open' : ''}`}>
      <div className="sidebar__head">
        <Link to="/" className="sidebar__brand" aria-label="PredictAI overview" onClick={onNavigate}>
          <Logo />
        </Link>
        <button type="button" className="icon-btn sidebar__close" onClick={onClose} aria-label="Close navigation">
          <Icon name="close" />
        </button>
      </div>

      <nav className="sidebar__nav" aria-label="Primary">
        {groups.map((group) => (
          <div className="nav-group" key={group.id}>
            <p className="nav-group__label" id={`nav-group-${group.id}`}>
              {group.label}
              {group.note ? <span className="nav-group__note"> · {group.note}</span> : null}
            </p>
            <ul className="nav-list" aria-labelledby={`nav-group-${group.id}`}>
              {group.items.map((item) => (
                <li key={item.id}>
                  <NavLink to={item.path} end={item.path === '/'} className="nav-link" onClick={onNavigate}>
                    <Icon name={item.icon} />
                    <span className="nav-link__label">{item.label}</span>
                    {item.status !== 'existing' ? (
                      <span className="nav-link__tag" title={`${STATUS_LABEL[item.status]}: Phase ${item.phase}, ${ROADMAP_PHASES[item.phase]}`}>
                        Phase {item.phase}
                      </span>
                    ) : null}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="sidebar__foot">
        {auth.status === 'authenticated' ? (
          <>
            <span className="eyebrow">Signed in</span>
            <span className="sidebar__email" title={auth.user.email}>{auth.user.email}</span>
            <button type="button" className="btn btn--ghost btn--sm sidebar__signout" onClick={signOut}>
              <Icon name="logout" size={16} /> Sign out
            </button>
          </>
        ) : auth.status === 'anonymous' ? (
          <>
            <span className="eyebrow">Not signed in</span>
            <Link to="/login" className="btn btn--sm" onClick={onNavigate}>Sign in</Link>
          </>
        ) : (
          <>
            <span className="eyebrow">Phase 2</span>
            <span>Identity &amp; security</span>
          </>
        )}
      </div>
    </div>
  );
}
