import { phase7 } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { quotaText } from '../../features/phase7/labels.js';
import { Empty, Loaded, Notice, PageHeader, Stat, StateBadge, when } from './shared.jsx';

function Section({ title, section, children, recoveryHint }) {
  return (
    <section className="card" aria-labelledby={`dash-${title}`}>
      <div className="card__row"><h2 className="card__title" id={`dash-${title}`}>{title}</h2>{section.state !== 'ok' ? <StateBadge state="unavailable" /> : null}</div>
      {section.state === 'ok' ? children : <Notice kind="warn" title={section.reason}>{section.recovery ?? recoveryHint}</Notice>}
    </section>
  );
}

const LINKS = { reports: '/reports', models: '/models', exam: '/exam', workspaces: '/workspaces', schedules: '/schedules' };

/** The signed-in person's dashboard. Every number is a count of their own records, loaded from the server; a section that cannot be read says so and the rest still work. */
export function DashboardPage() {
  const res = useResource(() => phase7.dashboard(), []);
  return (
    <article className="page">
      <PageHeader eyebrow="Your workspace" title="Dashboard" lead="What you have, what needs attention, and what changed recently. Only your own work is counted here." />
      <Loaded res={res} what="your dashboard">
        {(d) => {
          const s = d.sections;
          const attention = s.attention.state === 'ok' ? s.attention.items : [];
          return (
            <>
              {!d.complete ? <Notice kind="warn" title="Some sections could not be loaded">{d.degraded.join(', ')}. The rest is accurate. <button type="button" className="link-button" onClick={() => res.reload()}>Try again</button></Notice> : null}
              <Section title="Needs attention" section={s.attention}>
                {attention.length === 0 ? <p>Nothing needs your attention.</p> : (
                  <ul className="list">{attention.map((a, i) => <li key={i} className="row"><div className="row__main"><span className="row__title"><Link to={a.where}>{a.text}</Link></span><span className="row__meta">{a.severity}</span></div></li>)}</ul>
                )}
              </Section>
              <div className="grid grid--2">
                <Section title="Your work" section={s.work}>
                  <div className="stats">
                    <Stat label="Projects" value={s.work.projects} /><Stat label="Datasets" value={s.work.datasets} /><Stat label="Documents" value={s.work.documents} />
                    <Stat label="Trained models" value={s.work.models_trained} /><Stat label="Registered models" value={s.work.registered_models} /><Stat label="Research results" value={s.work.research_results} />
                    <Stat label="Reports" value={s.work.reports} /><Stat label="Exam subjects" value={s.work.exam_subjects} /><Stat label="Analyses" value={s.work.intelligence_runs} />
                  </div>
                </Section>
                <Section title="Your limits" section={s.quotas}>
                  <p>{quotaText(s.quotas.documents_used, s.quotas.max_documents)}{s.quotas.documents_remaining !== null && s.quotas.documents_remaining !== undefined ? `, ${s.quotas.documents_remaining} left` : ''}.</p>
                  {s.quotas.max_pages_per_document ? <p className="row__meta">Up to {s.quotas.max_pages_per_document} pages per document. Largest file: {s.quotas.max_file_mb} MB.</p> : null}
                  <p className="row__meta">{s.quotas.note}</p>
                  <p className="row__meta">Training jobs running now: {s.quotas.ml_jobs_running}</p>
                </Section>
                <Section title="Reports" section={s.reports}>
                  {s.reports.recent.length === 0 ? <Empty title="No reports yet"><Link to="/reports" className="btn">Create a report</Link></Empty> : (
                    <ul className="list">{s.reports.recent.map((r) => <li key={r.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/reports/${encodeURIComponent(r.id)}`}>{r.title}</Link></span><span className="row__meta">version {r.current_version} · updated {when(r.updated_at)}</span></div></li>)}</ul>
                  )}
                  <p className="row__meta">{s.reports.exports} exported file(s) in total.</p>
                </Section>
                <Section title="Models" section={s.models}>
                  {Object.keys(s.models.versions_by_state).length === 0 ? <p>No registered models yet. <Link to={LINKS.models}>Open the registry</Link>.</p> : (
                    <ul className="list">{Object.entries(s.models.versions_by_state).map(([k, n]) => <li key={k} className="row"><div className="row__main"><span className="row__title">{k}</span><span className="row__meta">{n} version(s)</span></div></li>)}</ul>
                  )}
                  <p className="row__meta">{s.models.note}</p>
                </Section>
                <Section title="Schedules" section={s.schedules}>
                  {s.schedules.items.length === 0 ? <p>No scheduled reports. <Link to={LINKS.schedules}>Schedules</Link></p> : (
                    <ul className="list">{s.schedules.items.map((x) => <li key={x.id} className="row"><div className="row__main"><span className="row__title"><Link to={`/schedules/${encodeURIComponent(x.id)}`}>{x.frequency}</Link></span><span className="row__meta">{x.status} · next {when(x.next_run_at)}</span></div></li>)}</ul>
                  )}
                </Section>
                <Section title="Shared with you" section={s.collaboration}>
                  <p>{s.collaboration.shared_with_me} item(s) shared with you across {s.collaboration.workspaces.length} workspace(s).{s.collaboration.pending_invitations ? ` ${s.collaboration.pending_invitations} invitation(s) waiting.` : ''}</p>
                  <Link to={LINKS.workspaces} className="btn btn--ghost">Open workspaces</Link>
                </Section>
              </div>
              <Section title="Recent activity" section={s.activity}>
                {s.activity.items.length === 0 ? <p>Nothing yet.</p> : <ul className="list">{s.activity.items.map((a) => <li key={`${a.kind}${a.id}`} className="row"><div className="row__main"><span className="row__title">{a.label}</span><span className="row__meta">{a.kind} · {when(a.at)}</span></div></li>)}</ul>}
              </Section>
              <p className="row__meta">Updated {when(d.generated_at)}.</p>
            </>
          );
        }}
      </Loaded>
    </article>
  );
}
