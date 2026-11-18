import { useState } from 'react';
import { reviews } from '../../api/index.js';
import { useResource } from '../../features/identity/useResource.js';
import { authorLine, stars, summaryLine } from '../../features/reviews/logic.js';
import { Empty, Loaded, when } from '../phase7/shared.jsx';

/** Owner: every review, newest first, with the account that wrote it. Reviews of deleted accounts stay, labelled "Deleted account #…", without the person's details. */
export function ReviewsTab() {
  const [filter, setFilter] = useState({ kind: '', rating: '', page: 1 });
  const res = useResource(() => reviews.ownerList(filter), [filter.kind, filter.rating, filter.page]);
  return (
    <section aria-label="Reviews">
      <Loaded res={res} what="reviews">
        {(d) => (
          <>
            <div className="card">
              <h2 className="card__title">Reviews by kind of work</h2>
              {d.summary.length === 0 ? <p className="row__meta">No reviews yet.</p> : (
                <ul className="list">{d.summary.map((s) => <li key={s.kind} className="row__meta"><strong>{s.label}:</strong> {summaryLine(s)}</li>)}</ul>)}
              <p className="row__meta">When someone deletes their account, their reviews stay without their name, email or contact details.</p>
            </div>
            <div className="btn-row">
              <label className="field"><span className="field__label">Kind of work</span>
                <select className="field__input" value={filter.kind} onChange={(e) => setFilter({ ...filter, kind: e.target.value, page: 1 })}>
                  <option value="">All</option>{d.summary.map((s) => <option key={s.kind} value={s.kind}>{s.label}</option>)}</select></label>
              <label className="field"><span className="field__label">Stars</span>
                <select className="field__input" value={filter.rating} onChange={(e) => setFilter({ ...filter, rating: e.target.value, page: 1 })}>
                  <option value="">All</option>{[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
            </div>
            {d.items.length === 0 ? <Empty title="No reviews match">Reviews appear here when people rate their work.</Empty> : (
              <ul className="list">{d.items.map((r) => (
                <li key={r.id} className="row"><div className="row__main">
                  <span className="row__title"><span aria-label={`${r.rating} of 5 stars`}>{stars(r.rating)}</span> {r.target_label}: {r.target_title}</span>
                  {r.comment ? <span>{r.comment}</span> : null}
                  <span className="row__meta">{authorLine(r.author)} · {when(r.created_at)}{r.updated_at !== r.created_at ? ' (edited)' : ''}</span></div></li>))}</ul>)}
            {d.pages > 1 ? (
              <div className="btn-row"><button type="button" className="btn btn--ghost" disabled={d.page <= 1} onClick={() => setFilter({ ...filter, page: d.page - 1 })}>Newer</button>
                <span className="row__meta">Page {d.page} of {d.pages}</span>
                <button type="button" className="btn btn--ghost" disabled={d.page >= d.pages} onClick={() => setFilter({ ...filter, page: d.page + 1 })}>Older</button></div>) : null}
          </>
        )}
      </Loaded>
    </section>
  );
}
