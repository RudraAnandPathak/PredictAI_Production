import { useState } from 'react';
import { predictive } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { modelTitle } from '../../features/predictive/format.js';
import { Notice } from './shared.jsx';

/** Rename and delete a model. Both are server-checked (owner only, project must be writable); this only asks and reports. */
export function RenameModal({ model, onClose, onDone }) {
  const [name, setName] = useState(model.name ?? '');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  async function submit(event) {
    event.preventDefault();
    if (!name.trim()) { setFailure('Enter a name.'); return; }
    setBusy(true); setFailure('');
    try { onDone(await predictive.renameModel(model.id, name.trim())); } catch (e) { setFailure(describeError(e)); setBusy(false); }
  }
  return (
    <Modal title="Rename this model" onClose={onClose}>
      <form className="form" onSubmit={submit} noValidate>
        {failure ? <Notice kind="error">{failure}</Notice> : null}
        <Field label="Model name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} data-autofocus />
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Saving" /> : null} Save name</button>
        </div>
      </form>
    </Modal>
  );
}

export function DeleteModelModal({ model, onClose, onDone }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  async function confirm() {
    setBusy(true); setFailure('');
    try { await predictive.deleteModel(model.id); onDone(model); } catch (e) { setFailure(describeError(e)); setBusy(false); }
  }
  return (
    <Modal title="Delete this model?" onClose={onClose}>
      <p><strong>{modelTitle(model)}</strong> will be deleted together with its training report and every prediction made with it. Your dataset is not affected. This cannot be undone.</p>
      {failure ? <Notice kind="error">{failure}</Notice> : null}
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" onClick={onClose} data-autofocus>Keep it</button>
        <button type="button" className="btn btn--danger" disabled={busy} onClick={confirm}>{busy ? <Spinner label="Deleting" /> : null} Delete model</button>
      </div>
    </Modal>
  );
}
