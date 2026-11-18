import { useState } from 'react';
import { svgToString } from '../../features/analytics/chartMath.js';
import { formatNumber } from '../../features/analytics/format.js';

/** Resolve CSS variables so an exported SVG looks the same outside the app. */
function inlineVars(text) {
  const styles = getComputedStyle(document.documentElement);
  return text.replace(/var\((--[\w-]+)\)/g, (_, name) => styles.getPropertyValue(name).trim() || '#888');
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Title, toolbar, the SVG (as children) and the chart's own data table. Every chart can show and export the numbers it draws. */
export function ChartFrame({ title, subtitle, table, svgRef, actions, legend, caption, children }) {
  const [showTable, setShowTable] = useState(false);
  const exportSvg = () => {
    if (!svgRef?.current) return;
    downloadBlob(new Blob([inlineVars(svgToString(svgRef.current))], { type: 'image/svg+xml' }), `${(title || 'chart').replace(/[^\w]+/g, '-').toLowerCase()}.svg`);
  };
  return (
    <figure className="chart">
      <figcaption className="chart__head">
        <div>
          <p className="chart__title">{title}</p>
          {subtitle ? <p className="chart__sub">{subtitle}</p> : null}
        </div>
        <div className="chart__tools">
          {actions}
          <button type="button" className="btn btn--ghost btn--sm" aria-pressed={showTable} onClick={() => setShowTable((v) => !v)}>{showTable ? 'Hide data table' : 'Data table'}</button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={exportSvg}>Save image</button>
        </div>
      </figcaption>
      {legend}
      {children}
      {caption ? <p className="chart__caption">{caption}</p> : null}
      {showTable && table ? <DataTable table={table} /> : null}
    </figure>
  );
}

export function DataTable({ table, limit = 100 }) {
  const [sort, setSort] = useState({ index: null, dir: 1 });
  let rows = table.rows;
  if (sort.index !== null) {
    rows = [...rows].sort((a, b) => {
      const x = a[sort.index]; const y = b[sort.index];
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * sort.dir;
      return String(x ?? '').localeCompare(String(y ?? '')) * sort.dir;
    });
  }
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Chart data table">
      <table className="table table--compact">
        <thead>
          <tr>
            {table.columns.map((c, i) => (
              <th key={c} scope="col" aria-sort={sort.index === i ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
                <button type="button" className="th-sort" onClick={() => setSort((s) => ({ index: i, dir: s.index === i ? -s.dir : 1 }))}>{c}</button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, limit).map((r, i) => (
            <tr key={i}>{r.map((v, j) => <td key={j}>{typeof v === 'number' ? formatNumber(v, 4) : (v ?? '—')}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {rows.length > limit ? <p className="muted">Showing {limit} of {formatNumber(rows.length)} rows. Export the table to get all of them.</p> : null}
    </div>
  );
}

export function Tooltip({ at, children }) {
  if (!at) return null;
  return (
    <div className="chart__tip" role="status" style={{ left: `${(at.x / 720) * 100}%`, top: `${(at.y / 380) * 100}%` }}>{children}</div>
  );
}
