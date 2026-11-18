import { useState } from 'react';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { DeleteModelModal, RenameModal } from './ModelActions.jsx';
import { ModelRow } from './shared.jsx';

const MAX_COMPARE = 5;

/** A list of saved models with selection for comparison, rename and delete. `onChanged()` asks the parent to reload. */
export function ModelsList({ items, onChanged, readOnly = false, compareHint = true }) {
  const { navigate } = useRouter();
  const [selected, setSelected] = useState([]);
  const [renaming, setRenaming] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const toggle = (id) => setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= MAX_COMPARE ? cur : [...cur, id]));
  return (
    <div className="stack">
      <ul className="list" aria-label="Saved models">
        {items.map((m) => (
          <ModelRow key={m.id} model={m} selected={selected.includes(m.id)} onToggle={toggle}
            extra={readOnly ? null : (
              <div className="btn-row">
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setRenaming(m)}>Rename</button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeleting(m)}>Delete</button>
              </div>
            )} />
        ))}
      </ul>
      <div className="btn-row">
        <button type="button" className="btn" disabled={selected.length < 2} onClick={() => navigate(`/predictions/compare?ids=${selected.map(encodeURIComponent).join(',')}`)}>Compare {selected.length >= 2 ? `${selected.length} models` : 'selected models'}</button>
        {compareHint ? <span className="row__meta">Tick two to {MAX_COMPARE} models that predict the same target from the same dataset.</span> : null}
      </div>
      {renaming ? <RenameModal model={renaming} onClose={() => setRenaming(null)} onDone={() => { setRenaming(null); onChanged?.(); }} /> : null}
      {deleting ? <DeleteModelModal model={deleting} onClose={() => setDeleting(null)} onDone={(m) => { setDeleting(null); setSelected((cur) => cur.filter((x) => x !== m.id)); onChanged?.(); }} /> : null}
    </div>
  );
}
