// Thin wrappers over /api/v1/notebooks (Studio outputs). Sources, chat and notes live in the research-intelligence API: a notebook is one of its workspaces.
const id = encodeURIComponent;

export const notebookApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const call = (method, path, body, timeoutMs = 60000) => data(client.request(`/notebooks${path}`, { method, body: body ?? undefined, timeoutMs }));
  return {
    kinds: () => call('GET', '/studio/kinds'),
    generate: (nid, kind, instructions, sourceIds) => call('POST', `/${id(nid)}/studio`, { kind, instructions: instructions || null, source_ids: sourceIds && sourceIds.length ? sourceIds : null }, 240000),
    list: (nid) => call('GET', `/${id(nid)}/studio`),
    get: (oid) => call('GET', `/outputs/${id(oid)}`),
    remove: (oid) => call('DELETE', `/outputs/${id(oid)}`),
    // Resolves { blob, filename }. Formats: markdown (any), csv (table, flashcards, quiz), pptx (slides).
    download: (oid, format) => client.download(`/notebooks/outputs/${id(oid)}/export?format=${id(format)}`, { method: 'GET', timeoutMs: 120000 }),
    deepResearch: (nid, question, maxSources) => call('POST', `/${id(nid)}/deep-research`, { question, max_sources: maxSources }, 240000),
    legacy: () => call('GET', '/legacy'),
    importLegacy: () => call('POST', '/legacy/import', {}, 300000),
  };
};
