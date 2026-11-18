import { useState } from 'react';
import { phase7 } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { DELIVERY_OUTCOME_LABEL, EXPORT_FORMATS, EXPORT_FORMAT_LABEL, FREQUENCY_LABEL, SCHEDULE_STATUS_LABEL } from '../../features/phase7/labels.js';
import { saveBlob } from '../research/shared.jsx';
import { Badge, Empty, Loaded, Notice, PageHeader, attempt, when } from './shared.jsx';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const browserZone = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } };

function CreateForm({ adapters, initialReport, onDone }) {
  const reports = useResource(() => phase7.reports.list({ limit: 100 }), []);
  const [reportId, setReportId] = useState(initialReport ?? '');
  const [frequency, setFrequency] = useState('weekly');
  const [mode, setMode] = useState('refresh');
  const [time, setTime] = useState('09:00');
  const [weekday, setWeekday] = useState(0);
  const [dom, setDom] = useState(1);
  const [runAt, setRunAt] = useState('');
  const [format, setFormat] = useState('pdf');
  const [email, setEmail] = useState('');
  const [zone, setZone] = useState(browserZone());
  const [msg, setMsg] = useState(null);
  const emailOn = adapters.find((a) => a.kind === 'email')?.configured;
  async function submit(e) {
    e.preventDefault();
    const spec = { mode, frequency, timezone: zone, export_format: format, recipients: [{ kind: 'local', target: 'me' }, ...(emailOn && email.trim() ? [{ kind: 'email', target: email.trim() }] : [])] };
    if (frequency === 'once') spec.run_at = runAt ? new Date(runAt).toISOString() : '';
    else spec.local_time = time;
    if (frequency === 'weekly') spec.weekday = Number(weekday);
    if (frequency === 'monthly') spec.day_of_month = Number(dom);
    const out = await attempt(() => phase7.schedules.create(reportId, spec), setMsg, 'Schedule created.');
    if (out) onDone();
  }
  return (
    <form className="card" onSubmit={submit} aria-label="New schedule">
      <h2 className="card__title">New schedule</h2>
      <Field as="select" label="Report" value={reportId} onChange={(e) => setReportId(e.target.value)} required><option value="">Choose…</option>{(reports.data?.items ?? []).map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}</Field>
      <Field as="select" label="How often" value={frequency} onChange={(e) => setFrequency(e.target.value)}>{Object.entries(FREQUENCY_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Field>
      {frequency === 'once' ? <Field type="datetime-local" label="When" value={runAt} onChange={(e) => setRunAt(e.target.value)} required /> : <Field type="time" label="At" value={time} onChange={(e) => setTime(e.target.value)} required />}
      {frequency === 'weekly' ? <Field as="select" label="On" value={weekday} onChange={(e) => setWeekday(e.target.value)}>{WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</Field> : null}
      {frequency === 'monthly' ? <Field label="Day of month" inputMode="numeric" value={dom} onChange={(e) => setDom(e.target.value.replace(/\D/g, ''))} hint="Short months use their last day." /> : null}
      <Field label="Time zone" value={zone} onChange={(e) => setZone(e.target.value)} hint="For example Asia/Kolkata." />
      <Field as="select" label="Each run sends" value={mode} onChange={(e) => setMode(e.target.value)}><option value="refresh">The report’s latest saved version</option><option value="snapshot">The version it has now (pinned)</option></Field>
      <Field as="select" label="File" value={format} onChange={(e) => setFormat(e.target.value)}>{EXPORT_FORMATS.map((f) => <option key={f} value={f}>{EXPORT_FORMAT_LABEL[f]}</option>)}</Field>
      <Notice kind="info">Every run keeps the file in PredictAI for you to download. Email: {emailOn ? 'available.' : 'not available, because this server has no email delivery set up.'} Slack: {adapters.find((a) => a.kind === 'slack')?.configured ? 'available.' : 'not available, because it is not configured on this server.'}</Notice>
      {emailOn ? <Field type="email" label="Also email to (optional)" value={email} onChange={(e) => setEmail(e.target.value)} /> : null}
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
      <div className="card__actions"><button className="btn" disabled={!reportId}>Create schedule</button></div>
    </form>
  );
}

/** Scheduled report delivery. A schedule only runs while the scheduler script is being run on the server. */
export function SchedulesPage() {
  const list = useResource(() => phase7.schedules.list({ limit: 100 }), []);
  const adapters = useResource(() => phase7.schedules.adapters(), []);
  const initial = new URLSearchParams(window.location.search).get('report');
  return (
    <article className="page">
      <PageHeader eyebrow="Reports" title="Scheduled delivery" lead="Create a report file on a schedule and keep it for download. Access to the report is checked again on every run." />
      {adapters.data ? <Notice kind="info" title="How schedules run">{adapters.data.scheduler}</Notice> : null}
      <Loaded res={list} what="your schedules">
        {(d) => (
          <>
            {d.items.length === 0 ? <Empty title="No schedules yet"><p>Create one below.</p></Empty> : (
              <ul className="list">{d.items.map((s) => (
                <li key={s.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/schedules/${encodeURIComponent(s.id)}`}>{FREQUENCY_LABEL[s.frequency]} · {EXPORT_FORMAT_LABEL[s.export_format]}</Link></span>
                  <span className="row__meta"><Badge tone={s.status === 'active' ? 'ok' : s.status === 'blocked' ? 'danger' : 'muted'}>{SCHEDULE_STATUS_LABEL[s.status]}</Badge> · next {s.next_run_at ? when(s.next_run_at) : 'none'}{s.last_run ? ` · last run ${s.last_run.outcome}` : ''}</span></div></li>))}</ul>
            )}
            {adapters.data ? <CreateForm adapters={adapters.data.items} initialReport={initial} onDone={() => list.reload({ quiet: true })} /> : null}
          </>
        )}
      </Loaded>
    </article>
  );
}

/** One schedule: pause, resume, end, run now, history with delivery outcomes, manual retry and download. */
export function SchedulePage() {
  const { scheduleId } = useParams('/schedules/:scheduleId');
  const res = useResource(() => phase7.schedules.get(scheduleId), [scheduleId]);
  const runs = useResource(() => phase7.schedules.runs(scheduleId), [scheduleId]);
  const [msg, setMsg] = useState(null);
  const refresh = () => { res.reload({ quiet: true }); runs.reload({ quiet: true }); };
  const act = async (action, text) => { const out = await attempt(() => phase7.schedules.action(scheduleId, action), setMsg, text); if (out) refresh(); };
  return (
    <article className="page">
      <Loaded res={res} what="this schedule">
        {(s) => (
          <>
            <PageHeader eyebrow={SCHEDULE_STATUS_LABEL[s.status]} title={`${FREQUENCY_LABEL[s.frequency]} · ${EXPORT_FORMAT_LABEL[s.export_format]}`} back={{ to: '/schedules', label: 'Schedules' }} lead={s.mode_note} />
            {s.status === 'blocked' ? <Notice kind="error" title="Blocked">Your access to the report ended, so nothing will be sent. Ask for access again or end this schedule.</Notice> : null}
            <p className="row__meta">{s.local_time} {s.timezone} · next run {s.next_run_at ? when(s.next_run_at) : 'none'} · report <Link to={`/reports/${encodeURIComponent(s.report_id)}`}>open</Link></p>
            {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
            <div className="btn-row">
              {s.status === 'active' ? <button type="button" className="btn btn--ghost" onClick={() => act('pause', 'Paused.')}>Pause</button> : null}
              {s.status === 'paused' ? <button type="button" className="btn btn--ghost" onClick={() => act('resume', 'Resumed.')}>Resume</button> : null}
              {s.status !== 'ended' ? <button type="button" className="btn btn--ghost" onClick={async () => { if (window.confirm('End this schedule? It cannot be restarted.')) act('end', 'Ended.'); }}>End</button> : null}
              {s.status !== 'ended' && s.status !== 'blocked' ? <button type="button" className="btn" onClick={async () => { const out = await attempt(() => phase7.schedules.run(scheduleId), setMsg, 'Ran now.'); if (out) refresh(); }}>Run now</button> : null}
            </div>
            <section className="card" aria-label="History"><h2 className="card__title">History</h2>
              <Loaded res={runs} what="the history">{(d) => d.items.length === 0 ? <p>No runs yet.</p> : (
                <ul className="list">{d.items.map((r) => (
                  <li key={r.id} className="row"><div className="row__main"><span className="row__title">{when(r.scheduled_for)}{r.manual ? ' (run by you)' : ''}</span>
                    <span className="row__meta">{r.outcome}{r.reason ? `: ${r.reason}` : ''}{r.report_version ? ` · version ${r.report_version}` : ''}</span>
                    {r.deliveries.map((x) => <span key={x.id} className="row__meta"> {x.provider} → {x.recipient}: {DELIVERY_OUTCOME_LABEL[x.outcome]} ({x.attempts}/{x.max_attempts}){x.last_error ? `, ${x.last_error}` : ''}{x.outcome === 'dead' || x.outcome === 'failed' ? <button type="button" className="link-button" onClick={async () => { await attempt(() => phase7.schedules.retry(x.id), setMsg, 'Retried.'); refresh(); }}>Retry</button> : null}</span>)}</div>
                    {r.export_id ? <button type="button" className="btn btn--ghost" onClick={() => attempt(async () => saveBlob(await phase7.schedules.download(r.id)), setMsg)}>Download</button> : null}</li>))}</ul>
              )}</Loaded>
            </section>
          </>
        )}
      </Loaded>
    </article>
  );
}
