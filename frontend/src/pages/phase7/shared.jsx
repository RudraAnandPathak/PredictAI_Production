import { Link } from '../../app/router/Link.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { describeError } from '../../features/identity/validation.js';
import { SECTION_STATE_LABEL, SECTION_STATE_TONE } from '../../features/phase7/labels.js';
import { Badge, Notice, Stat } from '../predictive/shared.jsx';

export { Badge, Notice, Stat };

export const StateBadge = ({ state }) => <Badge tone={SECTION_STATE_TONE[state] ?? 'muted'}>{SECTION_STATE_LABEL[state] ?? state}</Badge>;

export function Empty({ title, children }) {
  return <div className="state" role="status"><div className="state__body"><h2 className="state__title">{title}</h2>{children}</div></div>;
}

/** Loading, error and empty handling for a useResource result. Children render only when data is present. */
export function Loaded({ res, what, children }) {
  if (res.loading && !res.data) return <PageLoading label={`Loading ${what}…`} />;
  if (res.error && !res.data) return <ErrorState title={`Could not load ${what}`} message={describeError(res.error)} onRetry={() => res.reload()} />;
  return res.data ? children(res.data) : null;
}

export function PageHeader({ eyebrow, title, lead, back }) {
  return (
    <header className="page__header">
      <p className="eyebrow">{back ? <><Link to={back.to}>{back.label}</Link> / </> : null}{eyebrow}</p>
      <h1 className="page-title">{title}</h1>
      {lead ? <p className="lead">{lead}</p> : null}
    </header>
  );
}

/** Runs an async action, reporting success or the server's message. Never pretends progress. */
export async function attempt(fn, setMessage, okText) {
  try {
    const out = await fn();
    if (okText) setMessage({ kind: 'success', text: okText });
    return out;
  } catch (e) {
    setMessage({ kind: 'error', text: describeError(e) });
    return null;
  }
}

export const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
