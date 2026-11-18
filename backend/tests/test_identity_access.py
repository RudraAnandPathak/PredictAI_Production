import re
import threading
import unittest

from app.core.errors import AppError
from app.modules.identity.authorization import permissions_for
from app.modules.identity.constants import Event, Perm, Role, Tier
from app.modules.identity.provisioning import provision_owner, recover_owner_access
from support import GOOD_PASSWORD, OTHER_PASSWORD, Harness


class Base(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.addCleanup(self.h.close)

    def code_of(self, fn, *args, **kwargs):
        with self.assertRaises(AppError) as ctx:
            fn(*args, **kwargs)
        return ctx.exception

    def events(self, event_type):
        return self.h.rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", (event_type,))

    def owner_and_user(self):
        owner, owner_token = self.h.make_owner()
        user, user_token = self.h.make_user("alice@example.com")
        return owner_token, user_token, user


class RoleAndPermissionTests(Base):
    def test_normal_special_and_owner_permission_sets(self):
        owner_token, user_token, user = self.owner_and_user()
        normal = self.h.principal(user_token)
        self.assertEqual(normal.roles, {Role.USER})
        self.assertTrue(normal.can(Perm.ACCOUNT_READ))
        for denied in (Perm.OWNER_USERS_READ, Perm.OWNER_ACCESS_CODES_MANAGE, Perm.UPLOADS_UNLIMITED, Perm.WORKSPACE_DEVELOPMENT):
            self.assertFalse(normal.can(denied), denied)
        owner = self.h.principal(owner_token)
        for allowed in (Perm.OWNER_USERS_READ, Perm.OWNER_AUDIT_READ, Perm.OWNER_OWNERS_MANAGE, Perm.UPLOADS_UNLIMITED):
            self.assertTrue(owner.can(allowed), allowed)

    def test_unknown_roles_and_tiers_grant_nothing(self):
        self.assertEqual(permissions_for(frozenset({"ghost"}), frozenset({"imaginary"})), frozenset())

    def test_owner_services_refuse_ordinary_users(self):
        _, user_token, user = self.owner_and_user()
        principal = self.h.principal(user_token)
        s = self.h.services()
        for call in (lambda: s.users.list_users(principal), lambda: s.users.block_user(principal, user.user_id),
                     lambda: s.users.promote_owner(principal, user.user_id), lambda: s.codes.create_code(principal),
                     lambda: s.codes.grant_special(principal, user.user_id), lambda: s.codes.list_codes(principal)):
            self.assertEqual(self.code_of(call).code, "FORBIDDEN")

    def test_there_is_no_way_to_assign_yourself_a_role_through_the_services(self):
        _, user_token, user = self.owner_and_user()
        principal = self.h.principal(user_token)
        self.assertEqual(self.code_of(self.h.services().users.promote_owner, principal, user.user_id).code, "FORBIDDEN")
        self.assertEqual(self.code_of(self.h.services().codes.grant_special, principal, user.user_id).code, "FORBIDDEN")
        self.assertEqual(self.h.principal(user_token).roles, {Role.USER})


class AccessCodeTests(Base):
    def make_code(self, owner_token, **kwargs):
        return self.h.services().codes.create_code(self.h.principal(owner_token), **kwargs)

    def test_codes_are_random_individual_and_stored_only_as_hashes(self):
        owner_token, *_ = self.owner_and_user()
        a, b = self.make_code(owner_token, label="for Alice"), self.make_code(owner_token)
        self.assertNotEqual(a["code"], b["code"])
        self.assertRegex(a["code"], r"^PAI(-[0-9A-Z]{5}){4}$")
        self.assertNotIn(a["code"], self.h.dump())
        self.assertNotIn(a["code"].replace("-", ""), self.h.dump())
        listing = self.h.services().codes.list_codes(self.h.principal(owner_token))
        self.assertNotIn(a["code"], repr(listing))
        self.assertEqual({row["state"] for row in listing}, {"unused"})
        created = self.events(Event.ACCESS_CODE_CREATED)
        self.assertEqual(len(created), 2)
        self.assertNotIn(a["code"], repr(created))

    def test_redeeming_a_code_gives_special_access_that_persists_across_sessions(self):
        owner_token, user_token, user = self.owner_and_user()
        code = self.make_code(owner_token)["code"]
        before = self.h.principal(user_token)
        self.assertFalse(before.can(Perm.UPLOADS_UNLIMITED))
        result = self.h.services().codes.activate(before, code.lower().replace("-", " "))  # forgiving input format
        self.assertEqual(result["tier"], Tier.SPECIAL)
        self.assertTrue(self.h.principal(user_token).can(Perm.UPLOADS_UNLIMITED))     # same session, immediately
        self.h.services().auth.logout(user_token)
        fresh = self.h.login("alice@example.com").principal                            # a brand-new session / device
        self.assertIn(Tier.SPECIAL, fresh.tiers)
        self.assertTrue(fresh.can(Perm.WORKSPACE_DEVELOPMENT))
        self.assertEqual(self.h.rows("SELECT source FROM entitlements")[0]["source"], "access_code")
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Special access was added to your account")
        self.assertEqual({r["state"] for r in self.h.services().codes.list_codes(self.h.principal(owner_token))}, {"redeemed"})

    def test_invalid_codes_all_look_the_same_and_are_audited(self):
        owner_token, user_token, user = self.owner_and_user()
        principal = self.h.principal(user_token)
        errors = [self.code_of(self.h.services().codes.activate, principal, bad) for bad in ("PAI-AAAAA-AAAAA-AAAAA-AAAAA", "", "x" * 200, None, 12)]
        self.assertEqual({(e.code, e.status_code, e.message) for e in errors}, {("INVALID_CODE", 400, "That access code is not valid.")})
        self.assertEqual([e["reason"] for e in self.events(Event.ACCESS_CODE_FAILED)], ["unknown", "malformed", "malformed", "malformed", "malformed"])
        self.assertFalse(self.h.principal(user_token).can(Perm.UPLOADS_UNLIMITED))

    def test_a_code_cannot_be_reused(self):
        owner_token, user_token, _ = self.owner_and_user()
        bob, bob_token = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        code = self.make_code(owner_token)["code"]
        self.h.services().codes.activate(self.h.principal(user_token), code)
        self.assertEqual(self.code_of(self.h.services().codes.activate, self.h.principal(bob_token), code).code, "INVALID_CODE")
        self.assertEqual(self.code_of(self.h.services().codes.activate, self.h.principal(user_token), code).code, "INVALID_CODE")
        self.assertFalse(self.h.principal(bob_token).can(Perm.UPLOADS_UNLIMITED))
        self.assertEqual(self.events(Event.ACCESS_CODE_FAILED)[0]["reason"], "already_used")

    def test_concurrent_redemption_succeeds_exactly_once(self):
        owner_token, user_token, _ = self.owner_and_user()
        _, bob_token = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        code = self.make_code(owner_token)["code"]
        principals = [self.h.principal(user_token), self.h.principal(bob_token)]
        results, barrier = [], threading.Barrier(2)

        def redeem(principal):
            conn = self.h.db.connect()
            try:
                services = self.h.services(conn=conn)
                barrier.wait()
                services.codes.activate(principal, code)
                results.append("ok")
            except AppError as error:
                results.append(error.code)
            finally:
                conn.close()

        threads = [threading.Thread(target=redeem, args=(p,)) for p in principals]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["INVALID_CODE", "ok"])
        self.assertEqual(self.h.rows("SELECT COUNT(*) AS n FROM entitlements")[0]["n"], 1)

    def test_codes_expire_and_can_be_bound_to_one_person(self):
        owner_token, user_token, user = self.owner_and_user()
        _, bob_token = self.h.make_user("bob@example.com", OTHER_PASSWORD)
        short = self.make_code(owner_token, expires_in_days=1)["code"]
        self.h.clock.advance(days=2)
        user_token = self.h.login("alice@example.com").session.token           # sessions idled out while time advanced
        self.assertEqual(self.code_of(self.h.services().codes.activate, self.h.principal(user_token), short).code, "INVALID_CODE")
        self.assertEqual(self.events(Event.ACCESS_CODE_FAILED)[-1]["reason"], "expired")

        owner_token = self.h.login("owner@example.com").session.token
        bob_token = self.h.login("bob@example.com", OTHER_PASSWORD).session.token
        bound = self.make_code(owner_token, target_user_id=user.user_id)["code"]
        self.assertEqual(self.code_of(self.h.services().codes.activate, self.h.principal(bob_token), bound).code, "INVALID_CODE")
        self.assertEqual(self.events(Event.ACCESS_CODE_FAILED)[-1]["reason"], "wrong_user")
        self.h.services().codes.activate(self.h.principal(user_token), bound)

    def test_unused_codes_can_be_revoked_but_redeemed_ones_cannot(self):
        owner_token, user_token, _ = self.owner_and_user()
        revoked, used = self.make_code(owner_token), self.make_code(owner_token)
        owner = self.h.principal(owner_token)
        self.h.services().codes.revoke_code(owner, revoked["id"])
        self.assertEqual(self.code_of(self.h.services().codes.activate, self.h.principal(user_token), revoked["code"]).code, "INVALID_CODE")
        self.h.services().codes.activate(self.h.principal(user_token), used["code"])
        self.assertEqual(self.code_of(self.h.services().codes.revoke_code, owner, used["id"]).code, "CODE_ALREADY_USED")
        self.assertEqual(len(self.events(Event.ACCESS_CODE_REVOKED)), 1)

    def test_revoking_special_access_takes_effect_on_the_very_next_check(self):
        owner_token, user_token, user = self.owner_and_user()
        self.h.services().codes.activate(self.h.principal(user_token), self.make_code(owner_token)["code"])
        self.assertTrue(self.h.principal(user_token).can(Perm.UPLOADS_UNLIMITED))
        entitlement = self.h.rows("SELECT id FROM entitlements")[0]["id"]
        self.h.services().codes.revoke_entitlement(self.h.principal(owner_token), entitlement, "no longer needed")
        after = self.h.principal(user_token)                       # same session, no re-login
        self.assertFalse(after.can(Perm.UPLOADS_UNLIMITED))
        self.assertEqual(after.tiers, {Tier.STANDARD})
        self.assertEqual(self.events(Event.ENTITLEMENT_REVOKED)[0]["reason"], "no longer needed")
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Special access was removed from your account")

    def test_owner_can_grant_directly_and_not_twice(self):
        owner_token, user_token, user = self.owner_and_user()
        self.h.services().codes.grant_special(self.h.principal(owner_token), user.user_id)
        self.assertTrue(self.h.principal(user_token).can(Perm.UPLOADS_UNLIMITED))
        self.assertEqual(self.code_of(self.h.services().codes.grant_special, self.h.principal(owner_token), user.user_id).code, "ALREADY_SPECIAL")
        self.assertEqual(self.h.rows("SELECT source FROM entitlements")[0]["source"], "owner_grant")

    def test_wrong_codes_are_throttled_per_user(self):
        owner_token, user_token, _ = self.owner_and_user()
        principal = self.h.principal(user_token)
        for _ in range(5):
            self.assertEqual(self.code_of(self.h.services().codes.activate, principal, "PAI-WRONG-WRONG-WRONG-WRONG").code, "INVALID_CODE")
        self.assertEqual(self.code_of(self.h.services().codes.activate, principal, "PAI-WRONG-WRONG-WRONG-WRONG").code, "RATE_LIMITED")

    def test_creating_codes_needs_a_recent_sign_in(self):
        owner_token, *_ = self.owner_and_user()
        self.h.clock.advance(minutes=11)                           # still inside the 60 min owner idle window
        self.assertEqual(self.code_of(self.make_code, owner_token).code, "REAUTH_REQUIRED")
        issued = self.h.services().auth.reauthenticate(self.h.principal(owner_token), GOOD_PASSWORD)
        self.assertTrue(self.make_code(issued.token)["code"])

    def test_code_options_are_validated(self):
        owner_token, *_ = self.owner_and_user()
        for kwargs in ({"expires_in_days": 0}, {"expires_in_days": 500}, {"expires_in_days": "7"}, {"target_user_id": "nope"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.code_of(self.make_code, owner_token, **kwargs).code, "VALIDATION_ERROR")


class OwnerAdministrationTests(Base):
    def test_listing_and_searching_users(self):
        owner_token, user_token, _ = self.owner_and_user()
        self.h.make_user("bob@example.com", OTHER_PASSWORD, "Bobby")
        s, owner = self.h.services(), self.h.principal(owner_token)
        page = s.users.list_users(owner)
        self.assertEqual(page["total"], 3)
        self.assertEqual([u["email"] for u in s.users.list_users(owner, query="bob")["items"]], ["bob@example.com"])
        self.assertEqual(s.users.list_users(owner, query="BOBBY")["total"], 1)
        self.assertEqual(s.users.list_users(owner, query="100%")["total"], 0)       # wildcards are escaped
        self.assertEqual(s.users.list_users(owner, limit=1, offset=1)["items"].__len__(), 1)
        for item in page["items"]:
            self.assertFalse({"password_hash", "token_hash", "authz_version"} & set(item))

    def test_blocking_ends_sessions_and_prevents_sign_in_and_unblocking_restores(self):
        owner_token, user_token, user = self.owner_and_user()
        owner = self.h.principal(owner_token)
        self.h.services().users.block_user(owner, user.user_id, "abuse report")
        self.assertEqual(self.code_of(self.h.principal, user_token).code, "SESSION_EXPIRED")
        self.assertEqual(self.code_of(self.h.login, "alice@example.com").code, "INVALID_CREDENTIALS")
        self.assertEqual(self.events(Event.USER_BLOCKED)[0]["reason"], "abuse report")
        self.assertEqual(self.h.last_email("alice@example.com").subject, "Your PredictAI account was suspended")
        self.h.services().users.unblock_user(owner, user.user_id)
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)
        self.assertEqual(len(self.events(Event.USER_UNBLOCKED)), 1)

    def test_owners_and_self_cannot_be_blocked(self):
        owner_token, *_ = self.owner_and_user()
        owner = self.h.principal(owner_token)
        self.assertEqual(self.code_of(self.h.services().users.block_user, owner, owner.user_id).code, "CANNOT_BLOCK_SELF")
        _, backup_token = self.h.make_owner("backup@example.com")
        backup = self.h.principal(backup_token)
        self.assertEqual(self.code_of(self.h.services().users.block_user, owner, backup.user_id).code, "OWNER_PROTECTED")

    def test_mutations_need_step_up_but_reads_do_not(self):
        owner_token, _, user = self.owner_and_user()
        self.h.clock.advance(minutes=11)
        owner = self.h.principal(owner_token)
        self.assertTrue(self.h.services().users.list_users(owner))
        self.assertEqual(self.code_of(self.h.services().users.block_user, owner, user.user_id).code, "REAUTH_REQUIRED")

    def test_backup_owner_promotion_rules(self):
        owner_token, user_token, user = self.owner_and_user()
        owner = self.h.principal(owner_token)
        self.h.services().users.promote_owner(owner, user.user_id)
        self.assertEqual(self.code_of(self.h.principal, user_token).code, "SESSION_EXPIRED")     # sessions reset on role change
        self.assertIn(Role.OWNER, self.h.login("alice@example.com").principal.roles)
        self.assertEqual(self.code_of(self.h.services().users.promote_owner, owner, user.user_id).code, "ALREADY_OWNER")
        self.assertEqual(len(self.events(Event.OWNER_PROMOTED)), 1)

    def test_owner_limit_pending_accounts_and_last_owner_protection(self):
        owner_token, *_ = self.owner_and_user()
        owner = self.h.principal(owner_token)
        for i in range(2):
            u, _ = self.h.make_user(f"extra{i}@example.com", OTHER_PASSWORD)
            self.h.services().users.promote_owner(owner, u.user_id)
        late, _ = self.h.make_user("late@example.com", OTHER_PASSWORD)
        self.assertEqual(self.code_of(self.h.services().users.promote_owner, owner, late.user_id).code, "OWNER_LIMIT")
        # demotion: not yourself, and never the last owner
        self.assertEqual(self.code_of(self.h.services().users.demote_owner, owner, owner.user_id).code, "CANNOT_DEMOTE_SELF")
        others = [o for o in self.h.rows("SELECT user_id FROM user_roles WHERE role = 'owner'") if o["user_id"] != owner.user_id]
        for other in others:
            self.h.services().users.demote_owner(owner, other["user_id"])
        self.assertEqual(self.code_of(self.h.services().users.demote_owner, owner, owner.user_id).code, "CANNOT_DEMOTE_SELF")
        self.assertEqual(len(self.h.services().ctx.repo.active_owner_ids()), 1)

    def test_unverified_accounts_cannot_become_owners_when_verification_is_required(self):
        h = Harness(require_email_verification=True)
        self.addCleanup(h.close)
        _, owner_token = h.make_owner()
        h.register("alice@example.com")
        alice = h.rows("SELECT id FROM users WHERE email_normalized = 'alice@example.com'")[0]["id"]
        owner = h.principal(owner_token)
        self.assertEqual(self.code_of(h.services().users.promote_owner, owner, alice).code, "TARGET_NOT_ELIGIBLE")

    def test_audit_trail_is_queryable_filterable_paginated_and_secret_free(self):
        owner_token, user_token, user = self.owner_and_user()
        owner = self.h.principal(owner_token)
        self.h.services().users.block_user(owner, user.user_id, "test")
        s = self.h.services()
        everything = s.audit.query(limit=500)["items"]
        self.assertTrue(everything)
        blocked = s.audit.query(event_type="user.")["items"]
        self.assertEqual([e["event_type"] for e in blocked], [Event.USER_BLOCKED])
        self.assertEqual(blocked[0]["actor_email"], "owner@example.com")
        self.assertEqual(blocked[0]["target_email"], "alice@example.com")
        first = s.audit.query(limit=2)
        second = s.audit.query(limit=2, before_id=first["next_before_id"])
        self.assertTrue(set(i["id"] for i in first["items"]).isdisjoint(i["id"] for i in second["items"]))
        text = repr(everything)
        for secret in (GOOD_PASSWORD, user_token, owner_token):
            self.assertNotIn(secret, text)
        self.assertTrue(all(e["source_ip"] in (None, "203.0.113.0") for e in everything))   # truncated address only

    def test_audit_metadata_is_redacted_defensively(self):
        from app.modules.identity.audit import clean_metadata

        text = clean_metadata({"password": "hunter2", "api_key": "k", "token": "t", "code": "PAI-1", "access_code_id": "abc", "provider": "google"})
        self.assertNotIn("hunter2", text)
        self.assertIn("access_code_id", text)
        self.assertEqual(text.count("[redacted]"), 4)


class DeletionTests(Base):
    def test_deleting_an_account_erases_personal_data_and_blocks_sign_in(self):
        owner_token, user_token, user = self.owner_and_user()
        principal = self.h.principal(user_token)
        self.h.services().users.delete_account(principal, GOOD_PASSWORD)
        row = self.h.rows("SELECT * FROM users WHERE id = ?", (user.user_id,))[0]
        self.assertEqual((row["status"], row["display_name"], row["email"]), ("deleted", None, f"deleted-{user.user_id}@deleted.invalid"))
        self.assertNotIn("alice@example.com", self.h.dump().replace("deleted-", ""))
        self.assertEqual(self.h.rows("SELECT * FROM identities WHERE user_id = ?", (user.user_id,)), [])
        self.assertEqual(self.h.rows("SELECT * FROM sessions WHERE user_id = ?", (user.user_id,)), [])
        self.assertEqual(self.code_of(self.h.login, "alice@example.com").code, "INVALID_CREDENTIALS")
        self.assertEqual(self.code_of(self.h.principal, user_token).code, "SESSION_EXPIRED")
        self.assertEqual(len(self.events(Event.ACCOUNT_DELETED)), 1)         # history survives, pseudonymously
        self.assertEqual(self.h.last_email().subject, "Your PredictAI account was deleted")
        self.h.register("alice@example.com")                                  # the address can be registered again
        self.assertTrue(self.h.login("alice@example.com").principal.user_id)

    def test_deletion_needs_the_password(self):
        _, user_token, _ = self.owner_and_user()
        principal = self.h.principal(user_token)
        self.assertEqual(self.code_of(self.h.services().users.delete_account, principal, "wrong-wrong-1").code, "INVALID_PASSWORD")
        self.assertEqual(self.h.rows("SELECT status FROM users WHERE email_normalized = 'alice@example.com'")[0]["status"], "active")

    def test_the_last_owner_cannot_delete_their_account(self):
        owner_token, *_ = self.owner_and_user()
        self.assertEqual(self.code_of(self.h.services().users.delete_account, self.h.principal(owner_token), GOOD_PASSWORD).code, "LAST_OWNER")

    def test_deleted_users_disappear_from_owner_listings(self):
        owner_token, user_token, user = self.owner_and_user()
        self.h.services().users.delete_account(self.h.principal(user_token), GOOD_PASSWORD)
        owner = self.h.principal(owner_token)
        self.assertEqual([u["email"] for u in self.h.services().users.list_users(owner)["items"]], ["owner@example.com"])
        self.assertEqual(self.code_of(self.h.services().users.get_user, owner, user.user_id).code, "NOT_FOUND")


class ProvisioningTests(Base):
    def test_a_fresh_database_has_no_owner_and_no_default_credentials(self):
        self.assertEqual(self.h.rows("SELECT * FROM users"), [])
        self.assertEqual(self.h.rows("SELECT * FROM user_roles"), [])

    def test_first_owner_then_backup_owner_rules(self):
        s = self.h.services()
        created = provision_owner(s.ctx, email="boss@example.com", password=GOOD_PASSWORD, display_name="Boss")
        self.assertTrue(created["created"])
        user = self.h.rows("SELECT status, verified_at FROM users")[0]
        self.assertEqual(user["status"], "active")
        self.assertIsNotNone(user["verified_at"])
        self.assertEqual(self.code_of(provision_owner, s.ctx, email="second@example.com", password=GOOD_PASSWORD).code, "OWNER_EXISTS")
        provision_owner(s.ctx, email="second@example.com", password=GOOD_PASSWORD, allow_additional=True)
        provision_owner(s.ctx, email="third@example.com", password=GOOD_PASSWORD, allow_additional=True)
        self.assertEqual(self.code_of(provision_owner, s.ctx, email="fourth@example.com", password=GOOD_PASSWORD, allow_additional=True).code, "OWNER_LIMIT")
        self.assertEqual(len(self.events(Event.OWNER_PROVISIONED)), 3)

    def test_weak_passwords_are_rejected_and_nothing_is_created(self):
        for password in (None, "short", "password123"):
            self.assertEqual(self.code_of(provision_owner, self.h.services().ctx, email="boss@example.com", password=password).code, "VALIDATION_ERROR")
        self.assertEqual(self.h.rows("SELECT * FROM users"), [])

    def test_an_existing_account_can_be_promoted_without_changing_its_password(self):
        self.h.make_user("alice@example.com")
        result = provision_owner(self.h.services().ctx, email="alice@example.com", password=None)
        self.assertFalse(result["created"])
        self.assertIn(Role.OWNER, self.h.login("alice@example.com").principal.roles)

    def test_break_glass_recovery_resets_password_unblocks_and_ends_sessions(self):
        owner, token = self.h.make_owner("boss@example.com")
        self.h.conn.execute("UPDATE users SET status = 'blocked' WHERE id = ?", (owner.user_id,))
        recover_owner_access(self.h.services().ctx, email="boss@example.com", new_password=OTHER_PASSWORD)
        self.assertEqual(self.code_of(self.h.principal, token).code, "SESSION_EXPIRED")
        self.assertTrue(self.h.login("boss@example.com", OTHER_PASSWORD).principal.is_owner)
        event = self.events(Event.OWNER_RECOVERED)[0]
        self.assertIsNone(event["actor_user_id"])                      # done from the server, not through the API
        self.assertEqual(self.code_of(recover_owner_access, self.h.services().ctx, email="nobody@example.com", new_password=OTHER_PASSWORD).code, "NOT_FOUND")
        self.h.make_user("alice@example.com")
        self.assertEqual(self.code_of(recover_owner_access, self.h.services().ctx, email="alice@example.com", new_password=OTHER_PASSWORD).code, "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
