import { useId, useRef, useState } from 'react';
import { ACCEPT, checkFile, SUPPORTED_HINT } from '../../features/workspace/uploadRules.js';
import { formatBytes } from '../../features/workspace/format.js';
import { Spinner } from '../../components/ui/Spinner.jsx';

const PHASE_TEXT = {
  queued: 'Waiting', checking: 'Preparing', starting: 'Starting', uploading: 'Uploading', processing: 'Reading the file',
  done: 'Added', duplicate: 'Already in this project', failed: 'Could not be added', cancelled: 'Cancelled', rejected: 'Not uploaded',
};
const ACTIVE = ['queued', 'checking', 'starting', 'uploading', 'processing'];

export function UploadZone({ uploads, limits, quota }) {
  const inputId = useId();
  const input = useRef(null);
  const [over, setOver] = useState(false);

  function pick(fileList) {
    const files = [...fileList];
    if (files.length) uploads.add(files, checkFile);
    if (input.current) input.current.value = '';
  }

  return (
    <section className="card" aria-labelledby="upload-h">
      <div className="card__head">
        <h2 className="card__title" id="upload-h">Add files</h2>
        <p className="card__desc">{SUPPORTED_HINT} Your originals are never changed.</p>
      </div>
      {quota ? (
        <p className="muted" id="quota-note">
          {quota.unlimited
            ? `Special access: no limit on documents. Files up to ${quota.max_file_mb} MB.`
            : `Documents used: ${quota.documents_used} of ${quota.max_documents} (up to ${quota.max_pages_per_document} pages each). Datasets are not counted. Files up to ${quota.max_file_mb} MB.`}
        </p>
      ) : null}
      <div
        className={`dropzone${over ? ' dropzone--over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files); }}
      >
        <p><strong>Drag files here</strong> or</p>
        <label className="btn" htmlFor={inputId}>Choose files</label>
        <input id={inputId} ref={input} className="sr-only" type="file" multiple accept={ACCEPT} aria-describedby="quota-note"
          onChange={(e) => pick(e.target.files)} />
      </div>

      {uploads.items.length > 0 ? (
        <ul className="rows" aria-live="polite" aria-label="Uploads">
          {uploads.items.map((it) => (
            <li className="row upload" key={it.id}>
              <div className="row__main">
                <span className="row__title">{it.name} <span className="muted">({formatBytes(it.size)})</span></span>
                <span className="row__meta">
                  {ACTIVE.includes(it.phase) ? <Spinner label={PHASE_TEXT[it.phase]} /> : null} {PHASE_TEXT[it.phase]}
                  {it.phase === 'uploading' ? ` — ${it.progress}%` : ''}
                </span>
                {it.phase === 'uploading' || it.phase === 'processing' ? (
                  <progress max="100" value={it.phase === 'processing' ? undefined : it.progress} aria-label={`${it.name}: ${PHASE_TEXT[it.phase]}`} />
                ) : null}
                {it.error ? <span className="row__meta upload__error" role="alert">{it.error}</span> : null}
                {it.phase === 'duplicate' ? <span className="row__meta">This exact file was already uploaded, so nothing new was created.</span> : null}
              </div>
              <div className="row__actions">
                {ACTIVE.includes(it.phase) ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => uploads.cancel(it.id)}>Cancel<span className="sr-only"> {it.name}</span></button> : null}
                {it.phase === 'failed' && it.file && !['UNSUPPORTED_FILE', 'FILE_CORRUPTED', 'PAGE_LIMIT_EXCEEDED', 'DOCUMENT_LIMIT_REACHED', 'FILE_TOO_LARGE'].includes(it.errorCode)
                  ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => uploads.retry(it.id)}>Try again<span className="sr-only"> {it.name}</span></button> : null}
                {!ACTIVE.includes(it.phase) ? <button type="button" className="link-btn" onClick={() => uploads.dismiss(it.id)}>Dismiss<span className="sr-only"> {it.name}</span></button> : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
