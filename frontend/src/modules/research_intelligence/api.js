// Thin wrappers over /api/v1/research-intelligence. Every call returns the unwrapped `data`; components never build URLs.
// Ownership, limits and the AI data policy are decided by the server on every request; nothing here is a security boundary.
const id = encodeURIComponent;
const BASE = '/research-intelligence';

export const researchIntelligenceApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const call = (method, path, body, timeoutMs = 60000) => data(client.request(`${BASE}${path}`, { method, body: body ?? undefined, timeoutMs }));
  const get = (path) => data(client.request(`${BASE}${path}`, { timeoutMs: 60000 }));
  const W = (wid) => `/workspaces/${id(wid)}`;
  return {
    limits: () => get('/limits'),
    workspaces: {
      list: () => get('/workspaces'),
      create: (name, description) => call('POST', '/workspaces', { name, description: description || null }),
      overview: (wid) => get(W(wid)),
      update: (wid, body) => call('PATCH', W(wid), body),
      remove: (wid) => call('DELETE', W(wid)),
      settings: (wid) => get(`${W(wid)}/settings`),
      setPolicy: (wid, aiDataPolicy, confirm = false) => call('PUT', `${W(wid)}/settings`, { ai_data_policy: aiDataPolicy, confirm }),
    },
    sources: {
      list: (wid) => get(`${W(wid)}/sources`),
      get: (wid, sid) => get(`${W(wid)}/sources/${id(sid)}`),
      // The file travels as raw bytes (the server caps the size while reading); the name goes in the query string.
      upload: (wid, file, { signal, onProgress } = {}) => data(client.upload(`${BASE}${W(wid)}/sources/upload?filename=${id(file.name)}`, { body: file, signal, onProgress, timeoutMs: 15 * 60 * 1000 })),
      addReference: (wid, reference) => call('POST', `${W(wid)}/sources/reference`, { reference }),
      setIncluded: (wid, sid, included) => call('PATCH', `${W(wid)}/sources/${id(sid)}`, { included }),
      rename: (wid, sid, title) => call('PATCH', `${W(wid)}/sources/${id(sid)}`, { title }),
      remove: (wid, sid) => call('DELETE', `${W(wid)}/sources/${id(sid)}`),
      reindex: (wid, sid) => call('POST', `${W(wid)}/sources/${id(sid)}/reindex`, {}),
      chunks: (wid, sid, offset = 0) => get(`${W(wid)}/sources/${id(sid)}/chunks?offset=${offset}`),
      passage: (wid, chunkId) => get(`${W(wid)}/passages/${id(chunkId)}`),
    },
    jobs: {
      list: (wid) => get(`${W(wid)}/jobs`),
      cancel: (jobId) => call('POST', `/jobs/${id(jobId)}/cancel`, {}),
      retry: (jobId) => call('POST', `/jobs/${id(jobId)}/retry`, {}),
    },
    search: (wid, query, mode = 'hybrid') => call('POST', `${W(wid)}/search`, { query, mode }),
    conversations: {
      list: (wid) => get(`${W(wid)}/conversations`),
      create: (wid, title) => call('POST', `${W(wid)}/conversations`, { title: title || null }),
      get: (wid, cid) => get(`${W(wid)}/conversations/${id(cid)}`),
      rename: (wid, cid, title) => call('PATCH', `${W(wid)}/conversations/${id(cid)}`, { title }),
      remove: (wid, cid) => call('DELETE', `${W(wid)}/conversations/${id(cid)}`),
      setScope: (wid, cid, sourceId, mode) => call('PUT', `${W(wid)}/conversations/${id(cid)}/scope`, { source_id: sourceId, mode }),
    },
    ask: (wid, question, conversationId, sourceIds) => call('POST', `${W(wid)}/ask`, { question, conversation_id: conversationId || null, source_ids: sourceIds && sourceIds.length ? sourceIds : null }, 180000),
    notes: {
      list: (wid) => get(`${W(wid)}/notes`),
      create: (wid, body, sourceId) => call('POST', `${W(wid)}/notes`, { body, source_id: sourceId || null }),
      update: (wid, noteId, body) => call('PATCH', `${W(wid)}/notes/${id(noteId)}`, { body }),
      remove: (wid, noteId) => call('DELETE', `${W(wid)}/notes/${id(noteId)}`),
    },
    findings: {
      list: (wid) => get(`${W(wid)}/findings`),
      create: (wid, sourceId, chunkId, kind = 'finding', comment = null) => call('POST', `${W(wid)}/findings`, { source_id: sourceId, chunk_id: chunkId, kind, comment }),
      remove: (wid, findingId) => call('DELETE', `${W(wid)}/findings/${id(findingId)}`),
      patch: (wid, findingId, body) => call('PATCH', `${W(wid)}/findings/${id(findingId)}`, body),
    },
    home: () => get('/home'),
    activity: (wid) => get(`${W(wid)}/activity`),
    discovery: {
      providers: () => get('/discovery/providers'),
      search: (body) => call('POST', '/discovery/search', body, 120000),
      searches: () => get('/discovery/searches'),
      results: (searchId, query = '') => get(`/discovery/searches/${id(searchId)}${query ? `?${query}` : ''}`),
      removeSearch: (searchId) => call('DELETE', `/discovery/searches/${id(searchId)}`),
      clearHistory: () => call('DELETE', '/discovery/searches'),
      save: (searchId, rid) => call('POST', '/discovery/library', { search_id: searchId, rid }),
      library: () => get('/discovery/library'),
      libraryItem: (lid) => get(`/discovery/library/${id(lid)}`),
      removeLibrary: (lid) => call('DELETE', `/discovery/library/${id(lid)}`),
      compare: (ids) => call('POST', '/discovery/compare', { ids }),
      importTo: (wid, libraryId, fullText = false) => call('POST', `${W(wid)}/discovery/import`, { library_id: libraryId, full_text: fullText }),
    },
    analyses: {
      list: (wid) => get(`${W(wid)}/analyses`),
      get: (wid, aid) => get(`${W(wid)}/analyses/${id(aid)}`),
      start: (wid, body) => call('POST', `${W(wid)}/analyses`, body),
      cancel: (wid, aid) => call('POST', `${W(wid)}/analyses/${id(aid)}/cancel`, {}),
      remove: (wid, aid) => call('DELETE', `${W(wid)}/analyses/${id(aid)}`),
    },
    studio: {
      evidence: (wid) => get(`${W(wid)}/studio/evidence`),
      links: (wid) => get(`${W(wid)}/studio/links`),
      link: (wid, fromId, toId, relation, note) => call('POST', `${W(wid)}/studio/links`, { from_id: fromId, to_id: toId, relation, note: note || null }),
      unlink: (wid, linkId) => call('DELETE', `${W(wid)}/studio/links/${id(linkId)}`),
      groups: (wid) => get(`${W(wid)}/studio/groups`),
      createGroup: (wid, name) => call('POST', `${W(wid)}/studio/groups`, { name }),
      renameGroup: (wid, gid, name) => call('PATCH', `${W(wid)}/studio/groups/${id(gid)}`, { name }),
      setGroupItems: (wid, gid, findingIds) => call('PUT', `${W(wid)}/studio/groups/${id(gid)}/items`, { finding_ids: findingIds }),
      removeGroup: (wid, gid) => call('DELETE', `${W(wid)}/studio/groups/${id(gid)}`),
      outlines: (wid) => get(`${W(wid)}/studio/outlines`),
      outline: (wid, oid) => get(`${W(wid)}/studio/outlines/${id(oid)}`),
      createOutline: (wid, title, items) => call('POST', `${W(wid)}/studio/outlines`, { title, items }),
      updateOutline: (wid, oid, body) => call('PATCH', `${W(wid)}/studio/outlines/${id(oid)}`, body),
      removeOutline: (wid, oid) => call('DELETE', `${W(wid)}/studio/outlines/${id(oid)}`),
      generate: (wid, kind, analysisId, sourceIds) => call('POST', `${W(wid)}/studio/generate`, { kind, analysis_id: analysisId, source_ids: sourceIds ?? null }),
      generated: (wid) => get(`${W(wid)}/studio/generated`),
      generatedItem: (wid, gid) => get(`${W(wid)}/studio/generated/${id(gid)}`),
      removeGenerated: (wid, gid) => call('DELETE', `${W(wid)}/studio/generated/${id(gid)}`),
    },
    reports: {
      types: () => get('/report-types'),
      all: () => get('/reports'),
      list: (wid) => get(`${W(wid)}/reports`),
      create: (wid, body) => call('POST', `${W(wid)}/reports`, body),
      get: (wid, rid) => get(`${W(wid)}/reports/${id(rid)}`),
      update: (wid, rid, body) => call('PATCH', `${W(wid)}/reports/${id(rid)}`, body),
      remove: (wid, rid) => call('DELETE', `${W(wid)}/reports/${id(rid)}`),
      regenerate: (wid, rid, body = {}) => call('POST', `${W(wid)}/reports/${id(rid)}/regenerate`, body),
      versions: (wid, rid) => get(`${W(wid)}/reports/${id(rid)}/versions`),
      restore: (wid, rid, vid) => call('POST', `${W(wid)}/reports/${id(rid)}/versions/${id(vid)}/restore`, {}),
      // Resolves { blob, filename }; the page turns it into a download. Formats: pdf, docx, pptx, md.
      export: (wid, rid, format) => client.download(`${BASE}${W(wid)}/reports/${id(rid)}/export?format=${id(format)}`, { method: 'GET', timeoutMs: 120000 }),
    },
  };
};
