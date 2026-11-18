"""Reviews: a person reviews their own work; the owner sees who wrote each review; after account deletion the review stays, anonymised."""
import unittest

from app.modules.reviews.service import ReviewService, scrub
from test_p14_notebook import NbBase, code_of

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise
GOOD = base.GOOD_PASSWORD


class Service(NbBase):
    def setUp(self):
        super().setUp()
        self.rv = ReviewService(self.ws.ctx)
        self.setup_gemini()
        wid = self.notebook()
        self.gem.answer = lambda req: "Began in 1789 [S1]."
        self.out = self.nb.generate(self.alice, wid, "briefing")

    def test_write_change_read_and_withdraw_my_review(self):
        self.assertIsNone(self.rv.get_mine(self.alice, "notebook_output", self.out["id"])["review"])
        r = self.rv.put(self.alice, "notebook_output", self.out["id"], 4, "  Useful summary  ")
        self.assertEqual((r["rating"], r["comment"]), (4, "Useful summary"))
        r2 = self.rv.put(self.alice, "notebook_output", self.out["id"], 2, "")
        self.assertEqual((r2["id"], r2["rating"], r2["comment"]), (r["id"], 2, None))            # one review per piece of work, edited in place
        self.assertEqual(self.rv.get_mine(self.alice, "notebook_output", self.out["id"])["review"]["rating"], 2)
        self.assertTrue(self.rv.delete_mine(self.alice, "notebook_output", self.out["id"])["deleted"])
        self.assertIsNone(self.rv.get_mine(self.alice, "notebook_output", self.out["id"])["review"])

    def test_only_my_own_work_and_valid_input(self):
        self.assertEqual(code_of(self.rv.put, self.bob, "notebook_output", self.out["id"], 5), "NOT_FOUND")
        self.assertEqual(code_of(self.rv.put, self.alice, "nonsense", self.out["id"], 5), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.rv.put, self.alice, "notebook_output", self.out["id"], 6), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.rv.put, self.alice, "notebook_output", self.out["id"], True), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.rv.put, self.alice, "notebook_output", self.out["id"], 3, "x" * 2001), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.rv.delete_mine, self.bob, "notebook_output", self.out["id"]), "NOT_FOUND")

    def test_the_owner_sees_the_account_and_ordinary_people_cannot_list(self):
        self.rv.put(self.alice, "notebook_output", self.out["id"], 1, "Wrong dates")
        out = self.rv.owner_list(self.owner)
        a = out["items"][0]["author"]
        self.assertEqual((a["deleted"], a["email"]), (False, self.alice.email))
        self.assertEqual(out["summary"][0], {"kind": "notebook_output", "label": "Notebook output", "count": 1, "average": 1.0, "low": 1})
        self.assertEqual(self.rv.owner_list(self.owner, rating="5")["total"], 0)
        self.assertEqual(self.rv.owner_list(self.owner, kind="notebook_output")["total"], 1)
        self.assertEqual(code_of(self.rv.owner_list, self.alice), "FORBIDDEN")

    def test_the_review_outlives_the_work(self):
        self.rv.put(self.alice, "notebook_output", self.out["id"], 5, "Great")
        self.nb.delete(self.alice, self.out["id"])
        self.assertEqual(self.rv.owner_list(self.owner)["items"][0]["comment"], "Great")


class Scrub(unittest.TestCase):
    def test_contact_details_and_names_are_blanked(self):
        t = scrub("I'm Alice Sharma (alice.s@mail.com, +91 98765 43210). ALICE liked it; Alicea is someone else.", ["Alice Sharma", "Alice", "Sharma", "alice.s"])
        self.assertNotIn("Sharma", t)
        self.assertNotIn("@", t)
        self.assertNotIn("98765", t)
        self.assertNotIn("ALICE ", t)
        self.assertIn("Alicea", t)                                                                  # whole words only
        self.assertEqual(scrub(None, ["x"]), None)
        self.assertEqual(scrub("Year 1789 and 2024", []), "Year 1789 and 2024")                    # years are not phone numbers


class AccountDeletionOverHttp(base.ApiBase):
    def add_target(self, email):
        """A piece of the person's work: a notebook (created over HTTP) with one Studio output inserted directly."""
        conn = self.api.h.services().ctx.conn
        uid = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        r = self.api.call("POST", "/research-intelligence/workspaces", {"name": "Study"})
        self.assertEqual(r.status_code, 201, r.text)
        wid = r.json()["data"]["id"]
        conn.execute("INSERT INTO nb_outputs (id, workspace_id, owner_user_id, kind, title, content, mode, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     ("out-1", wid, uid, "briefing", "Briefing: Rahul's notes", "text", "extractive", "2026-01-01T00:00:00Z"))
        return "out-1"

    def test_after_deletion_the_review_stays_without_the_person(self):
        self.api.signed_in("rahul@example.com")
        self.api.call("PATCH", "/me", {"display_name": "Rahul Verma"})
        rid = self.add_target("rahul@example.com")
        r = self.api.call("PUT", f"/reviews/notebook_output/{rid}", {"rating": 2, "comment": "Rahul here, call me on 9876543210 or rahul@example.com"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.api.call("GET", f"/reviews/notebook_output/{rid}").json()["data"]["review"]["rating"], 2)
        self.assertEqual(self.api.call("POST", "/me/delete", {"password": GOOD}).status_code, 200)
        self.api.owner_signed_in()
        items = self.api.call("GET", "/owner/reviews").json()["data"]["items"]
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertTrue(it["author"]["deleted"])
        self.assertRegex(it["author"]["label"], r"^Deleted account #[0-9a-f]{6}$")
        self.assertNotIn("rahul", it["comment"].lower())
        self.assertNotIn("9876543210", it["comment"])
        self.assertEqual(it["target_title"], "Notebook output")                                     # the title named the person
        self.assertEqual(it["rating"], 2)
        row = self.api.h.services().ctx.conn.execute("SELECT author_user_id FROM reviews").fetchone()
        self.assertIsNone(row["author_user_id"])

    def test_routes_need_a_session_csrf_and_owner_for_the_owner_list(self):
        for method, path in (("GET", "/reviews/kinds"), ("GET", "/reviews/analysis/x"), ("PUT", "/reviews/analysis/x"), ("DELETE", "/reviews/analysis/x"), ("GET", "/owner/reviews")):
            self.assertEqual(self.error_code(self.api.call(method, path, {"rating": 3} if method == "PUT" else None), 401), "UNAUTHENTICATED", f"{method} {path}")
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("PUT", "/reviews/analysis/x", {"rating": 3}, csrf=False), 403), "CSRF_FAILED")
        self.assertEqual(self.error_code(self.api.call("GET", "/owner/reviews"), 403), "FORBIDDEN")
        self.assertEqual(self.error_code(self.api.call("PUT", "/reviews/analysis/nope", {"rating": 3}), 404), "NOT_FOUND")
        self.assertEqual(self.api.call("PUT", "/reviews/analysis/x", {"rating": 9}).status_code, 422)


class Migration(unittest.TestCase):
    def test_migration_sixteen_is_additive(self):
        from pathlib import Path
        sql = (Path(__file__).resolve().parents[1] / "app" / "db" / "migrations" / "0016_reviews.sql").read_text()
        self.assertIn("CREATE TABLE reviews", sql)
        self.assertNotRegex(sql.upper(), r"DROP TABLE|ALTER TABLE|DELETE FROM")


if __name__ == "__main__":
    unittest.main()
