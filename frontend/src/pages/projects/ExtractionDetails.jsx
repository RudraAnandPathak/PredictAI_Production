import { workspace } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { RetentionCard } from './RetentionCard.jsx';
import { columnQuality, confidenceTone, describePage, typeLabel } from '../../features/workspace/extraction.js';

export function ExtractionDetails({ asset, ready, readOnly = false, onChanged }) {
  const ext = useResource(() => (ready ? workspace.assets.extraction(asset.id) : Promise.resolve(null)), [asset.id, ready]);
  if (ext.loading && !ext.data && ready) return <PageLoading label="Loading details…" />;
  if (ext.error) return <ErrorState title="Could not load the details" message={describeError(ext.error)} onRetry={() => ext.reload()} />;
  const e = ext.data;
  return (
    <section aria-label="File details" className="stack">
      <dl className="kv">
        <dt>Original checksum (SHA-256)</dt><dd><code className="cell-clip">{asset.checksum_sha256}</code></dd>
        <dt>Versions</dt><dd>{asset.version_list?.length ?? 1} (the original is never modified)</dd>
        {e ? <><dt>Read with</dt><dd>{e.parser} v{e.parser_version} on {formatDate(e.created_at)}</dd></> : null}
        <dt>Retention</dt><dd>{asset.retain_until ? `Deleted automatically after ${formatDate(asset.retain_until)}` : 'Kept until you delete it'}</dd>
      </dl>
      <RetentionCard asset={asset} readOnly={readOnly} onChanged={onChanged} />
      {!e ? <p className="muted">Nothing was extracted from this file.</p> : null}
      {e?.warnings?.length ? (
        <div className="message message--warn" role="status">
          <p className="message__title">Things to check</p>
          <ul className="message__body">{e.warnings.map((w, i) => <li key={i}>{w.message}</li>)}</ul>
        </div>
      ) : null}
      {e && asset.kind === 'dataset' ? <ColumnTable columns={e.summary.columns} rows={e.summary.rows} /> : null}
      {e && asset.kind === 'document' ? <PageTable ext={e} /> : null}
      {e ? <p className="muted">Counts are exact. Column types are a heuristic guess: a type is chosen when at least 90% of the non-empty values fit it, otherwise the column is shown as Mixed.</p> : null}
    </section>
  );
}

function ColumnTable({ columns, rows }) {
  return (
    <div className="table-wrap" tabIndex={0}>
      <table className="table">
        <caption>Columns ({columns.length}) — {rows.toLocaleString()} rows</caption>
        <thead><tr><th scope="col">Column</th><th scope="col">Type (guessed)</th><th scope="col">Empty</th><th scope="col">Don’t fit type</th><th scope="col">Filled</th></tr></thead>
        <tbody>
          {columns.map((c) => {
            const q = columnQuality(c, rows);
            return (
              <tr key={c.index}>
                <th scope="row">{c.name}{c.original_name ? <span className="row__meta">was “{c.original_name}”</span> : null}</th>
                <td>{typeLabel(c.type)}</td><td>{c.missing.toLocaleString()}</td><td>{c.invalid.toLocaleString()}</td>
                <td><span className="bar-track" role="img" aria-label={`${q.filledPct}% filled`}><span className="bar" style={{ width: `${q.filledPct}%` }} /></span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PageTable({ ext }) {
  const s = ext.summary;
  return (
    <>
      <div className="stat-grid">
        <div className="stat"><span className="stat__value">{s.pages}{s.page_count_estimated ? '*' : ''}</span><span className="stat__label">{s.page_count_estimated ? 'Sections (estimated)' : 'Pages'}</span></div>
        <div className="stat"><span className="stat__value">{s.characters.toLocaleString()}</span><span className="stat__label">Characters</span></div>
        <div className="stat"><span className="stat__value">{s.headings}</span><span className="stat__label">Headings</span></div>
        <div className="stat"><span className="stat__value">{s.tables}</span><span className="stat__label">Tables</span></div>
        {s.ocr_mean_confidence != null ? <div className="stat"><span className="stat__value">{Math.round(s.ocr_mean_confidence)}%</span><span className="stat__label">Mean OCR confidence</span></div> : null}
      </div>
      <div className="table-wrap" tabIndex={0}>
        <table className="table">
          <caption>Pages and how their text was obtained</caption>
          <thead><tr><th scope="col">Page</th><th scope="col">How it was read</th><th scope="col">Characters</th></tr></thead>
          <tbody>
            {(ext.pages ?? []).map((p) => (
              <tr key={p.number}>
                <th scope="row">{p.number}</th>
                <td><span className={`badge ${p.method === 'ocr' ? confidenceTone(p.ocr_confidence) ?? 'badge--warn' : p.method === 'none' ? 'badge--danger' : 'badge--muted'}`}>{describePage(p)}</span>{p.warning ? <span className="row__meta">{p.warning}</span> : null}</td>
                <td>{p.characters.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
