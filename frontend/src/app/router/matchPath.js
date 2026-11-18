// Pure route matching (no React), so it can be unit-tested in Node.

export function normalizePath(input) {
  let path = String(input ?? '/').split('#')[0].split('?')[0];
  if (!path.startsWith('/')) path = `/${path}`;
  if (path.length > 1) path = path.replace(/\/+$/, '');
  return path || '/';
}

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function compile(pattern) {
  const keys = [];
  const source = normalizePath(pattern)
    .split('/')
    .map((segment) => {
      if (segment.startsWith(':')) {
        keys.push(segment.slice(1));
        return '([^/]+)';
      }
      return escapeRegExp(segment);
    })
    .join('/');
  return { regex: new RegExp(`^${source}$`), keys };
}

/** Return { route, params } for the first route whose path matches, or null. */
export function matchRoute(routes, pathname) {
  const path = normalizePath(pathname);
  for (const route of routes) {
    const { regex, keys } = compile(route.path);
    const found = regex.exec(path);
    if (found) {
      const params = {};
      keys.forEach((key, index) => {
        params[key] = safeDecode(found[index + 1]);
      });
      return { route, params };
    }
  }
  return null;
}

/** True when `pathname` is the link target itself or lives beneath it. */
export function isActivePath(pathname, target, { end = false } = {}) {
  const current = normalizePath(pathname);
  const dest = normalizePath(target);
  if (current === dest) return true;
  if (end || dest === '/') return false;
  return current.startsWith(`${dest}/`);
}
