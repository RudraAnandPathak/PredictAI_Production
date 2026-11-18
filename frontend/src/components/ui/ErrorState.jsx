import { Icon } from './Icon.jsx';

export function ErrorState({ title, message, detail, onRetry, retryLabel = 'Try again' }) {
  return (
    <div className="state state--error" role="alert">
      <Icon name="alert" size={22} className="state__icon" />
      <div className="state__body">
        <h2 className="state__title">{title}</h2>
        <p>{message}</p>
        {detail ? <p className="state__detail">{detail}</p> : null}
        {onRetry ? (
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" size={16} /> {retryLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
