"""The PredictAI Brain: Gemini setup, the key that never enters PredictAI, request caps with Google's quota day, consent for private text, fallback, failures, status.

Everything runs against GeminiFake (a local stand-in that follows Google's documented OpenAI-compatible endpoint). No request reaches Google, so none of this shows how the live service behaves."""
import json
import unittest

from app.core.errors import AppError
from app.modules.brain import BrainService
from app.modules.providers import AdapterFactory, BreakerBoard, ProviderService
from app.modules.providers.openai_compat import OpenAICompatAdapter
from app.modules.providers.http import JsonHttp

from brain_support import KEY, PUBLIC, BrainBase, GeminiFake, GeminiFactory
from support import Harness
from test_providers import OllamaFake
from web_support import FakeWeb


def code_of(call, *a, **kw):
    try:
        call(*a, **kw)
    except AppError as e:
        return e.code
    return None


class Setup(BrainBase):
    def test_only_the_owner_can_set_up_or_test_and_the_inputs_are_checked(self):
        for call in (self.brain.setup_gemini, self.brain.models, self.brain.test):
            kw = {"model": "m", "acknowledge_data_use": True} if call == self.brain.setup_gemini else {}
            self.assertEqual(code_of(call, self.alice, **kw), "FORBIDDEN", call.__name__)
        for bad in ("", "has space", None, "x" * 200):
            self.assertEqual(code_of(self.brain.setup_gemini, self.owner, model=bad, acknowledge_data_use=True), "VALIDATION_ERROR", repr(bad))
        for kw in ({"daily_request_limit": 0}, {"daily_request_limit": True}, {"requests_per_minute": -1}):
            self.assertEqual(code_of(self.brain.setup_gemini, self.owner, model="m", acknowledge_data_use=True, **kw), "VALIDATION_ERROR", kw)
        self.assertEqual(self.h.rows("SELECT * FROM ai_providers WHERE key = 'gemini'"), [])

    def test_setup_creates_an_external_free_provider_with_google_quota_rules_and_checks_the_key(self):
        out = self.setup_gemini()
        self.assertEqual(out["action"], "created")
        row = self.h.rows("SELECT * FROM ai_providers WHERE key = 'gemini'")[0]
        self.assertEqual((row["kind"], row["privacy_class"], row["paid"], row["enabled"], row["secret_env"]), ("openai_compatible", "external", 0, 1, "PREDICTAI_AI_KEY_GEMINI"))
        self.assertEqual(row["base_url"], "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual((row["daily_request_limit"], row["requests_per_minute"], row["quota_tz"], row["priority"]), (15, 4, "America/Los_Angeles", 20))
        self.assertTrue(out["probe"]["ok"], out["probe"])                      # "models/gemini-fake-flash" is found although the caller named "gemini-fake-flash"
        self.assertIn("ai.provider.created", [r["event_type"] for r in self.h.rows("SELECT event_type FROM audit_events")])
        self.assertIn("brain.gemini.setup", [r["event_type"] for r in self.h.rows("SELECT event_type FROM audit_events")])
        again = self.setup_gemini(model="gemini-fake-lite", daily_request_limit=7)
        self.assertEqual(again["action"], "updated")
        row = self.h.rows("SELECT * FROM ai_providers WHERE key = 'gemini'")[0]
        self.assertEqual((row["model"], row["daily_request_limit"], row["requests_per_minute"]), ("gemini-fake-lite", 7, 4))

    def test_a_model_the_key_cannot_see_is_reported_not_hidden(self):
        out = self.setup_gemini(model="gemini-does-not-exist")
        self.assertFalse(out["probe"]["ok"])
        self.assertTrue(any("gemini-does-not-exist" in step for step in out["next_steps"]))

    def test_without_the_key_the_provider_exists_but_stays_off_and_the_steps_say_why(self):
        self.rs.providers.factory = GeminiFactory(self.gem, env={})
        out = self.setup_gemini()
        row = self.h.rows("SELECT * FROM ai_providers WHERE key = 'gemini'")[0]
        self.assertEqual(row["enabled"], 0)
        self.assertTrue(any("PREDICTAI_AI_KEY_GEMINI" in s for s in out["next_steps"]))
        self.assertFalse(out["status"]["ready"])
        self.assertTrue(any("key is missing" in b for b in out["status"]["blocked_by"]))
        self.assertEqual(self.gem.requests, [])

    def test_gemini_needs_no_platform_switch_for_outside_hosts_and_no_tick_box(self):
        h2 = Harness(ai_allow_remote_host=False)
        self.addCleanup(h2.close)
        owner2, _ = h2.make_owner("o2@example.com")
        providers = ProviderService(h2.services().ctx, BreakerBoard(h2.clock), AdapterFactory(env={}, resolver=PUBLIC), resolver=PUBLIC)
        brain = BrainService(h2.services().ctx, providers, None)
        out = brain.setup_gemini(owner2, model="m")
        self.assertEqual(out["action"], "created")
        self.assertEqual(len(h2.rows("SELECT * FROM ai_providers WHERE key = 'gemini'")), 1)
        self.assertFalse(any("PREDICTAI_AI_ALLOW_REMOTE_HOST" in b for b in brain.status(owner2)["blocked_by"]))

    def test_the_key_never_appears_in_any_table_response_or_audit_entry(self):
        out = self.setup_gemini()
        self.allow_external()
        self.brain.test(self.owner)
        self.brain.models(self.owner)
        blob = json.dumps(out) + json.dumps(self.brain.status(self.owner)) + json.dumps(self.brain.status(self.alice))
        for table in ("ai_providers", "ai_usage", "audit_events", "research_settings"):
            blob += json.dumps([dict(r) for r in self.h.rows(f"SELECT * FROM {table}")], default=str)
        self.assertNotIn(KEY, blob)
        self.assertIn("PREDICTAI_AI_KEY_GEMINI", blob)                                   # the NAME of the variable is shown, never its value

    def test_the_model_list_strips_googles_models_prefix_and_needs_the_key(self):
        self.setup_gemini()
        self.assertEqual(self.brain.models(self.owner)["models"], ["gemini-fake-flash", "gemini-fake-lite"])
        self.rs.providers.factory = GeminiFactory(self.gem, env={"PREDICTAI_AI_KEY_GEMINI": "wrong"})
        bad = self.brain.models(self.owner)
        self.assertEqual((bad["ok"], bad["error"]), (False, "PROVIDER_AUTH"))


class WireFormat(BrainBase):
    def test_the_request_follows_the_documented_shape_and_carries_the_key_only_in_the_header(self):
        self.setup_gemini()
        self.allow_external()
        out = self.brain.ask(self.alice, self.pid, "synthesize", "Say hello.", [("S1", "Cells are small.")], max_tokens=77, temperature=0.3)
        self.assertTrue(out.ok)
        req, hdr = self.gem.requests[0], self.gem.headers[0]
        self.assertEqual((req["model"], req["stream"], req["max_tokens"], req["temperature"]), ("gemini-fake-flash", False, 77, 0.3))
        self.assertEqual([m["role"] for m in req["messages"]], ["system", "user"])
        self.assertIn('<source id="S1">', req["messages"][1]["content"])
        self.assertEqual(hdr["authorization"], f"Bearer {KEY}")
        self.assertNotIn(KEY, json.dumps(req))
        self.assertEqual(self.gem_web.hits[-1], "/v1beta/openai/chat/completions")

    def test_usage_is_recorded_as_counts_only(self):
        self.setup_gemini()
        self.allow_external()
        self.brain.ask(self.alice, self.pid, "synthesize", "Say hello.", [("S1", "Confidential course notes about mitochondria.")])
        rows = self.h.rows("SELECT * FROM ai_usage WHERE provider_key = 'gemini'")
        self.assertEqual((len(rows), rows[0]["outcome"], rows[0]["units_in"], rows[0]["units_out"], rows[0]["privacy_class"]), (1, "ok", 120, 60, "external"))
        self.assertNotIn("mitochondria", json.dumps([dict(r) for r in self.h.rows("SELECT * FROM ai_usage")]))


class Consent(BrainBase):
    def test_private_text_is_sent_by_default_and_not_after_the_person_switches_it_off(self):
        self.setup_gemini()
        out = self.brain.ask(self.alice, self.pid, "synthesize", "Write a question.", [("S1", "My private past paper text.")])
        self.assertTrue(out.ok)
        self.assertEqual((out.provider, self.gemini_requests()), ("gemini", 1))                  # on by default
        self.switch_off()
        out = self.brain.ask(self.alice, self.pid, "synthesize", "Write a question.", [("S1", "My private past paper text.")])
        self.assertFalse(out.ok)
        self.assertTrue(out.deterministic)
        self.assertEqual(self.gemini_requests(), 1)                                              # nothing more left the platform
        self.assertTrue(any("allow external AI" in r for r in out.reasons), out.reasons)

    def test_one_persons_switch_off_does_not_close_the_door_for_another(self):
        self.setup_gemini()
        self.switch_off(self.alice)
        self.assertFalse(self.brain.ask(self.alice, self.pid, "synthesize", "x", [("S1", "alice text")]).ok)
        self.assertEqual(self.gemini_requests(), 0)
        out = self.brain.ask(self.bob, self.pid, "synthesize", "x", [("S1", "bob text")])
        self.assertTrue(out.ok)                                                                  # Alice's choice affects only Alice
        self.assertEqual(self.gemini_requests(), 1)

    def test_public_text_such_as_a_search_query_can_go_without_the_switch(self):
        self.setup_gemini()
        out = self.brain.ask(self.alice, None, "summarize", "Reply ready", [], data_class="public")
        self.assertTrue(out.ok)
        self.assertEqual(self.gemini_requests(), 1)

    def test_a_project_can_narrow_but_the_person_setting_decides_first(self):
        self.setup_gemini()
        self.assertTrue(self.brain.external_allowed(self.alice, self.pid))
        self.svc.put_settings(self.alice, self.pid, {"external_ai": False})
        self.assertFalse(self.brain.external_allowed(self.alice, self.pid))                      # a project can narrow
        self.svc.put_settings(self.alice, self.pid, {"external_ai": True})
        self.switch_off()
        self.assertFalse(self.brain.external_allowed(self.alice, self.pid))                      # the person's own switch wins
        self.assertTrue(self.brain.external_allowed(self.bob, self.pid))


class Caps(BrainBase):
    def test_the_daily_request_cap_stops_requests_and_resets_at_midnight_pacific(self):
        self.setup_gemini(daily_request_limit=2)
        self.allow_external()
        ask = lambda: self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])  # noqa: E731
        self.assertTrue(ask().ok)
        self.assertTrue(ask().ok)
        third = ask()
        self.assertFalse(third.ok)
        self.assertEqual(self.gemini_requests(), 2)                                      # the third never left the platform
        self.assertTrue(any("daily request limit" in r for r in third.reasons), third.reasons)
        st = self.brain.status(self.owner)["usage"]
        self.assertEqual((st["requests_today"], st["daily_request_limit"], st["remaining_today"], st["reset_zone"]), (2, 2, 0, "America/Los_Angeles"))
        self.h.clock.advance(hours=21)                                                  # 12:00 UTC Jan 1 is 04:00 Pacific; +21 h is 01:00 Pacific on Jan 2: a new quota day
        self.assertTrue(ask().ok)
        self.assertEqual(self.gemini_requests(), 3)

    def test_the_quota_day_is_pacific_not_utc(self):
        self.setup_gemini(daily_request_limit=1)
        self.allow_external()
        ask = lambda: self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])  # noqa: E731
        self.assertTrue(ask().ok)
        self.h.clock.advance(hours=9)                                                   # 21:00 UTC Jan 1 = 13:00 Pacific Jan 1: UTC midnight has NOT passed, nor has Pacific
        self.assertFalse(ask().ok)
        self.h.clock.advance(hours=4)                                                   # 01:00 UTC Jan 2 = 17:00 Pacific Jan 1: UTC midnight passed, the Pacific day did not
        self.assertFalse(ask().ok, "a cap that reset at UTC midnight would let this through and Google would refuse it")
        self.assertEqual(self.gemini_requests(), 1)

    def test_the_per_minute_cap_waits_out_the_minute(self):
        self.setup_gemini(requests_per_minute=1, daily_request_limit=50)
        self.allow_external()
        ask = lambda: self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])  # noqa: E731
        self.assertTrue(ask().ok)
        second = ask()
        self.assertFalse(second.ok)
        self.assertTrue(any("last minute" in r for r in second.reasons), second.reasons)
        self.h.clock.advance(seconds=61)
        self.assertTrue(ask().ok)
        self.assertEqual(self.gemini_requests(), 2)

    def test_failed_requests_count_against_the_cap_so_a_failing_key_cannot_loop_forever(self):
        self.setup_gemini(daily_request_limit=3)
        self.allow_external()
        self.gem.answer = lambda req: (500, {"error": "boom"})
        for _ in range(4):
            self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])
            self.rs.providers.board.reset()
        self.assertLessEqual(self.gemini_requests(), 3)


class Fallback(BrainBase):
    def local_ollama(self):
        web = FakeWeb()
        web.__enter__()
        self.addCleanup(lambda: web.__exit__())
        OllamaFake(web, chat_text="local answer")
        self.rs.providers.create(self.owner, {"key": "local", "kind": "ollama", "label": "Local", "base_url": web.base, "model": "llama3.2", "priority": 50, "tasks": ["synthesize", "summarize", "plan"]})
        self.rs.providers.set_enabled(self.owner, "local", True)

    def test_when_gemini_is_capped_the_local_model_answers_and_the_reason_is_shown(self):
        self.local_ollama()
        self.setup_gemini(daily_request_limit=1)
        self.allow_external()
        first = self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])
        self.assertEqual(first.provider, "gemini")
        second = self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])
        self.assertEqual((second.ok, second.provider, second.text), (True, "local", "local answer"))
        self.assertTrue(any("gemini" in r and "daily request limit" in r for r in second.reasons), second.reasons)
        self.assertEqual(self.gemini_requests(), 1)

    def test_a_rate_limit_answer_from_gemini_falls_back_and_never_leaks_the_key(self):
        self.local_ollama()
        self.setup_gemini()
        self.allow_external()
        self.gem.answer = lambda req: (429, {"error": {"message": f"quota exceeded for key {KEY}"}})
        out = self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])
        self.assertEqual((out.ok, out.provider), (True, "local"))
        self.assertTrue(any("PROVIDER_RATE_LIMITED" in r for r in out.reasons), out.reasons)
        self.assertNotIn(KEY, json.dumps([dict(r) for r in self.h.rows("SELECT * FROM ai_usage")]) + json.dumps(out.reasons))

    def test_with_nothing_available_the_answer_says_so_and_the_caller_uses_its_rules(self):
        out = self.brain.ask(self.alice, self.pid, "synthesize", "q", [("S1", "t")])
        self.assertEqual((out.ok, out.deterministic, out.provider, out.text), (False, True, "builtin", None))
        self.assertTrue(out.reasons)

    def test_the_test_call_reports_who_answered(self):
        self.setup_gemini()
        t = self.brain.test(self.owner)
        self.assertEqual((t["ok"], t["answered_by"]), (True, "gemini"))
        self.assertIn("Gemini answered", t["note"])


class Status(BrainBase):
    def test_status_tells_everyone_what_is_ready_and_only_the_owner_the_wiring(self):
        before = self.brain.status(self.alice)
        self.assertFalse(before["ready"])
        self.assertFalse(before["configured"])
        self.setup_gemini()
        mine, theirs = self.brain.status(self.alice), self.brain.status(self.owner)
        self.assertTrue(mine["ready"])
        self.assertTrue(mine["for_you"]["external_ai"])
        self.switch_off()
        self.assertFalse(self.brain.status(self.alice)["for_you"]["external_ai"])
        self.assertIn("allow external AI", self.brain.status(self.alice)["for_you"]["note"])
        self.assertNotIn("secret_env", mine["provider"])
        self.assertEqual(theirs["provider"]["secret_env"], "PREDICTAI_AI_KEY_GEMINI")
        self.assertTrue(theirs["provider"]["data_leaves_platform"])
        self.assertIn("improve its products", mine["data_notice"])
        self.assertIn("billing", mine["cost_notice"])
        self.allow_external()
        self.assertTrue(self.brain.status(self.alice)["for_you"]["external_ai"])

    def test_the_status_lists_which_features_use_the_brain_and_which_do_not(self):
        feats = {f["feature"]: f for f in self.brain.status(self.alice)["features"]}
        self.assertTrue(feats["Exam prediction: predicted questions"]["uses_brain"])
        self.assertFalse(feats["Search by meaning (embeddings)"]["uses_brain"])


class Adapters(unittest.TestCase):
    def test_the_probe_accepts_both_spellings_of_a_model_name(self):
        with FakeWeb() as web:
            GeminiFake(web)
            a = OpenAICompatAdapter(web.base + "/v1beta/openai", "gemini-fake-flash", None, JsonHttp(allow_private=True), lambda: KEY)
            self.assertTrue(a.probe(5)["ok"])
            b = OpenAICompatAdapter(web.base + "/v1beta/openai", "models/gemini-fake-lite", None, JsonHttp(allow_private=True), lambda: KEY)
            self.assertTrue(b.probe(5)["ok"])
            c = OpenAICompatAdapter(web.base + "/v1beta/openai", "nope", None, JsonHttp(allow_private=True), lambda: KEY)
            self.assertFalse(c.probe(5)["ok"])


if __name__ == "__main__":
    unittest.main()


class ExternalDefaultOn(BrainBase):
    """External AI is ON for people who have not chosen. An explicit choice by the person wins, and affects only that person."""
    def test_on_for_someone_who_has_not_chosen(self):
        self.assertTrue(self.brain.consent.get(self.alice.user_id, None)["external_ai"])

    def test_a_person_who_switched_it_off_stays_off_and_others_stay_on(self):
        self.svc.put_settings(self.alice, None, {"external_ai": False})
        self.assertFalse(self.brain.consent.get(self.alice.user_id, None)["external_ai"])
        self.assertTrue(self.brain.consent.get(self.bob.user_id, None)["external_ai"])


class ExternalDefaultCanBeTurnedOffByTheOwner(BrainBase):
    settings_overrides = {**BrainBase.settings_overrides, "ai_external_default": False}

    def test_off_for_someone_who_has_not_chosen(self):
        self.assertFalse(self.brain.consent.get(self.alice.user_id, None)["external_ai"])
