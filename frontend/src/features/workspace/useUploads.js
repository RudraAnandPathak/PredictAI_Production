import { useCallback, useRef, useState } from 'react';
import { workspace } from '../../api/index.js';
import { isAbortError } from '../../api/errors.js';
import { describeError } from '../identity/validation.js';
import { runUpload } from './uploader.js';

let counter = 0;

/** A small upload queue: files are sent one at a time; each can be cancelled or retried. */
export function useUploads({ projectId, limits, onSettled }) {
  const [items, setItems] = useState([]);
  const itemsRef = useRef([]);
  const controllers = useRef(new Map());
  const running = useRef(false);
  const queue = useRef([]);

  const patch = useCallback((id, changes) => setItems((list) => list.map((it) => (it.id === id ? { ...it, ...changes } : it))), []);

  const drain = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    while (queue.current.length) {
      const id = queue.current.shift();
      const item = itemsRef.current.find((it) => it.id === id);
      if (!item || item.phase === 'cancelled') continue;
      const controller = new AbortController();
      controllers.current.set(id, controller);
      patch(id, { phase: 'checking', error: null, progress: 0 });
      try {
        const result = await runUpload({
          api: workspace, projectId, file: item.file, signal: controller.signal,
          onPhase: (phase) => patch(id, { phase }),
          onProgress: (done, total) => patch(id, { progress: total ? Math.min(100, Math.round((done / total) * 100)) : 0 }),
        });
        const failed = result.asset?.status === 'failed';
        patch(id, {
          phase: failed ? 'failed' : result.duplicate ? 'duplicate' : 'done', progress: 100, asset: result.asset,
          error: failed ? result.asset.status_detail : null, errorCode: failed ? result.asset.status_code : null,
        });
      } catch (error) {
        if (isAbortError(error)) patch(id, { phase: 'cancelled', error: null });
        else patch(id, { phase: 'failed', error: describeError(error), errorCode: error?.code ?? null });
      } finally {
        controllers.current.delete(id);
      }
      onSettled?.();
    }
    running.current = false;
  }, [patch, projectId, onSettled]);

  itemsRef.current = items; // lets the async loop see the latest list without re-creating it on every update

  const add = useCallback((files, checkFile) => {
    const accepted = [];
    const rejected = [];
    for (const file of files) {
      const problem = checkFile(file, limits);
      if (problem) rejected.push({ id: `u${++counter}`, file, name: file.name, size: file.size, phase: 'rejected', error: problem, progress: 0 });
      else accepted.push({ id: `u${++counter}`, file, name: file.name, size: file.size, phase: 'queued', error: null, progress: 0 });
    }
    itemsRef.current = [...itemsRef.current, ...rejected, ...accepted];
    setItems(itemsRef.current);
    queue.current.push(...accepted.map((a) => a.id));
    drain();
  }, [drain, limits]);

  const cancel = useCallback((id) => {
    const controller = controllers.current.get(id);
    if (controller) controller.abort();
    else { queue.current = queue.current.filter((q) => q !== id); patch(id, { phase: 'cancelled' }); }
  }, [patch]);

  const retry = useCallback((id) => {
    patch(id, { phase: 'queued', error: null, errorCode: null, progress: 0 });
    itemsRef.current = itemsRef.current.map((it) => (it.id === id ? { ...it, phase: 'queued', error: null } : it));
    queue.current.push(id);
    drain();
  }, [drain, patch]);

  const dismiss = useCallback((id) => setItems((list) => list.filter((it) => it.id !== id)), []);
  const busy = items.some((it) => ['queued', 'checking', 'starting', 'uploading', 'processing'].includes(it.phase));
  return { items, add, cancel, retry, dismiss, busy };
}
