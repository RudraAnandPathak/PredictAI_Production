import { useState } from 'react';
import { workspace } from '../../api/index.js';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading, Spinner } from '../../components/ui/Spinner.jsx';
import { formatDate } from '../../features/identity/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { typeLabel } from '../../features/workspace/extraction.js';
import { basisInfo, formatNumber, formatPct, histogramHeights, sourceLine } from '../../features/workspace/profile.js';

function Basis({ basis }) {
  const b = basisInfo(basis);
  return <span className={`badge ${b.badge}`} title={b.help}>{b.label}</span>;
}

export function ProfilePanel({ asset, readOnly }) {
  const result = useResource(() => workspace.assets.profile(asset.id), [asset.id]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const [fresh, setFresh] = useState(null);

  async function generate(refresh) {
    setBusy(true);
    setFailure(null);
    try {
      setFresh(await workspace.assets.createProfile(asset.id, { refresh: refresh ? 'true' : undefined }));
    } catch (error) {
      setFailure(describeError(error));
    } finally {
      setBusy(false);
    }
  }
  if (result.loading && !result.data) return <PageLoading label="Loading profile…" />;
  if (result.error) return <ErrorState title="Could not load the profile" message={describeError(result.error)} onRetry={() => result.reload()} />;
  const current = fresh ?? result.data;
  const profile = current?.profile;

  return (
    <section aria-label="Data profile" className="stack">
      {failure ? <FormMessage kind="error">{failure}</FormMessage> : null}
      {!profile ? (
        <div className="empty">
          <p><strong>No profile yet.</strong> A profile summarises what is in this file: counts, empty values, ranges and common values. It only describes the data; it makes no predictions.</p>
          {!readOnly ? <button type="button" className="btn btn--primary" disabled={busy} onClick={() => generate(false)}>{busy ? <Spinner label="Profiling" /> : null} Create profile</button> : <p className="muted">Restore the project to create a profile.</p>}
        </div>
      ) : (
        <>
          <p className="muted" role="status">
            Made {formatDate(current.generated_at)} from {sourceLine(current)} · profiler v{current.profiler_version}.
            {current.stale ? ' This profile is out of date for the current data.' : ''}
          </p>
          <p className="pill-row" aria-label="How to read the labels">
            <Basis basis="fact" /> <span className="row__meta">computed exactly</span>
            <Basis basis="heuristic" /> <span className="row__meta">a named rule of thumb</span>
            <Basis basis="estimate" /> <span className="row__meta">approximate</span>
          </p>
          {asset.kind === 'dataset' ? <DatasetProfile p={profile} /> : <DocumentProfile p={profile} />}
          <Explanation value={current.explanation} level={3} />
          {!readOnly ? <p><button type="button" className="btn btn--ghost" disabled={busy} onClick={() => generate(true)}>{busy ? <Spinner label="Profiling" /> : null} Recalculate</button></p> : null}
          <p className="row__meta">Checksum of this result: {profile.content_hash?.slice(0, 16)}… Recalculating unchanged data gives the same checksum.</p>
        </>
      )}
    </section>
  );
}

function Stat({ value, label, basis }) {
  return <div className="stat"><span className="stat__value">{value}</span><span className="stat__label">{label}</span>{basis ? <Basis basis={basis} /> : null}</div>;
}

function DatasetProfile({ p }) {
  const d = p.dataset;
  return (
    <>
      <div className="stat-grid">
        <Stat value={d.rows.toLocaleString()} label="Rows" basis="fact" />
        <Stat value={d.columns.toLocaleString()} label="Columns" basis="fact" />
        <Stat value={d.duplicate_rows.toLocaleString()} label="Duplicate rows" basis="fact" />
        <Stat value={formatPct(d.missing_pct)} label="Empty cells" basis="fact" />
        <Stat value={d.complete_rows.toLocaleString()} label="Complete rows" basis="fact" />
      </div>
      <div className="table-wrap" tabIndex={0}>
        <table className="table">
          <caption>Columns</caption>
          <thead><tr><th scope="col">Column</th><th scope="col">Type</th><th scope="col">Empty</th><th scope="col">Distinct</th><th scope="col">Range</th><th scope="col">Notes</th></tr></thead>
          <tbody>{p.columns.map((c) => <ColumnRow key={c.name} c={c} />)}</tbody>
        </table>
      </div>
    </>
  );
}

function ColumnRow({ c }) {
  const n = c.numeric;
  const heights = n ? histogramHeights(n.histogram.bins) : null;
  return (
    <tr>
      <th scope="row">{c.name}</th>
      <td>{typeLabel(c.type.value)} <Basis basis="heuristic" /></td>
      <td>{c.missing.toLocaleString()} ({formatPct(c.missing_pct)})</td>
      <td>{c.distinct.exact ? '' : 'at least '}{c.distinct.value.toLocaleString()}</td>
      <td>
        {n ? (
          <details>
            <summary>{formatNumber(n.min)} to {formatNumber(n.max)}</summary>
            <dl className="kv">
              <dt>Mean</dt><dd>{formatNumber(n.mean)}</dd><dt>Median</dt><dd>{formatNumber(n.median)}</dd>
              <dt>Quartiles</dt><dd>{formatNumber(n.q1)} – {formatNumber(n.q3)}</dd><dt>Spread (std. dev.)</dt><dd>{formatNumber(n.std)}</dd>
            </dl>
            <div className="histogram" role="img" aria-label={`Distribution of ${c.name}: ${n.histogram.bins.map((b) => b.count).join(', ')} values per bin`}>
              {heights.map((h, i) => <span key={i} className="histogram__bar" style={{ height: `${h}%` }} title={`${formatNumber(n.histogram.bins[i].from)}–${formatNumber(n.histogram.bins[i].to)}: ${n.histogram.bins[i].count}`} />)}
            </div>
          </details>
        ) : c.dates ? `${c.dates.min.slice(0, 10)} to ${c.dates.max.slice(0, 10)}` : (c.top_values.length ? <details><summary>Common values</summary><ul>{c.top_values.map((t) => <li key={t.value}>{t.value} <span className="row__meta">× {t.count}</span></li>)}</ul></details> : '—')}
      </td>
      <td>{c.flags.length ? <ul className="plain">{c.flags.map((f) => <li key={f.code}>{f.message} <Basis basis={f.basis} /></li>)}</ul> : '—'}</td>
    </tr>
  );
}

function DocumentProfile({ p }) {
  const d = p.document;
  return (
    <>
      <div className="stat-grid">
        <Stat value={`${d.pages.value}${d.pages.basis === 'estimate' ? '*' : ''}`} label={d.pages.basis === 'estimate' ? 'Sections (estimated)' : 'Pages'} basis={d.pages.basis} />
        <Stat value={d.words.value.toLocaleString()} label="Words" basis="fact" />
        <Stat value={formatNumber(d.words_per_page)} label="Words per page" basis="fact" />
        <Stat value={d.headings.count} label="Headings" basis={d.headings.basis} />
        <Stat value={d.tables.count} label="Tables" basis={d.tables.basis} />
        <Stat value={`${d.reading_time_minutes.value} min`} label="Reading time" basis="estimate" />
        {d.ocr ? <Stat value={`${Math.round(d.ocr.mean_confidence)}%`} label="OCR confidence" basis="estimate" /> : null}
      </div>
      {d.ocr ? <p className="muted">{d.ocr.note}</p> : null}
      {p.flags?.length ? <ul className="plain">{p.flags.map((f) => <li key={f.code}>{f.message} <Basis basis={f.basis} /></li>)}</ul> : null}
    </>
  );
}
