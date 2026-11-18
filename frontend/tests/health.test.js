import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { fetchHealth, parseHealth } from '../src/api/health.js';
import { ApiError } from '../src/api/errors.js';

const contract = JSON.parse(readFileSync(new URL('../../shared/contracts/health.example.json', import.meta.url), 'utf8'));
const asResult = () => ({ data: structuredClone(contract.data), requestId: contract.meta.request_id });

test('the shared contract example parses into the normalised health object', () => {
  const health = parseHealth(asResult());
  assert.equal(health.status, 'ok');
  assert.equal(health.service, 'predictai-api');
  assert.equal(health.version, '0.1.0');
  assert.equal(health.uptimeSeconds, 0);
  assert.equal(health.requestId, contract.meta.request_id);
});

test('parseHealth rejects payloads that break the contract', () => {
  const broken = [
    (r) => { r.data.status = 'degraded'; },
    (r) => { delete r.data.version; },
    (r) => { r.data.service = ''; },
    (r) => { r.data.uptime_seconds = '12'; },
    (r) => { r.data = null; },
  ];
  for (const mutate of broken) {
    const result = asResult();
    mutate(result);
    assert.throws(() => parseHealth(result), (error) => error instanceof ApiError && error.code === 'INVALID_RESPONSE');
  }
});

test('fetchHealth asks the client for /health and parses the answer', async () => {
  let path;
  const client = { request: async (p) => { path = p; return asResult(); } };
  const health = await fetchHealth(client);
  assert.equal(path, '/health');
  assert.equal(health.environment, 'development');
});
