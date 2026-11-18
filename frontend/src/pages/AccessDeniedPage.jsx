import { Link } from '../app/router/Link.jsx';

export function AccessDeniedPage() {
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">No access</p>
        <h1 className="page-title">Not available to your account</h1>
        <p className="lead">This area is restricted. If you think you should have access, contact the person who runs your PredictAI workspace.</p>
      </header>
      <Link to="/" className="btn">Back to overview</Link>
    </article>
  );
}
