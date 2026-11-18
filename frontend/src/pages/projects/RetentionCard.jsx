import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { describeError } from '../../features/identity/validation.js';
import { checkRetention, dateInputValue, earliestRetention } from '../../features/workspace/governance.js';

/** Lets the owner choose a date after which the file (and everything derived from it) is deleted automatically. */
export function RetentionCard({ asset, readOnly, onChanged }) {
  const [value, setValue] = useState(dateInputValue(asset.retain_until));
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const [saved, setSaved] = useState(null);
  const error = checkRetention(value);
  const dirty = value !== dateInputValue(asset.retain_until);

  async function save(next) {
    setBusy(true);
    setProblem(null);
    setSaved(null);
    try {
      await workspace.assets.setRetention(asset.id, next);
      setSaved(next ? `This file will be deleted automatically after ${formatDate(`${next}T00:00:00Z`)}.` : 'Automatic deletion is off. The file is kept until you delete it.');
      onChanged?.();
    } catch (e) {
      setProblem(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="retention-h">
      <h2 className="card__title" id="retention-h">Keep for a limited time</h2>
      <p className="muted">
        {asset.retain_until
          ? `Scheduled for automatic deletion after ${formatDate(asset.retain_until)}, together with cleaned versions, extracted text and profiles.`
          : 'Files are kept until you delete them. You can choose a date after which this one is removed automatically.'}
      </p>
      {readOnly ? <p className="muted">Restore the project to change this.</p> : (
        <form className="toolbar" onSubmit={(e) => { e.preventDefault(); if (!error) save(value); }} noValidate>
          <Field label="Delete automatically after" name="retain_until" type="date" value={value} min={earliestRetention()} error={error} onChange={(e) => { setValue(e.target.value); setSaved(null); }} />
          <button type="submit" className="btn" disabled={busy || !dirty || !value || !!error}>{busy ? <Spinner label="Saving" /> : null} Save date</button>
          {asset.retain_until ? <button type="button" className="btn btn--ghost" disabled={busy} onClick={() => { setValue(''); save(null); }}>Keep until I delete it</button> : null}
        </form>
      )}
      {problem ? <FormMessage kind="error">{problem}</FormMessage> : null}
      {saved ? <FormMessage kind="success" focusOnShow={false}>{saved}</FormMessage> : null}
    </section>
  );
}
