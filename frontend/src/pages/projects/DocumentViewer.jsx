import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { confidenceTone, describePage } from '../../features/workspace/extraction.js';

export function DocumentViewer({ asset }) {
  const [page, setPage] = useState(1);
  const result = useResource(() => workspace.assets.pages(asset.id, { start: page, limit: 1 }), [asset.id, page]);
  const structure = useResource(() => workspace.assets.structure(asset.id), [asset.id]);
  if (result.loading && !result.data) return <PageLoading label="Loading text…" />;
  if (result.error) return <ErrorState title="Could not load the text" message={describeError(result.error)} onRetry={() => result.reload()} />;
  const d = result.data;
  const current = d.pages[0];
  const heads = structure.data?.headings?.filter((h) => h.page === page) ?? [];
  const tables = structure.data?.tables?.filter((t) => t.page === page) ?? [];
  const label = d.page_count_estimated ? 'Section' : 'Page';
  return (
    <section aria-label="Document text" className="stack">
      <div className="form__actions">
        <button type="button" className="btn btn--ghost" disabled={page <= 1 || result.loading} onClick={() => setPage(page - 1)}>Previous {label.toLowerCase()}</button>
        <span role="status" aria-live="polite">{label} {page} of {d.page_count}{d.page_count_estimated ? ' (estimated — this format has no fixed pages)' : ''}</span>
        <button type="button" className="btn btn--ghost" disabled={page >= d.page_count || result.loading} onClick={() => setPage(page + 1)}>Next {label.toLowerCase()}</button>
      </div>
      {current ? (
        <>
          <p className="pill-row">
            <span className="badge badge--muted">Source: {current.ref.type === 'page' ? `page ${current.ref.page} of the original` : `blocks ${current.ref.from}–${current.ref.to}`}</span>
            <span className={`badge ${current.method === 'ocr' ? confidenceTone(current.ocr_confidence) ?? 'badge--warn' : 'badge--muted'}`}>{describePage(current)}</span>
          </p>
          {current.warning ? <p className="muted">{current.warning}</p> : null}
          {heads.length ? <p className="muted">Headings here{heads.some((h) => h.heuristic) ? ' (guessed from line shape)' : ''}: {heads.map((h) => h.text).join(' · ')}</p> : null}
          <pre className="text-page" tabIndex={0} aria-label={`${label} ${page} text`}>{current.text || '(no text on this page)'}</pre>
          {tables.map((t, i) => (
            <div className="table-wrap" key={i} tabIndex={0}>
              <table className="table"><caption>Table on this {label.toLowerCase()}{t.truncated ? ` (first 50 of ${t.row_count} rows)` : ''}</caption>
                <tbody>{t.rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => (ri === 0 ? <th scope="col" key={ci}>{c}</th> : <td key={ci}>{c}</td>))}</tr>)}</tbody>
              </table>
            </div>
          ))}
        </>
      ) : <p className="muted">There is nothing to show for this {label.toLowerCase()}.</p>}
    </section>
  );
}
