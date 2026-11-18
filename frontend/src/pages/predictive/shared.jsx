import { Link } from '../../app/router/Link.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { JOB_STATUS_LABEL, JOB_STATUS_TONE, PROBLEM_SHORT, formatScore, formatSeconds, formatWhen, headline, modelTitle } from '../../features/predictive/format.js';

export const Badge = ({ tone = 'muted', children, title }) => <span className={`badge badge--${tone}`} title={title}>{children}</span>;

export const JobBadge = ({ status }) => <Badge tone={JOB_STATUS_TONE[status] ?? 'muted'}>{JOB_STATUS_LABEL[status] ?? status}</Badge>;

export function Notice({ kind = 'info', title, children }) {
  return (
    <div className={`message message--${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {title ? <p className="message__title">{title}</p> : null}
      {children}
    </div>
  );
}

/** One saved model as a list row: links to its page and shows the headline score the server computed. */
export function ModelRow({ model, selected, onToggle, extra }) {
  return (
    <li className="row" data-model-id={model.id}>
      <div className="row__main">
        {onToggle ? (
          <label className="row__check"><input type="checkbox" checked={Boolean(selected)} onChange={() => onToggle(model.id)} aria-label={`Select ${modelTitle(model)} to compare`} /></label>
        ) : null}
        <span className="row__title"><Link to={`/predictions/models/${encodeURIComponent(model.id)}`}>{modelTitle(model)}</Link></span>
        <span className="row__meta">
          {model.algorithm_label}{model.tuned ? ' (tuned)' : ''} · {PROBLEM_SHORT[model.problem_type] ?? model.problem_type} · target “{model.target}” · {headline(model)} · trained {formatWhen(model.trained_at)}
        </span>
      </div>
      {extra}
    </li>
  );
}

export function KeyFacts({ items }) {
  const shown = items.filter(([, v]) => v !== undefined && v !== null && v !== '');
  return <dl className="kv">{shown.map(([k, v]) => <div key={k} className="kv__pair"><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

export const Stat = ({ label, value, hint }) => (
  <div className="stat"><span className="stat__value">{value}</span><span className="stat__label">{label}</span>{hint ? <span className="row__meta">{hint}</span> : null}</div>
);

export { formatNumber, formatScore, formatSeconds, formatWhen };
