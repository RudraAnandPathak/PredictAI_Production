import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  AUDIENCE, NAV_GROUPS, NAV_ITEMS, ROADMAP_PHASES, STATUS, STATUS_LABEL, findNavItem, validateNavigation, visibleGroups,
} from '../src/app/navigation.js';

test('the real navigation model is consistent', () => {
  assert.deepEqual(validateNavigation(), []);
});

test('ids and paths are unique and every status has a label', () => {
  assert.equal(new Set(NAV_ITEMS.map((i) => i.id)).size, NAV_ITEMS.length);
  assert.equal(new Set(NAV_ITEMS.map((i) => i.path)).size, NAV_ITEMS.length);
  for (const status of Object.values(STATUS)) assert.ok(STATUS_LABEL[status], status);
});

test('only what is really built is marked existing; everything else is honestly planned', () => {
  const existing = NAV_ITEMS.filter((i) => i.status === STATUS.EXISTING).map((i) => i.id);
  assert.deepEqual(existing, ['overview', 'status', 'dashboard', 'projects', 'uploads', 'conversations', 'analytics', 'predictions', 'exam', 'notebook', 'reports', 'schedules', 'workspaces', 'intelligence', 'models', 'account', 'development', 'compute', 'owner']);
  for (const entry of NAV_ITEMS) if (!existing.includes(entry.id)) assert.equal(entry.status, STATUS.PLANNED, entry.id);
});

test('professional and owner areas are marked for their audiences', () => {
  const byGroup = Object.fromEntries(NAV_GROUPS.map((g) => [g.id, g.items.map((i) => i.audience)]));
  assert.ok(byGroup.professional.every((a) => a === AUDIENCE.SPECIAL));
  assert.deepEqual(byGroup.owner, [AUDIENCE.OWNER]);
});

test('findNavItem looks up by path', () => {
  assert.equal(findNavItem('/analytics').label, 'Analytics');
  assert.equal(findNavItem('/unknown'), null);
});

test('validateNavigation catches duplicates, bad statuses and unknown phases', () => {
  const bad = [{ id: 'g', label: 'g', items: [
    { id: 'a', label: 'A', path: '/a', status: 'planned', phase: 3, audience: 'standard', access: 'user', summary: 's' },
    { id: 'a', label: 'A2', path: '/a', status: 'live', phase: 99, audience: 'nobody', access: 'everyone', summary: '' },
  ] }];
  const problems = validateNavigation(bad).join('\n');
  for (const needle of ['duplicate id', 'duplicate path', 'unknown status', 'unknown phase', 'unknown audience', 'unknown access', 'missing summary']) {
    assert.ok(problems.includes(needle), needle);
  }
  assert.ok(ROADMAP_PHASES[3]);
});

import { satisfies } from '../src/features/identity/permissions.js';

const person = (roles = ['user'], tiers = ['standard']) => ({ roles, tiers, permissions: [] });
const idsFor = (user) => visibleGroups((access) => satisfies(user, access)).flatMap((g) => g.items.map((i) => i.id));

test('signed-out visitors only see the public platform pages', () => {
  assert.deepEqual(idsFor(null), ['overview', 'status']);
});

test('an ordinary account sees the workspace but neither professional nor owner areas', () => {
  const ids = idsFor(person());
  assert.ok(ids.includes('account') && ids.includes('projects'));
  for (const hidden of ['development', 'compute', 'owner']) assert.ok(!ids.includes(hidden), hidden);
});

test('special access reveals the professional group; only owners see the owner area', () => {
  const special = idsFor(person(['user'], ['standard', 'special']));
  assert.ok(special.includes('development') && !special.includes('owner'));
  const owner = idsFor(person(['owner'], ['standard']));
  assert.ok(owner.includes('owner') && owner.includes('development'));
});

test('every item has a known access level and the owner area is owner-only', () => {
  assert.equal(findNavItem('/owner').access, 'owner');
  assert.equal(findNavItem('/account').access, 'user');
  assert.equal(findNavItem('/').access, 'public');
});

test('Notebook replaces the two older research areas in the menu, is built, and is open to signed-in people', () => {
  assert.equal(NAV_ITEMS.find((i) => i.id === 'research'), undefined);
  assert.equal(NAV_ITEMS.find((i) => i.id === 'research-intelligence'), undefined);
  const entry = NAV_ITEMS.find((i) => i.id === 'notebook');
  assert.ok(entry);
  assert.equal(entry.status, STATUS.EXISTING);
  assert.equal(entry.phase, 10);
  assert.doesNotMatch(entry.summary, /not built yet/i);
  assert.equal(entry.access, 'user');
  assert.ok(ROADMAP_PHASES[10]);
  assert.deepEqual(validateNavigation(), []);
});
