// Links in research results come from outside (web pages, news feeds). They are untrusted: only http(s) addresses become clickable, and every one opens
// without telling the destination who referred it. Everything else is shown as plain text.

export const LINK_REL = 'noopener noreferrer nofollow';

/** The address if it is a plain http(s) link, else null. Never returns javascript:, data:, file: or credentials-in-URL addresses. */
export function safeHref(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  if (!text || text.length > 2000 || /[\s<>"'`\\]/.test(text)) return null;
  let url;
  try { url = new URL(text); } catch { return null; }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.username || url.password) return null;
  return url.href;
}

export function domainOf(value) {
  const href = safeHref(value);
  if (!href) return '';
  return new URL(href).hostname.replace(/^www\./, '');
}
