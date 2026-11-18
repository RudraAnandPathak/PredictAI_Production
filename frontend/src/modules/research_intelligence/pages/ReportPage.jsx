import { useEffect, useMemo, useState } from 'react';
import { ri } from '../../../api/index.js';
import { useRouter } from '../../../app/router/RouterProvider.jsx';
import { ReviewBox } from '../../../components/reviews/ReviewBox.jsx';
import { useParams } from '../../../app/router/useParams.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Loaded, Notice, attempt, when } from '../../../pages/phase7/shared.jsx';
import { BlockEdit, BlockView } from '../components/ReportBlocks.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';
import { EXPORT_FORMATS, REPORT_STATE_HINT, addSection, addTextBlock, cleanForSave, downloadName, moveSection, removeBlock, removeSection, reportCounts, sectionsChanged, setBlockBullets, setBlockText, sourceByLabel } from '../logic.js';

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.rel = 'noopener';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const accessNote = (a) => (a === 'full_text' ? 'The analysis read the full text.' : a === 'abstract_only' ? 'Based on the abstract only, not the full text.' : a === 'metadata_only' ? 'Based on the bibliographic record only.' : '');

function SourceCard({ source, onClose }) {
  if (!source) return null;
  return (
    <aside className="card" role="region" aria-label={`Source ${source.label}`}>
      <p className="row__meta"><button type="button" className="link-button" onClick={onClose}>Close</button></p>
      <h3 className="card__title">[{source.label}]</h3>
      <p className="note__body">{source.text}</p>
      {source.doi ? <p className="row__meta">DOI {source.doi}</p> : null}
      <p className="row__meta">{accessNote(source.access)}</p>
    </aside>
  );
}

/** Read, edit, regenerate, restore and export one report. */
export function ReportPage() {
  const { workspaceId, reportId } = useParams('/research-intelligence/:workspaceId/reports/:reportId');
  const { navigate } = useRouter();
  const res = useResource(() => ri.reports.get(workspaceId, reportId), [workspaceId, reportId]);
  const versions = useResource(() => ri.reports.versions(workspaceId, reportId), [workspaceId, reportId]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [title, setTitle] = useState('');
  const [cited, setCited] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const report = res.data;
  useEffect(() => { if (report && !editing) { setDraft(report.sections); setTitle(report.title); } }, [report, editing]);
  const labels = useMemo(() => (report?.sources ?? []).map((s) => String(s.label)), [report]);
  const dirty = editing && report && (sectionsChanged(report.sections, cleanForSave(draft ?? [])) || title !== report.title);
  const wsLink = `/research-intelligence/${encodeURIComponent(workspaceId)}`;

  async function save() {
    setBusy(true);
    const out = await attempt(() => ri.reports.update(workspaceId, reportId, { title, sections: cleanForSave(draft) }), setMsg, 'Saved. The previous version was kept in the history.');
    setBusy(false);
    if (out) { setEditing(false); res.reload({ quiet: true }); versions.reload({ quiet: true }); }
  }
  async function regenerate() {
    if (!window.confirm('Rebuild the generated parts from the analysis? Your own text is kept, and the current version is saved to the history.')) return;
    setBusy(true);
    const out = await attempt(() => ri.reports.regenerate(workspaceId, reportId, { keep_author_text: true }), setMsg, 'Regenerated. Your own text was kept.');
    setBusy(false);
    if (out) { res.reload({ quiet: true }); versions.reload({ quiet: true }); }
  }
  async function exportAs(format) {
    setBusy(true);
    const out = await attempt(() => ri.reports.export(workspaceId, reportId, format), setMsg);
    setBusy(false);
    if (out) { saveBlob(out.blob, downloadName(out.filename, `report.${format}`)); setMsg({ kind: 'success', text: `Exported as ${format.toUpperCase()}.` }); }
  }
  async function remove() {
    if (!window.confirm('Delete this report and its version history? This cannot be undone.')) return;
    const out = await attempt(() => ri.reports.remove(workspaceId, reportId), setMsg);
    if (out) navigate('/research-intelligence/reports');
  }
  const setSec = (fn) => setDraft((d) => fn(d));
  const counts = report ? reportCounts(report) : null;

  return (
    <ResearchFrame current="reports" title={report?.title ?? 'Report'} back={{ to: `${wsLink}/studio`, label: '← Back to Studio' }} lead={REPORT_STATE_HINT}>
      <Loaded res={res} what="this report">
        {(r) => (
          <>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <p className="row__meta">{r.type_label} · version {r.version} · {r.sources.length} source(s) · updated {when(r.updated_at)}</p>
            <ReviewBox key={reportId} kind="research_report" targetId={reportId} />
            <p className="row__meta">{counts.source} quoted · {counts.analysis} computed · {counts.author} written by you</p>
            <Notice kind="info">{r.notice}</Notice>
            <div className="btn-row" role="toolbar" aria-label="Report actions">
              {editing ? (
                <>
                  <button type="button" className="btn" disabled={busy || !dirty} onClick={save}>{busy ? 'Saving…' : 'Save changes'}</button>
                  <button type="button" className="btn btn--ghost" onClick={() => { setEditing(false); setDraft(r.sections); setTitle(r.title); }}>Discard changes</button>
                </>
              ) : (
                <>
                  <button type="button" className="btn" onClick={() => setEditing(true)}>Edit</button>
                  <button type="button" className="btn btn--ghost" disabled={busy} onClick={regenerate}>Regenerate</button>
                  {EXPORT_FORMATS.map(([f, l]) => <button key={f} type="button" className="btn btn--ghost" disabled={busy} onClick={() => exportAs(f)}>Export {l}</button>)}
                  <button type="button" className="btn btn--ghost" onClick={remove}>Delete</button>
                </>
              )}
            </div>
            {editing ? <Field label="Report title" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /> : null}
            <div className="ri-report-layout">
              <div>
                {(editing ? draft : r.sections).map((s, si) => (
                  <section key={s.id} className="card" aria-labelledby={`h-${s.id}`}>
                    <h2 className="card__title" id={`h-${s.id}`}>{s.heading}</h2>
                    {editing ? (
                      <>
                        {s.blocks.map((b) => <BlockEdit key={b.id} block={b} labels={labels}
                          onText={(v) => setSec((d) => setBlockText(d, s.id, b.id, v))} onBullets={(v) => setSec((d) => setBlockBullets(d, s.id, b.id, v))}
                          onCites={(c) => setSec((d) => d.map((x) => (x.id === s.id ? { ...x, blocks: x.blocks.map((y) => (y.id === b.id ? { ...y, cite: c } : y)) } : x)))}
                          onRemove={() => setSec((d) => removeBlock(d, s.id, b.id))} />)}
                        <div className="btn-row">
                          <button type="button" className="btn btn--ghost" onClick={() => setSec((d) => addTextBlock(d, s.id))}>Add text</button>
                          <button type="button" className="btn btn--ghost" disabled={si === 0} onClick={() => setSec((d) => moveSection(d, s.id, -1))}>Move section up</button>
                          <button type="button" className="btn btn--ghost" disabled={si === draft.length - 1} onClick={() => setSec((d) => moveSection(d, s.id, 1))}>Move section down</button>
                          <button type="button" className="btn btn--ghost" onClick={() => setSec((d) => removeSection(d, s.id))}>Remove section</button>
                        </div>
                      </>
                    ) : s.blocks.map((b) => <BlockView key={b.id} block={b} onCite={(c) => setCited(sourceByLabel(r, c))} />)}
                  </section>
                ))}
                {editing ? <button type="button" className="btn btn--ghost" onClick={() => setSec((d) => addSection(d, ''))}>Add a section</button> : null}
              </div>
              <div>
                <SourceCard source={cited} onClose={() => setCited(null)} />
                <section className="card" aria-label="Sources in this report">
                  <h2 className="card__title">Sources</h2>
                  <ol className="list">{r.sources.map((s) => <li key={s.label} className="row"><div className="row__main"><button type="button" className="link-button" onClick={() => setCited(s)}>[{s.label}] {s.text}</button></div></li>)}</ol>
                </section>
                <section className="card" aria-label="Version history">
                  <h2 className="card__title">Version history</h2>
                  {(versions.data?.items ?? []).length === 0 ? <p className="row__meta">No earlier versions yet.</p> : (
                    <ul className="list">{versions.data.items.map((v) => <li key={v.id} className="row"><div className="row__main"><span className="row__meta">Version {v.version} · {v.reason} · {when(v.created_at)}</span>
                      <button type="button" className="link-button" disabled={editing || busy} onClick={() => { if (window.confirm('Restore this version? The current one is saved to the history first.')) attempt(() => ri.reports.restore(workspaceId, reportId, v.id), setMsg, 'Version restored.').then((o) => { if (o) { res.reload({ quiet: true }); versions.reload({ quiet: true }); } }); }}>Restore</button></div></li>)}</ul>
                  )}
                </section>
              </div>
            </div>
          </>
        )}
      </Loaded>
    </ResearchFrame>
  );
}
