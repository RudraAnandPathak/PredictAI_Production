import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isActivePath, matchRoute, normalizePath } from '../src/app/router/matchPath.js';

const routes = [
  { path: '/', id: 'home' },
  { path: '/system/status', id: 'status' },
  { path: '/projects/:projectId', id: 'project' },
  { path: '/projects', id: 'projects' },
];

test('normalizePath handles slashes, queries and hashes', () => {
  assert.equal(normalizePath(''), '/');
  assert.equal(normalizePath('/a/b/'), '/a/b');
  assert.equal(normalizePath('a/b'), '/a/b');
  assert.equal(normalizePath('/a?x=1#top'), '/a');
  assert.equal(normalizePath('///'), '/');
});

test('matches exact paths, including the root', () => {
  assert.equal(matchRoute(routes, '/').route.id, 'home');
  assert.equal(matchRoute(routes, '/system/status/').route.id, 'status');
  assert.equal(matchRoute(routes, '/projects?x=1').route.id, 'projects');
});

test('extracts and decodes path parameters', () => {
  const match = matchRoute(routes, '/projects/a%20b');
  assert.equal(match.route.id, 'project');
  assert.deepEqual(match.params, { projectId: 'a b' });
  assert.equal(matchRoute(routes, '/projects/%E0%A4%A').params.projectId, '%E0%A4%A', 'bad encoding is kept as is');
});

test('returns null for unknown paths and does not treat a prefix as a match', () => {
  assert.equal(matchRoute(routes, '/nope'), null);
  assert.equal(matchRoute(routes, '/system'), null);
  assert.equal(matchRoute(routes, '/projects/1/extra'), null);
});

test('special characters in a pattern are matched literally', () => {
  assert.equal(matchRoute([{ path: '/a.b' }], '/axb'), null);
  assert.ok(matchRoute([{ path: '/a.b' }], '/a.b'));
});

test('isActivePath: exact, nested and the root special case', () => {
  assert.ok(isActivePath('/projects', '/projects'));
  assert.ok(isActivePath('/projects/42', '/projects'));
  assert.ok(!isActivePath('/projects-old', '/projects'));
  assert.ok(!isActivePath('/projects/42', '/projects', { end: true }));
  assert.ok(isActivePath('/', '/'));
  assert.ok(!isActivePath('/projects', '/'), 'root is only active on the root');
});
