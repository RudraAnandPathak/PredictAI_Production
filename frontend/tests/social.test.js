import assert from 'node:assert/strict';
import { test } from 'node:test';
import { identityApi, parseMe } from '../src/api/identity.js';
import { oauthErrorMessage, readOAuthError } from '../src/features/identity/oauthMessages.js';

const ME = { data: { id: 'u1', email: 'a@example.com', roles: ['user'], permissions: [], tiers: ['standard'], csrf_token: 't' }, requestId: 'r' };

test('parseMe reads has_password and connected providers, defaulting to a password account', () => {
  assert.equal(parseMe(ME).user.hasPassword, true);
  assert.deepEqual(parseMe(ME).user.connectedProviders, []);
  const social = parseMe({ ...ME, data: { ...ME.data, has_password: false, connected_providers: ['google'] } }).user;
  assert.equal(social.hasPassword, false);
  assert.deepEqual([...social.connectedProviders], ['google']);
});

test('error codes from the server become fixed, plain messages', () => {
  assert.match(oauthErrorMessage('oauth_email_exists', 'google'), /never merged automatically/);
  assert.match(oauthErrorMessage('oauth_no_account', 'github'), /GitHub/);
  assert.match(oauthErrorMessage('denied', 'google'), /cancelled/);
  assert.match(oauthErrorMessage('<script>alert(1)</script>', 'google'), /could not complete/);
  assert.equal(readOAuthError('?foo=1'), null);
  assert.match(readOAuthError('?oauth_error=oauth_email_unverified&provider=google'), /did not confirm an email/);
  assert.doesNotMatch(oauthErrorMessage('<b>x</b>', 'google'), /<b>/);
});

test('sign-in wrappers call the documented endpoints and never send secrets', async () => {
  const calls = [];
  const client = { request: async (path, options = {}) => { calls.push([path, options.method ?? 'GET', options.body]); return { data: { items: [], authorization_url: 'https://accounts.example/auth' } }; } };
  const api = identityApi(client);
  assert.equal(await api.startOAuthSignIn('google', 'register'), 'https://accounts.example/auth');
  await api.startOAuthReauth('github');
  await api.oauthAvailable();
  await api.deleteAccount(undefined);
  await api.unlinkOAuth('google');
  assert.deepEqual(calls[0], ['/auth/oauth/google/signin', 'POST', { intent: 'register' }]);
  assert.deepEqual(calls[1], ['/auth/oauth/github/reauth', 'POST', undefined]);
  assert.equal(calls[2][0], '/auth/oauth/available');
  assert.deepEqual(calls[3][2], { password: null });
  assert.deepEqual(calls[4][2], { password: null });
});
