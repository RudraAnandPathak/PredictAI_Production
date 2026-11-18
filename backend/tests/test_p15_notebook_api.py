"""Notebook API over HTTP: every route needs a session (and CSRF for changes), and the routes answer with the standard envelope."""
import unittest

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise

N = "/notebooks"


class NotebookApi(base.ApiBase):
    def test_every_route_needs_a_session_and_changes_need_csrf(self):
        for method, path in (("GET", f"{N}/studio/kinds"), ("POST", f"{N}/x/studio"), ("GET", f"{N}/x/studio"), ("GET", f"{N}/outputs/x"), ("DELETE", f"{N}/outputs/x"),
                             ("GET", f"{N}/outputs/x/export"), ("POST", f"{N}/x/deep-research"), ("GET", f"{N}/legacy"), ("POST", f"{N}/legacy/import")):
            self.assertEqual(self.error_code(self.api.call(method, path, {} if method != "GET" else None), 401), "UNAUTHENTICATED", f"{method} {path}")
        self.api.signed_in()
        self.assertEqual(self.error_code(self.api.call("POST", f"{N}/x/studio", {"kind": "faq"}, csrf=False), 403), "CSRF_FAILED")

    def test_signed_in_routes_answer(self):
        self.api.signed_in()
        kinds = self.api.call("GET", f"{N}/studio/kinds")
        self.assertEqual(kinds.status_code, 200)
        self.assertIn("flashcards", [k["kind"] for k in kinds.json()["data"]["items"]])
        self.assertEqual(self.api.call("GET", f"{N}/legacy").json()["data"], {"not_imported": 0, "imported": 0})
        self.assertEqual(self.api.call("POST", f"{N}/legacy/import", {}).json()["data"]["imported"], [])
        self.assertEqual(self.error_code(self.api.call("POST", f"{N}/nope/studio", {"kind": "faq"}), 404), "NOT_FOUND")
        self.assertEqual(self.error_code(self.api.call("POST", f"{N}/nope/deep-research", {"question": "q"}), 404), "NOT_FOUND")
        self.assertEqual(self.api.call("POST", f"{N}/nope/deep-research", {"question": "q", "max_sources": 99}).status_code, 422)
        self.assertEqual(self.error_code(self.api.call("GET", f"{N}/outputs/nope/export"), 404), "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
