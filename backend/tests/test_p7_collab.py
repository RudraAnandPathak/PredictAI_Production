"""Collaboration: roles, invitations, sharing, revocation, expiry, platform-owner separation, audit. Direct service-level attempts by people without access."""
from datetime import datetime, timedelta, timezone

from app.core.errors import AppError
from p7_support import P7Base, code_of


def future(clock, days):
    return (clock.now() + timedelta(days=days)).isoformat()


class Collab(P7Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dave, _ = cls.h.make_user("dave@example.com")
        cls.platform_owner, _ = cls.h.make_owner("boss@example.com")
        cls.c = cls.p7.collab
        cls.mid = cls.train()
        cls.reg = cls.p7.registry.register(cls.alice, cls.mid, name="Shared model")

    def setUp(self):
        self.w = self.c.create_workspace(self.alice, name=f"Team {id(self)}")
        self.wid = self.w["id"]
        self.addCleanup(self.ctx_exec, "UPDATE ws_workspaces SET status = 'closed' WHERE id = ?", self.wid)
        self.report = self.p7.reports.create(self.alice, self.project, source_kind="prediction", source={"version_ids": [self.reg["versions"][0]["id"]]})
        self.rid = self.report["id"]

    def join(self, user, email, role):
        inv = self.c.invite(self.alice, self.wid, email=email, role=role)
        token = inv["invite_link"].split("token=")[1]
        self.c.respond(user, token, True)
        return token

    def test_members_get_what_their_role_allows_and_nothing_before_sharing(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.join(self.carol, "carol@example.com", "editor")
        self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404, "membership alone shares nothing")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid, permission="edit")
        v = self.p7.reports.get(self.bob, self.rid)
        self.assertEqual(v["your_access"], "view", "a viewer is capped at view even on an edit grant")
        self.assertFalse(v["editable"])
        self.assertEqual(code_of(self.p7.reports.edit, self.bob, self.rid, 1, [{"op": "set_title", "text": "x"}])[1], 404)
        e = self.p7.reports.edit(self.carol, self.rid, 1, [{"op": "set_title", "text": "Edited by carol"}])
        self.assertEqual(e["version"], 2)
        self.assertEqual(code_of(self.p7.reports.delete, self.carol, self.rid)[1], 404, "only the owner deletes")
        self.assertEqual(self.p7.reports.get(self.alice, self.rid)["title"], "Edited by carol")

    def test_a_view_grant_caps_an_editor(self):
        self.join(self.carol, "carol@example.com", "editor")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid, permission="view")
        self.assertEqual(self.p7.reports.get(self.carol, self.rid)["your_access"], "view")
        self.assertEqual(code_of(self.p7.reports.edit, self.carol, self.rid, 1, [{"op": "set_title", "text": "x"}])[1], 404)

    def test_revocation_takes_effect_on_the_next_request_including_downloads(self):
        self.join(self.bob, "bob@example.com", "viewer")
        g = self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        ex = self.p7.reports.export(self.alice, self.rid, "md")
        self.assertTrue(self.p7.reports.download(self.bob, ex["export_id"]))
        self.c.unshare(self.alice, g["grant_id"])
        self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404)
        self.assertEqual(code_of(self.p7.reports.download, self.bob, ex["export_id"])[1], 404)
        self.assertEqual(self.p7.reports.list(self.bob)["shared_with_me"], [])

    def test_removal_expiry_and_closing_end_access(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        self.assertTrue(self.p7.reports.get(self.bob, self.rid))
        self.c.remove_member(self.alice, self.wid, self.bob.user_id)
        self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404)
        self.join(self.bob, "bob@example.com", "viewer")
        self.assertTrue(self.p7.reports.get(self.bob, self.rid))
        self.c.close_workspace(self.alice, self.wid)
        self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404)
        self.assertEqual(code_of(self.c.get_workspace, self.bob, self.wid)[1], 404)

    def test_expiring_grant_stops_working(self):
        self.join(self.bob, "bob@example.com", "viewer")
        g = self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid, expires_at=future(self.h.clock, 1))
        self.assertTrue(self.p7.reports.get(self.bob, self.rid))
        self.h.clock.advance(hours=25)
        self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404, "an expired grant stops working without anyone revoking it")
        self.assertEqual(code_of(self.c.share, self.alice, self.wid, resource_type="report", resource_id=self.rid, expires_at="2000-01-01T00:00:00Z")[0], "VALIDATION_ERROR")

    def ctx_exec(self, sql, *args):
        with self.ws.ctx.conn:
            self.ws.ctx.conn.execute(sql, args)

    def test_strangers_and_direct_unauthorised_attempts_see_nothing(self):
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        for who in (self.bob, self.dave):
            self.assertEqual(code_of(self.c.get_workspace, who, self.wid)[1], 404)
            self.assertEqual(code_of(self.c.invite, who, self.wid, email="x@example.com", role="viewer")[1], 404)
            self.assertEqual(code_of(self.c.share, who, self.wid, resource_type="report", resource_id=self.rid)[1], 404)
            self.assertEqual(code_of(self.c.events, who, self.wid)[1], 404)
            self.assertEqual(code_of(self.p7.reports.get, who, self.rid)[1], 404)
            self.assertEqual(code_of(self.p7.reports.export, who, self.rid, "md")[1], 404)
        grant = self.c.shares(self.alice, self.wid)["items"][0]["grant_id"]
        self.assertEqual(code_of(self.c.unshare, self.bob, grant)[1], 404)

    def test_only_the_owner_can_share_and_only_from_a_member_role_that_may(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.join(self.carol, "carol@example.com", "editor")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid, permission="edit")
        self.assertEqual(code_of(self.c.share, self.carol, self.wid, resource_type="report", resource_id=self.rid)[1], 404, "a shared item cannot be passed on")
        bobs = self.p7.reports.create  # bob has no project; a viewer cannot share anyway
        self.assertEqual(code_of(self.c.share, self.bob, self.wid, resource_type="report", resource_id=self.rid)[0], "FORBIDDEN_ROLE")
        self.assertEqual(code_of(self.c.invite, self.carol, self.wid, email="z@example.com", role="viewer")[0], "FORBIDDEN_ROLE")
        self.assertEqual(code_of(self.c.share, self.alice, self.wid, resource_type="nonsense", resource_id=self.rid)[0], "VALIDATION_ERROR")

    def test_platform_owner_privileges_are_not_workspace_membership(self):
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        self.assertTrue(self.platform_owner.is_owner)
        self.assertEqual(code_of(self.p7.reports.get, self.platform_owner, self.rid)[1], 404)
        self.assertEqual(code_of(self.c.get_workspace, self.platform_owner, self.wid)[1], 404)

    def test_invitations_are_bound_to_the_invited_email_single_use_and_revocable(self):
        inv = self.c.invite(self.alice, self.wid, email="bob@example.com", role="viewer")
        token = inv["invite_link"].split("token=")[1]
        stored = self.ws.ctx.conn.execute("SELECT token_hash FROM ws_invitations WHERE id = ?", (inv["id"],)).fetchone()[0]
        self.assertNotIn(token, stored, "only a hash is stored")
        self.assertEqual(code_of(self.c.respond, self.carol, token, True)[1], 404, "someone else cannot use it")
        self.assertEqual(code_of(self.c.invite, self.alice, self.wid, email="bob@example.com", role="editor")[0], "ALREADY_INVITED")
        self.c.respond(self.bob, token, True)
        self.assertEqual(code_of(self.c.respond, self.bob, token, True)[1], 404, "single use")
        inv2 = self.c.invite(self.alice, self.wid, email="carol@example.com", role="viewer")
        self.c.revoke_invitation(self.alice, self.wid, inv2["id"])
        self.assertEqual(code_of(self.c.respond, self.carol, inv2["invite_link"].split("token=")[1], True)[1], 404)
        self.assertEqual(code_of(self.c.invite, self.alice, self.wid, email="not-an-email", role="viewer")[0], "VALIDATION_ERROR")

    def test_the_last_admin_cannot_leave_or_be_demoted(self):
        self.assertEqual(code_of(self.c.remove_member, self.alice, self.wid, self.alice.user_id)[0], "LAST_ADMIN")
        self.assertEqual(code_of(self.c.set_role, self.alice, self.wid, self.alice.user_id, role="viewer")[0], "LAST_ADMIN")

    def test_history_records_who_did_what_for_admins_only(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        ev = [e["event"] for e in self.c.events(self.alice, self.wid)["items"]]
        for needed in ("workspace.created", "invitation.created", "invitation.accepted", "resource.shared"):
            self.assertIn(needed, ev)
        self.assertEqual(code_of(self.c.events, self.bob, self.wid)[0], "FORBIDDEN_ROLE")

    def test_shared_model_is_usable_by_an_editor_but_only_viewable_by_a_viewer(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.join(self.carol, "carol@example.com", "editor")
        self.c.share(self.alice, self.wid, resource_type="model", resource_id=self.reg["id"], permission="edit")
        self.assertTrue(self.p7.registry.get(self.bob, self.reg["id"]))
        vid = self.reg["versions"][0]["id"]
        self.p7.registry.transition(self.alice, vid, "evaluated")
        self.assertEqual(code_of(self.p7.registry.transition, self.carol, vid, "staging")[1], 404, "lifecycle stays with the owner")

    def test_blocked_owner_account_ends_shared_access(self):
        self.join(self.bob, "bob@example.com", "viewer")
        self.c.share(self.alice, self.wid, resource_type="report", resource_id=self.rid)
        self.assertTrue(self.p7.reports.get(self.bob, self.rid))
        self.ctx_exec("UPDATE users SET status = 'blocked' WHERE id = ?", self.alice.user_id)
        try:
            self.assertEqual(code_of(self.p7.reports.get, self.bob, self.rid)[1], 404)
        finally:
            self.ctx_exec("UPDATE users SET status = 'active' WHERE id = ?", self.alice.user_id)
        self.assertTrue(self.p7.reports.get(self.bob, self.rid))
