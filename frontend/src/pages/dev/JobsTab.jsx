import { useEffect, useState } from 'react';
import { dev } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { ACTIVE_JOB, blockedReasonText, bytesText, canCancel, canRetry, cleanPath, errorClassText, isActive, secondsText } from '../../features/dev/labels.js';
import { Empty, JobStatus, Loaded, Notice, attempt, when } from './shared.jsx';

function Logs({ projectId, jobId }) {
  const logs = useResource(() => dev.jobs.logs(projectId, jobId), [projectId, jobId]);
  return (
    <Loaded res={logs} what="logs">
      {(d) => (
        <div>
          <p className="row__meta">Secrets are replaced with *** before logs are stored.{d.stdout?.truncated || d.stderr?.truncated ? ' Output was cut at the limit.' : ''}</p>
          <h4>Output</h4><pre className="code" tabIndex={0}>{d.stdout?.text || '(nothing printed)'}</pre>
          <h4>Errors</h4><pre className="code" tabIndex={0}>{d.stderr?.text || '(none)'}</pre>
        </div>
      )}
    </Loaded>
  );
}

/** Submit, watch, cancel and retry jobs. Jobs run in the separate worker, never inside the web server. */
export function JobsTab({ projectId }) {
  const jobs = useResource(() => dev.jobs.list(projectId), [projectId]);
  const [entry, setEntry] = useState('');
  const [kind, setKind] = useState('script');
  const [msg, setMsg] = useState(null);
  const [shown, setShown] = useState(null);
  const items = jobs.data?.items ?? [];
  const anyActive = items.some((j) => isActive(j.status));

  useEffect(() => {                                    // poll only while something is queued or running
    if (!anyActive) return undefined;
    const t = setInterval(() => jobs.reload({ quiet: true }), 3000);
    return () => clearInterval(t);
  }, [anyActive, jobs]);

  async function submit(e) {
    e.preventDefault();
    const p = cleanPath(entry);
    if (!p) { setMsg({ kind: 'error', text: 'Enter the path of a .py file or notebook in this project.' }); return; }
    const out = await attempt(() => dev.jobs.submit(projectId, { kind, entry: p }), setMsg, 'Job queued. A worker picks it up; this page updates by itself.');
    if (out) { setEntry(''); jobs.reload({ quiet: true }); }
  }
  const act = async (fn, ok) => { const out = await attempt(fn, setMsg, ok); if (out) jobs.reload({ quiet: true }); };

  return (
    <div>
      <form className="card" onSubmit={submit} aria-label="Run a job">
        <Field as="select" label="What to run" value={kind} onChange={(e) => setKind(e.target.value)}><option value="script">A Python file</option><option value="notebook">A notebook (code cells in order)</option></Field>
        <Field label="Path" value={entry} onChange={(e) => setEntry(e.target.value)} hint="Runs with no network access, with time, memory and output limits." />
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={!entry.trim()}>Run</button></div>
      </form>
      <Loaded res={jobs} what="jobs">
        {() => (items.length === 0 ? <Empty title="No jobs yet"><p>Run a file above. Results and logs appear here.</p></Empty> : (
          <ul className="list">{items.map((j) => (
            <li key={j.id} className="row"><div className="row__main"><span className="row__title">{j.entry || j.kind} <JobStatus status={j.status} /></span>
              <span className="row__meta">{when(j.created_at)} · attempt {j.attempts} of {j.max_attempts}{j.wall_ms != null ? ` · ${secondsText(j.wall_ms / 1000)}` : ''}{j.peak_rss_bytes ? ` · peak memory ${bytesText(j.peak_rss_bytes)}` : ''}{j.exit_code != null ? ` · exit ${j.exit_code}` : ''}</span>
              {errorClassText(j.error_class) ? <span className="row__meta">{errorClassText(j.error_class)}</span> : null}
              {blockedReasonText(j.blocked_reason) ? <span className="row__meta">{blockedReasonText(j.blocked_reason)}</span> : null}</div>
              <div className="card__actions">
                <button className="btn btn--ghost" type="button" onClick={() => setShown(shown === j.id ? null : j.id)}>{shown === j.id ? 'Hide logs' : 'Logs'}</button>
                {canCancel(j.status) ? <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.jobs.cancel(projectId, j.id), 'Stopping the job.')}>Cancel</button> : null}
                {canRetry(j.status) ? <button className="btn btn--ghost" type="button" onClick={() => act(() => dev.jobs.retry(projectId, j.id), 'Queued again.')}>Retry</button> : null}
              </div>
              {shown === j.id ? <Logs projectId={projectId} jobId={j.id} /> : null}
            </li>))}</ul>
        ))}
      </Loaded>
      <p className="row__meta">Active states: {ACTIVE_JOB.join(', ')}. A job whose worker disappeared is recovered automatically and retried a bounded number of times.</p>
    </div>
  );
}
