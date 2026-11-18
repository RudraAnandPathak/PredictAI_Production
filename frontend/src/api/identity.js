import { ApiError } from './errors.js';

// Thin wrappers over the identity endpoints. Components never build URLs or read raw envelopes.
const isText = (v) => typeof v === 'string' && v.length > 0;

/** Validate and normalise the /me payload (see backend api/presenters.py). */
export function parseMe(result) {
  const d = result?.data;
  const valid = d && typeof d === 'object' && isText(d.id) && isText(d.email) && Array.isArray(d.roles)
    && Array.isArray(d.permissions) && Array.isArray(d.tiers) && isText(d.csrf_token);
  if (!valid) {
    throw new ApiError({ code: 'INVALID_RESPONSE', message: 'The server sent an account response this app cannot read.', requestId: result?.requestId ?? null });
  }
  return Object.freeze({
    user: Object.freeze({
      id: d.id,
      email: d.email,
      displayName: d.display_name ?? '',
      status: d.status,
      emailVerified: Boolean(d.email_verified),
      createdAt: d.created_at ?? null,
      roles: Object.freeze([...d.roles]),
      tiers: Object.freeze([...d.tiers]),
      permissions: Object.freeze([...d.permissions]),
      hasPassword: d.has_password !== false,
      connectedProviders: Object.freeze(Array.isArray(d.connected_providers) ? [...d.connected_providers] : []),
    }),
    csrfToken: d.csrf_token,
  });
}

export const identityApi = (client) => ({
  me: (opts) => client.request('/me', opts).then(parseMe),
  login: ({ email, password, rememberMe }) =>
    client.request('/auth/login', { method: 'POST', body: { email, password, remember_me: rememberMe } }).then(parseMe),
  logout: ({ everywhere = false } = {}) => client.request('/auth/logout', { method: 'POST', body: { everywhere }, silent: true }),
  register: ({ email, password, displayName }) =>
    client.request('/auth/register', { method: 'POST', body: { email, password, display_name: displayName || null } }),
  verifyEmail: (token) => client.request('/auth/verify', { method: 'POST', body: { token } }),
  resendVerification: (email) => client.request('/auth/verify/resend', { method: 'POST', body: { email } }),
  requestRecovery: (email) => client.request('/auth/recovery/request', { method: 'POST', body: { email } }),
  completeRecovery: ({ token, newPassword }) =>
    client.request('/auth/recovery/complete', { method: 'POST', body: { token, new_password: newPassword } }),
  requestMagicLink: (email) => client.request('/auth/magic-link/request', { method: 'POST', body: { email } }),
  consumeMagicLink: ({ token, rememberMe = false }) =>
    client.request('/auth/magic-link/consume', { method: 'POST', body: { token, remember_me: rememberMe } }).then(parseMe),
  reauth: (password) => client.request('/auth/reauth', { method: 'POST', body: { password } }),
  updateProfile: (displayName) => client.request('/me', { method: 'PATCH', body: { display_name: displayName } }).then(parseMe),
  changePassword: ({ currentPassword, newPassword }) =>
    client.request('/me/password', { method: 'POST', body: { current_password: currentPassword, new_password: newPassword } }),
  deleteAccount: (password) => client.request('/me/delete', { method: 'POST', body: { password: password ?? null } }),
  activateCode: (code) => client.request('/access-codes/activate', { method: 'POST', body: { code } }),
  entitlements: () => client.request('/me/entitlements'),
  sessions: () => client.request('/auth/sessions'),
  revokeSession: (id) => client.request(`/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  providers: () => client.request('/auth/oauth'),
  startOAuth: (provider) => client.request(`/auth/oauth/${encodeURIComponent(provider)}/link`, { method: 'POST' }),
  unlinkOAuth: (provider, password) =>
    client.request(`/auth/oauth/${encodeURIComponent(provider)}/unlink`, { method: 'POST', body: { password: password ?? null } }),
  oauthAvailable: () => client.request('/auth/oauth/available').then((r) => r.data.items ?? []),
  startOAuthSignIn: (provider, intent) =>
    client.request(`/auth/oauth/${encodeURIComponent(provider)}/signin`, { method: 'POST', body: { intent } }).then((r) => r.data.authorization_url),
  startOAuthReauth: (provider) =>
    client.request(`/auth/oauth/${encodeURIComponent(provider)}/reauth`, { method: 'POST' }).then((r) => r.data.authorization_url),
});

export const ownerApi = (client) => {
  const q = (params) => {
    const usp = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') usp.set(k, String(v));
    const s = usp.toString();
    return s ? `?${s}` : '';
  };
  const id = encodeURIComponent;
  return {
    users: (params = {}) => client.request(`/owner/users${q(params)}`),
    user: (userId) => client.request(`/owner/users/${id(userId)}`),
    block: (userId, reason) => client.request(`/owner/users/${id(userId)}/block`, { method: 'POST', body: { reason: reason || null } }),
    unblock: (userId) => client.request(`/owner/users/${id(userId)}/unblock`, { method: 'POST' }),
    grantSpecial: (userId) => client.request(`/owner/users/${id(userId)}/entitlements`, { method: 'POST', body: { tier: 'special' } }),
    revokeEntitlement: (entitlementId, reason) =>
      client.request(`/owner/entitlements/${id(entitlementId)}/revoke`, { method: 'POST', body: { reason: reason || null } }),
    codes: () => client.request('/owner/access-codes'),
    createCode: ({ label, targetUserId, expiresInDays }) =>
      client.request('/owner/access-codes', { method: 'POST', body: { label: label || null, target_user_id: targetUserId || null, expires_in_days: expiresInDays || null } }),
    revokeCode: (codeId) => client.request(`/owner/access-codes/${id(codeId)}/revoke`, { method: 'POST' }),
    audit: (params = {}) => client.request(`/owner/audit-events${q(params)}`),
    owners: () => client.request('/owner/owners'),
    promote: (userId) => client.request('/owner/owners', { method: 'POST', body: { user_id: userId } }),
    demote: (userId) => client.request(`/owner/owners/${id(userId)}`, { method: 'DELETE' }),
  };
};
