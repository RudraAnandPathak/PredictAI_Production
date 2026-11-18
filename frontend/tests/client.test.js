import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient, joinUrl } from '../src/api/client.js';
import { ApiError, isAbortError } from '../src/api/errors.js';

const json = (body, init = {}) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' }, ...init });

const clientWith = (fetchImpl, options = {}) => createApiClient({ baseUrl: '/api/v1', fetchImpl, ...options });

async function rejection(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  assert.fail('expected the promise to reject');
}

test('joinUrl produces exactly one slash between base and path', () => {
  assert.equal(joinUrl('/api/v1', '/health'), '/api/v1/health');
  assert.equal(joinUrl('/api/v1/', 'health'), '/api/v1/health');
  assert.equal(joinUrl('', '/health'), '/health');
  assert.equal(joinUrl('https://x.test/api', '/health'), 'https://x.test/api/health');
});

test('returns data and the request id from the success envelope', async () => {
  let seen;
  const client = clientWith(async (url, init) => {
    seen = { url, init };
    return json({ data: { ok: true }, meta: { request_id: 'req-from-body' } });
  });
  const result = await client.request('/health');
  assert.deepEqual(result, { data: { ok: true }, requestId: 'req-from-body', status: 200 });
  assert.equal(seen.url, '/api/v1/health');
  assert.equal(seen.init.method, 'GET');
  assert.equal(seen.init.headers.Accept, 'application/json');
  assert.equal(seen.init.credentials, 'include');
});

test('falls back to the X-Request-ID header when the body has no meta', async () => {
  const client = clientWith(async () => json({ data: 1 }, { headers: { 'x-request-id': 'req-from-header' } }));
  assert.equal((await client.request('/x')).requestId, 'req-from-header');
});

test('maps the server error envelope to an ApiError', async () => {
  const body = { error: { code: 'NOT_FOUND', message: 'The requested resource was not found.', request_id: 'req-404' } };
  const error = await rejection(clientWith(async () => json(body, { status: 404 })).request('/missing'));
  assert.ok(error instanceof ApiError);
  assert.deepEqual([error.code, error.status, error.requestId], ['NOT_FOUND', 404, 'req-404']);
  assert.equal(error.message, 'The requested resource was not found.');
  assert.equal(error.isConnectionProblem, false);
});

test('keeps validation details from the envelope', async () => {
  const body = { error: { code: 'VALIDATION_ERROR', message: 'Bad.', request_id: 'r', details: [{ field: 'q', issue: 'missing' }] } };
  const error = await rejection(clientWith(async () => json(body, { status: 422 })).request('/x'));
  assert.deepEqual(error.details, [{ field: 'q', issue: 'missing' }]);
});

test('a non-JSON error body becomes a generic HTTP_ERROR without leaking server text', async () => {
  const client = clientWith(async () => new Response('<html>Traceback: secret</html>', { status: 502 }));
  const error = await rejection(client.request('/x'));
  assert.equal(error.code, 'HTTP_ERROR');
  assert.equal(error.status, 502);
  assert.ok(!error.message.includes('secret'));
});

test('a network failure becomes NETWORK_ERROR', async () => {
  const error = await rejection(clientWith(async () => { throw new TypeError('Failed to fetch'); }).request('/x'));
  assert.equal(error.code, 'NETWORK_ERROR');
  assert.equal(error.isConnectionProblem, true);
});

test('a slow server becomes TIMEOUT', async () => {
  const slow = (url, { signal }) =>
    new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  const error = await rejection(clientWith(slow, { timeoutMs: 20 }).request('/x'));
  assert.equal(error.code, 'TIMEOUT');
  assert.equal(error.isConnectionProblem, true);
});

test('a caller-initiated abort is rethrown as an AbortError, not an ApiError', async () => {
  const slow = (url, { signal }) =>
    new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
  const controller = new AbortController();
  const pending = rejection(clientWith(slow, { timeoutMs: 5000 }).request('/x', { signal: controller.signal }));
  controller.abort();
  const error = await pending;
  assert.ok(isAbortError(error));
  assert.ok(!(error instanceof ApiError));
});

test('an already-aborted signal never reaches the network as a success', async () => {
  const controller = new AbortController();
  controller.abort();
  const client = clientWith((url, { signal }) => (signal.aborted ? Promise.reject(new DOMException('aborted', 'AbortError')) : json({ data: 1 })));
  assert.ok(isAbortError(await rejection(client.request('/x', { signal: controller.signal }))));
});

test('a 200 response that is not valid JSON, or lacks data, is INVALID_RESPONSE', async () => {
  const notJson = await rejection(clientWith(async () => new Response('hello', { status: 200 })).request('/x'));
  assert.equal(notJson.code, 'INVALID_RESPONSE');
  const noData = await rejection(clientWith(async () => json({ ok: true })).request('/x'));
  assert.equal(noData.code, 'INVALID_RESPONSE');
});
