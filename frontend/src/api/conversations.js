// Conversations hub: one list and one chat over the dataset assistant and the research assistant. Components never build URLs; every answer, label and
// citation comes from the server.
import { queryString } from './workspace.js';

const id = encodeURIComponent;

export const conversationsApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const base = '/conversations';
  return {
    list: (params) => data(client.request(`${base}${queryString(params)}`)),
    targets: () => data(client.request(`${base}/targets`)),
    create: (kind, targetId, title) => data(client.request(base, { method: 'POST', body: { kind, target_id: targetId, ...(title ? { title } : {}) } })),
    get: (kind, conversationId) => data(client.request(`${base}/${id(kind)}/${id(conversationId)}`)),
    send: (kind, conversationId, text, extra = {}) => data(client.request(`${base}/${id(kind)}/${id(conversationId)}/messages`, { method: 'POST', body: { text, ...extra }, timeoutMs: 120000 })),
    rename: (kind, conversationId, title) => data(client.request(`${base}/${id(kind)}/${id(conversationId)}`, { method: 'PATCH', body: { title } })),
    remove: (kind, conversationId) => data(client.request(`${base}/${id(kind)}/${id(conversationId)}`, { method: 'DELETE' })),
    exportMarkdown: (kind, conversationId) => client.download(`${base}/${id(kind)}/${id(conversationId)}/export`, { method: 'GET' }),
  };
};
