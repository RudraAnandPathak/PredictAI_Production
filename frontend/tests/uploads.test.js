import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from '../src/api/client.js';
import { ApiError } from '../src/api/errors.js';
import { chunkRanges, checkFile, extensionOf, kindOfName } from '../src/features/workspace/uploadRules.js';
import { isRetryable, runUpload, withRetry } from '../src/features/workspace/uploader.js';

const file = (name, size, bytes) => ({ name, size, slice: (a, b) => ({ from: a, to: b, size: b - a }), bytes });
const noWait = () => Promise.resolve();

test('file checks give plain-language reasons', () => {
  assert.equal(checkFile(file('a.csv', 10)), null);
  assert.match(checkFile(file('a.exe', 10)), /cannot read/);
  assert.match(checkFile(file('noext', 10)), /no file extension/);
  assert.match(checkFile(file('a.csv', 0)), /empty/);
  assert.match(checkFile(file('a.csv', 100), { maxBytes: 50 }), /larger than/);
  assert.match(checkFile(null), /not a file/);
  assert.equal(extensionOf('Report.FINAL.PDF'), 'pdf');
  assert.equal(extensionOf('.hidden'), '');
  assert.equal(kindOfName('x.md'), 'document');
  assert.equal(kindOfName('x.xlsx'), 'dataset');
});

test('chunk ranges cover the file exactly once', () => {
  assert.deepEqual(chunkRanges(10, 4), [[0, 4], [4, 8], [8, 10]]);
  assert.deepEqual(chunkRanges(8, 4), [[0, 4], [4, 8]]);
  assert.deepEqual(chunkRanges(0, 4), []);
});

function fakeApi({ resume = [], duplicate = false, failChunk = null, completeResult } = {}) {
  const calls = { chunks: [], cancelled: [], started: 0, completed: 0 };
  let failures = 0;
  return {
    calls,
    uploads: {
      start: async () => {
        calls.started += 1;
        if (duplicate) return { duplicate: true, asset: { id: 'a0' } };
        return { duplicate: false, upload: { id: 'u1', chunk_size: 4, total_chunks: 3, received_chunks: resume, received_bytes: resume.length * 4 } };
      },
      chunk: async (id, index, blob, { onProgress }) => {
        if (failChunk && index === failChunk.index && failures < failChunk.times) { failures += 1; throw new ApiError(failChunk.error); }
        calls.chunks.push(index);
        onProgress?.(blob.size);
        return { received_chunks: [] };
      },
      complete: async () => { calls.completed += 1; return completeResult ?? { duplicate: false, asset: { id: 'a1', status: 'ready' } }; },
      cancel: async (id) => { calls.cancelled.push(id); return {}; },
    },
  };
}

test('uploads send every part once and then finalise', async () => {
  const api = fakeApi();
  const progress = [];
  const phases = [];
  const result = await runUpload({ api, projectId: 'p', file: file('a.csv', 10), hash: async () => 'abc', onProgress: (d, t) => progress.push([d, t]), onPhase: (p) => phases.push(p), wait: noWait });
  assert.deepEqual(api.calls.chunks, [0, 1, 2]);
  assert.equal(result.asset.id, 'a1');
  assert.deepEqual(phases, ['checking', 'starting', 'uploading', 'processing']);
  assert.deepEqual(progress.at(-1), [10, 10]);
});

test('a resumed upload only sends the missing parts', async () => {
  const api = fakeApi({ resume: [0, 2] });
  await runUpload({ api, projectId: 'p', file: file('a.csv', 10), hash: async () => null, wait: noWait });
  assert.deepEqual(api.calls.chunks, [1]);
});

test('an already-uploaded file is recognised without sending anything', async () => {
  const api = fakeApi({ duplicate: true });
  const result = await runUpload({ api, projectId: 'p', file: file('a.csv', 10), hash: async () => 'abc', wait: noWait });
  assert.equal(result.duplicate, true);
  assert.deepEqual(api.calls.chunks, []);
  assert.equal(api.calls.completed, 0);
});

test('network failures on a part are retried; client mistakes are not', async () => {
  const flaky = fakeApi({ failChunk: { index: 1, times: 2, error: { code: 'NETWORK_ERROR', message: 'x' } } });
  await runUpload({ api: flaky, projectId: 'p', file: file('a.csv', 10), hash: async () => null, wait: noWait });
  assert.deepEqual(flaky.calls.chunks, [0, 1, 2]);
  const broken = fakeApi({ failChunk: { index: 0, times: 5, error: { code: 'CHUNK_SIZE_MISMATCH', message: 'x', status: 400 } } });
  await assert.rejects(runUpload({ api: broken, projectId: 'p', file: file('a.csv', 10), hash: async () => null, wait: noWait }), (e) => e.code === 'CHUNK_SIZE_MISMATCH');
  const down = fakeApi({ failChunk: { index: 0, times: 99, error: { code: 'NETWORK_ERROR', message: 'x' } } });
  await assert.rejects(runUpload({ api: down, projectId: 'p', file: file('a.csv', 10), hash: async () => null, wait: noWait }), (e) => e.code === 'NETWORK_ERROR');
  assert.equal(isRetryable(new ApiError({ code: 'X', message: 'x', status: 503 })), true);
  assert.equal(isRetryable(new ApiError({ code: 'X', message: 'x', status: 413 })), false);
});

test('cancelling asks the server to discard the partial upload', async () => {
  const controller = new AbortController();
  const api = fakeApi();
  const original = api.uploads.chunk;
  api.uploads.chunk = async (...args) => { controller.abort(); throw new DOMException('cancelled', 'AbortError'); };
  await assert.rejects(runUpload({ api, projectId: 'p', file: file('a.csv', 10), hash: async () => null, signal: controller.signal, wait: noWait }), (e) => e.name === 'AbortError');
  assert.deepEqual(api.calls.cancelled, ['u1']);
  assert.ok(original);
});

test('withRetry gives up after the attempts and reports the last error', async () => {
  let tries = 0;
  await assert.rejects(withRetry(async () => { tries += 1; throw new ApiError({ code: 'TIMEOUT', message: 't' }); }, { attempts: 3, wait: noWait }), (e) => e.code === 'TIMEOUT');
  assert.equal(tries, 3);
});

class FakeXhr {
  static last = null;
  constructor() { FakeXhr.last = this; this.headers = {}; this.upload = {}; }
  open(method, url) { this.method = method; this.url = url; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  getResponseHeader(k) { return this.responseHeaders?.[k.toLowerCase()] ?? null; }
  send(body) { this.body = body; }
  abort() { this.onabort?.(); }
  respond(status, payload) { this.status = status; this.responseText = JSON.stringify(payload); this.onload(); }
}

test('client.upload sends raw bytes with CSRF, reports progress and unwraps the envelope', async () => {
  const client = createApiClient({ baseUrl: '/api/v1' });
  client.configure({ getCsrfToken: () => 'tok' });
  const seen = [];
  const promise = client.upload('/uploads/u/chunks/0', { body: 'BYTES', xhrImpl: FakeXhr, onProgress: (a, b) => seen.push([a, b]) });
  const xhr = FakeXhr.last;
  assert.equal(xhr.method, 'POST');
  assert.equal(xhr.url, '/api/v1/uploads/u/chunks/0');
  assert.equal(xhr.headers['X-CSRF-Token'], 'tok');
  assert.equal(xhr.headers['Content-Type'], 'application/octet-stream');
  assert.equal(xhr.withCredentials, true);
  xhr.upload.onprogress({ lengthComputable: true, loaded: 5, total: 10 });
  xhr.respond(200, { data: { ok: 1 }, meta: { request_id: 'r1' } });
  assert.deepEqual(await promise, { data: { ok: 1 }, requestId: 'r1', status: 200 });
  assert.deepEqual(seen, [[5, 10]]);
});

test('client.upload turns failures into ApiError and supports cancel', async () => {
  const client = createApiClient({ baseUrl: '/api/v1' });
  const failing = client.upload('/x', { body: 'b', xhrImpl: FakeXhr });
  FakeXhr.last.respond(413, { error: { code: 'CHUNK_TOO_LARGE', message: 'big', request_id: 'r' } });
  await assert.rejects(failing, (e) => e instanceof ApiError && e.code === 'CHUNK_TOO_LARGE' && e.status === 413);
  const controller = new AbortController();
  const cancelled = client.upload('/x', { body: 'b', xhrImpl: FakeXhr, signal: controller.signal });
  controller.abort();
  await assert.rejects(cancelled, (e) => e.name === 'AbortError');
  const offline = client.upload('/x', { body: 'b', xhrImpl: FakeXhr });
  FakeXhr.last.onerror();
  await assert.rejects(offline, (e) => e.code === 'NETWORK_ERROR');
});

test('cancelling while the start request is in flight cleans up a session the browser never heard about', async () => {
  const controller = new AbortController();
  const cancelled = [];
  const api = {
    uploads: {
      start: async () => { controller.abort(); throw new DOMException('cancelled', 'AbortError'); },
      list: async () => ({ items: [{ id: 'orphan', filename: 'a.csv', size_bytes: 10 }, { id: 'other', filename: 'b.csv', size_bytes: 10 }] }),
      cancel: async (id) => { cancelled.push(id); return {}; },
    },
  };
  await assert.rejects(runUpload({ api, projectId: 'p', file: file('a.csv', 10), hash: async () => null, signal: controller.signal, wait: noWait }), (e) => e.name === 'AbortError');
  assert.deepEqual(cancelled, ['orphan']);
});
