import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { projectNarrowingNote } from '../../features/research/consent.js';
import { consentLines } from '../../features/research/plan.js';
import { ConsentForm } from './ConsentForm.jsx';

/** This project's own choices. A project can only narrow what you allowed in your settings; it can never allow more. */
export function PrivacyPanel({ projectId }) {
  const settings = useResource(() => research.settings(projectId), [projectId]);
  const s = settings.data;
  if (settings.loading && !s) return <PageLoading label="Reading the privacy settings…" />;
  if (settings.error) return <ErrorState title="Could not load the settings" message={describeError(settings.error)} onRetry={() => settings.reload()} />;
  const narrowing = projectNarrowingNote(s);
  const locked = { web: s.person.web_enabled ? null : 'Off in your own settings.', news: s.person.news_enabled ? null : 'Off in your own settings.', ai: s.person.external_ai ? null : 'Off in your own settings.' };
  return (
    <section className="stack" aria-label="Privacy for this project">
      <div className="card stack">
        <h2 className="card__title">What is allowed in this project right now</h2>
        <ul>{consentLines(s).map((l, i) => <li key={i}>{l}</li>)}</ul>
        {narrowing.length ? <ul className="row__meta">{narrowing.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
        <p className="row__meta">{s.project_overrides ? 'This project has its own choices.' : 'This project follows your settings.'} Your own settings are on the <Link to="/research/settings">research settings page</Link>.</p>
      </div>
      <ConsentForm legend="Choices for this project" row={s.project_overrides ? s.project : s.person} platform={s.platform} locked={locked} onSave={async (changes) => { await research.putSettings(changes, projectId); await settings.reload({ quiet: true }); }} />
    </section>
  );
}
