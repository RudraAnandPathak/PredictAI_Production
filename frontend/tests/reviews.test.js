import test from 'node:test';
import assert from 'node:assert/strict';
import { authorLine, reviewProblem, stars, summaryLine } from '../src/features/reviews/logic.js';

test('a review needs 1 to 5 stars and a comment of at most 2000 characters', () => {
  assert.equal(reviewProblem({ rating: 0 }), 'Choose 1 to 5 stars.');
  assert.equal(reviewProblem({ rating: 2.5 }), 'Choose 1 to 5 stars.');
  assert.match(reviewProblem({ rating: 3, comment: 'x'.repeat(2001) }), /2000/);
  assert.equal(reviewProblem({ rating: 5, comment: '' }), null);
});

test('stars and the owner view of the author', () => {
  assert.equal(stars(3), '★★★☆☆');
  assert.equal(stars(9), '★★★★★');
  assert.equal(authorLine({ deleted: true, label: 'Deleted account #a1b2c3' }), 'Deleted account #a1b2c3');
  assert.equal(authorLine({ deleted: false, email: 'r@x.in', display_name: 'Rahul' }), 'Rahul (r@x.in)');
  assert.equal(authorLine({ deleted: false, email: 'r@x.in', display_name: null }), 'r@x.in');
  assert.equal(authorLine(null), 'Unknown');
});

test('summary line per kind of work', () => {
  assert.equal(summaryLine({ count: 1, average: 4, low: 0 }), '4.0 from 1 review');
  assert.equal(summaryLine({ count: 12, average: 4.21, low: 1 }), '4.2 from 12 reviews, 1 low (1–2 stars)');
  assert.equal(summaryLine({ count: 0 }), 'No reviews yet.');
});
