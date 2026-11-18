# Phase 10E — Requirements & status
Scope: reviews of a person's own work, shown under the work, visible to the owner with the account that wrote them, kept anonymised after account deletion. Also fixes account and project deletion, which Phases 10B–10D had broken.

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 10E-01 | Every signed-in person can rate their own work 1–5 stars with an optional comment (up to 2000 characters); one review per piece of work, editable and withdrawable | Implemented, tested | test_p16_reviews |
| 10E-02 | Review box under: notebook outputs, exam predictions, exam papers, reports, research reports, analysis answers; optional and hidden on work that is not the person's | Implemented (box is never rendered in a browser here) | test_p16_reviews, reviews.test.js |
| 10E-03 | A person can review only work they own | Implemented, tested | test_p16_reviews |
| 10E-04 | Owner → Reviews: every review, newest first, with the account's name and email, filters by kind and stars, average and low-rating count per kind | Implemented, tested | test_p16_reviews |
| 10E-05 | On account deletion the review stays: the link to the account is removed, emails, phone numbers and the person's name are blanked from the comment, the work's title is replaced by its kind, and the review gets a random "Deleted account #…" label shared by that account's reviews | Implemented, tested end to end over HTTP | test_p16_reviews |
| 10E-06 | Reviews survive the deletion of the work they are about | Implemented, tested | test_p16_reviews |
| 10E-07 | The review box tells the person the owner reads reviews with their name and that reviews stay, without the name, after account deletion | Implemented | — |
| 10E-08 | Migration 0016 is additive (reviews only) | Implemented, tested | test_p16_reviews, test_p11_release |
| 10E-09 | FIX: account and project deletion remove exam predictions, notebook outputs and import records first (they blocked deletion with a foreign-key error since Phases 10B–10D) | Fixed, tested | test_p16_deletion_new_tables |
| 10E-10 | Reviews of models, forecasts and other kinds of work | NOT built (the list of reviewable kinds is one table in modules/reviews/service.py) | — |
