import { ApiError } from './errors.js';

const isText = (value) => typeof value === 'string' && value.length > 0;

/** Validate the health payload against shared/contracts/health.example.json. */
export function parseHealth(result) {
  const d = result?.data;
  const valid =
    d && typeof d === 'object' && d.status === 'ok' &&
    isText(d.service) && isText(d.version) && isText(d.environment) && isText(d.timestamp) &&
    typeof d.uptime_seconds === 'number' && Number.isFinite(d.uptime_seconds);
  if (!valid) {
    throw new ApiError({
      code: 'INVALID_RESPONSE',
      message: 'The health endpoint answered with an unexpected format.',
      requestId: result?.requestId ?? null,
    });
  }
  return Object.freeze({
    status: d.status,
    service: d.service,
    version: d.version,
    environment: d.environment,
    timestamp: d.timestamp,
    uptimeSeconds: d.uptime_seconds,
    requestId: result.requestId ?? null,
  });
}

export async function fetchHealth(client, { signal } = {}) {
  return parseHealth(await client.request('/health', { signal }));
}
