"""Phase 8: research workspace foundation (service level): ownership, limits by role, owner-managed limits, validation, deletion."""
import sqlite3
import unittest
from dataclasses import replace

from app.core.errors import AppError
from app.modules.ownerops.service import OwnerOpsService
from app.modules.researchspace.service import ResearchSpaceService, SOURCE_KINDS
from support import Harness


def code_of(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


class ResearchWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness(standard_max_research_workspaces=3, standard_max_research_sources=2, safeguard_max_research_workspaces=5, safeguard_max_research_sources=4)
        self.addCleanup(self.h.close)
        self.alice, _ = self.h.make_user("alice@example.com")
        self.bob, _ = self.h.make_user("bob@example.com")
        self.svc = ResearchSpaceService(self.h.services().ctx)

    def special(self, principal, who):
        owner, _ = self.h.make_owner()
        self.h.services().codes.grant_special(owner, principal.user_id)
        return self.h.login(who, "correct-horse-battery-staple").principal

    def test_create_list_get_rename_archive_restore_delete(self):
        w = self.svc.create(self.alice, "  Climate   papers ", "notes")
        self.assertEqual(w["name"], "Climate papers")
        self.assertEqual(w["counts"], {"sources": 0, "conversations": 0, "notes": 0})
        self.assertEqual([x["id"] for x in self.svc.list(self.alice)["items"]], [w["id"]])
        self.assertEqual(self.svc.update(self.alice, w["id"], name="Climate")["name"], "Climate")
        self.assertEqual(self.svc.update(self.alice, w["id"], status="archived")["status"], "archived")
        self.assertEqual(self.svc.list(self.alice, "archived")["items"][0]["id"], w["id"])
        self.assertEqual(self.svc.list(self.alice, "active")["items"], [])
        self.assertEqual(self.svc.update(self.alice, w["id"], status="active")["status"], "active")
        self.assertTrue(self.svc.delete(self.alice, w["id"])["deleted"])
        self.assertEqual(code_of(self.svc.get, self.alice, w["id"]), ("NOT_FOUND", 404))

    def test_one_person_can_have_many_workspaces_but_names_are_unique_per_person_ignoring_case(self):
        self.svc.create(self.alice, "Thesis")
        self.assertEqual(code_of(self.svc.create, self.alice, "THESIS")[0], "WORKSPACE_NAME_TAKEN")
        self.svc.create(self.bob, "Thesis")                      # another person may use the same name
        self.svc.create(self.alice, "Other")

    def test_other_people_get_404_for_read_change_and_delete(self):
        w = self.svc.create(self.alice, "Private")
        for call in (lambda: self.svc.get(self.bob, w["id"]), lambda: self.svc.update(self.bob, w["id"], name="x"), lambda: self.svc.delete(self.bob, w["id"])):
            self.assertEqual(code_of(call), ("NOT_FOUND", 404))
        self.assertEqual(self.svc.list(self.bob)["items"], [])
        self.assertEqual(self.svc.get(self.alice, w["id"])["name"], "Private")

    def test_the_platform_owner_gets_no_access_to_other_peoples_workspaces(self):
        owner, _ = self.h.make_owner()
        w = self.svc.create(self.alice, "Private")
        self.assertEqual(code_of(self.svc.get, owner, w["id"]), ("NOT_FOUND", 404))
        self.assertEqual(self.svc.list(owner)["items"], [])

    def test_standard_limit_then_owner_override_then_reset(self):
        for i in range(3):
            self.svc.create(self.alice, f"w{i}")
        self.assertEqual(code_of(self.svc.create, self.alice, "w3"), ("WORKSPACE_LIMIT_REACHED", 403))
        owner, _ = self.h.make_owner()
        p7_owner = OwnerOpsService(self.h.services().ctx, self.h.workspace())
        p7_owner.set_quota(owner, "standard_max_research_workspaces", 4)
        self.assertEqual(self.svc.limits(self.alice)["max_workspaces"], 4)
        self.svc.create(self.alice, "w3")
        self.assertEqual(code_of(self.svc.create, self.alice, "w4")[0], "WORKSPACE_LIMIT_REACHED")
        self.assertEqual(code_of(p7_owner.set_quota, owner, "standard_max_research_workspaces", 0)[0], "VALIDATION_ERROR")
        self.assertEqual(code_of(p7_owner.set_quota, owner, "standard_max_research_workspaces", 10 ** 6)[0], "VALIDATION_ERROR")
        p7_owner.reset_quota(owner, "standard_max_research_workspaces")
        self.assertEqual(self.svc.limits(self.alice)["max_workspaces"], 3)
        self.assertEqual(code_of(p7_owner.set_quota, self.alice, "standard_max_research_workspaces", 99)[1], 403)

    def test_special_accounts_get_the_safeguard_ceiling_not_unlimited(self):
        self.h.register("sam@example.com")
        sam = self.h.login("sam@example.com").principal
        sam = self.special(sam, "sam@example.com")
        lim = self.svc.limits(sam)
        self.assertEqual((lim["tier"], lim["max_workspaces"], lim["max_sources_per_workspace"], lim["safeguard_applies"]), ("special", 5, 4, True))
        for i in range(5):
            self.svc.create(sam, f"s{i}")
        self.assertEqual(code_of(self.svc.create, sam, "s5")[0], "WORKSPACE_LIMIT_REACHED")
        self.assertEqual(self.svc.limits(self.alice)["tier"], "standard")

    def test_revoking_special_access_applies_the_standard_limit_again(self):
        self.h.register("sam@example.com")
        sam = self.special(self.h.login("sam@example.com").principal, "sam@example.com")
        self.assertEqual(self.svc.limits(sam)["max_workspaces"], 5)
        owner, _ = self.h.make_owner("owner2@example.com")
        ent = self.h.services().codes.entitlements_for(sam)[0]["id"]
        self.h.services().codes.revoke_entitlement(owner, ent, "test")
        sam_again = self.h.login("sam@example.com").principal
        self.assertEqual(self.svc.limits(sam_again)["max_workspaces"], 3)

    def test_source_slot_check_for_phase_ten(self):
        w = self.svc.create(self.alice, "Lit")
        ins = lambda i: self.h.conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                                            (f"s{i}", w["id"], self.alice.user_id, "pdf", "t", "pending", "t", "t"))
        self.svc.ensure_source_slot(self.alice, w["id"])
        ins(1), ins(2)
        self.assertEqual(code_of(self.svc.ensure_source_slot, self.alice, w["id"]), ("SOURCE_LIMIT_REACHED", 403))
        self.assertEqual(code_of(self.svc.ensure_source_slot, self.bob, w["id"]), ("NOT_FOUND", 404))
        self.assertEqual(self.svc.get(self.alice, w["id"])["counts"]["sources"], 2)

    def test_supported_source_kinds_and_database_constraints(self):
        self.assertEqual(set(SOURCE_KINDS), {"pdf", "docx", "web_article", "academic_paper", "csv", "xlsx"})
        self.assertEqual(set(self.svc.limits(self.alice)["supported_source_kinds"]), set(SOURCE_KINDS))
        w = self.svc.create(self.alice, "Lit")
        with self.assertRaises(sqlite3.IntegrityError):
            self.h.conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('x',?,?,'video','t','pending','t','t')", (w["id"], self.alice.user_id))
        with self.assertRaises(sqlite3.IntegrityError):    # a source cannot be attached to someone else's workspace
            self.h.conn.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('y',?,?,'pdf','t','pending','t','t')", (w["id"], self.bob.user_id))

    def test_conversation_rows_store_questions_responses_and_source_references(self):
        w = self.svc.create(self.alice, "Lit")
        c = self.h.conn
        c.execute("INSERT INTO rw_sources (id,workspace_id,owner_user_id,kind,title,status,created_at,updated_at) VALUES ('s1',?,?,'pdf','Paper','ready','t','t')", (w["id"], self.alice.user_id))
        c.execute("INSERT INTO rw_conversations (id,workspace_id,owner_user_id,title,created_at,updated_at) VALUES ('c1',?,?,'Q','t','t')", (w["id"], self.alice.user_id))
        c.execute("INSERT INTO rw_messages (id,conversation_id,workspace_id,owner_user_id,role,body,source_refs,created_at) VALUES ('m1','c1',?,?,'user','What is X?','[]','t')", (w["id"], self.alice.user_id))
        c.execute("INSERT INTO rw_messages (id,conversation_id,workspace_id,owner_user_id,role,body,source_refs,created_at) VALUES ('m2','c1',?,?,'assistant','X is Y','[{\"source_id\":\"s1\",\"locator\":\"p3\"}]','t')", (w["id"], self.alice.user_id))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO rw_messages (id,conversation_id,workspace_id,owner_user_id,role,body,source_refs,created_at) VALUES ('m3','c1',?,?,'user','x','not json','t')", (w["id"], self.alice.user_id))
        with self.assertRaises(sqlite3.IntegrityError):    # a message cannot be written into someone else's conversation
            c.execute("INSERT INTO rw_messages (id,conversation_id,workspace_id,owner_user_id,role,body,source_refs,created_at) VALUES ('m4','c1',?,?,'user','x','[]','t')", (w["id"], self.bob.user_id))
        self.assertEqual(self.svc.get(self.alice, w["id"])["counts"], {"sources": 1, "conversations": 1, "notes": 0})
        self.svc.delete(self.alice, w["id"])
        for t in ("rw_sources", "rw_conversations", "rw_messages", "rw_notes", "rw_workspaces"):
            self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 0, t)

    def test_validation(self):
        for bad in ("", "   ", None, "x" * 121, 5, "a\x00b" * 0):
            self.assertEqual(code_of(self.svc.create, self.alice, bad)[0], "VALIDATION_ERROR", repr(bad))
        w = self.svc.create(self.alice, "ok")
        self.assertEqual(code_of(self.svc.update, self.alice, w["id"], status="deleted")[0], "VALIDATION_ERROR")
        self.assertEqual(code_of(self.svc.update, self.alice, ["x"], name="y")[0], "NOT_FOUND")

    def test_audit_records_ids_not_content(self):
        w = self.svc.create(self.alice, "Secret topic name", "secret description")
        dump = repr([dict(r) for r in self.h.conn.execute("SELECT * FROM audit_events").fetchall()])
        self.assertIn("rw.workspace.created", dump)
        self.assertNotIn("Secret topic name", dump)
        self.assertNotIn("secret description", dump)
        self.assertIn(w["id"], dump)


if __name__ == "__main__":
    unittest.main()
