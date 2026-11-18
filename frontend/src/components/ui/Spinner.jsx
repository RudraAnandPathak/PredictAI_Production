export function Spinner({ label = 'Loading' }) {
  return (
    <span className="spinner" role="status">
      <span className="spinner__ring" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function PageLoading({ label = 'Loading…' }) {
  return (
    <div className="page-loading">
      <Spinner label={label} />
      <p aria-hidden="true">{label}</p>
    </div>
  );
}
