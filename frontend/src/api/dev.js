// Thin wrappers over the Development Workspace endpoints (/api/v1/dev). Every call returns the unwrapped `data`; components never build URLs.
// Access (Special access, project ownership) is decided by the server on every request; nothing here is a security boundary.
import { queryString } from './workspace.js';

const id = encodeURIComponent;

export const devApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const call = (method, path, body, timeoutMs = 60000) => data(client.request(path, { method, body: body ?? undefined, timeoutMs }));
  const get = (path, params) => data(client.request(`${path}${queryString(params)}`, { timeoutMs: 60000 }));
  const P = (projectId) => `/dev/projects/${id(projectId)}`;
  return {
    overview: () => get('/dev/overview'),
    history: (projectId, params) => get(`${P(projectId)}/history`, params),
    timeline: (projectId) => get(`${P(projectId)}/timeline`),
    files: {
      list: (projectId) => get(`${P(projectId)}/files`),
      read: (projectId, path, version) => get(`${P(projectId)}/files/content`, { path, version }),
      write: (projectId, path, content, expectedVersion) => call('PUT', `${P(projectId)}/files/content`, { path, content, expected_version: expectedVersion ?? null }),
      remove: (projectId, path, confirm) => call('POST', `${P(projectId)}/files/delete`, { path, confirm }),
      restore: (projectId, path, version) => call('POST', `${P(projectId)}/files/restore`, { path, version }),
      history: (projectId, path) => get(`${P(projectId)}/files/history`, { path }),
      search: (projectId, q) => get(`${P(projectId)}/files/search`, { q }),
    },
    notebooks: {
      create: (projectId, path, title) => call('POST', `${P(projectId)}/notebooks`, { path, title: title || null }),
      info: (projectId, path) => get(`${P(projectId)}/notebooks/info`, { path }),
      exportAs: (projectId, path, format, stripOutputs = false) => call('POST', `${P(projectId)}/notebooks/export`, { path, format, strip_outputs: stripOutputs }),
    },
    sessions: {
      status: (projectId) => get(`${P(projectId)}/sessions`),
      start: (projectId, kind) => call('POST', `${P(projectId)}/sessions`, { kind }),
      touch: (projectId, sessionId) => call('POST', `${P(projectId)}/sessions/${id(sessionId)}/touch`, {}),
      stop: (projectId, sessionId) => call('POST', `${P(projectId)}/sessions/${id(sessionId)}/stop`, {}),
    },
    envs: {
      list: (projectId) => get(`${P(projectId)}/envs`),
      get: (projectId, envId) => get(`${P(projectId)}/envs/${id(envId)}`),
      create: (projectId, body) => call('POST', `${P(projectId)}/envs`, body),
      addPackage: (projectId, envId, pkg) => call('POST', `${P(projectId)}/envs/${id(envId)}/packages`, { package: pkg }),
      removePackage: (projectId, envId, pkg) => call('POST', `${P(projectId)}/envs/${id(envId)}/packages/remove`, { package: pkg }),
      rebuild: (projectId, envId) => call('POST', `${P(projectId)}/envs/${id(envId)}/rebuild`, {}),
      remove: (projectId, envId, confirm) => call('POST', `${P(projectId)}/envs/${id(envId)}/delete`, { confirm }),
    },
    jobs: {
      list: (projectId, params) => get(`${P(projectId)}/jobs`, params),
      get: (projectId, jobId) => get(`${P(projectId)}/jobs/${id(jobId)}`),
      logs: (projectId, jobId) => get(`${P(projectId)}/jobs/${id(jobId)}/logs`),
      submit: (projectId, body) => call('POST', `${P(projectId)}/jobs`, body),
      cancel: (projectId, jobId) => call('POST', `${P(projectId)}/jobs/${id(jobId)}/cancel`, {}),
      retry: (projectId, jobId) => call('POST', `${P(projectId)}/jobs/${id(jobId)}/retry`, {}),
      artifacts: (projectId, jobId) => get(`${P(projectId)}/jobs/${id(jobId)}/artifacts`),
    },
    secrets: {
      list: (projectId) => get(`${P(projectId)}/secrets`),
      create: (projectId, name, value) => call('POST', `${P(projectId)}/secrets`, { name, value }),
      revoke: (projectId, secretId) => call('POST', `${P(projectId)}/secrets/${id(secretId)}/revoke`, {}),
    },
    artifacts: {
      summary: (projectId) => get(`${P(projectId)}/artifacts/summary`),
      list: (projectId, params) => get(`${P(projectId)}/artifacts`, params),
      read: (projectId, artifactId) => get(`${P(projectId)}/artifacts/${id(artifactId)}`),
      remove: (projectId, artifactId, confirm) => call('POST', `${P(projectId)}/artifacts/${id(artifactId)}/delete`, { confirm }),
      retention: (projectId, artifactId, days) => call('POST', `${P(projectId)}/artifacts/${id(artifactId)}/retention`, { days }),
    },
    git: {
      status: (projectId) => get(`${P(projectId)}/git`),
      init: (projectId) => call('POST', `${P(projectId)}/git/init`, {}),
      connect: (projectId, body) => call('POST', `${P(projectId)}/git/connect`, body),
      disconnect: (projectId) => call('POST', `${P(projectId)}/git/disconnect`, {}),
      log: (projectId) => get(`${P(projectId)}/git/log`),
      branches: (projectId) => get(`${P(projectId)}/git/branches`),
      tags: (projectId) => get(`${P(projectId)}/git/tags`),
      diff: (projectId) => get(`${P(projectId)}/git/diff`),
      commit: (projectId, message, paths) => call('POST', `${P(projectId)}/git/commit`, { message, paths: paths ?? null }),
      createBranch: (projectId, name, switchTo = true) => call('POST', `${P(projectId)}/git/branches`, { name, switch: switchTo }),
      switchBranch: (projectId, name) => call('POST', `${P(projectId)}/git/branches/switch`, { name }),
      pull: (projectId) => call('POST', `${P(projectId)}/git/pull`, {}),
      push: (projectId, branch) => call('POST', `${P(projectId)}/git/push`, { branch: branch || null }),
    },
    pipelines: {
      list: (projectId) => get(`${P(projectId)}/pipelines`),
      create: (projectId, body) => call('POST', `${P(projectId)}/pipelines`, body),
      run: (projectId, pipelineId) => call('POST', `${P(projectId)}/pipelines/${id(pipelineId)}/run`, {}),
      runs: (projectId) => get(`${P(projectId)}/pipeline-runs`),
      cancelRun: (projectId, runId) => call('POST', `${P(projectId)}/pipeline-runs/${id(runId)}/cancel`, {}),
      approve: (projectId, runId, note) => call('POST', `${P(projectId)}/pipeline-runs/${id(runId)}/approve`, { reason: note || null }),
      reject: (projectId, runId, note) => call('POST', `${P(projectId)}/pipeline-runs/${id(runId)}/reject`, { reason: note || null }),
    },
    experiments: {
      list: (projectId) => get(`${P(projectId)}/experiments`),
      get: (projectId, experimentId) => get(`${P(projectId)}/experiments/${id(experimentId)}`),
      create: (projectId, body) => call('POST', `${P(projectId)}/experiments`, body),
      run: (projectId, experimentId, entry) => call('POST', `${P(projectId)}/experiments/${id(experimentId)}/run`, { entry }),
      compare: (projectId, ids) => call('POST', `${P(projectId)}/experiments/compare`, { ids }),
      lineage: (projectId, experimentId) => get(`${P(projectId)}/experiments/${id(experimentId)}/lineage`),
    },
    compute: {
      limits: (projectId) => get(`${P(projectId)}/compute/limits`),
      usage: (projectId) => get(`${P(projectId)}/compute/usage`),
      capabilities: () => get('/dev/compute/capabilities'),
      overrides: (params) => get('/dev/owner/compute-overrides', params),
      setOverride: (body) => call('PUT', '/dev/owner/compute-overrides', body),
      clearOverride: (body) => call('POST', '/dev/owner/compute-overrides/clear', body),
    },
  };
};
