import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { queryString, workspaceApi } from '../src/api/workspace.js';
import { matchRoute } from '../src/app/router/matchPath.js';
import {
  checkDescription, checkProjectName, describeEvent, DESCRIPTION_MAX, formatBytes, NAME_MAX, plural,
} from '../src/features/workspace/format.js';

const json = (body, init = {}) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });

function recorder(response = { data: {}, meta: { request_id: 'r' } }) {
  const calls = [];
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async (url, init) => { calls.push({ url, init }); return json(response); } });
  client.configure({ getCsrfToken: () => 'csrf-1' });
  return { calls, api: workspaceApi(client) };
}

test('queryString drops empty values and encodes the rest', () => {
  assert.equal(queryString({ status: 'active', q: '', limit: 10, none: undefined }), '?status=active&limit=10');
  assert.equal(queryString({ q: 'a b&c' }), '?q=a+b%26c');
  assert.equal(queryString({}), '');
});

test('project calls use the right method, path, body and CSRF header, and return unwrapped data', async () => {
  const { calls, api } = recorder({ data: { id: 'p1' }, meta: { request_id: 'r' } });
  assert.deepEqual(await api.projects.create({ name: 'A', description: '' }), { id: 'p1' });
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].init.body), { name: 'A', description: null, metadata: null });
  assert.equal(calls[0].init.headers['X-CSRF-Token'], 'csrf-1');
  await api.projects.get('a/b');
  assert.equal(calls[1].url, '/api/v1/projects/a%2Fb');
  await api.projects.remove('p1', 'My project');
  assert.equal(calls[2].url, '/api/v1/projects/p1/delete');
  assert.deepEqual(JSON.parse(calls[2].init.body), { confirm_name: 'My project' });
  await api.projects.update('p1', { name: 'B' });
  assert.equal(calls[3].init.method, 'PATCH');
  await api.projects.archive('p1');
  await api.projects.restore('p1');
  assert.deepEqual(calls.slice(4).map((c) => c.url), ['/api/v1/projects/p1/archive', '/api/v1/projects/p1/restore']);
});

test('API failures keep their stable code (404 for a foreign project looks like a missing one)', async () => {
  const client = createApiClient({ baseUrl: '/api/v1', fetchImpl: async () => json({ error: { code: 'NOT_FOUND', message: 'nope', request_id: 'x' } }, { status: 404 }) });
  await assert.rejects(workspaceApi(client).projects.get('p'), (e) => e.code === 'NOT_FOUND' && e.status === 404);
});

test('project name and description checks mirror the server limits', () => {
  assert.match(checkProjectName('   '), /name/i);
  assert.equal(checkProjectName('Physics'), null);
  assert.ok(checkProjectName('x'.repeat(NAME_MAX + 1)));
  assert.equal(checkDescription('x'.repeat(DESCRIPTION_MAX)), null);
  assert.ok(checkDescription('x'.repeat(DESCRIPTION_MAX + 1)));
});

test('formatting helpers', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB');
  assert.equal(formatBytes(-1), '—');
  assert.equal(plural(1, 'dataset'), '1 dataset');
  assert.equal(plural(2, 'document'), '2 documents');
  assert.equal(describeEvent('project.created'), 'Project created');
  assert.equal(describeEvent('some.unknown_event'), 'some unknown event');
});

test('the nested project route captures its id (and decodes it)', () => {
  const routes = [{ path: '/projects' }, { path: '/projects/:projectId' }];
  assert.equal(matchRoute(routes, '/projects').route.path, '/projects');
  assert.deepEqual(matchRoute(routes, '/projects/abc123').params, { projectId: 'abc123' });
  assert.deepEqual(matchRoute(routes, '/projects/a%2Fb').params, { projectId: 'a/b' });
  assert.equal(matchRoute(routes, '/projects/a/b'), null);
});
