// Personal API tokens (for your own scripts and AI agents such as the PredictAI agent skill). Formatting and input checks only;
// the server decides everything else.

export const EXPIRY_CHOICES = Object.freeze([30, 90, 180]);
export const STATE_LABEL = Object.freeze({ active: 'Active', expired: 'Expired', revoked: 'Revoked' });
export const STATE_TONE = Object.freeze({ active: 'ok', expired: 'muted', revoked: 'muted' });

export function tokenProblem({ name, password, hasPassword, days }) {
  const n = (name ?? '').trim();
  if (!n) return 'Give the token a name, for example "Claude Code on my laptop".';
  if (n.length > 80) return 'Keep the name under 80 characters.';
  if (!EXPIRY_CHOICES.includes(Number(days))) return 'Choose 30, 90 or 180 days.';
  if (hasPassword && !password) return 'Type your password to confirm it is you.';
  return null;
}

export function tokenBody({ name, password, days }) {
  return { name: name.trim(), password: password || null, expires_in_days: Number(days) };
}

/** "Last used 3 days ago" style text from ISO dates; plain dates keep it locale-neutral. */
export function usedLine(token) {
  if (!token.last_used_at) return 'Never used';
  return `Last used ${token.last_used_at.slice(0, 10)}`;
}

export const activeCount = (items) => (items ?? []).filter((t) => t.state === 'active').length;
