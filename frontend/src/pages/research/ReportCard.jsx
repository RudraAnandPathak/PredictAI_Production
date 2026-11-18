import { ALIGNMENT_LABEL, SECTION_SCOPE_LABEL, labelOf } from '../../features/research/labels.js';
import { PROVIDER_STATUS_LABEL, PROVIDER_STATUS_TONE, answerLines, comparisonRows, datasetSummary, evidenceTags, explanationOf, externalGroups, hasSeparation, providerRows, rejectedRows, reportOf, reportSections } from '../../features/research/report.js';
import { Badge, ExternalLink, Notice } from './shared.jsx';
import { sourceTitle } from '../../features/research/evidence.js';

function CiteLinks({ ids, onCite }) {
  return ids.map((id) => <button key={id} type="button" className="link-button" onClick={() => onCite(id)} aria-label={`Open citation ${id}`}>{id}</button>);
}

const Tags = ({ item }) => <p className="conclusion__tags">{evidenceTags(item).map((t) => <span key={t.key}><Badge tone={t.tone} title={t.title}>{t.label}</Badge> </span>)}</p>;

function Line({ item, onCite }) {
  return (
    <li className="conclusion" data-conclusion-id={item.conclusion}>
      <p className="conclusion__text">{item.text}</p>
      <Tags item={item} />
      {item.caveats?.length ? <ul className="conclusion__caveats">{item.caveats.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
      <p className="row__meta">Passages: <CiteLinks ids={item.evidence ?? []} onCite={onCite} /></p>
    </li>
  );
}

const where = (c) => c?.where || '';

function Explanation({ report, onCite }) {
  const x = explanationOf(report);
  if (!x) return null;
  const cited = (d) => <span className="row__meta"> — {(d.cites ?? []).map((c) => where(c)).filter(Boolean).join('; ')} <CiteLinks ids={d.evidence ?? []} onCite={onCite} /></span>;
  return (
    <div className="stack" aria-label="Explanation">
      <h3 className="sub">Explanation{x.subject.length ? `: ${x.subject.join(', ')}` : ''}</h3>
      <p className="row__meta">{x.howToRead}</p>
      {x.direct.length ? <ol>{x.direct.map((d) => <li key={d.key}><strong>{d.part}.</strong> {d.text}{cited(d)}</li>)}</ol> : <p className="muted">No part of the question could be answered from the sources that were checked as relevant.</p>}
      {x.sideBySide.length ? (
        <div className="stack" aria-label="Side by side">
          <h4 className="sub">Side by side</h4>
          {x.sideBySide.map((e) => (
            <div key={e.key}>
              <p><strong>{e.name}</strong>{e.years.length ? <span className="row__meta"> (years in the passages: {e.years.join(', ')})</span> : null}</p>
              <ul>{e.statements.map((st, i) => <li key={i}>{st.text}{cited(st)}</li>)}</ul>
            </div>
          ))}
        </div>
      ) : null}
      {x.connections.length ? <div aria-label="How the statements connect"><h4 className="sub">How the statements connect</h4><ul>{x.connections.map((c, i) => <li key={i}>{c.text} <span className="row__meta">{c.part}</span> <CiteLinks ids={c.evidence ?? []} onCite={onCite} /></li>)}</ul></div> : null}
      {x.unknown.length ? <div aria-label="What is not known"><h4 className="sub">What is not known or was not checked</h4><ul>{x.unknown.map((u, i) => <li key={i}>{u}</li>)}</ul></div> : null}
    </div>
  );
}

function Providers({ report }) {
  const rows = providerRows(report);
  const rejected = rejectedRows(report);
  if (!rows.length && !rejected.length) return null;
  return (
    <div className="stack" aria-label="Search providers">
      <h3 className="sub">Where the sources came from</h3>
      <ul>{rows.map((r) => <li key={r.provider}><strong>{r.provider}</strong> <Badge tone={PROVIDER_STATUS_TONE[r.status] ?? 'muted'}>{PROVIDER_STATUS_LABEL[r.status] ?? r.status}</Badge> <span className="row__meta">{r.note}</span>{r.errors?.length ? <span className="row__meta"> {r.errors.join(' ')}</span> : null}</li>)}</ul>
      {rejected.length ? <details><summary>Results that were set aside ({rejected.length})</summary><ul>{rejected.map((r) => <li key={r.key}>{r.title} <span className="row__meta">({r.provider}) {r.reason}</span></li>)}</ul></details> : null}
    </div>
  );
}

/** The structured answer: the strongest line for each part of the question, the parts in full, what was calculated on the person's data, which outside sources were used and what they are,
 *  where a calculated result and an outside passage mention the same thing, and everything the reader should weigh. Every line is a quotation or a calculated sentence with its citation. */
export function ReportCard({ bundle, onCite }) {
  const report = reportOf(bundle);
  if (!report) return null;
  const answer = answerLines(report);
  const sections = reportSections(report);
  const dataset = datasetSummary(report);
  const external = externalGroups(report, bundle);
  const comparison = comparisonRows(report);
  return (
    <section className="card stack" aria-label="Report">
      <h2 className="card__title">Report</h2>
      <p className="row__meta">{report.reading_guide}</p>
      <Explanation report={report} onCite={onCite} />
      <Providers report={report} />

      {answer.length ? (
        <div className="stack" aria-label="In short">
          <h3 className="sub">In short</h3>
          <ol className="conclusions">
            {answer.map((a) => (
              <li key={a.key} className="conclusion" data-conclusion-id={a.conclusion}>
                <p className="row__meta"><strong>{a.section}</strong></p>
                <p className="conclusion__text">{a.text}</p>
                <Tags item={a} />
                <p className="row__meta">Passages: <CiteLinks ids={a.evidence} onCite={onCite} /></p>
              </li>
            ))}
          </ol>
        </div>
      ) : <Notice kind="warn" title="No answer could be drawn">Nothing relevant enough was found, so no answer is given. The parts below say what was missing.</Notice>}

      {hasSeparation(report) ? <Notice kind="info" title="Your data and outside sources are kept apart">{report.separation_note}</Notice> : null}

      {dataset ? (
        <div className="stack" aria-label="What was calculated on your data">
          <h3 className="sub">Calculated on your data</h3>
          {dataset.datasets.length === 0 ? <p className="muted">No calculation could be made on the selected data.</p> : null}
          {dataset.datasets.map((d) => (
            <div key={d.assetId} className="stack">
              <p><strong>{d.name}</strong> <span className="row__meta">{d.runs.length} calculation{d.runs.length === 1 ? '' : 's'} by PredictAI’s analysis tools; a language model calculated nothing.</span></p>
              <ul className="list">
                {d.runs.map((r) => (
                  <li key={r.runId} className="row"><div className="row__main"><span className="row__title">{r.kind || 'Analysis'}{r.operations.length ? ` — ${r.operations.join(', ')}` : ''}</span><span className="row__meta">{r.rows != null ? `${r.rows.toLocaleString()} rows used` : 'Rows used not recorded'}{r.status ? ` · ${r.status}` : ''}</span></div></li>
                ))}
              </ul>
            </div>
          ))}
          {dataset.notes.map((n, i) => <p key={i} className="row__meta">{n}</p>)}
        </div>
      ) : null}

      <div className="stack" aria-label="The parts of the question">
        <h3 className="sub">Part by part</h3>
        {sections.map((s) => (
          <details key={s.id} open={s.answered && sections.length <= 4}>
            <summary><strong>{s.title}</strong> <Badge tone={s.answered ? 'ok' : 'warn'}>{s.answered ? 'Answered' : 'Nothing found'}</Badge> <span className="row__meta">{labelOf(SECTION_SCOPE_LABEL, s.scope)}</span></summary>
            {s.answered ? <ol className="conclusions">{s.items.map((it) => <Line key={it.conclusion} item={it} onCite={onCite} />)}</ol> : <p className="muted">{s.note || 'Nothing relevant was found for this part.'}</p>}
          </details>
        ))}
      </div>

      {external.groups.length || external.unread.length || external.requested ? (
        <div className="stack" aria-label="Outside sources used">
          <h3 className="sub">Outside sources</h3>
          {!external.used && external.requested ? <Notice kind="warn" title="Outside research was asked for but not run">Only your own material was used. The quality checks say why.</Notice> : null}
          {external.groups.map((g) => (
            <div key={g.cls}>
              <p><strong>{g.label}</strong> <span className="row__meta">{g.description}</span></p>
              <ul className="list">
                {g.sources.map((s) => (
                  <li key={s.id} className="row" data-source-id={s.id}>
                    <div className="row__main">
                      <span className="row__title"><strong>{s.id}</strong> {s.url ? <ExternalLink url={s.url}>{sourceTitle(s)}</ExternalLink> : sourceTitle(s)}</span>
                      <span className="row__meta">{s.content_scope ? `Read: ${s.content_scope}` : ''}{s.sections_used?.length ? ` · sections: ${s.sections_used.join(', ')}` : ''}</span>
                      {s.meta?.permalink ? <span className="row__meta">Exact version read: <ExternalLink url={s.meta.permalink}>permanent link</ExternalLink></span> : null}
                      {s.meta?.pdf_url ? <span className="row__meta">Paper: <ExternalLink url={s.meta.pdf_url}>PDF</ExternalLink>{s.meta.is_latest === false ? ' · a newer version exists' : ''}</span> : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {external.unread.length ? <details><summary>{external.unread.length} outside source{external.unread.length === 1 ? '' : 's'} could not be read</summary><ul>{external.unread.map((u) => <li key={u.source}><strong>{u.source}</strong> {u.title || ''}{u.reason ? ` — ${u.reason}` : ''}</li>)}</ul></details> : null}
        </div>
      ) : null}

      {comparison.length ? (
        <div className="stack" aria-label="Your data beside outside sources">
          <h3 className="sub">Your data beside outside sources</h3>
          {comparison.map((c) => (
            <div key={c.key} className="conflict">
              <p><Badge tone="ok">Calculated</Badge> {c.calculated.text} <CiteLinks ids={[c.calculated.evidence]} onCite={onCite} /></p>
              <ul>
                {c.external.map((x) => (
                  <li key={x.evidence}><Badge tone="muted">{x.label}</Badge> “{x.text}” <CiteLinks ids={[x.evidence]} onCite={onCite} />{x.alignment ? <span className="row__meta"> · {labelOf(ALIGNMENT_LABEL, x.alignment)}</span> : null}</li>
                ))}
              </ul>
              <p className="row__meta">{c.note}</p>
            </div>
          ))}
        </div>
      ) : null}

      {report.uncertainty?.length ? (
        <div className="stack" aria-label="What is uncertain">
          <h3 className="sub">What is uncertain</h3>
          <ul>{report.uncertainty.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}
