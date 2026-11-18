import { ApiError } from './errors.js';

export function joinUrl(base, path) {
  const left = String(base ?? '').replace(/\/+$/, '');
  const right = String(path ?? '').replace(/^\/+/, '');
  return `${left}/${right}`;
}

function abortError() {
  return new DOMException('The request was cancelled.', 'AbortError');
}

/**
 * Small JSON client for the PredictAI API envelope:
 *   success -> { data, meta: { request_id } }
 *   failure -> { error: { code, message, request_id, details? } }
 * Every failure is thrown as an ApiError with a stable `code`; raw server text is never shown.
 * A caller-initiated abort is re-thrown as an AbortError so callers can ignore it.
 */
export function createApiClient({ baseUrl, timeoutMs = 8000, fetchImpl } = {}) {
  const doFetch = fetchImpl ?? ((...args) => globalThis.fetch(...args));
  // Set once by the identity layer: where to read the CSRF token from, and who to tell about failures.
  const hooks = { getCsrfToken: () => null, onError: null };
  const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

  async function send(path, { method = 'GET', body, signal, timeoutMs: perCall } = {}) {
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, perCall ?? timeoutMs);
    const forwardAbort = () => controller.abort();
    if (signal) {
      if (signal.aborted) controller.abort();
      else signal.addEventListener('abort', forwardAbort, { once: true });
    }
    const timeoutError = () => new ApiError({ code: 'TIMEOUT', message: 'The server took too long to respond.' });

    try {
      let response;
      try {
        const headers = { Accept: 'application/json' };
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        if (!SAFE.has(method)) {
          const csrf = hooks.getCsrfToken();
          if (csrf) headers['X-CSRF-Token'] = csrf;
        }
        // 'include' so the session cookie also travels when the API lives on another origin in production.
        response = await doFetch(joinUrl(baseUrl, path), {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          credentials: 'include',
          signal: controller.signal,
        });
      } catch {
        if (timedOut) throw timeoutError();
        if (signal?.aborted) throw abortError();
        throw new ApiError({ code: 'NETWORK_ERROR', message: 'Could not reach the server.' });
      }

      const headerRequestId = response.headers?.get?.('x-request-id') ?? null;
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        if (timedOut) throw timeoutError();
        if (signal?.aborted) throw abortError();
      }

      if (!response.ok) {
        const err = payload && typeof payload === 'object' ? payload.error : null;
        throw new ApiError({
          code: typeof err?.code === 'string' ? err.code : 'HTTP_ERROR',
          message: typeof err?.message === 'string' ? err.message : 'The request could not be completed.',
          status: response.status,
          requestId: typeof err?.request_id === 'string' ? err.request_id : headerRequestId,
          details: Array.isArray(err?.details) ? err.details : null,
          retryAfter: Number(response.headers?.get?.('retry-after')) || null,
        });
      }
      if (!payload || typeof payload !== 'object' || !('data' in payload)) {
        throw new ApiError({
          code: 'INVALID_RESPONSE',
          message: 'The server sent a response this app cannot read.',
          status: response.status,
          requestId: headerRequestId,
        });
      }
      const metaId = payload.meta && typeof payload.meta.request_id === 'string' ? payload.meta.request_id : null;
      return { data: payload.data, requestId: metaId ?? headerRequestId, status: response.status };
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener?.('abort', forwardAbort);
    }
  }

  /** Same as send(), but tells the registered error hook about API failures (unless `silent`). */
  async function request(path, options = {}) {
    try {
      return await send(path, options);
    } catch (error) {
      if (!options.silent && error instanceof ApiError) hooks.onError?.(error, { path, method: options.method ?? 'GET' });
      throw error;
    }
  }

  /** Binary upload with progress and cancel (fetch cannot report upload progress). Same envelope and error handling as request(). */
  function upload(path, { body, headers = {}, signal, onProgress, timeoutMs: perCall, xhrImpl, silent = false } = {}) {
    return new Promise((resolve, reject) => {
      const Xhr = xhrImpl ?? globalThis.XMLHttpRequest;
      const xhr = new Xhr();
      const fail = (error) => {
        if (!silent && error instanceof ApiError) hooks.onError?.(error, { path, method: 'POST' });
        reject(error);
      };
      xhr.open('POST', joinUrl(baseUrl, path));
      xhr.withCredentials = true;
      xhr.timeout = perCall ?? 120000;
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      const csrf = hooks.getCsrfToken();
      if (csrf) xhr.setRequestHeader('X-CSRF-Token', csrf);
      for (const [name, value] of Object.entries(headers)) xhr.setRequestHeader(name, value);
      if (xhr.upload) xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress?.(event.loaded, event.total); };
      xhr.onload = () => {
        let payload = null;
        try { payload = JSON.parse(xhr.responseText); } catch { /* handled below */ }
        const requestId = xhr.getResponseHeader?.('x-request-id') ?? null;
        if (xhr.status >= 200 && xhr.status < 300 && payload && typeof payload === 'object' && 'data' in payload) {
          resolve({ data: payload.data, requestId: payload.meta?.request_id ?? requestId, status: xhr.status });
          return;
        }
        const err = payload && typeof payload === 'object' ? payload.error : null;
        fail(new ApiError({
          code: typeof err?.code === 'string' ? err.code : (xhr.status >= 200 && xhr.status < 300 ? 'INVALID_RESPONSE' : 'HTTP_ERROR'),
          message: typeof err?.message === 'string' ? err.message : 'The request could not be completed.',
          status: xhr.status, requestId: typeof err?.request_id === 'string' ? err.request_id : requestId,
          details: Array.isArray(err?.details) ? err.details : null,
          retryAfter: Number(xhr.getResponseHeader?.('retry-after')) || null,
        }));
      };
      xhr.onerror = () => fail(new ApiError({ code: 'NETWORK_ERROR', message: 'Could not reach the server.' }));
      xhr.ontimeout = () => fail(new ApiError({ code: 'TIMEOUT', message: 'The server took too long to respond.' }));
      xhr.onabort = () => reject(abortError());
      if (signal) {
        if (signal.aborted) { reject(abortError()); return; }
        signal.addEventListener('abort', () => xhr.abort(), { once: true });
      }
      xhr.send(body);
    });
  }

  /** Request that returns a file (an export; POST by default, GET for read-only downloads). Resolves { blob, filename }; failures are ApiErrors like request(). */
  async function download(path, { body, signal, timeoutMs: perCall, method = 'POST' } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), perCall ?? 60000);
    const forward = () => controller.abort();
    signal?.addEventListener?.('abort', forward, { once: true });
    try {
      const reading = method === 'GET';
      const headers = reading ? { Accept: '*/*' } : { Accept: '*/*', 'Content-Type': 'application/json' };
      const csrf = hooks.getCsrfToken();
      if (csrf && !reading) headers['X-CSRF-Token'] = csrf;
      let response;
      try {
        response = await doFetch(joinUrl(baseUrl, path), { method, headers, ...(reading ? {} : { body: JSON.stringify(body ?? {}) }), credentials: 'include', signal: controller.signal });
      } catch {
        throw signal?.aborted ? abortError() : new ApiError({ code: 'NETWORK_ERROR', message: 'Could not reach the server.' });
      }
      if (!response.ok) {
        let err = null;
        try { err = (await response.json())?.error; } catch { /* not JSON */ }
        const error = new ApiError({
          code: typeof err?.code === 'string' ? err.code : 'HTTP_ERROR',
          message: typeof err?.message === 'string' ? err.message : 'The download could not be completed.',
          status: response.status, requestId: typeof err?.request_id === 'string' ? err.request_id : null,
          details: Array.isArray(err?.details) ? err.details : null, retryAfter: Number(response.headers?.get?.('retry-after')) || null,
        });
        hooks.onError?.(error, { path, method });
        throw error;
      }
      const disposition = response.headers?.get?.('content-disposition') ?? '';
      const match = /filename="([^"]+)"/.exec(disposition);
      return { blob: await response.blob(), filename: match ? match[1] : 'download' };
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener?.('abort', forward);
    }
  }

  const url = (path) => joinUrl(baseUrl, path);

  function configure(next = {}) {
    if ('getCsrfToken' in next) hooks.getCsrfToken = next.getCsrfToken ?? (() => null);
    if ('onError' in next) hooks.onError = next.onError ?? null;
  }

  return { request, upload, download, url, configure };
}
