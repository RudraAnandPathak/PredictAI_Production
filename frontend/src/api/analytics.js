// Thin wrappers over the analytics endpoints. Components never build URLs or read raw envelopes; every call returns the unwrapped `data`.
// All numbers come from the server's analysis engine. Nothing in the browser calculates a statistic.
import { queryString } from './workspace.js';

const id = encodeURIComponent;
const LONG = 5 * 60 * 1000;

export const analyticsApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const post = (path, body, timeoutMs = LONG) => data(client.request(path, { method: 'POST', body: body ?? {}, timeoutMs }));
  return {
    schema: (assetId, params) => data(client.request(`/assets/${id(assetId)}/analytics/schema${queryString(params)}`)),
    run: (assetId, body) => post(`/assets/${id(assetId)}/analytics/run`, body),
    preset: (assetId, name, body) => post(`/assets/${id(assetId)}/analytics/presets/${id(name)}`, body),
    hypothesis: (assetId, body) => post(`/assets/${id(assetId)}/analytics/hypothesis`, body),
    runs: (assetId, params) => data(client.request(`/assets/${id(assetId)}/analytics/runs${queryString(params)}`)),
    getRun: (runId) => data(client.request(`/analytics/runs/${id(runId)}`)),
    saveRun: (runId, { saved = true, label } = {}) => post(`/analytics/runs/${id(runId)}/save`, { saved, label: label ?? null }, 30000),
    rerun: (runId) => post(`/analytics/runs/${id(runId)}/rerun`),
    rows: (runId, { select, offset = 0, limit = 50, columns } = {}) => post(`/analytics/runs/${id(runId)}/rows`, { select: select ?? { type: 'all' }, offset, limit, columns: columns ?? null }, 60000),
    exportFile: (runId, body) => client.download(`/analytics/runs/${id(runId)}/export`, { body }),
    generateInsights: (assetId, body) => post(`/assets/${id(assetId)}/insights/generate`, body ?? {}),
    insights: (assetId, params) => data(client.request(`/assets/${id(assetId)}/insights${queryString(params)}`)),
    insight: (insightId) => data(client.request(`/insights/${id(insightId)}`)),
    setInsightState: (insightId, state) => post(`/insights/${id(insightId)}/state`, { state }, 30000),
    ask: (assetId, body) => post(`/assets/${id(assetId)}/assistant/ask`, body, 3 * 60 * 1000),
    conversations: (assetId) => data(client.request(`/assets/${id(assetId)}/assistant/conversations`)),
    conversation: (conversationId) => data(client.request(`/assistant/conversations/${id(conversationId)}`)),
    provider: () => data(client.request('/assistant/provider')),
  };
};
