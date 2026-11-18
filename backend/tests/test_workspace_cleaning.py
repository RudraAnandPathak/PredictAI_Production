"""Cleaning: preview accuracy, confirmation, derived versions, history, rollback, and the guarantee that originals never change."""
import hashlib
import json
import unittest
from datetime import timedelta

from app.core.errors import AppError
from app.modules.workspace.cleaning_ops import Plan, convert_value
from test_workspace_extraction import Base
from test_workspace_uploads import code_of

DATA = (b"id,name,score,joined,city\n"
        b"1, Ada ,90,2024-01-05,Pune\n"
        b"2,Grace,,2024-02-10,Agra\n"
        b"2,Grace,,2024-02-10,Agra\n"
        b"3,Linus,abc,2024-03-15,Pune\n"
        b"4,,70,2024-04-20,Delhi\n"
        b"5,Ken,80,2024-05-25,pune\n")


class CleaningBase(Base):
    def setUp(self):
        super().setUp()
        self.asset = self.ok("people.csv", DATA)
        self.aid = self.asset["id"]
        self.original = self.ws.repo.versions(self.aid)[0]

    def preview(self, ops, who=None):
        return self.ws.cleaning.preview(who or self.alice, self.aid, ops)

    def apply(self, ops):
        p = self.preview(ops)
        return p, self.ws.cleaning.confirm(self.alice, p["id"], True)

    def table(self, version_id=None):
        rows = self.ws.data.preview(self.alice, self.aid, version_id=version_id, limit=200)
        return [c["name"] for c in rows["columns"]], rows["rows"]

    def original_bytes(self):
        return self.h.storage.read_bytes(self.original["storage_key"])


class PlanTests(unittest.TestCase):
    H = ["a", "b"]

    def bad(self, ops, header=None):
        with self.assertRaises(AppError) as cm:
            Plan(ops, header or self.H)
        self.assertEqual(cm.exception.code, "VALIDATION_ERROR")
        return cm.exception.details[0]["message"]

    def test_unknown_steps_columns_and_values_are_refused_with_a_plain_reason(self):
        self.assertIn("not supported", self.bad([{"op": "eval", "code": "1"}]))
        self.assertIn("no column called", self.bad([{"op": "drop_columns", "columns": ["zzz"]}]))
        self.assertIn("at least one column must remain", self.bad([{"op": "drop_columns", "columns": ["a", "b"]}]))
        self.assertIn("already called", self.bad([{"op": "rename_column", "column": "a", "new_name": "B"}]))
        self.assertIn("needs a number", self.bad([{"op": "filter_rows", "column": "a", "filter": "gt", "value": "x"}]))
        self.assertIn("Add at least one", self.bad([]))
        self.assertIn("25 steps", self.bad([{"op": "trim_whitespace"}] * 26))
        self.assertIn("up to 500", self.bad([{"op": "fill_missing", "column": "a", "strategy": "constant", "value": "x" * 501}]))
        self.bad("not a list")

    def test_later_steps_see_the_schema_produced_by_earlier_ones(self):
        plan = Plan([{"op": "rename_column", "column": "a", "new_name": "alpha"}, {"op": "drop_columns", "columns": ["b"]}], self.H)
        self.assertEqual(plan.names, ["alpha"])
        self.bad([{"op": "rename_column", "column": "a", "new_name": "alpha"}, {"op": "drop_columns", "columns": ["a"]}])  # old name is gone

    def test_every_step_has_an_explanation_and_the_statistic_source_is_stated(self):
        plan = Plan([{"op": "fill_missing", "column": "a", "strategy": "median"}, {"op": "drop_duplicates"}], self.H)
        self.assertEqual(len(plan.explanations), 2)
        self.assertIn("of the original column", plan.explanations[0])

    def test_conversions(self):
        self.assertEqual(convert_value("$1,234.50", "float"), "1234.5")
        self.assertEqual(convert_value("12.0", "integer"), "12")
        self.assertIsNone(convert_value("12.5", "integer"))
        self.assertIsNone(convert_value("abc", "float"))
        self.assertEqual(convert_value("Yes", "boolean"), "true")
        self.assertEqual(convert_value("0", "boolean"), "false")
        self.assertIsNone(convert_value("maybe", "boolean"))
        self.assertEqual(convert_value("25/12/2024", "date", "dmy"), "2024-12-25")
        self.assertEqual(convert_value("12/25/2024", "date", "mdy"), "2024-12-25")
        self.assertIsNone(convert_value("25/12/2024", "date", "mdy"))  # impossible month: never guessed
        self.assertEqual(convert_value("2024-02-03", "date"), "2024-02-03")


class PreviewTests(CleaningBase):
    def test_preview_changes_nothing_and_reports_exact_effects(self):
        p = self.preview([{"op": "drop_duplicates"}, {"op": "trim_whitespace"}])
        s = p["summary"]
        self.assertEqual((s["rows_before"], s["rows_after"], s["rows_removed"], s["cells_changed"]), (6, 5, 1, 1))
        self.assertEqual([x["rows_removed"] for x in s["steps"]], [1, 0])
        self.assertEqual(p["removed_rows"][0]["row"], 3)  # the repeated Grace row
        self.assertIn("repeats", p["removed_rows"][0]["reason"].lower() + " repeats")
        self.assertEqual(p["changed_rows"][0]["before"][1], " Ada ")
        self.assertEqual(p["changed_rows"][0]["after"][1], "Ada")
        self.assertEqual(p["changed_rows"][0]["changed_columns"], [1])
        self.assertEqual(len(p["explanation"]), 2)
        self.assertEqual(self.ws.repo.asset(self.alice.user_id, self.aid)["current_version_id"], self.original["id"])
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 1)
        self.assertEqual(self.ws.cleaning.history(self.alice, self.aid)["items"], [])

    def test_fill_statistics_come_from_the_source_column(self):
        p = self.preview([{"op": "filter_rows", "column": "id", "filter": "gt", "value": "3", "action": "keep"},
                          {"op": "fill_missing", "column": "score", "strategy": "median"}])
        # numeric scores in the SOURCE are 90, 70, 80 -> median 80, even though the filter removed most rows first
        self.assertEqual(p["summary"]["steps"][1].get("fill_value"), "80")

    def test_convert_reports_values_that_cannot_be_converted(self):
        p = self.preview([{"op": "convert_type", "column": "score", "to": "integer", "on_invalid": "empty"}])
        self.assertEqual(p["summary"]["steps"][0]["unconvertible"], 1)
        self.assertTrue(any(w["code"] == "UNCONVERTIBLE" for w in p["warnings"]))

    def test_steps_that_change_nothing_or_remove_everything_cannot_be_applied(self):
        none = self.preview([{"op": "change_case", "columns": ["id"], "case": "lower"}])
        self.assertFalse(none["summary"]["can_apply"])
        self.assertIn("would not change", none["blocked_reason"])
        every = self.preview([{"op": "filter_rows", "column": "id", "filter": "gt", "value": "999", "action": "keep"}])
        self.assertFalse(every["summary"]["can_apply"])
        self.assertIn("every row", every["blocked_reason"])
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, every["id"], True), "NOT_APPLICABLE")

    def test_large_removals_get_a_warning(self):
        p = self.preview([{"op": "filter_rows", "column": "city", "filter": "eq", "value": "pune", "action": "keep"}])
        self.assertEqual(p["summary"]["rows_after"], 3)  # Pune, Pune, pune (case-insensitive)
        self.assertTrue(any(w["code"] == "MANY_ROWS_REMOVED" for w in p["warnings"]))

    def test_a_new_preview_replaces_the_previous_open_one(self):
        first = self.preview([{"op": "drop_duplicates"}])
        second = self.preview([{"op": "trim_whitespace"}])
        self.assertEqual(code_of(self.ws.cleaning.get_preview, self.alice, first["id"]), "PREVIEW_EXPIRED")
        self.assertEqual(self.ws.cleaning.get_preview(self.alice, second["id"])["id"], second["id"])
        self.assertFalse(self.h.storage.exists(f"p/{self.asset['project_id']}/a/{self.aid}/pv/{first['id']}.json"))

    def test_datasets_only_and_ready_only(self):
        doc = self.ok("n.txt", b"some words in a document\n")
        self.assertEqual(code_of(self.ws.cleaning.preview, self.alice, doc["id"], [{"op": "drop_duplicates"}]), "VALIDATION_ERROR")
        bad = self.failed("b.json", b"{")
        self.assertEqual(code_of(self.ws.cleaning.preview, self.alice, bad["id"], [{"op": "drop_duplicates"}]), "ASSET_NOT_READY")


class ApplyTests(CleaningBase):
    def test_confirm_creates_a_derived_version_and_never_touches_the_original(self):
        before = self.original_bytes()
        p, done = self.apply([{"op": "drop_duplicates"}, {"op": "trim_whitespace"}, {"op": "rename_column", "column": "joined", "new_name": "joined_on"}])
        self.assertTrue(done["applied"])
        self.assertEqual(done["version_number"], 2)
        self.assertEqual(self.original_bytes(), before)
        self.assertEqual(hashlib.sha256(self.original_bytes()).hexdigest(), self.asset["checksum_sha256"])
        v = {x["number"]: x for x in self.ws.assets.get(self.alice, self.aid)["version_list"]}
        self.assertEqual((v[2]["kind"], v[2]["parent_version_id"], v[2]["transformation_id"], v[2]["current"], v[1]["current"]), ("derived", v[1]["id"], p["id"], True, False))
        names, rows = self.table()
        self.assertEqual(names[3], "joined_on")
        self.assertEqual((len(rows), rows[0][1]), (5, "Ada"))
        self.assertEqual(self.ws.assets.get(self.alice, self.aid)["row_count"], 5)
        # the original version's own table is still reachable and unchanged
        n1, r1 = self.table(self.original["id"])
        self.assertEqual((len(r1), n1[3], r1[0][1]), (6, "joined", " Ada "))

    def test_result_matches_the_preview(self):
        p, _ = self.apply([{"op": "fill_missing", "column": "score", "strategy": "median"}, {"op": "drop_missing", "columns": ["name"], "how": "any"},
                           {"op": "change_case", "columns": ["city"], "case": "title"}])
        names, rows = self.table()
        self.assertEqual(len(rows), p["summary"]["rows_after"])
        self.assertEqual([r[4] for r in rows], ["Pune", "Agra", "Agra", "Pune", "Pune"])
        self.assertEqual([r[2] for r in rows], ["90", "80", "80", "abc", "80"])  # empty cells get the source median (80); "abc" is not empty so it stays
        ext = self.ws.data.extraction(self.alice, self.aid)
        self.assertEqual(ext["summary"]["rows"], len(rows))  # the new version has its own extraction facts
        self.assertEqual(ext["parser"], "cleaning/derived")

    def test_confirmation_is_required_single_use_and_expires(self):
        p = self.preview([{"op": "drop_duplicates"}])
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"]), "VALIDATION_ERROR")
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], "yes"), "VALIDATION_ERROR")
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 1)
        self.ws.cleaning.confirm(self.alice, p["id"], True)
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], True), "PREVIEW_ALREADY_USED")
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 2)
        q = self.preview([{"op": "trim_whitespace"}])
        self.h.clock.advance(minutes=self.h.settings.preview_ttl_minutes + 1)
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, q["id"], True), "PREVIEW_EXPIRED")

    def test_discard(self):
        p = self.preview([{"op": "drop_duplicates"}])
        self.assertTrue(self.ws.cleaning.discard(self.alice, p["id"])["discarded"])
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], True), "PREVIEW_ALREADY_USED")
        self.assertFalse(self.h.storage.exists(f"p/{self.asset['project_id']}/a/{self.aid}/pv/{p['id']}.json"))

    def test_stale_preview_after_the_data_moved_on_is_refused(self):
        a = self.preview([{"op": "trim_whitespace"}])
        b_preview = self.ws.cleaning.preview(self.alice, self.aid, [{"op": "drop_duplicates"}])  # supersedes a
        self.ws.cleaning.confirm(self.alice, b_preview["id"], True)
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, a["id"], True), "PREVIEW_ALREADY_USED")
        c = self.preview([{"op": "trim_whitespace"}])
        self.ws.cleaning.rollback(self.alice, self.aid, self.original["id"], True)  # data moves while c is open (rollback discards open previews)
        self.assertIn(code_of(self.ws.cleaning.confirm, self.alice, c["id"], True), ("PREVIEW_ALREADY_USED", "DATA_CHANGED"))

    def test_confirm_refuses_when_the_current_version_moved_after_the_preview(self):
        p = self.preview([{"op": "drop_duplicates"}])
        self.ws.repo.update_asset(self.aid, current_version_id="f" * 32)  # as if a concurrent request changed the current version
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], True), "DATA_CHANGED")
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 1)

    def test_history_records_who_what_and_when(self):
        self.apply([{"op": "drop_duplicates"}])
        self.apply([{"op": "trim_whitespace"}, {"op": "change_case", "columns": ["city"], "case": "lower"}])
        h = self.ws.cleaning.history(self.alice, self.aid)
        self.assertEqual([(i["kind"], i["from_version"], i["to_version"]) for i in h["items"]], [("clean", 1, 2), ("clean", 2, 3)])
        first = h["items"][0]
        self.assertEqual(first["performed_by"]["user_id"], self.alice.user_id)
        self.assertTrue(first["performed_by"]["name"] and first["at"])
        self.assertEqual(first["operations"], [{"op": "drop_duplicates"}])
        self.assertTrue(first["explanation"])
        self.assertEqual(first["summary"]["rows_removed"], 1)
        self.assertEqual([v["number"] for v in h["versions"]], [1, 2, 3])
        self.assertEqual(sum(1 for v in h["versions"] if v["current"]), 1)
        self.assertEqual(h["versions"][2]["parent_version_id"], h["versions"][1]["id"])

    def test_chained_cleaning_builds_on_the_current_version(self):
        self.apply([{"op": "drop_duplicates"}])
        self.apply([{"op": "filter_rows", "column": "id", "filter": "lt", "value": "4", "action": "keep"}])
        _, rows = self.table()
        self.assertEqual([r[0] for r in rows], ["1", "2", "3"])

    def test_version_limit(self):
        from app.modules.workspace import cleaning, constants
        for module in (cleaning,):
            module.MAX_VERSIONS_PER_ASSET = 2
        self.addCleanup(lambda: setattr(cleaning, "MAX_VERSIONS_PER_ASSET", constants.MAX_VERSIONS_PER_ASSET))
        self.apply([{"op": "drop_duplicates"}])
        p = self.preview([{"op": "trim_whitespace"}])
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], True), "VERSION_LIMIT")


class RollbackTests(CleaningBase):
    def test_rollback_restores_an_earlier_version_without_deleting_anything(self):
        self.apply([{"op": "drop_duplicates"}])
        self.apply([{"op": "drop_columns", "columns": ["city"]}])
        self.assertEqual(self.table()[0], ["id", "name", "score", "joined"])
        v = self.ws.cleaning.history(self.alice, self.aid)["versions"]
        self.assertEqual(code_of(self.ws.cleaning.rollback, self.alice, self.aid, v[1]["id"]), "VALIDATION_ERROR")  # needs confirm
        out = self.ws.cleaning.rollback(self.alice, self.aid, v[1]["id"], True)
        self.assertTrue(out["rolled_back"])
        names, rows = self.table()
        self.assertEqual((names[-1], len(rows)), ("city", 5))
        a = self.ws.assets.get(self.alice, self.aid)
        self.assertEqual((a["row_count"], a["column_count"], a["current_version_id"]), (5, 5, v[1]["id"]))
        self.assertEqual(len(a["version_list"]), 3)  # version 3 still exists
        self.assertEqual(len(self.table(v[2]["id"])[1]), 5)
        h = self.ws.cleaning.history(self.alice, self.aid)["items"]
        self.assertEqual((h[-1]["kind"], h[-1]["from_version"], h[-1]["to_version"]), ("rollback", 3, 2))
        # rolling forward again is also a rollback to a recorded version
        self.ws.cleaning.rollback(self.alice, self.aid, v[2]["id"], True)
        self.assertEqual(self.table()[0], ["id", "name", "score", "joined"])
        # and all the way back to the untouched original
        self.ws.cleaning.rollback(self.alice, self.aid, v[0]["id"], True)
        self.assertEqual((len(self.table()[1]), self.table()[1][0][1]), (6, " Ada "))
        self.assertEqual(self.original_bytes(), DATA)

    def test_rollback_errors(self):
        self.apply([{"op": "drop_duplicates"}])
        cur = self.ws.assets.get(self.alice, self.aid)["current_version_id"]
        self.assertEqual(code_of(self.ws.cleaning.rollback, self.alice, self.aid, cur, True), "ALREADY_CURRENT")
        self.assertEqual(code_of(self.ws.cleaning.rollback, self.alice, self.aid, "f" * 32, True), "NOT_FOUND")
        other = self.ok("o.csv", b"x\n1\n")
        self.assertEqual(code_of(self.ws.cleaning.rollback, self.alice, self.aid, other["current_version_id"], True), "NOT_FOUND")

    def test_a_new_cleaning_after_rollback_branches_from_the_restored_version(self):
        self.apply([{"op": "drop_duplicates"}])
        self.ws.cleaning.rollback(self.alice, self.aid, self.original["id"], True)
        self.apply([{"op": "trim_whitespace"}])
        h = self.ws.cleaning.history(self.alice, self.aid)["versions"]
        self.assertEqual(h[2]["parent_version_id"], h[0]["id"])  # version 3 descends from version 1, not 2
        self.assertEqual(len(self.table()[1]), 6)


class IsolationAndPrivacyTests(CleaningBase):
    def test_other_users_cannot_preview_confirm_discard_roll_back_or_read_history(self):
        p = self.preview([{"op": "drop_duplicates"}])
        calls = [lambda: self.ws.cleaning.preview(self.bob, self.aid, [{"op": "drop_duplicates"}]), lambda: self.ws.cleaning.get_preview(self.bob, p["id"]),
                 lambda: self.ws.cleaning.confirm(self.bob, p["id"], True), lambda: self.ws.cleaning.discard(self.bob, p["id"]),
                 lambda: self.ws.cleaning.rollback(self.bob, self.aid, self.original["id"], True), lambda: self.ws.cleaning.history(self.bob, self.aid),
                 lambda: self.ws.cleaning.suggestions(self.bob, self.aid)]
        for call in calls:
            self.assertEqual(code_of(call), "NOT_FOUND")
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 1)

    def test_archived_projects_are_read_only_for_cleaning(self):
        p = self.preview([{"op": "drop_duplicates"}])
        self.ws.projects.archive(self.alice, self.project)
        self.assertEqual(code_of(self.ws.cleaning.preview, self.alice, self.aid, [{"op": "drop_duplicates"}]), "PROJECT_NOT_ACTIVE")
        self.assertEqual(code_of(self.ws.cleaning.confirm, self.alice, p["id"], True), "PROJECT_NOT_ACTIVE")
        self.assertEqual(self.ws.cleaning.history(self.alice, self.aid)["items"], [])  # reading still works

    def test_data_values_never_reach_the_database_audit_or_activity_log(self):
        data = b"name,secret\nZebulon Quimby,hunter2\nZebulon Quimby,hunter2\nAda,pw\n"
        a = self.ok("s.csv", data)
        p = self.ws.cleaning.preview(self.alice, a["id"], [{"op": "drop_duplicates"}, {"op": "filter_rows", "column": "name", "filter": "ne", "value": "Nobody", "action": "keep"}])
        self.ws.cleaning.confirm(self.alice, p["id"], True)
        self.assertIn("Zebulon", json.dumps(p))  # the user sees values in the preview...
        dump = "\n".join(str(tuple(x)) for t in ("audit_events", "project_events", "extractions", "asset_versions", "assets")
                         for x in self.h.conn.execute(f"SELECT * FROM {t}").fetchall())
        for needle in ("Zebulon", "hunter2", "Nobody"):
            self.assertNotIn(needle, dump)  # ...but only the user's own storage holds them
        row = self.h.conn.execute("SELECT preview FROM transformations WHERE id = ?", (p["id"],)).fetchone()
        self.assertIsNone(row["preview"])

    def test_expired_previews_are_cleaned_up_including_their_files(self):
        p = self.preview([{"op": "drop_duplicates"}])
        key = f"p/{self.asset['project_id']}/a/{self.aid}/pv/{p['id']}.json"
        self.assertTrue(self.h.storage.exists(key))
        self.h.clock.advance(minutes=self.h.settings.preview_ttl_minutes + 1)
        done = self.ws.governance.run_retention()
        self.assertEqual(done["previews"], 1)
        self.assertFalse(self.h.storage.exists(key))
        self.assertEqual(self.ws.repo.transformation(self.alice.user_id, p["id"])["status"], "expired")

    def test_deleting_the_asset_removes_every_version_preview_and_history_row(self):
        self.apply([{"op": "drop_duplicates"}])
        self.preview([{"op": "trim_whitespace"}])
        self.ws.assets.delete(self.alice, self.aid)
        self.assertEqual(self.h.storage.list_prefix("p"), [])
        for t in ("asset_versions", "transformations", "extractions", "profiles"):
            self.assertEqual(self.h.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"], 0, t)


class SuggestionTests(CleaningBase):
    def test_suggestions_are_labelled_heuristic_and_apply_nothing(self):
        s = self.ws.cleaning.suggestions(self.alice, self.aid)
        self.assertEqual(s["basis"], "heuristic")
        self.assertTrue(s["needs_profile"])
        titles = " ".join(i["title"] for i in s["items"])
        self.assertIn("score", titles)
        self.ws.profiles.generate(self.alice, self.aid)
        s2 = self.ws.cleaning.suggestions(self.alice, self.aid)
        self.assertFalse(s2["needs_profile"])
        self.assertIn("Remove duplicate rows", [i["title"] for i in s2["items"]])
        self.assertTrue(any("Trim spaces" in i["title"] for i in s2["items"]))
        for item in s2["items"]:  # every suggestion with operations must be a valid plan for this table
            if item["operations"]:
                Plan(item["operations"], ["id", "name", "score", "joined", "city"])
        self.assertEqual(len(self.ws.repo.versions(self.aid)), 1)


if __name__ == "__main__":
    unittest.main()
