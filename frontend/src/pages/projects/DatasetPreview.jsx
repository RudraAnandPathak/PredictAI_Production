import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { pageWindow, typeLabel } from '../../features/workspace/extraction.js';

const PAGE = 50;

export function DatasetPreview({ asset }) {
  const [offset, setOffset] = useState(0);
  const preview = useResource(() => workspace.assets.preview(asset.id, { offset, limit: PAGE }), [asset.id, offset]);
  if (preview.loading && !preview.data) return <PageLoading label="Loading rows…" />;
  if (preview.error) return <ErrorState title="Could not load the rows" message={describeError(preview.error)} onRetry={() => preview.reload()} />;
  const d = preview.data;
  const w = pageWindow(offset, PAGE, d.total_rows);
  return (
    <section aria-label="Data preview" className="stack">
      <p className="muted" role="status">Rows {w.from.toLocaleString()}–{w.to.toLocaleString()} of {(d.total_rows ?? 0).toLocaleString()}. This is a read-only view; your original file is never changed.</p>
      <div className="table-wrap" tabIndex={0} aria-label="Scrollable data table">
        <table className="table">
          <caption className="sr-only">Preview of {asset.name}</caption>
          <thead><tr><th scope="col"><span className="sr-only">Row</span></th>{d.columns.map((c) => <th scope="col" key={c.index ?? c.name}>{c.name}<span className="row__meta">{typeLabel(c.type)}</span></th>)}</tr></thead>
          <tbody>
            {d.rows.map((row, i) => (
              <tr key={offset + i}>
                <th scope="row" className="row__meta">{offset + i + 1}</th>
                {d.columns.map((c, j) => <td key={j} className="cell-clip" title={row[j] ?? ''}>{row[j] === '' || row[j] == null ? <span className="row__meta">empty</span> : row[j]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" disabled={!w.hasPrev || preview.loading} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous rows</button>
        <button type="button" className="btn btn--ghost" disabled={!w.hasNext || preview.loading} onClick={() => setOffset(offset + PAGE)}>Next rows</button>
      </div>
    </section>
  );
}
