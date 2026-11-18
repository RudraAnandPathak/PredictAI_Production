// Pure configuration parsing (no import.meta here, so it is testable in Node).

export class ConfigError extends Error {
  constructor(problems) {
    super(`Invalid frontend configuration:\n- ${problems.join('\n- ')}`);
    this.name = 'ConfigError';
    this.problems = problems;
  }
}

const DEFAULTS = Object.freeze({ apiBaseUrl: '/api/v1', apiTimeoutMs: 8000, healthPollIntervalMs: 30000 });
const ABSOLUTE_URL = /^https?:\/\/[^\s/]+(\/\S*)?$/i;

function readInteger(name, raw, fallback, problems, valid, rule) {
  if (raw === undefined || raw === null || String(raw).trim() === '') return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || !valid(value)) {
    problems.push(`${name} must be ${rule} (got ${JSON.stringify(raw)}).`);
    return fallback;
  }
  return value;
}

export function loadConfig(env = {}) {
  const problems = [];

  const rawBase = String(env.VITE_API_BASE_URL ?? DEFAULTS.apiBaseUrl).trim();
  if (!(rawBase.startsWith('/') || ABSOLUTE_URL.test(rawBase))) {
    problems.push(`VITE_API_BASE_URL must be a path like /api/v1 or an http(s) URL (got ${JSON.stringify(rawBase)}).`);
  }
  const apiBaseUrl = rawBase.replace(/\/+$/, '');

  const apiTimeoutMs = readInteger(
    'VITE_API_TIMEOUT_MS', env.VITE_API_TIMEOUT_MS, DEFAULTS.apiTimeoutMs, problems,
    (n) => n >= 500 && n <= 60000, 'an integer from 500 to 60000',
  );
  const healthPollIntervalMs = readInteger(
    'VITE_HEALTH_POLL_INTERVAL_MS', env.VITE_HEALTH_POLL_INTERVAL_MS, DEFAULTS.healthPollIntervalMs, problems,
    (n) => n === 0 || (n >= 5000 && n <= 3600000), '0 (off) or an integer from 5000 to 3600000',
  );

  if (problems.length) throw new ConfigError(problems);
  const isDevelopment = env.DEV === true || env.DEV === 'true';
  return Object.freeze({ appName: 'PredictAI', apiBaseUrl, apiTimeoutMs, healthPollIntervalMs, isDevelopment });
}
