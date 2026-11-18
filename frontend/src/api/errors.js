export class ApiError extends Error {
  constructor({ code, message, status = null, requestId = null, details = null, retryAfter = null }) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
    this.retryAfter = retryAfter;
  }

  /** True when the server could not be reached at all (as opposed to answering with an error). */
  get isConnectionProblem() {
    return this.code === 'NETWORK_ERROR' || this.code === 'TIMEOUT';
  }
}

export function isAbortError(error) {
  return Boolean(error) && error.name === 'AbortError';
}
