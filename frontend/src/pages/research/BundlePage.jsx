import { useState } from 'react';
import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatWhen } from '../../features/predictive/format.js';
import { EXPORT_FORMATS, conclusionEvidence, conclusionMeta, describeSourceLine, evidenceBySubtopic, locationLabel, sortedChecks, sourceById, sourceTitle } from '../../features/research/evidence.js';
import { BUNDLE_STATUS_LABEL, BUNDLE_STATUS_TONE, CHECK_STATUS_LABEL, CHECK_STATUS_TONE, ORIGIN_LABEL, QUALITY_VERDICT_LABEL, QUALITY_VERDICT_TONE, labelOf } from '../../features/research/labels.js';
import { reportOf } from '../../features/research/report.js';
import { CitationDialog } from './CitationDialog.jsx';
import { FollowUpForm } from './FollowUpForm.jsx';
import { ReportCard } from './ReportCard.jsx';
import { Badge, ConfidenceBadge, ExternalLink, Notice, OriginBadge, StatementBadge, saveBlob } from './shared.jsx';

function Conclusions({ bundle, onCite }) {
  if (!bundle.conclusions.length) return <p className="muted">No conclusions could be drawn from the evidence that was found.</p>;
  return (
    <ol className="conclusions" aria-label="Conclusions">
      {bundle.conclusions.map((c) => {
        const meta = conclusionMeta(c);
        const { found, missing } = conclusionEvidence(bundle, c);
        const origins = [...new Set(found.map((e) => e.origin))];
        return (
          <li key={c.id} className="conclusion" data-conclusion-id={c.id}>
            <p className="conclusion__text"><strong>{c.id}.</strong> {c.text}</p>
            <p className="conclusion__tags">
              {origins.map((o) => <OriginBadge key={o} origin={o} />)} <StatementBadge kind={c.kind} /> <ConfidenceBadge confidence={c.confidence} />
              {meta.aiAuthored ? <Badge tone="warn" title="The wording of this was written by an AI model. The quotes under it are unchanged.">AI-worded</Badge> : null}
            </p>
            {c.caveats?.length ? <ul className="conclusion__caveats">{c.caveats.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
            <p className="row__meta">
              Rests on {meta.evidenceCount} passage{meta.evidenceCount === 1 ? '' : 's'} from {meta.sourceCount} source{meta.sourceCount === 1 ? '' : 's'}:{' '}
              {found.map((e) => <button key={e.id} type="button" className="link-button" onClick={() => onCite(e.id)} aria-label={`Open citation ${e.id}`}>{e.id}</button>)}
              {missing.length ? <span className="row__meta"> (missing: {missing.join(', ')})</span> : null}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

function EvidenceViewer({ bundle, onCite }) {
  const groups = evidenceBySubtopic(bundle);
  return (
    <div className="stack" aria-label="Evidence">
      {groups.map((g) => (
        <details key={g.id || 'other'} open={g.items.length > 0 && groups.length <= 3}>
          <summary><strong>{g.title}</strong> <span className="row__meta">{g.items.length} passage{g.items.length === 1 ? '' : 's'}</span></summary>
          {g.items.length === 0 ? <p className="muted">Nothing relevant was found for this part.</p> : null}
          <ul className="list">
            {g.items.map((e) => {
              const src = sourceById(bundle, e.source);
              return (
                <li key={e.id} className="evidence" data-evidence-id={e.id}>
                  <blockquote className="citation__quote"><mark>{e.quote}</mark></blockquote>
                  <p className="row__meta">
                    <strong>{e.id}</strong> · {src ? sourceTitle(src) : e.source} · {locationLabel(e.location)} <OriginBadge origin={e.origin} /> <StatementBadge kind={e.kind} />{' '}
                    <button type="button" className="link-button" onClick={() => onCite(e.id)}>Inspect citation</button>
                  </p>
                </li>
              );
            })}
          </ul>
        </details>
      ))}
    </div>
  );
}

function SourceList({ bundle }) {
  return (
    <ul className="list" aria-label="Sources">
      {bundle.sources.map((s) => (
        <li key={s.id} className="row" data-source-id={s.id}>
          <div className="row__main">
            <span className="row__title"><strong>{s.id}</strong> {s.url ? <ExternalLink url={s.url}>{sourceTitle(s)}</ExternalLink> : sourceTitle(s)}</span>
            {s.source_label ? <span className="row__meta"><Badge tone="muted">{s.source_label}</Badge></span> : null}
            <span className="row__meta">{describeSourceLine(s)}{s.status && s.status !== 'ok' ? ` · ${s.status}` : ''}</span>
            {s.content_scope ? <span className="row__meta">Read: {s.content_scope}{s.sections_used?.length ? ` · sections: ${s.sections_used.join(', ')}` : ''}</span> : null}
            {s.failure ? <span className="row__meta">{s.failure}</span> : null}
            {s.reliability?.signals?.length ? <details><summary>Why this reliability indicator</summary><ul>{s.reliability.signals.map((g, i) => <li key={i}><strong>{g.name}.</strong> {g.basis}</li>)}</ul><p className="row__meta">{s.reliability.note}</p></details> : null}
            {s.freshness?.note && s.freshness.label !== 'not applicable' ? <span className="row__meta">{s.freshness.note}</span> : null}
            {s.injection_removed ? <span className="row__meta">{s.injection_removed} sentence(s) that looked like instructions were removed before reading.</span> : null}
          </div>
          <OriginBadge origin={s.origin} />
        </li>
      ))}
    </ul>
  );
}

function Quality({ quality }) {
  return (
    <section className="card stack" aria-label="Quality checks">
      <div className="card__row"><h2 className="card__title">Quality checks</h2><Badge tone={QUALITY_VERDICT_TONE[quality.verdict] ?? 'muted'}>{labelOf(QUALITY_VERDICT_LABEL, quality.verdict)}</Badge></div>
      <ul className="list">
        {sortedChecks(quality).map((c) => (
          <li key={c.id} className="row"><div className="row__main"><span className="row__title">{c.title}</span><span className="row__meta">{c.detail}</span>{c.items?.length ? <ul>{c.items.map((x, i) => <li key={i} className="row__meta">{typeof x === 'string' ? x : JSON.stringify(x)}</li>)}</ul> : null}</div><Badge tone={CHECK_STATUS_TONE[c.status] ?? 'muted'}>{labelOf(CHECK_STATUS_LABEL, c.status)}</Badge></li>
        ))}
      </ul>
      <p className="row__meta">{quality.note}</p>
    </section>
  );
}

function WhatWasUsed({ bundle }) {
  const counts = bundle.context?.counts ?? {};
  const web = bundle.web ?? {};
  return (
    <section className="card stack" aria-label="What was used">
      <h2 className="card__title">What this is based on</h2>
      <dl className="kv">
        {Object.keys(ORIGIN_LABEL).map((o) => <div key={o} className="kv__pair"><dt>{ORIGIN_LABEL[o]}</dt><dd>{counts[o] ?? 0} item{(counts[o] ?? 0) === 1 ? '' : 's'}</dd></div>)}
        <div className="kv__pair"><dt>Web search</dt><dd>{web.used ? `Used (${(web.providers ?? []).join(', ')})` : web.requested ? `Asked for but not used${web.denied_reason ? `: ${web.denied_reason}` : ''}` : 'Not used. Nothing left this server.'}</dd></div>
      </dl>
      {web.queries_sent?.length ? <div><h3 className="sub">Search phrases that were sent</h3><ul>{web.queries_sent.map((q, i) => <li key={i}><code>{q}</code></li>)}</ul></div> : null}
      {bundle.collection?.failures?.length ? <details open><summary>Search problems ({bundle.collection.failures.length})</summary><ul>{bundle.collection.failures.map((f, i) => <li key={i}>{f.provider ? `${f.provider}: ` : ''}{f.message}</li>)}</ul></details> : null}
      {bundle.collection?.dropped?.length ? <details><summary>Search results not read because they did not match the question ({bundle.collection.dropped.length})</summary><ul>{bundle.collection.dropped.map((d, i) => <li key={i}>{d.title}{d.provider ? ` (${d.provider})` : ''}</li>)}</ul></details> : null}
      {bundle.context?.excluded?.length ? <details><summary>Left out ({bundle.context.excluded.length})</summary><ul>{bundle.context.excluded.map((x, i) => <li key={i}>{x.name ?? x.kind}{x.reason ? ` — ${x.reason}` : ''}</li>)}</ul></details> : null}
    </section>
  );
}

/** The evidence viewer: conclusions with their origin and strength, every quoted passage with its source and place, possible disagreements, gaps and quality checks. */
export function BundlePage() {
  const { bundleId } = useParams('/research/bundle/:bundleId');
  const res = useResource(() => research.bundle(bundleId), [bundleId]);
  const [citing, setCiting] = useState(null);
  const [message, setMessage] = useState(null);
  const [label, setLabel] = useState(null);

  if (res.loading && !res.data) return <PageLoading label="Opening the result…" />;
  if (res.error) {
    const missing = res.error.status === 404;
    const damaged = res.error.code === 'BUNDLE_DAMAGED';
    return (
      <article className="page">
        <ErrorState title={damaged ? 'This result failed its integrity check' : missing ? 'Result not found' : 'Could not open the result'} message={damaged ? 'The saved copy no longer matches its fingerprint, so it is not shown. Run the research again to get a fresh result.' : missing ? 'This result does not exist, or it is not yours.' : describeError(res.error)} onRetry={missing || damaged ? null : () => res.reload()} />
        <p><Link to="/research" className="btn btn--ghost">Back to research</Link></p>
      </article>
    );
  }
  const { bundle, explanation } = res.data;
  const sessionHref = `/research/session/${encodeURIComponent(bundle.session_id)}`;

  async function download(format) {
    setMessage(null);
    try { saveBlob(await research.exportBundle(bundle.id, format)); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }
  async function toggleSaved() {
    try { await research.updateBundle(bundle.id, { saved: !res.data.saved }); res.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }
  async function saveLabel(event) {
    event.preventDefault();
    try { await research.updateBundle(bundle.id, { label: label.trim() }); setLabel(null); res.reload({ quiet: true }); } catch (e) { setMessage({ kind: 'error', text: describeError(e) }); }
  }

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to={sessionHref}>Back to the research</Link></p>
        <h1 className="page-title">{bundle.question}</h1>
        <p className="row__meta">
          Run {bundle.number} · {bundle.level_label} · {formatWhen(bundle.created_at)} <Badge tone={BUNDLE_STATUS_TONE[bundle.status] ?? 'muted'}>{labelOf(BUNDLE_STATUS_LABEL, bundle.status)}</Badge>{' '}
          <Badge tone={QUALITY_VERDICT_TONE[bundle.quality.verdict] ?? 'muted'}>{labelOf(QUALITY_VERDICT_LABEL, bundle.quality.verdict)}</Badge>
          {res.data.label ? ` · “${res.data.label}”` : ''}{res.data.saved ? ' · saved' : ''}
        </p>
      </header>

      {!res.data.integrity_ok ? <Notice kind="error" title="Fingerprint mismatch">The stored copy does not match its fingerprint. Do not rely on it; run the research again.</Notice> : null}
      {res.data.warnings?.length ? <Notice kind="warn" title="Read these first"><ul>{res.data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Notice> : null}

      <ReportCard bundle={bundle} onCite={setCiting} />

      <section className="card stack" aria-label="Conclusions">
        <h2 className="card__title">{reportOf(bundle) ? 'All conclusions' : 'Conclusions'}</h2>
        <p className="row__meta">Each conclusion is a quotation or close restatement of a source, labelled with where it came from and how strong it is. PredictAI has not checked that any source is correct.</p>
        <Conclusions bundle={bundle} onCite={setCiting} />
      </section>

      {bundle.conflicts.length ? (
        <section className="card stack" aria-label="Possible disagreements">
          <h2 className="card__title">Possible disagreements</h2>
          {bundle.conflicts.map((c) => (
            <div key={c.id} className="conflict" data-conflict-id={c.id}>
              <p><strong>{c.id}.</strong> {c.explanation}</p>
              <ul>{c.statements.map((s) => <li key={s.evidence}><button type="button" className="link-button" onClick={() => setCiting(s.evidence)}>{s.evidence}</button> “{s.text}”</li>)}</ul>
              <p className="row__meta">{c.hint}</p>
            </div>
          ))}
        </section>
      ) : null}

      {bundle.gaps.length ? (
        <section className="card stack" aria-label="Not found">
          <h2 className="card__title">What was not found</h2>
          <ul>{bundle.gaps.map((g) => <li key={g.subtopic}><strong>{g.title}.</strong> {g.note}</li>)}</ul>
        </section>
      ) : null}

      <section className="card stack" aria-label="Evidence">
        <h2 className="card__title">Evidence, by part of the question</h2>
        <EvidenceViewer bundle={bundle} onCite={setCiting} />
      </section>

      <section className="card stack" aria-label="Source list">
        <h2 className="card__title">Sources ({bundle.sources.length})</h2>
        <SourceList bundle={bundle} />
      </section>

      <WhatWasUsed bundle={bundle} />
      <Quality quality={bundle.quality} />

      <section className="card stack" aria-label="Limitations">
        <h2 className="card__title">Limits of this result</h2>
        <ul>{bundle.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul>
      </section>

      <Explanation value={explanation} />

      <FollowUpForm sessionId={bundle.session_id} webWasUsed={Boolean(bundle.web?.requested)} />

      <section className="card stack" aria-label="Keep or share">
        <h2 className="card__title">Keep and export</h2>
        <div className="btn-row">
          <button type="button" className="btn btn--ghost" onClick={toggleSaved}>{res.data.saved ? 'Remove from saved' : 'Save this result'}</button>
          {EXPORT_FORMATS.map((f) => <button key={f.key} type="button" className="btn btn--ghost" onClick={() => download(f.key)}>Download {f.label}</button>)}
        </div>
        {label === null ? <div className="btn-row"><button type="button" className="btn btn--ghost btn--sm" onClick={() => setLabel(res.data.label ?? '')}>{res.data.label ? 'Rename label' : 'Add a label'}</button></div> : (
          <form className="filterbar" onSubmit={saveLabel}><label className="sr-only" htmlFor="bundle-label">Label</label><input id="bundle-label" className="field__input" maxLength={200} value={label} onChange={(e) => setLabel(e.target.value)} /><button className="btn btn--sm" type="submit">Save label</button><button className="btn btn--ghost btn--sm" type="button" onClick={() => setLabel(null)}>Cancel</button></form>
        )}
        {message ? <Notice kind={message.kind}>{message.text}</Notice> : null}
        <p className="row__meta">Fingerprint {bundle.integrity.sha256.slice(0, 16)}… (SHA-256 of the whole result). Downloads leave out internal storage paths.</p>
      </section>

      {citing ? <CitationDialog bundleId={bundle.id} evidenceId={citing} onClose={() => setCiting(null)} /> : null}
    </article>
  );
}
