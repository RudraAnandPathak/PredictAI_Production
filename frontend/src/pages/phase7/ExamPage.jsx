import { useState } from 'react';
import { phase7, workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { ORIGIN_LABEL } from '../../features/phase7/labels.js';
import { PredictionPanel } from './PredictionPanel.jsx';
import { ReviewBox } from '../../components/reviews/ReviewBox.jsx';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

const DOC_KINDS = { syllabus: 'Syllabus', curriculum: 'Curriculum', notes: 'Notes', slides: 'Slides', past_paper: 'Past paper', other: 'Other' };

/** Exam tools: one subject per project, built from documents you uploaded. */
export function ExamPage() {
  const projects = useResource(() => workspace.projects.list({ status: 'active', limit: 100 }), []);
  const [projectId, setProjectId] = useState('');
  const subjects = useResource(() => (projectId ? phase7.exam.subjects(projectId) : Promise.resolve({ items: [] })), [projectId]);
  const [name, setName] = useState('');
  const [level, setLevel] = useState('');
  const [msg, setMsg] = useState(null);
  async function create(e) {
    e.preventDefault();
    const out = await attempt(() => phase7.exam.createSubject(projectId, { name, level: level || undefined }), setMsg, 'Subject created.');
    if (out) { setName(''); setLevel(''); subjects.reload({ quiet: true }); }
  }
  return (
    <article className="page">
      <PageHeader eyebrow="Exam tools" title="Exam intelligence" lead="Analyse past papers against a syllabus and build mock exams from your own material. Topic figures are historical frequencies from the papers you supply, never a forecast of a future paper." />
      <Loaded res={projects} what="your projects">
        {(p) => (
          <>
            <Field as="select" label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">Choose a project…</option>
              {p.items.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </Field>
            {projectId ? (
              <>
                {(subjects.data?.items ?? []).length === 0 ? <Empty title="No subjects in this project yet"><p>Create one below, then add its documents.</p></Empty> : (
                  <ul className="list">{subjects.data.items.map((s) => <li key={s.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/exam/${encodeURIComponent(s.id)}`}>{s.name}</Link></span><span className="row__meta">{s.level ?? 'no level set'} · created {when(s.created_at)}</span></div></li>)}</ul>
                )}
                <form className="card" onSubmit={create}>
                  <h2 className="card__title">New subject</h2>
                  <Field label="Subject name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required />
                  <Field label="Level (optional)" value={level} maxLength={60} onChange={(e) => setLevel(e.target.value)} />
                  {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
                  <div className="card__actions"><button className="btn" disabled={!name.trim()}>Create subject</button></div>
                </form>
              </>
            ) : null}
          </>
        )}
      </Loaded>
    </article>
  );
}

function Documents({ subject }) {
  const docs = useResource(() => phase7.exam.documents(subject.id), [subject.id]);
  const assets = useResource(() => workspace.assets.list(subject.project_id, { kind: 'document', limit: 100 }), [subject.project_id]);
  const [assetId, setAssetId] = useState('');
  const [kind, setKind] = useState('past_paper');
  const [year, setYear] = useState('');
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  async function add(e) {
    e.preventDefault();
    setBusy(true);
    const out = await attempt(() => phase7.exam.addDocument(subject.id, { asset_id: assetId, kind, exam_year: year ? Number(year) : undefined }), setMsg, 'Document added and read.');
    setBusy(false);
    if (out) docs.reload({ quiet: true });
  }
  return (
    <section className="card" aria-label="Documents">
      <h2 className="card__title">Documents</h2>
      <Notice kind="info">Documents come from your uploads. Scanned images are only supported inside scanned PDFs; a standard account can use up to 3 documents.</Notice>
      <Loaded res={docs} what="documents">{(d) => d.items.length === 0 ? <p>No documents added yet.</p> : (
        <ul className="list">{d.items.map((x) => <li key={x.id} className="row"><div className="row__main"><span className="row__title">{x.title ?? x.id}</span><span className="row__meta">{DOC_KINDS[x.kind] ?? x.kind}{x.exam_year ? ` · ${x.exam_year}` : ''} · {x.status}</span></div></li>)}</ul>
      )}</Loaded>
      <form onSubmit={add}>
        <Field as="select" label="Uploaded document" value={assetId} onChange={(e) => setAssetId(e.target.value)}>
          <option value="">Choose…</option>
          {(assets.data?.items ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Field>
        <Field as="select" label="What is it?" value={kind} onChange={(e) => setKind(e.target.value)}>{Object.entries(DOC_KINDS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Field>
        {kind === 'past_paper' ? <Field label="Exam year" inputMode="numeric" value={year} maxLength={4} onChange={(e) => setYear(e.target.value.replace(/\D/g, ''))} /> : null}
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={busy || !assetId}>{busy ? 'Reading…' : 'Add document'}</button></div>
      </form>
    </section>
  );
}

function Analysis({ subject }) {
  const list = useResource(() => phase7.exam.analyses(subject.id), [subject.id]);
  const [current, setCurrent] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const latest = current ?? list.data?.items?.[0]?.id ?? null;
  const shown = useResource(() => (latest ? phase7.exam.analysis(latest) : Promise.resolve(null)), [latest]);
  async function run() {
    setBusy(true);
    const out = await attempt(() => phase7.exam.analyse(subject.id, {}), setMsg);
    setBusy(false);
    if (out) { setCurrent(out.id); list.reload({ quiet: true }); }
  }
  const r = shown.data?.result;
  return (
    <section className="card" aria-label="Topic analysis">
      <div className="card__row"><h2 className="card__title">Topic analysis</h2><button type="button" className="btn" disabled={busy} onClick={run}>{busy ? 'Analysing…' : r ? 'Analyse again' : 'Analyse papers'}</button></div>
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      {r ? (
        <>
          <Notice kind="info">{r.notice}</Notice>
          {r.warnings?.map((w, i) => <Notice key={i} kind="warn">{w}</Notice>)}
          <p className="row__meta">{r.papers} paper(s), {r.questions} question(s), {r.marks_total} marks. {r.unmapped_questions ? `${r.unmapped_questions} question(s) could not be matched to a chapter.` : ''}</p>
          <table className="table"><thead><tr><th scope="col">Rank</th><th scope="col">Chapter</th><th scope="col">Historical frequency (questions)</th><th scope="col">Relative emphasis (marks share)</th><th scope="col">Heuristic score</th></tr></thead>
            <tbody>{r.chapters.map((c) => <tr key={c.chapter}><td>{c.rank ?? '—'}</td><td>{c.chapter}</td><td>{c.questions}</td><td>{c.marks_share !== undefined ? `${Math.round(c.marks_share * 1000) / 10}%` : '—'}</td><td>{c.score ?? '—'}</td></tr>)}</tbody></table>
          {r.never_asked?.length ? <p className="row__meta">Not asked in the supplied papers: {r.never_asked.join(', ')}.</p> : null}
        </>
      ) : <p>No analysis yet. Add the syllabus and past papers, then analyse.</p>}
    </section>
  );
}

function MockExams({ subject }) {
  const list = useResource(() => phase7.exam.assessments(subject.id), [subject.id]);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [counts, setCounts] = useState({ mcq: 10, short: 5, long: 2 });
  const [openId, setOpenId] = useState(null);
  async function generate(e) {
    e.preventDefault();
    setBusy(true);
    const out = await attempt(async () => { const bp = await phase7.exam.blueprint(subject.id, { type_counts: counts }); return phase7.exam.generate(bp.id, {}); }, setMsg, 'A draft mock exam was created. Review it before approving.');
    setBusy(false);
    if (out) { list.reload({ quiet: true }); setOpenId(out.id); }
  }
  return (
    <section className="card" aria-label="Mock exams">
      <h2 className="card__title">Mock exams</h2>
      <Loaded res={list} what="mock exams">{(d) => d.items.length === 0 ? <p>None yet.</p> : (
        <ul className="list">{d.items.map((a) => <li key={a.id} className="row"><div className="row__main"><span className="row__title">{a.title}</span><span className="row__meta"><Badge tone={a.status === 'approved' ? 'ok' : a.status === 'rejected' ? 'muted' : 'warn'}>{a.status}</Badge> · {when(a.created_at)}</span></div><button type="button" className="btn btn--ghost" onClick={() => setOpenId(a.id)}>Review</button></li>)}</ul>
      )}</Loaded>
      <form onSubmit={generate}>
        <div className="grid grid--2">{[['mcq', 'Multiple choice'], ['short', 'Short answer'], ['long', 'Long answer']].map(([k, l]) => (
          <Field key={k} label={l} inputMode="numeric" value={counts[k]} onChange={(e) => setCounts({ ...counts, [k]: Number(e.target.value.replace(/\D/g, '') || 0) })} />))}</div>
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={busy}>{busy ? 'Building…' : 'Build a draft mock exam'}</button></div>
      </form>
      {openId ? <Assessment id={openId} onChanged={() => list.reload({ quiet: true })} /> : null}
    </section>
  );
}

function Assessment({ id, onChanged }) {
  const res = useResource(() => phase7.exam.assessment(id), [id]);
  const [msg, setMsg] = useState(null);
  const [ack, setAck] = useState(false);
  const [confirm, setConfirm] = useState(false);
  async function approve() {
    const out = await attempt(() => phase7.exam.approve(id, { confirm: true, acknowledgeWarnings: ack }), setMsg, 'Approved. It is now published.');
    if (out) { res.reload({ quiet: true }); onChanged(); }
  }
  async function reject() {
    const out = await attempt(() => phase7.exam.reject(id), setMsg, 'Rejected.');
    if (out) { res.reload({ quiet: true }); onChanged(); }
  }
  return (
    <Loaded res={res} what="the mock exam">
      {(a) => {
        const blocking = a.validation?.issues?.filter((i) => i.severity === 'blocking') ?? [];
        const warnings = a.validation?.issues?.filter((i) => i.severity === 'warning') ?? [];
        return (
          <div className="card" role="region" aria-label={a.title}>
            <h3 className="card__title">{a.title} <Badge tone={a.status === 'approved' ? 'ok' : 'warn'}>{a.status}</Badge></h3>
            {a.notice ? <Notice kind="info">{a.notice}</Notice> : null}
            {a.prediction_notice ? <Notice kind="warn">{a.prediction_notice}</Notice> : null}
            {blocking.map((i, k) => <Notice key={k} kind="error">{i.message}</Notice>)}
            {warnings.map((i, k) => <Notice key={k} kind="warn">{i.message}</Notice>)}
            <ol>{a.questions.filter((q) => q.included).map((q) => (
              <li key={q.id}><p>{q.text} <span className="row__meta">({q.marks} marks, {q.difficulty})</span></p>
                <p className="row__meta"><Badge tone={q.origin === 'past_paper' || q.origin === 'source_derived' ? 'ok' : 'warn'}>{ORIGIN_LABEL[q.origin] ?? q.origin}</Badge> {q.chapter ?? ''}</p>
                {q.options ? <ul>{q.options.map((o, i) => <li key={i}>{typeof o === 'string' ? o : `${o.label ? `${o.label}. ` : ''}${o.text ?? ''}`}</li>)}</ul> : null}</li>))}</ol>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            {a.status === 'draft' && a.your_access !== 'view' ? (
              <div className="btn-row">
                {warnings.length ? <label className="row__check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have read the warnings</label> : null}
                {confirm ? <button type="button" className="btn" disabled={blocking.length > 0 || (warnings.length > 0 && !ack)} onClick={approve}>Confirm approval</button>
                  : <button type="button" className="btn" disabled={blocking.length > 0} onClick={() => setConfirm(true)}>Approve…</button>}
                <button type="button" className="btn btn--ghost" onClick={reject}>Reject</button>
              </div>
            ) : null}
            <ReviewBox key={a.id} kind="exam_paper" targetId={a.id} />
          </div>
        );
      }}
    </Loaded>
  );
}

/** One exam subject: documents, topic analysis and mock exams. */
export function ExamSubjectPage() {
  const { subjectId } = useParams('/exam/:subjectId');
  const res = useResource(() => phase7.exam.subject(subjectId), [subjectId]);
  return (
    <article className="page">
      <Loaded res={res} what="this subject">
        {(s) => (
          <>
            <PageHeader eyebrow={s.level ?? 'Subject'} title={s.name} back={{ to: '/exam', label: 'Exam tools' }} />
            <Documents subject={s} />
            <Analysis subject={s} />
            <PredictionPanel subject={s} />
            <MockExams subject={s} />
          </>
        )}
      </Loaded>
    </article>
  );
}
