// Mirrors backend/app/modules/identity/validation.py so people get instant feedback.
// The server remains the authority and repeats every check.
export const PASSWORD_MIN = 10;
export const PASSWORD_MAX = 128;
export const DISPLAY_NAME_MAX = 60;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function checkEmail(value) {
  const v = String(value ?? '').trim();
  if (!v) return 'Enter your email address.';
  if (v.length > 254 || !EMAIL.test(v)) return 'Enter a valid email address, like name@example.com.';
  return null;
}

export function checkPassword(value, { email = '' } = {}) {
  const v = String(value ?? '');
  if (!v) return 'Enter a password.';
  if (v.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (v.length > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  const local = String(email).split('@')[0].toLowerCase();
  if (local.length >= 4 && v.toLowerCase().includes(local)) return 'Do not include your email name in your password.';
  return null;
}

export function checkDisplayName(value) {
  const v = String(value ?? '').trim();
  return v.length > DISPLAY_NAME_MAX ? `Use at most ${DISPLAY_NAME_MAX} characters.` : null;
}

/** Map API field problems ({field, message|issue}) to { field: message } for inline display. */
export function fieldErrors(error) {
  const out = {};
  for (const d of error?.details ?? []) {
    const field = String(d.field ?? '').replace(/^body\./, '');
    if (field && !out[field]) out[field] = d.message || 'Please check this value.';
  }
  return out;
}

/** Error text that is always safe to show; includes the support reference when there is one. */
export function describeError(error, fallback = 'Something went wrong. Please try again.') {
  if (!error) return fallback;
  if (error.code === 'RATE_LIMITED') {
    const wait = Number(error.retryAfter);
    return wait > 0 ? `Too many attempts. Please wait about ${Math.max(1, Math.ceil(wait / 60))} minute(s) and try again.` : 'Too many attempts. Please wait a little and try again.';
  }
  if (error.isConnectionProblem) return 'Could not reach PredictAI. Check your connection and try again.';
  return error.message || fallback;
}
