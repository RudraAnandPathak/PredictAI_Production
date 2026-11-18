/** Only same-site, absolute-path redirects are honoured (blocks open redirects via ?next=). */
export function safeNext(value, fallback = '/') {
  if (typeof value !== 'string' || value.length === 0 || value.length > 300) return fallback;
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\')) return fallback;
  if (/[\u0000-\u001f\\]/.test(value)) return fallback;
  if (/^\/(login|register|forgot-password|reset-password|verify-email|magic-link)(\/|\?|#|$)/.test(value)) return fallback;
  return value;
}

export function loginUrl(next, { expired = false } = {}) {
  const params = new URLSearchParams();
  const target = safeNext(next, '');
  if (target && target !== '/') params.set('next', target);
  if (expired) params.set('expired', '1');
  const query = params.toString();
  return query ? `/login?${query}` : '/login';
}

/** Read a single-use token from the URL fragment (kept out of server logs and Referer headers). */
export function tokenFromHash(hash) {
  const match = /(?:^#|&)token=([A-Za-z0-9_-]{8,512})(?:&|$)/.exec(String(hash ?? ''));
  return match ? match[1] : null;
}
