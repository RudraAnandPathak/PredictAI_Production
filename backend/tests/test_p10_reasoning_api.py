"""R5 over HTTP: the analysis flow through the real worker loop, validation as 4xx (never 500), isolation, and a response that contains only the person's own quotes."""
import unittest

try:
    import test_identity_api as base
except unittest.SkipTest:
    raise

from test_p10_api import P, RiApi
from test_p10_reasoning import S1, S2, S3


class Analyses(RiApi):
    def two_users(self):
        alice, bob = self.user("alice@example.com"), self.user("bob@example.com")
        return alice, bob

    def add(self, call, wid, name, text):
        r = self.upload(call, wid, name, text.encode())
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["data"]["id"]

    def test_flow_over_http(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        ids = [self.add(alice, wid, n, t) for n, t in (("a.txt", S1), ("b.txt", S2), ("c.txt", S3))]
        self.drain()
        r = alice("POST", f"{P}/workspaces/{wid}/analyses", {"question": "Does canopy reduce temperature?", "source_ids": ids})
        self.assertEqual(r.status_code, 202, r.text)
        aid = r.json()["data"]["id"]
        self.assertEqual(r.json()["data"]["status"], "queued")
        self.assertNotIn("result", r.json()["data"])
        self.assertEqual(self.drain(), ["succeeded"])
        got = alice("GET", f"{P}/workspaces/{wid}/analyses/{aid}")
        self.assertEqual(got.status_code, 200, got.text)
        d = got.json()["data"]
        self.assertEqual((d["status"], d["progress"]), ("succeeded", 100))
        self.assertEqual(len(d["result"]["findings"]["contradictions"]), 1)
        lst = alice("GET", f"{P}/workspaces/{wid}/analyses").json()["data"]["items"]
        self.assertEqual([x["id"] for x in lst], [aid])
        self.assertNotIn("result", lst[0])
        lim = alice("GET", f"{P}/limits").json()["data"]
        self.assertEqual((lim["analyses_today"], lim["analyses_per_day"]), (1, 10))
        self.assertEqual(alice("DELETE", f"{P}/workspaces/{wid}/analyses/{aid}").json()["data"]["deleted"], True)
        self.assertEqual(alice("GET", f"{P}/workspaces/{wid}/analyses/{aid}").status_code, 404)

    def test_cancel_over_http(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        for n, t in (("a.txt", S1), ("b.txt", S2)):
            self.add(alice, wid, n, t)
        self.drain()
        aid = alice("POST", f"{P}/workspaces/{wid}/analyses", {}).json()["data"]["id"]
        self.assertEqual(alice("POST", f"{P}/workspaces/{wid}/analyses/{aid}/cancel").json()["data"]["status"], "cancelled")
        self.assertEqual(alice("POST", f"{P}/workspaces/{wid}/analyses/{aid}/cancel").status_code, 409)
        self.assertEqual(self.drain(), [])

    def test_validation_is_4xx_never_500(self):
        alice = self.user("alice@example.com")
        wid = self.workspace(alice)
        self.add(alice, wid, "a.txt", S1)
        self.drain()
        for body in ({}, {"source_ids": []}, {"source_ids": ["x", "y"]}, {"source_ids": "x"}, {"question": "q" * 501}, {"use_model": "maybe"}, {"source_ids": [1, 2]}):
            r = alice("POST", f"{P}/workspaces/{wid}/analyses", body)
            self.assertIn(r.status_code, (400, 422), f"{body} -> {r.status_code} {r.text}")
        self.assertEqual(alice("GET", f"{P}/workspaces/nope/analyses").status_code, 404)
        self.assertEqual(alice("POST", f"{P}/workspaces/nope/analyses", {}).status_code, 404)

    def test_isolation_between_users(self):
        alice, bob = self.two_users()
        wa, wb = self.workspace(alice), self.workspace(bob)
        a = [self.add(alice, wa, n, t) for n, t in (("a.txt", S1), ("b.txt", S2))]
        b = [self.add(bob, wb, n, t) for n, t in (("a.txt", S1), ("b.txt", S2))]
        self.drain()
        aid = alice("POST", f"{P}/workspaces/{wa}/analyses", {}).json()["data"]["id"]
        self.drain()
        for method, path in (("GET", f"{P}/workspaces/{wa}/analyses/{aid}"), ("GET", f"{P}/workspaces/{wb}/analyses/{aid}"), ("DELETE", f"{P}/workspaces/{wa}/analyses/{aid}"),
                             ("POST", f"{P}/workspaces/{wa}/analyses/{aid}/cancel"), ("GET", f"{P}/workspaces/{wa}/analyses")):
            self.assertEqual(bob(method, path).status_code, 404, path)
        r = bob("POST", f"{P}/workspaces/{wb}/analyses", {"source_ids": [b[0], a[0]]})
        self.assertIn(r.status_code, (400, 422), r.text)
        self.assertEqual(bob("GET", f"{P}/workspaces/{wb}/analyses").json()["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()
