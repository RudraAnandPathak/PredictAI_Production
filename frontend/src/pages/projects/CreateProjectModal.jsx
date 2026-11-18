import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { checkDescription, checkProjectName, DESCRIPTION_MAX, NAME_MAX } from '../../features/workspace/format.js';
import { describeError, fieldErrors } from '../../features/identity/validation.js';

export function CreateProjectModal({ onClose, onCreated }) {
  const [form, setForm] = useState({ name: '', description: '' });
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  async function submit(event) {
    event.preventDefault();
    const found = {};
    const nameProblem = checkProjectName(form.name);
    const descProblem = checkDescription(form.description);
    if (nameProblem) found.name = nameProblem;
    if (descProblem) found.description = descProblem;
    setErrors(found);
    setFailure(null);
    if (Object.keys(found).length) return;
    setBusy(true);
    try {
      const project = await workspace.projects.create({ name: form.name.trim(), description: form.description.trim() });
      onCreated(project);
    } catch (error) {
      setErrors(fieldErrors(error));
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Create a project" onClose={onClose}>
      <p className="muted">A project keeps related datasets and documents together. Only you can see it.</p>
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      <form className="form" onSubmit={submit} noValidate>
        <Field label="Project name" name="name" value={form.name} onChange={set('name')} error={errors.name} maxLength={NAME_MAX + 20} required data-autofocus />
        <Field as="textarea" rows={3} label="Description (optional)" name="description" value={form.description} onChange={set('description')}
          error={errors.description} hint={`What is this project for? Up to ${DESCRIPTION_MAX} characters.`} />
        <div className="form__actions">
          <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Creating" /> : null} Create project</button>
        </div>
      </form>
    </Modal>
  );
}
