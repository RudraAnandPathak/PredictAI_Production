// Pure helpers for reviews. The server decides who may review what and who can read it; these only format and check input.

export const MAX_COMMENT = 2000;
export const STAR_LABEL = Object.freeze({ 1: 'Poor', 2: 'Weak', 3: 'OK', 4: 'Good', 5: 'Excellent' });

export function reviewProblem({ rating, comment }) {
  if (!Number.isInteger(rating) || rating < 1 || rating > 5) return 'Choose 1 to 5 stars.';
  if ((comment ?? '').length > MAX_COMMENT) return `Write up to ${MAX_COMMENT} characters.`;
  return null;
}

export const stars = (n) => '★'.repeat(Math.max(0, Math.min(5, n))) + '☆'.repeat(5 - Math.max(0, Math.min(5, n)));

/** Who wrote a review, as the owner sees it. */
export function authorLine(author) {
  if (!author) return 'Unknown';
  if (author.deleted) return author.label ?? 'Deleted account';
  return author.display_name ? `${author.display_name} (${author.email})` : author.email;
}

/** "4.2 from 12 reviews, 1 low (1–2 stars)". */
export function summaryLine(s) {
  if (!s || !s.count) return 'No reviews yet.';
  return `${Number(s.average).toFixed(1)} from ${s.count} review${s.count === 1 ? '' : 's'}${s.low ? `, ${s.low} low (1–2 stars)` : ''}`;
}
