import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { ApiError } from '../src/api/errors.js';
import { identityApi, ownerApi, parseMe } from '../src/api/identity.js';
import { formatDate, normaliseAccessCode } from '../src/features/identity/format.js';
import { can, hasSpecial, isOwner, satisfies } from '../src/features/identity/permissions.js';
import { loginUrl, safeNext, tokenFromHash } from '../src/features/identity/safeNext.js';
import { checkDisplayName, checkEmail, checkPassword, describeError, fieldErrors } from '../src/features/identity/validation.js';

const ME = {
  id: 'u1', email: 'a@example.com', display_name: 'Alice', status: 'active', email_verified: true, created_at: '2026-01-01T00:00:00Z',
  roles: ['user'], tiers: ['standard'], permissions: ['account.read'], authz_version: 1, csrf_token: 'csrf-1',
};
const ok = (data, init = {}) => new Response(JSON.stringify({ data, meta: { request_id: 'r1' } }), { status: 200, headers: { 'content-type': 'application/json' }, ...init });
const fail = (status, code, extra = {}, headers = {}) =>
  new Response(JSON.stringify({ error: { code, message: `msg ${code}`, request_id: 'r2', ...extra } }), { status, headers: { 'content-type': 'application/json', ...headers } });

test('safeNext only allows same-site absolute paths', () => {
  assert.equal(safeNext('/account'), '/account');
  assert.equal(safeNext('/projects?x=1#y'), '/projects?x=1#y');
  for (const bad of ['//evil.test', 'https://evil.test', 'javascript:alert(1)', '/\\evil.test', '', null, undefined, 'account', '/a\nb', '/login', '/register?x=1']) {
    assert.equal(safeNext(bad), '/', String(bad));
  }
  assert.equal(safeNext('//evil.test', '/home'), '/home');
});

test('loginUrl keeps a safe destination and the expired flag', () => {
  assert.equal(loginUrl('/account'), '/login?next=%2Faccount');
  assert.equal(loginUrl('/account', { expired: true }), '/login?next=%2Faccount&expired=1');
  assert.equal(loginUrl('//evil.test'), '/login');
  assert.equal(loginUrl('/'), '/login');
});

test('tokenFromHash reads only a well-formed token fragment', () => {
  assert.equal(tokenFromHash('#token=abcDEF123_-xyz'), 'abcDEF123_-xyz');
  assert.equal(tokenFromHash('#a=1&token=abcDEF123_-xyz'), 'abcDEF123_-xyz');
  for (const bad of ['', '#', '#token=', '#token=short', '#token=<script>alert(1)</script>', null]) assert.equal(tokenFromHash(bad), null, String(bad));
});

test('permission helpers fail closed', () => {
  const user = { roles: ['user'], tiers: ['standard'], permissions: ['account.read'] };
  const special = { roles: ['user'], tiers: ['standard', 'special'], permissions: [] };
  const owner = { roles: ['owner'], tiers: ['standard'], permissions: ['owner.users.read'] };
  assert.ok(can(user, 'account.read') && !can(user, 'owner.users.read') && !can(null, 'account.read'));
  assert.ok(!hasSpecial(user) && hasSpecial(special) && hasSpecial(owner));
  assert.ok(!isOwner(special) && isOwner(owner));
  assert.ok(satisfies(null, 'public') && !satisfies(null, 'user') && satisfies(user, 'user'));
  assert.ok(!satisfies(user, 'special') && !satisfies(user, 'owner') && satisfies(owner, 'owner'));
  assert.equal(satisfies(owner, 'made-up-level'), false);
});

test('client-side validation mirrors the server rules', () => {
  assert.ok(checkEmail('') && checkEmail('nope') && checkEmail('a@b'));
  assert.equal(checkEmail(' alice@example.com '), null);
  assert.match(checkPassword('short'), /at least 10/);
  assert.match(checkPassword('alice-secret-1', { email: 'alice@example.com' }), /email name/);
  assert.equal(checkPassword('correct horse battery staple', { email: 'alice@example.com' }), null);
  assert.ok(checkDisplayName('x'.repeat(61)) && checkDisplayName('Alice') === null);
});

test('error helpers never leak raw server text and map field problems', () => {
  const errs = fieldErrors({ details: [{ field: 'body.email', message: 'Bad email' }, { field: 'password', message: 'Weak' }] });
  assert.deepEqual(errs, { email: 'Bad email', password: 'Weak' });
  assert.match(describeError(new ApiError({ code: 'RATE_LIMITED', message: 'x', retryAfter: 600 })), /10 minute/);
  assert.match(describeError(new ApiError({ code: 'NETWORK_ERROR', message: 'x' })), /Could not reach/);
  assert.equal(describeError(new ApiError({ code: 'INVALID_CREDENTIALS', message: 'Check your details.' })), 'Check your details.');
});

test('access codes are normalised the way people type them', () => {
  assert.equal(normaliseAccessCode('  pai-abcde fghij–klmno pqrst '), 'PAI-ABCDE-FGHIJ-KLMNO-PQRST');
  assert.equal(formatDate('not a date'), '—');
});

test('parseMe validates and freezes the profile', () => {
  const parsed = parseMe({ data: ME });
  assert.equal(parsed.csrfToken, 'csrf-1');
  assert.equal(parsed.user.displayName, 'Alice');
  assert.ok(Object.isFrozen(parsed.user) && Object.isFrozen(parsed.user.permissions));
  assert.throws(() => parseMe({ data: { ...ME, csrf_token: undefined } }), (e) => e.code === 'INVALID_RESPONSE');
  assert.throws(() => parseMe({ data: null }), (e) => e.code === 'INVALID_RESPONSE');
});

test('the client sends JSON bodies, cookies and the CSRF token only on state-changing calls', async () => {
  const seen = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { seen.push({ url, init }); return ok({ done: true }); } });
  client.configure({ getCsrfToken: () => 'tok-9' });
  await client.request('/me');
  await client.request('/me', { method: 'PATCH', body: { display_name: 'A' } });
  await client.request('/auth/sessions/x', { method: 'DELETE' });
  assert.equal(seen[0].init.headers['X-CSRF-Token'], undefined);
  assert.equal(seen[0].init.credentials, 'include');
  assert.equal(seen[1].init.headers['X-CSRF-Token'], 'tok-9');
  assert.equal(seen[1].init.headers['Content-Type'], 'application/json');
  assert.equal(seen[1].init.body, JSON.stringify({ display_name: 'A' }));
  assert.equal(seen[2].init.headers['X-CSRF-Token'], 'tok-9');
  assert.equal(seen[2].init.body, undefined);
});

test('API failures keep their code, field details and Retry-After, and notify the error hook', async () => {
  const hook = [];
  const client = createApiClient({
    baseUrl: '/api/v1',
    fetchImpl: async () => fail(429, 'RATE_LIMITED', { details: [{ field: 'email', message: 'x' }] }, { 'retry-after': '120' }),
  });
  client.configure({ onError: (e, ctx) => hook.push([e.code, ctx.path]) });
  await assert.rejects(client.request('/auth/login', { method: 'POST', body: {} }), (e) => e.code === 'RATE_LIMITED' && e.retryAfter === 120 && e.details.length === 1);
  await assert.rejects(client.request('/me', { silent: true }));
  assert.deepEqual(hook, [['RATE_LIMITED', '/auth/login']]); // the silent call did not notify
});

test('identity and owner wrappers hit the documented endpoints', async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: '/api/v1',
    fetchImpl: async (url, init) => { calls.push([init.method, url, init.body ? JSON.parse(init.body) : null]); return ok(ME); },
  });
  const id = identityApi(client);
  const own = ownerApi(client);
  await id.login({ email: 'a@example.com', password: 'pw', rememberMe: false });
  await id.changePassword({ currentPassword: 'a', newPassword: 'b' });
  await id.activateCode('PAI-X');
  await id.revokeSession('s/1');
  await own.users({ q: 'ali', status: '', limit: 25, offset: 0 });
  await own.grantSpecial('u 1');
  await own.createCode({ label: 'For Bob', expiresInDays: 7 });
  assert.deepEqual(calls.map((c) => c.slice(0, 2)), [
    ['POST', '/api/v1/auth/login'], ['POST', '/api/v1/me/password'], ['POST', '/api/v1/access-codes/activate'],
    ['DELETE', '/api/v1/auth/sessions/s%2F1'], ['GET', '/api/v1/owner/users?q=ali&limit=25&offset=0'],
    ['POST', '/api/v1/owner/users/u%201/entitlements'], ['POST', '/api/v1/owner/access-codes'],
  ]);
  assert.deepEqual(calls[0][2], { email: 'a@example.com', password: 'pw', remember_me: false });
  assert.deepEqual(calls[1][2], { current_password: 'a', new_password: 'b' });
  assert.deepEqual(calls[5][2], { tier: 'special' });
  assert.deepEqual(calls[6][2], { label: 'For Bob', target_user_id: null, expires_in_days: 7 });
});
