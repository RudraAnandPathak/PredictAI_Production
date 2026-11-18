import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ConfigError, loadConfig } from '../src/app/configSchema.js';

test('defaults are sensible for local development', () => {
  const c = loadConfig({});
  assert.equal(c.appName, 'PredictAI');
  assert.equal(c.apiBaseUrl, '/api/v1');
  assert.equal(c.apiTimeoutMs, 8000);
  assert.equal(c.healthPollIntervalMs, 30000);
});

test('accepts a relative path or an absolute http(s) URL and trims trailing slashes', () => {
  assert.equal(loadConfig({ VITE_API_BASE_URL: '/api/v1/' }).apiBaseUrl, '/api/v1');
  assert.equal(loadConfig({ VITE_API_BASE_URL: 'https://api.example.com/api/v1//' }).apiBaseUrl, 'https://api.example.com/api/v1');
  assert.equal(loadConfig({ VITE_API_BASE_URL: 'http://127.0.0.1:8000' }).apiBaseUrl, 'http://127.0.0.1:8000');
});

test('rejects a base URL that is neither a path nor an http(s) URL', () => {
  for (const bad of ['api/v1', 'ftp://x.test', 'javascript:alert(1)', '']) {
    assert.throws(() => loadConfig({ VITE_API_BASE_URL: bad }), ConfigError, bad);
  }
});

test('timeout must be an integer within bounds', () => {
  assert.equal(loadConfig({ VITE_API_TIMEOUT_MS: '1500' }).apiTimeoutMs, 1500);
  for (const bad of ['100', '99999', 'soon', '1.5']) {
    assert.throws(() => loadConfig({ VITE_API_TIMEOUT_MS: bad }), /VITE_API_TIMEOUT_MS/, bad);
  }
});

test('health polling can be switched off with 0 but not set below 5 seconds', () => {
  assert.equal(loadConfig({ VITE_HEALTH_POLL_INTERVAL_MS: '0' }).healthPollIntervalMs, 0);
  assert.equal(loadConfig({ VITE_HEALTH_POLL_INTERVAL_MS: '60000' }).healthPollIntervalMs, 60000);
  assert.throws(() => loadConfig({ VITE_HEALTH_POLL_INTERVAL_MS: '1000' }), /VITE_HEALTH_POLL_INTERVAL_MS/);
});

test('every problem is reported together and the result is immutable', () => {
  assert.throws(
    () => loadConfig({ VITE_API_BASE_URL: 'nope', VITE_API_TIMEOUT_MS: 'x' }),
    (error) => error instanceof ConfigError && error.problems.length === 2,
  );
  const c = loadConfig({});
  assert.throws(() => {
    'use strict';
    c.apiBaseUrl = 'x';
  }, TypeError);
});
