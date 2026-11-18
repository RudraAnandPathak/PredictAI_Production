import { isAbortError } from '../../api/errors.js';
import { chunkRanges } from './uploadRules.js';

const MAX_HASH_BYTES = 256 * 1024 * 1024;

/** SHA-256 of a file as lowercase hex, or null when the browser cannot (insecure page, very large file). */
export async function sha256Hex(file) {
  try {
    if (!globalThis.crypto?.subtle || file.size > MAX_HASH_BYTES) return null;
    const digest = await globalThis.crypto.subtle.digest('SHA-256', await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return null;
  }
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('cancelled', 'AbortError')); }, { once: true });
});

export const isRetryable = (error) => error?.code === 'NETWORK_ERROR' || error?.code === 'TIMEOUT' || (error?.status >= 500 && error?.status < 600);

export async function withRetry(task, { attempts = 3, delays = [500, 1500], signal, wait = sleep } = {}) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await task(attempt);
    } catch (error) {
      if (isAbortError(error) || !isRetryable(error) || attempt >= attempts) throw error;
      await wait(delays[Math.min(attempt - 1, delays.length - 1)], signal);
    }
  }
}

async function discardMatching(api, projectId, file) {
  try {
    const { items } = await api.uploads.list(projectId);
    await Promise.all(items.filter((u) => u.filename === file.name && u.size_bytes === file.size).map((u) => api.uploads.cancel(u.id)));
  } catch {
    // Best effort: an unfinished session is listed under "Unfinished uploads" and expires on its own.
  }
}

/**
 * Upload one file: start (or resume) a session, send only the parts the server does not have, then finalise.
 * Cancelling asks the server to discard what was sent. Calling it again for the same file resumes.
 * Returns { duplicate, asset }.
 */
export async function runUpload({ api, projectId, file, signal, onPhase = () => {}, onProgress = () => {}, hash = sha256Hex, wait }) {
  onPhase('checking');
  const checksum = await hash(file);
  if (signal?.aborted) throw new DOMException('cancelled', 'AbortError');
  onPhase('starting');
  let started;
  try {
    started = await withRetry(() => api.uploads.start(projectId, { filename: file.name, size: file.size, checksum }), { signal, wait });
  } catch (error) {
    // Cancelled while the "start" request was in flight: the server may have created the session without us learning its id.
    if (isAbortError(error)) await discardMatching(api, projectId, file);
    throw error;
  }
  if (started.duplicate) return { duplicate: true, asset: started.asset };
  const { upload } = started;
  const have = new Set(upload.received_chunks);
  let confirmed = upload.received_bytes;
  onProgress(confirmed, file.size);
  try {
    onPhase('uploading');
    const ranges = chunkRanges(file.size, upload.chunk_size);
    for (let index = 0; index < ranges.length; index += 1) {
      if (have.has(index)) continue;
      const [from, to] = ranges[index];
      await withRetry(
        () => api.uploads.chunk(upload.id, index, file.slice(from, to), { signal, onProgress: (loaded) => onProgress(confirmed + loaded, file.size) }),
        { signal, wait },
      );
      confirmed += to - from;
      onProgress(confirmed, file.size);
    }
    onPhase('processing');
    return await api.uploads.complete(upload.id, { signal });
  } catch (error) {
    if (isAbortError(error)) {
      await api.uploads.cancel(upload.id).catch(() => {});
    }
    throw error;
  }
}
