import test from 'node:test';
import assert from 'node:assert/strict';
import { identityApi } from '../src/api/identity.js';
import { safeNext } from '../src/features/identity/safeNext.js';

test('the magic-link pages are never a post-login destination', () => {
  assert.equal(safeNext('/magic-link'), '/');
  assert.equal(safeNext('/magic-link/request'), '/');
  assert.equal(safeNext('/magic-link#token=abc'), '/');
  assert.equal(safeNext('/magic-links-info'), '/magic-links-info');
});

test('the magic-link calls send exactly the documented bodies', async () => {
  const calls = [];
  const client = { request: async (path, opts) => { calls.push([path, opts.method, opts.body]); return { data: { id: 'u', email: 'a@b.c', roles: [], permissions: [], tiers: [], csrf_token: 't' } }; } };
  const api = identityApi(client);
  await api.requestMagicLink('a@b.c');
  const me = await api.consumeMagicLink({ token: 'tok' });
  assert.deepEqual(calls, [['/auth/magic-link/request', 'POST', { email: 'a@b.c' }], ['/auth/magic-link/consume', 'POST', { token: 'tok', remember_me: false }]]);
  assert.equal(me.user.email, "a@b.c");
  assert.equal(me.csrfToken, "t");
});
