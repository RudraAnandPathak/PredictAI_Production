import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError, fieldErrors } from '../../features/identity/validation.js';
import { checkDescription, checkProjectName, DESCRIPTION_MAX, NAME_MAX } from '../../features/workspace/format.js';

export function SettingsPanel({ project, onChanged }) {
  const { navigate } = useRouter();
  const [form, setForm] = useState({ name: project.name, description: project.description ?? '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const archived = project.status === 'archived';

  async function save(event) {
    event.preventDefault();
    const found = {};
    const n = checkProjectName(form.name);
    const d = checkDescription(form.description);
    if (n) found.name = n;
    if (d) found.description = d;
    setErrors(found);
    setFailure(null);
    setSaved(false);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      onChanged(await workspace.projects.update(project.id, { name: form.name.trim(), description: form.description.trim() || null }));
      setSaved(true);
    } catch (error) {
      setErrors(fieldErrors(error));
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleArchive() {
    setBusy(true);
    setFailure(null);
    try {
      onChanged(await (archived ? workspace.projects.restore(project.id) : workspace.projects.archive(project.id)));
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="details-h">
        <div className="card__head">
          <h2 className="card__title" id="details-h">Project details</h2>
        </div>
        {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
        {saved ? <FormMessage kind="success" focusOnShow={false}>Saved.</FormMessage> : null}
        <form className="form" onSubmit={save} noValidate>
          <Field label="Project name" name="name" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} error={errors.name} maxLength={NAME_MAX + 20} />
          <Field as="textarea" rows={3} label="Description" name="description" value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} error={errors.description} hint={`Up to ${DESCRIPTION_MAX} characters.`} />
          <div className="form__actions">
            <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Saving" /> : null} Save details</button>
          </div>
        </form>
      </section>

      <section className="card" aria-labelledby="archive-h">
        <div className="card__head">
          <h2 className="card__title" id="archive-h">{archived ? 'Restore project' : 'Archive project'}</h2>
          <p className="card__desc">
            {archived
              ? 'This project is archived and read-only. Restore it to upload or change data again.'
              : 'Archiving keeps everything but makes the project read-only and moves it out of your active list. You can restore it later.'}
          </p>
        </div>
        <div className="card__actions">
          <button type="button" className="btn btn--ghost" disabled={busy} onClick={toggleArchive}>{archived ? 'Restore project' : 'Archive project'}</button>
        </div>
      </section>

      <section className="card danger-zone" aria-labelledby="delete-project-h">
        <div className="card__head">
          <h2 className="card__title" id="delete-project-h">Delete project</h2>
          <p className="card__desc">Permanently removes this project, every uploaded file, all cleaned versions, extracted text and profiles. This cannot be undone.</p>
        </div>
        <div className="card__actions">
          <button type="button" className="btn btn--danger" onClick={() => setConfirming(true)}>Delete this project…</button>
        </div>
      </section>

      {confirming ? (
        <DeleteDialog project={project} onClose={() => setConfirming(false)} onDeleted={() => navigate('/projects?deleted=1', { replace: true })} />
      ) : null}
    </div>
  );
}

function DeleteDialog({ project, onClose, onDeleted }) {
  const [typed, setTyped] = useState('');
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    try {
      const result = await workspace.projects.remove(project.id, typed.trim());
      if (result.deleted === false) setFailure(result.message);
      else onDeleted();
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Delete this project?" onClose={onClose}>
      <p>This permanently deletes <strong>{project.name}</strong> and all of its data. Type the project name to confirm.</p>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <Field label="Project name" name="confirm_name" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" data-autofocus />
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>Keep project</button>
          <button type="submit" className="btn btn--danger" disabled={busy || typed.trim() !== project.name}>
            {busy ? <Spinner label="Deleting" /> : null} Delete permanently
          </button>
        </div>
      </form>
    </Modal>
  );
}
