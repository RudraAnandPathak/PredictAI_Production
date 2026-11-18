# Phase 10E: Reviews, and the deletion fix

## Reviews
- Under each piece of a person's own work there is an optional "How useful was this?" box: 1–5 stars and an optional comment. One review per piece of work; it can be changed or withdrawn while the account exists.
- Where it appears: notebook Studio outputs, exam predictions, exam papers (mock and predicted), reports, research reports, and each answer in Analytics → Ask.
- The owner sees every review in **Owner → Reviews**, with the account's name and email, filters by kind of work and stars, and per-kind averages with a count of 1–2 star reviews.
- A review stays when the work it is about is deleted (the title is kept as it was).

## After an account is deleted
The review is kept, with the rating, comment, date and kind of work. During deletion, while the account still has its details:
- the link to the account is removed (`author_user_id` set to NULL);
- emails, phone numbers, the account's email, the part before the @, and the display name (and each word of it) are replaced with `[removed]` in the comment;
- the work's title is replaced by its kind ("Notebook output"), because titles can name people;
- every review of that account gets the same random label, e.g. "Deleted account #3fa91c", so the owner can still see they came from one person, without knowing who.
The review box tells people this before they save.

Why anonymise rather than keep the name: India's DPDP Act gives a right to erasure; anonymous data that cannot be linked back to the person is generally no longer personal data. The rights provisions take effect on 13 May 2027. This is not legal advice.

Limits of the scrub: a comment can still describe someone indirectly ("the class monitor of 10B"). Only emails, phone numbers and the account's own names are removed automatically.

## Deletion fix (important)
Phases 10B–10D added tables (`exam_predictions`, `nb_outputs`, `nb_imports`) that point at rows deleted during account or project deletion. Deletion did not remove them first, so deleting an account or project that had used exam prediction or Notebook failed with a foreign-key error. Deletion now removes them first; tests cover both account and project deletion.

## API
`GET /reviews/kinds`, `GET|PUT|DELETE /reviews/{kind}/{target_id}` (own work only), `GET /owner/reviews?kind=&rating=&page=` (owner).

## Schema (migration 0016)
`reviews (id, target_kind, target_id, target_title, author_user_id NULL after deletion, anon_label, rating 1–5, comment ≤2000, created_at, updated_at, anonymized_at)`, one review per author per piece of work.
