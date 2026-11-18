import { isActivePath } from './matchPath.js';
import { useRouter } from './RouterProvider.jsx';

export function Link({ to, replace = false, onClick, target, ...rest }) {
  const { navigate } = useRouter();

  const handleClick = (event) => {
    onClick?.(event);
    const plainLeftClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    if (event.defaultPrevented || !plainLeftClick || (target && target !== '_self')) return;
    event.preventDefault();
    navigate(to, { replace });
  };

  return <a href={to} target={target} onClick={handleClick} {...rest} />;
}

export function NavLink({ to, end = false, className = '', activeClassName = 'is-active', ...rest }) {
  const { pathname } = useRouter();
  const active = isActivePath(pathname, to, { end });
  return (
    <Link
      to={to}
      aria-current={active ? 'page' : undefined}
      className={[className, active ? activeClassName : ''].filter(Boolean).join(' ')}
      {...rest}
    />
  );
}
