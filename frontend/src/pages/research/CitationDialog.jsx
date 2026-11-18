import { useEffect, useState } from 'react';
import { research } from '../../api/index.js';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { locationLabel, quoteInContext, sourceTitle } from '../../features/research/evidence.js';
import { Badge, ExternalLink, Notice, OriginBadge, StatementBadge } from './shared.jsx';

/** The passage behind a citation, read again from the saved source just now, with the text before and after it. If it no longer matches, it says so. */
export function CitationDialog({ bundleId, evidenceId, onClose }) {
  const [state, setState] = useState({ loading: true, data: null, error: '' });
  useEffect(() => {
    let live = true;
    research.citation(bundleId, evidenceId).then((data) => { if (live) setState({ loading: false, data, error: '' }); }).catch((e) => { if (live) setState({ loading: false, data: null, error: describeError(e) }); });
    return () => { live = false; };
  }, [bundleId, evidenceId]);
  const d = state.data;
  // A calculated sentence has no page to read around it: it is rebuilt from the stored analysis, so the sentence itself is shown.
  const q = d ? (d.context ? quoteInContext(d.context) : { before: '', quote: d.evidence.quote, after: '' }) : null;
  return (
    <Modal title={`Citation ${evidenceId}`} onClose={onClose}>
      {state.loading ? <p><Spinner label="Reading the source" /> Reading the saved source…</p> : null}
      {state.error ? <Notice kind="error" title="Could not open this citation">{state.error}</Notice> : null}
      {d ? (
        <div className="stack">
          {d.verification.verified ? <Notice kind="success">The quote was found again at the recorded place and matches.</Notice> : <Notice kind="warn" title="Check before relying on this">{d.verification.reason || 'The passage could not be matched to the saved source.'}</Notice>}
          {d.verification.ocr ? <Notice kind="info">This page was read by character recognition{d.verification.ocr_confidence != null ? ` (confidence ${Math.round(d.verification.ocr_confidence * 100)}%)` : ''}, so check it against the original.</Notice> : null}
          <blockquote className="citation__quote" aria-label="Quoted passage">
            <span className="citation__around">{q.before}</span><mark>{q.quote}</mark><span className="citation__around">{q.after}</span>
          </blockquote>
          <p><OriginBadge origin={d.evidence.origin} /> <StatementBadge kind={d.evidence.kind} />{d.source_label ? <> <Badge tone="muted">{d.source_label}</Badge></> : null}</p>
          <dl className="kv">
            <div className="kv__pair"><dt>Source</dt><dd>{d.source.url ? <ExternalLink url={d.source.url}>{sourceTitle(d.source)}</ExternalLink> : sourceTitle(d.source)}</dd></div>
            <div className="kv__pair"><dt>Where</dt><dd>{locationLabel(d.evidence.location)}</dd></div>
            {d.source.retrieved_at ? <div className="kv__pair"><dt>Read on</dt><dd>{d.source.retrieved_at}</dd></div> : null}
            {d.source.content_scope ? <div className="kv__pair"><dt>What was read</dt><dd>{d.source.content_scope}</dd></div> : null}
            <div className="kv__pair"><dt>Reliability</dt><dd>{d.source.reliability?.level ?? '—'}</dd></div>
            {d.evidence.numbers?.length ? <div className="kv__pair"><dt>Numbers in the quote</dt><dd>{d.evidence.numbers.join(', ')}</dd></div> : null}
          </dl>
          {d.open?.type === 'external' ? (
            <div className="stack" aria-label="Open the original">
              <p><ExternalLink url={d.open.url}>{d.open.label}</ExternalLink>{d.open.section ? ` (${d.open.section})` : ''}</p>
              <p className="row__meta">{d.open.note}</p>
            </div>
          ) : null}
          {d.calculation ? (
            <div className="stack" aria-label="How this was calculated">
              <h3 className="sub">How this was calculated</h3>
              <p>{d.calculation.how}</p>
              <dl className="kv">
                {d.calculation.operation ? <div className="kv__pair"><dt>Analysis</dt><dd>{d.calculation.operation}</dd></div> : null}
                {d.calculation.run_id ? <div className="kv__pair"><dt>Run</dt><dd><code>{d.calculation.run_id}</code></dd></div> : null}
                {d.calculation.model_id ? <div className="kv__pair"><dt>Model</dt><dd><code>{d.calculation.model_id}</code></dd></div> : null}
                {d.calculation.result_hash ? <div className="kv__pair"><dt>Result fingerprint</dt><dd><code>{String(d.calculation.result_hash).slice(0, 16)}…</code></dd></div> : null}
              </dl>
              {d.open?.type === 'analysis_run' || d.open?.type === 'model' ? <p className="row__meta">{d.open.label}: find it under this project’s analysis{d.open.type === 'model' ? ' and models' : ' history'} using the run or model shown above.</p> : null}
            </div>
          ) : null}
          {d.source.injection_removed ? <Notice kind="warn">{d.source.injection_removed} sentence(s) that looked like instructions were removed from this source before it was read.</Notice> : null}
          <p className="row__meta">{d.note}</p>
        </div>
      ) : null}
      <div className="btn-row"><button type="button" className="btn" data-autofocus onClick={onClose}>Close</button></div>
    </Modal>
  );
}
