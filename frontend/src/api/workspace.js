// Thin wrappers over the workspace endpoints (projects, data assets, uploads, profiling, cleaning).
// Components never build URLs or read raw envelopes; every call returns the unwrapped `data`.
const id = encodeURIComponent;

export function queryString(params = {}) {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') usp.set(k, String(v));
  const s = usp.toString();
  return s ? `?${s}` : '';
}

export const workspaceApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  return {
    quota: () => data(client.request('/workspace/quota')),
    deletions: () => data(client.request('/workspace/deletions')),
    inventory: (params) => data(client.request(`/workspace/inventory${queryString(params)}`)),
    projects: {
      list: (params) => data(client.request(`/projects${queryString(params)}`)),
      create: ({ name, description, metadata }) =>
        data(client.request('/projects', { method: 'POST', body: { name, description: description || null, metadata: metadata ?? null } })),
      get: (projectId) => data(client.request(`/projects/${id(projectId)}`)),
      update: (projectId, changes) => data(client.request(`/projects/${id(projectId)}`, { method: 'PATCH', body: changes })),
      archive: (projectId) => data(client.request(`/projects/${id(projectId)}/archive`, { method: 'POST' })),
      restore: (projectId) => data(client.request(`/projects/${id(projectId)}/restore`, { method: 'POST' })),
      remove: (projectId, confirmName) =>
        data(client.request(`/projects/${id(projectId)}/delete`, { method: 'POST', body: { confirm_name: confirmName } })),
      activity: (projectId, params) => data(client.request(`/projects/${id(projectId)}/activity${queryString(params)}`)),
    },
    uploads: {
      start: (projectId, { filename, size, checksum }) =>
        data(client.request(`/projects/${id(projectId)}/uploads`, { method: 'POST', body: { filename, size, checksum_sha256: checksum || null } })),
      list: (projectId) => data(client.request(`/projects/${id(projectId)}/uploads`)),
      status: (uploadId) => data(client.request(`/uploads/${id(uploadId)}`)),
      chunk: (uploadId, index, blob, { signal, onProgress } = {}) =>
        data(client.upload(`/uploads/${id(uploadId)}/chunks/${index}`, { body: blob, signal, onProgress })),
      // Finalising validates and reads the whole file, so it may legitimately take a while for scanned documents.
      complete: (uploadId, { signal } = {}) =>
        data(client.request(`/uploads/${id(uploadId)}/complete`, { method: 'POST', timeoutMs: 15 * 60 * 1000, signal })),
      cancel: (uploadId) => data(client.request(`/uploads/${id(uploadId)}`, { method: 'DELETE', silent: true })),
    },
    assets: {
      list: (projectId, params) => data(client.request(`/projects/${id(projectId)}/assets${queryString(params)}`)),
      get: (assetId) => data(client.request(`/assets/${id(assetId)}`)),
      remove: (assetId) => data(client.request(`/assets/${id(assetId)}`, { method: 'DELETE' })),
      downloadUrl: (assetId) => client.url(`/assets/${id(assetId)}/download`),
      extraction: (assetId, params) => data(client.request(`/assets/${id(assetId)}/extraction${queryString(params)}`)),
      preview: (assetId, params) => data(client.request(`/assets/${id(assetId)}/preview${queryString(params)}`)),
      pages: (assetId, params) => data(client.request(`/assets/${id(assetId)}/pages${queryString(params)}`)),
      profile: (assetId, params) => data(client.request(`/assets/${id(assetId)}/profile${queryString(params)}`)),
      createProfile: (assetId, params) =>
        data(client.request(`/assets/${id(assetId)}/profile${queryString(params)}`, { method: 'POST', timeoutMs: 120000 })),
      cleaningSuggestions: (assetId) => data(client.request(`/assets/${id(assetId)}/cleaning/suggestions`)),
      previewCleaning: (assetId, operations) =>
        data(client.request(`/assets/${id(assetId)}/cleaning/preview`, { method: 'POST', body: { operations }, timeoutMs: 5 * 60 * 1000 })),
      confirmCleaning: (transformationId) =>
        data(client.request(`/cleaning/${id(transformationId)}/confirm`, { method: 'POST', body: { confirm: true }, timeoutMs: 5 * 60 * 1000 })),
      discardCleaning: (transformationId) => data(client.request(`/cleaning/${id(transformationId)}/discard`, { method: 'POST', silent: true })),
      history: (assetId) => data(client.request(`/assets/${id(assetId)}/history`)),
      rollback: (assetId, versionId) => data(client.request(`/assets/${id(assetId)}/rollback`, { method: 'POST', body: { version_id: versionId, confirm: true } })),
      footprint: (assetId) => data(client.request(`/assets/${id(assetId)}/footprint`)),
      setRetention: (assetId, retainUntil) =>
        data(client.request(`/assets/${id(assetId)}/retention`, { method: 'PATCH', body: { retain_until: retainUntil || null } })),
      structure: (assetId, params) => data(client.request(`/assets/${id(assetId)}/structure${queryString(params)}`)),
    },
  };
};
