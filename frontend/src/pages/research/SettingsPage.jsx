import { research } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { consentLines } from '../../features/research/plan.js';
import { ConsentForm } from './ConsentForm.jsx';
import { ProvidersPanel } from './ProvidersPanel.jsx';

/** Research settings: what you allow to leave this server, and the AI providers the platform owner has set up. */
export function SettingsPage() {
  const settings = useResource(() => research.settings(), []);
  const s = settings.data;
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/research">Research</Link></p>
        <h1 className="page-title">Research settings</h1>
        <p className="lead">Everything that talks to the outside world is off until you turn it on. Each project can narrow these choices further but never widen them.</p>
      </header>
      {settings.loading && !s ? <PageLoading label="Reading your settings…" /> : null}
      {settings.error ? <ErrorState title="Could not load your settings" message={describeError(settings.error)} onRetry={() => settings.reload()} /> : null}
      {s ? (
        <>
          <section className="card stack" aria-label="What is allowed">
            <h2 className="card__title">What is allowed now</h2>
            <ul>{consentLines({ effective: { ...s.person, web_allowed: s.person.web_enabled && s.platform.web_enabled, news_allowed: s.person.news_enabled, reasons: s.platform.web_enabled ? [] : ['The platform owner has not enabled web research on this server.'], providers: s.person.providers } }).map((l, i) => <li key={i}>{l}</li>)}</ul>
            {s.notes.map((n, i) => <p key={i} className="row__meta">{n}</p>)}
          </section>
          <ConsentForm legend="Your choices" row={s.person} platform={s.platform} onSave={async (changes) => { await research.putSettings(changes); await settings.reload({ quiet: true }); }} />
        </>
      ) : null}
      <ProvidersPanel />
    </article>
  );
}
