// Thin wrappers over the Phase 6 endpoints: research, world intelligence (news), AI providers, evidence reuse. Components never build URLs or read raw envelopes; every call returns the
// unwrapped `data`. Every statement, label and figure comes from the server; nothing in the browser searches, ranks, summarises or judges a source.
import { queryString } from './workspace.js';

const id = encodeURIComponent;
const LONG = 60 * 1000;

export const researchApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const post = (path, body, timeoutMs = 30000) => data(client.request(path, { method: 'POST', body: body ?? {}, timeoutMs }));
  const patch = (path, body, timeoutMs = 30000) => data(client.request(path, { method: 'PATCH', body, timeoutMs }));
  const del = (path, timeoutMs = 60000) => data(client.request(path, { method: 'DELETE', timeoutMs }));
  const get = (path, params, timeoutMs) => data(client.request(`${path}${queryString(params)}`, timeoutMs ? { timeoutMs } : {}));
  return {
    documentStatus: (projectId) => data(client.request(`/projects/${id(projectId)}/documents/index`)),
    buildIndex: (assetId, force = false) => post(`/assets/${id(assetId)}/index`, { force }, LONG),
    searchDocuments: (projectId, body) => post(`/projects/${id(projectId)}/documents/search`, body, LONG),
    compareDocuments: (projectId, body) => post(`/projects/${id(projectId)}/documents/compare`, body, LONG),
    settings: (projectId) => get('/research/settings', projectId ? { project_id: projectId } : undefined),
    putSettings: (changes, projectId) => patch('/research/settings', { ...changes, ...(projectId ? { project_id: projectId } : {}) }),
    capabilities: (projectId) => get(`/projects/${id(projectId)}/research/capabilities`),
    createSession: (projectId, body) => post(`/projects/${id(projectId)}/research/sessions`, body),
    sessions: (projectId, params) => get(`/projects/${id(projectId)}/research/sessions`, params),
    context: (projectId, body) => post(`/projects/${id(projectId)}/research/context`, body),
    search: (projectId, q) => get(`/projects/${id(projectId)}/research/search`, { q }),
    sources: (projectId, params) => get(`/projects/${id(projectId)}/research/sources`, params),
    bundles: (projectId, params) => get(`/projects/${id(projectId)}/research/bundles`, params),
    jobs: (projectId, params) => get(`/projects/${id(projectId)}/research/jobs`, params),
    cache: (projectId) => get(`/projects/${id(projectId)}/research/cache`),
    clearCache: (projectId) => del(`/projects/${id(projectId)}/research/cache`),
    session: (sessionId) => get(`/research/sessions/${id(sessionId)}`),
    updateSession: (sessionId, changes) => patch(`/research/sessions/${id(sessionId)}`, changes),
    deleteSession: (sessionId) => del(`/research/sessions/${id(sessionId)}`),
    followUp: (sessionId, body) => post(`/research/sessions/${id(sessionId)}/follow-up`, body),
    diagnoseProviders: (projectId, provider) => post(`/projects/${id(projectId)}/research/providers/diagnose`, provider ? { provider } : {}, LONG),
    providerSearch: (projectId, body) => post(`/projects/${id(projectId)}/research/providers/search`, body, LONG),
    providerOpen: (projectId, body) => post(`/projects/${id(projectId)}/research/providers/open`, body, LONG),
    approve: (sessionId, planHash) => post(`/research/sessions/${id(sessionId)}/approve`, { plan_hash: planHash }),
    run: (sessionId, approvePlanHash) => post(`/research/sessions/${id(sessionId)}/run`, approvePlanHash ? { approve_plan_hash: approvePlanHash } : {}),
    notes: (sessionId) => get(`/research/sessions/${id(sessionId)}/notes`),
    addNote: (sessionId, body, target) => post(`/research/sessions/${id(sessionId)}/notes`, { body, ...(target ?? {}) }),
    updateNote: (noteId, body) => patch(`/research/notes/${id(noteId)}`, { body }),
    deleteNote: (noteId) => del(`/research/notes/${id(noteId)}`),
    job: (jobId) => get(`/research/jobs/${id(jobId)}`, undefined, 30000),
    cancelJob: (jobId) => post(`/research/jobs/${id(jobId)}/cancel`, {}),
    compare: (a, b) => get('/research/compare', { a, b }),
    bundle: (bundleId) => get(`/research/bundles/${id(bundleId)}`),
    updateBundle: (bundleId, changes) => patch(`/research/bundles/${id(bundleId)}`, changes),
    deleteBundle: (bundleId) => del(`/research/bundles/${id(bundleId)}`),
    summary: (bundleId) => get(`/research/bundles/${id(bundleId)}/summary`),
    citation: (bundleId, evidenceId) => get(`/research/bundles/${id(bundleId)}/evidence/${id(evidenceId)}`),
    exportBundle: (bundleId, format) => client.download(`/research/bundles/${id(bundleId)}/export${queryString({ format })}`, { method: 'GET', timeoutMs: LONG }),
    source: (sourceId) => get(`/research/sources/${id(sourceId)}`),
    saveSource: (sourceId, saved) => patch(`/research/sources/${id(sourceId)}`, { saved }),
    evidence: (projectId, params) => get(`/projects/${id(projectId)}/evidence`, params),
    examCapabilities: () => get('/exam-research/capabilities'),
    examTopic: (projectId, body) => post(`/projects/${id(projectId)}/exam-research/topic`, body),
  };
};

export const worldApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const post = (path, body, timeoutMs = 30000) => data(client.request(path, { method: 'POST', body: body ?? {}, timeoutMs }));
  const patch = (path, body) => data(client.request(path, { method: 'PATCH', body, timeoutMs: 30000 }));
  const del = (path) => data(client.request(path, { method: 'DELETE', timeoutMs: 60000 }));
  const get = (path, params) => data(client.request(`${path}${queryString(params)}`));
  const base = (projectId) => `/projects/${id(projectId)}/world`;
  return {
    overview: (projectId) => get(`${base(projectId)}/overview`),
    topics: (projectId) => get(`${base(projectId)}/topics`),
    trends: (projectId, windowDays) => get(`${base(projectId)}/trends`, { window_days: windowDays }),
    freshness: (projectId) => get(`${base(projectId)}/freshness`),
    sources: (projectId) => get(`${base(projectId)}/sources`),
    addSource: (projectId, body) => post(`${base(projectId)}/sources`, body),
    updateSource: (sourceId, changes) => patch(`/world/sources/${id(sourceId)}`, changes),
    deleteSource: (sourceId) => del(`/world/sources/${id(sourceId)}`),
    ingest: (projectId, sourceIds) => post(`${base(projectId)}/ingest`, sourceIds ? { source_ids: sourceIds } : {}),
    articles: (projectId, params) => get(`${base(projectId)}/articles`, params),
    article: (articleId) => get(`/world/articles/${id(articleId)}`),
    deleteArticle: (articleId) => del(`/world/articles/${id(articleId)}`),
    events: (projectId, params) => get(`${base(projectId)}/events`, params),
    rebuild: (projectId) => post(`${base(projectId)}/events/rebuild`, {}),
    event: (eventId) => get(`/world/events/${id(eventId)}`),
    eventSummary: (eventId) => get(`/world/events/${id(eventId)}/summary`),
    influences: (projectId) => get(`${base(projectId)}/influence`),
    startInfluence: (projectId, body) => post(`${base(projectId)}/influence`, body),
    influence: (analysisId) => get(`/world/influence/${id(analysisId)}`),
    deleteInfluence: (analysisId) => del(`/world/influence/${id(analysisId)}`),
  };
};

export const aiApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const post = (path, body) => data(client.request(path, { method: 'POST', body: body ?? {}, timeoutMs: 30000 }));
  return {
    providers: () => data(client.request('/ai/providers')),
    usage: (days) => data(client.request(`/ai/usage${queryString({ days })}`)),
    create: (body) => post('/ai/providers', body),
    update: (key, changes) => data(client.request(`/ai/providers/${id(key)}`, { method: 'PATCH', body: changes, timeoutMs: 30000 })),
    setEnabled: (key, enabled, confirmPaid = false) => post(`/ai/providers/${id(key)}/enabled`, { enabled, confirm_paid: confirmPaid }),
    remove: (key) => data(client.request(`/ai/providers/${id(key)}`, { method: 'DELETE', timeoutMs: 30000 })),
    probe: (key) => post(`/ai/providers/${id(key)}/probe`, {}),
  };
};
