import { useEffect, useState } from 'react';
import { reviews } from '../../api/index.js';
import { MAX_COMMENT, STAR_LABEL, reviewProblem } from '../../features/reviews/logic.js';

/** Shown under a piece of the person's own work. Optional: nothing happens unless they choose stars and press Save. Hidden when the work is not theirs. */
export function ReviewBox({ kind, targetId }) {
  const [state, setState] = useState({ loading: true, hidden: false, review: null, notice: '' });
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState('');
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    reviews.mine(kind, targetId).then((d) => {
      if (!live) return;
      setState({ loading: false, hidden: false, review: d.review, notice: d.notice });
      if (d.review) { setRating(d.review.rating); setComment(d.review.comment ?? ''); }
    }).catch(() => { if (live) setState({ loading: false, hidden: true, review: null, notice: '' }); });
    return () => { live = false; };
  }, [kind, targetId]);
  if (state.loading || state.hidden || !targetId) return null;
  async function save(e) {
    e.preventDefault();
    const problem = reviewProblem({ rating, comment });
    if (problem) { setMsg({ kind: 'error', text: problem }); return; }
    setBusy(true);
    try {
      const r = await reviews.save(kind, targetId, rating, comment.trim());
      setState({ ...state, review: r });
      setEditing(false);
      setMsg({ kind: 'success', text: 'Thanks, your review was saved.' });
    } catch (err) {
      setMsg({ kind: 'error', text: err?.message ?? 'The review could not be saved.' });
    } finally { setBusy(false); }
  }
  async function withdraw() {
    try {
      await reviews.remove(kind, targetId);
      setState({ ...state, review: null }); setRating(0); setComment(''); setMsg({ kind: 'success', text: 'Review withdrawn.' });
    } catch (err) { setMsg({ kind: 'error', text: err?.message ?? 'The review could not be withdrawn.' }); }
  }
  const r = state.review;
  return (
    <section className="review-box" aria-label="Review this work">
      {r && !editing ? (
        <p className="row__meta">Your review: <span aria-label={`${r.rating} of 5 stars`}>{'★'.repeat(r.rating)}{'☆'.repeat(5 - r.rating)}</span>{r.comment ? ` — ${r.comment}` : ''}{' '}
          <button type="button" className="link-button" onClick={() => setEditing(true)}>Change</button>{' · '}<button type="button" className="link-button" onClick={withdraw}>Withdraw</button></p>
      ) : (
        <form onSubmit={save}>
          <fieldset className="review-box__stars"><legend>How useful was this? (optional)</legend>
            {[1, 2, 3, 4, 5].map((n) => (
              <label key={n} className="review-box__star" title={STAR_LABEL[n]}>
                <input type="radio" name={`review-${kind}-${targetId}`} value={n} checked={rating === n} onChange={() => setRating(n)} />
                <span aria-hidden="true">{n <= rating ? '★' : '☆'}</span><span className="sr-only">{n} star{n > 1 ? 's' : ''}, {STAR_LABEL[n]}</span>
              </label>))}
          </fieldset>
          {rating > 0 ? (
            <>
              <label className="field"><span className="field__label">Comment (optional)</span>
                <textarea className="field__input" rows={2} maxLength={MAX_COMMENT} value={comment} onChange={(e) => setComment(e.target.value)} /></label>
              <p className="row__meta">{state.notice}</p>
              <div className="btn-row"><button className="btn btn--sm" disabled={busy}>{busy ? 'Saving…' : 'Save review'}</button>
                {editing ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => { setEditing(false); setRating(r.rating); setComment(r.comment ?? ''); }}>Cancel</button> : null}</div>
            </>
          ) : null}
        </form>
      )}
      {msg ? <p className={msg.kind === 'error' ? 'field__error' : 'row__meta'} role="status">{msg.text}</p> : null}
    </section>
  );
}
