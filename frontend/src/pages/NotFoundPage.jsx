import { Link } from '../app/router/Link.jsx';

export function NotFoundPage() {
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Error 404</p>
        <h1 className="page-title">Page not found</h1>
        <p className="lead">That address does not exist in PredictAI. Check the link, or head back to the overview.</p>
      </header>
      <Link to="/" className="btn">Back to overview</Link>
    </article>
  );
}
