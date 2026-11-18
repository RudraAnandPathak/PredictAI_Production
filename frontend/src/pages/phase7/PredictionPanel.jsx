import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import {
  AI_MODES, FLAG_LABEL, MAX_ARTICLES, PREDICTION_NOTE, aiLines, backtestLine, bonusText, chapterRows, optionLines, predictBody, predictProblem, priorityLabel, priorityTone, scoreText, verdictLabel, verdictTone,
} from '../../features/phase7/predict.js';
import { ORIGIN_LABEL } from '../../features/phase7/labels.js';
import { Badge, Loaded, Notice, attempt, when } from './shared.jsx';
import { ReviewBox } from '../../components/reviews/ReviewBox.jsx';

/** Exam prediction: ranks chapters from your papers (and, if allowed, exam articles from the web), tests the ranking on your own papers, then builds an estimated paper. */
export function PredictionPanel({ subject }) {
  const options = useResource(() => phase7.exam.predictionOptions(subject.id), [subject.id]);
  const history = useResource(() => phase7.exam.predictions(subject.id), [subject.id]);
  const [form, setForm] = useState({ yearFrom: '', yearTo: '', useWeb: false, maxArticles: 4, examName: '' });
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState(null);
  async function run(e) {
    e.preventDefault();
    const problem = predictProblem(form);
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(true);
    const out = await attempt(() => phase7.exam.predict(subject.id, predictBody(form)), setMsg);
    setBusy(false);
    if (out) { setCurrent(out.id); history.reload({ quiet: true }); }
  }
  return (
    <section className="card" aria-label="Exam prediction">
      <h2 className="card__title">Exam prediction</h2>
      <Notice kind="info">{PREDICTION_NOTE}</Notice>
      <Loaded res={options} what="the prediction options">
        {(o) => (
          <>
            <ul className="list">{optionLines({ web: o.web, ai: o.ai }).map((l) => <li key={l} className="row__meta">{l}</li>)}</ul>
            <form onSubmit={run}>
              <div className="grid grid--2">
                <Field label="First year to use (optional)" inputMode="numeric" value={form.yearFrom} onChange={(e) => setForm({ ...form, yearFrom: e.target.value.replace(/\D/g, '').slice(0, 4) })} />
                <Field label="Last year to use (optional)" inputMode="numeric" value={form.yearTo} onChange={(e) => setForm({ ...form, yearTo: e.target.value.replace(/\D/g, '').slice(0, 4) })} />
                <Field label="Exam name for the web search (optional)" value={form.examName} onChange={(e) => setForm({ ...form, examName: e.target.value })} />
                <Field label={`Articles to read (1 to ${MAX_ARTICLES})`} inputMode="numeric" value={form.maxArticles} onChange={(e) => setForm({ ...form, maxArticles: e.target.value.replace(/\D/g, '').slice(0, 1) })} />
              </div>
              <label className="row__check"><input type="checkbox" checked={form.useWeb} disabled={!o.web?.allowed} onChange={(e) => setForm({ ...form, useWeb: e.target.checked })} /> Also read exam articles from the web</label>
              {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
              <div className="card__actions"><button className="btn" disabled={busy}>{busy ? 'Working…' : 'Rank the chapters'}</button></div>
            </form>
          </>
        )}
      </Loaded>
      <Loaded res={history} what="earlier predictions">
        {(d) => d.items.length === 0 ? null : (
          <ul className="list">{d.items.map((p) => (
            <li key={p.id} className="row"><div className="row__main"><span className="row__title">{when(p.created_at)}</span>
              <span className="row__meta">{p.verdict ? <Badge tone={verdictTone(p.verdict)}>{verdictLabel(p.verdict)}</Badge> : null} {p.web_used ? 'Web articles used' : 'Papers only'}</span></div>
              <button type="button" className="btn btn--ghost" onClick={() => setCurrent(p.id)}>Open</button></li>))}</ul>
        )}
      </Loaded>
      {current ? <PredictionResult id={current} /> : null}
    </section>
  );
}

function PredictionResult({ id }) {
  const res = useResource(() => phase7.exam.prediction(id), [id]);
  const [mode, setMode] = useState('all');
  const [counts, setCounts] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [paper, setPaper] = useState(null);
  async function build(r) {
    setBusy(true);
    const spec = counts ? { type_counts: counts } : {};
    const out = await attempt(() => phase7.exam.predictedPaper(id, { ai: mode, spec }), setMsg, 'A draft predicted paper was created. Review every question before approving.');
    setBusy(false);
    if (out) setPaper(out);
  }
  return (
    <Loaded res={res} what="the prediction">
      {(p) => {
        const r = p.result;
        const rows = chapterRows(r);
        const mix = r.type_mix?.type_counts ?? { mcq: 10, short: 5, long: 2 };
        const c = counts ?? mix;
        return (
          <div className="card" role="region" aria-label="Prediction result">
            <h3 className="card__title">Ranking <Badge tone={verdictTone(r.backtest?.verdict)}>{verdictLabel(r.backtest?.verdict)}</Badge></h3>
            <Notice kind={r.backtest?.verdict === 'better_than_chance' ? 'info' : 'warn'}>{backtestLine(r.backtest)}</Notice>
            <p className="row__meta">{r.papers} paper(s), {r.questions} question(s).</p>
            <table className="table"><thead><tr><th scope="col">Rank</th><th scope="col">Chapter</th><th scope="col">Score</th><th scope="col">From papers</th><th scope="col">From web</th><th scope="col">Reading</th></tr></thead>
              <tbody>{rows.map((x) => (
                <tr key={x.chapter}><td>{x.rank}</td><td>{x.chapter}</td><td>{scoreText(x.score)}</td><td>{scoreText(x.history)}</td><td>{bonusText(x.web)}</td>
                  <td><Badge tone={priorityTone(x.priority)}>{priorityLabel(x.priority)}</Badge>
                    {x.flags.map((f) => <div key={f.code} className="row__meta"><Badge tone="warn">{FLAG_LABEL[f.code] ?? f.code}</Badge> {f.message}</div>)}
                    {x.why.map((w, i) => <div key={i} className="row__meta">{w}</div>)}
                    {x.webSentences.map((s, i) => <div key={i} className="row__meta">“{s.text}” ({s.source})</div>)}</td></tr>))}</tbody></table>
            {r.web?.requested ? (
              <>
                <p className="row__meta">{r.web.used ? `Read ${r.web.sources?.length ?? 0} article(s) via ${r.web.provider}.` : `Web articles were not used: ${r.web.reason ?? 'unavailable'}.`}</p>
                {r.web.sources?.length ? <ul className="list">{r.web.sources.map((s) => <li key={s.url} className="row__meta"><a href={s.url} rel="noreferrer noopener" target="_blank">{s.title || s.url}</a></li>)}</ul> : null}
              </>
            ) : null}
            {r.recurring?.items?.length ? (
              <>
                <h4>Questions that came back</h4>
                <ul className="list">{r.recurring.items.slice(0, 8).map((q, i) => <li key={i} className="row__meta">{q.text ?? q.sample} ({(q.years ?? []).join(', ')})</li>)}</ul>
              </>
            ) : null}
            <h4>Build a predicted paper</h4>
            <fieldset><legend>Who writes the questions</legend>
              {AI_MODES.map((m) => (
                <label key={m.value} className="row__check"><input type="radio" name={`ai-${id}`} checked={mode === m.value} onChange={() => setMode(m.value)} /> {m.label}<span className="row__meta"> {m.hint}</span></label>))}
            </fieldset>
            <div className="grid grid--2">{[['mcq', 'Multiple choice'], ['short', 'Short answer'], ['long', 'Long answer']].map(([k, l]) => (
              <Field key={k} label={l} inputMode="numeric" value={c[k] ?? 0} onChange={(e) => setCounts({ ...c, [k]: Number(e.target.value.replace(/\D/g, '') || 0) })} />))}</div>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <div className="card__actions"><button type="button" className="btn" disabled={busy} onClick={() => build(r)}>{busy ? 'Building…' : 'Build a draft predicted paper'}</button></div>
            {paper ? <PaperSummary paper={paper} /> : null}
            <ReviewBox key={id} kind="exam_prediction" targetId={id} />
          </div>
        );
      }}
    </Loaded>
  );
}

function PaperSummary({ paper }) {
  const a = paper.assessment;
  return (
    <div role="status">
      <p className="row__title">{a.title}: {(a.questions ?? []).length} question(s)</p>
      {aiLines(paper.ai).map((l, i) => <p key={i} className="row__meta">{l}</p>)}
      <p className="row__meta">{Object.entries((a.questions ?? []).reduce((m, q) => ({ ...m, [q.origin]: (m[q.origin] ?? 0) + 1 }), {})).map(([k, n]) => `${n} ${ORIGIN_LABEL[k] ?? k}`).join(' · ')}</p>
      <p className="row__meta">It is a draft in “Mock exams” above. AI-written questions must be read and acknowledged before approval.</p>
    </div>
  );
}
