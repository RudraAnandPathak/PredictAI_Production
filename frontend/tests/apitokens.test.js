import test from 'node:test';
import assert from 'node:assert/strict';
import { activeCount, tokenBody, tokenProblem, usedLine } from '../src/features/apitokens/logic.js';

test('a token needs a name, a valid expiry and the password when the account has one', () => {
  assert.match(tokenProblem({ name: ' ', days: 90, hasPassword: true, password: 'x' }), /name/);
  assert.match(tokenProblem({ name: 'x'.repeat(81), days: 90, hasPassword: true, password: 'x' }), /80/);
  assert.match(tokenProblem({ name: 'Agent', days: 7, hasPassword: true, password: 'x' }), /30, 90 or 180/);
  assert.match(tokenProblem({ name: 'Agent', days: 90, hasPassword: true, password: '' }), /password/);
  assert.equal(tokenProblem({ name: 'Agent', days: 90, hasPassword: false, password: '' }), null);
  assert.deepEqual(tokenBody({ name: ' Agent ', password: '', days: '30' }), { name: 'Agent', password: null, expires_in_days: 30 });
});

test('list helpers', () => {
  assert.equal(usedLine({ last_used_at: null }), 'Never used');
  assert.equal(usedLine({ last_used_at: '2026-11-24T10:00:00Z' }), 'Last used 2026-11-24');
  assert.equal(activeCount([{ state: 'active' }, { state: 'revoked' }, { state: 'active' }]), 2);
});
