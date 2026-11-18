"""Unit tests for the pure world-intelligence modules (feeds, topics, entities, tone, statements, clustering, events, trends, influence). Every expectation is a known answer worked out by
hand or planted in generated data; nothing here contacts a network. The methods are heuristics: these tests show they do what they say on constructed input, not how accurate they are on a
real news feed."""
import random
import unittest
from datetime import datetime, timedelta, timezone

from app.modules.world import cluster, derive, entities, events, feeds, influence, tone, topics, trends

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def iso(day: float) -> str:
    return (T0 + timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ")


def art(i, title, lead, publisher, day=0.0):
    d = derive.derive(title, lead)
    d["lead"] = lead
    return {"id": i, "title": title, "lead": lead, "summary": lead, "publisher": publisher, "domain": publisher + ".example", "published": iso(day), "derived": d,
            "entities": [e["text"].lower() for e in d["entities"]["entities"]]}


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Wire &amp; Co</title><language>en-GB</language>
<item><title>First story</title><link>https://n.example/1?utm_source=x&amp;id=7#top</link><guid>g1</guid><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;.&lt;/p&gt;</description><pubDate>Tue, 20 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>No link here</title><description>x</description></item>
<item><title>Future</title><link>https://n.example/f</link><pubDate>Tue, 20 Oct 2030 08:00:00 GMT</pubDate></item>
<item><title>Bad scheme</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Wire</title>
<entry><title>An entry</title><link rel="self" href="https://n.example/self"/><link rel="alternate" href="https://n.example/a"/><id>urn:1</id><updated>2026-10-21T09:30:00Z</updated>
<summary>Short summary here.</summary><author><name>Pat Doe</name></author></entry></feed>"""


class Feeds(unittest.TestCase):
    NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def test_rss_is_read_with_markup_removed_and_tracking_parameters_dropped(self):
        f = feeds.parse_feed(RSS, now=self.NOW)
        self.assertEqual((f["format"], f["title"], f["language"]), ("rss", "Wire & Co", "en-gb"))
        first = f["items"][0]
        self.assertEqual(first["url"], "https://n.example/1?id=7")
        self.assertEqual(first["summary"], "Hello world.")
        self.assertEqual(first["published"], "2026-10-20T08:00:00Z")

    def test_unusable_items_are_counted_not_guessed_at(self):
        f = feeds.parse_feed(RSS, now=self.NOW)
        self.assertEqual([i["title"] for i in f["items"]], ["First story", "Future"])
        self.assertEqual(f["skipped"], 2)                                                        # no link, and a javascript: link
        self.assertEqual(f["future_dated"], 1)
        self.assertIsNone(f["items"][1]["published"])                                           # a date years ahead is dropped rather than believed

    def test_atom_prefers_the_alternate_link_and_reads_the_author(self):
        f = feeds.parse_feed(ATOM, now=self.NOW)
        self.assertEqual(f["format"], "atom")
        self.assertEqual((f["items"][0]["url"], f["items"][0]["author"], f["items"][0]["published"]), ("https://n.example/a", "Pat Doe", "2026-10-21T09:30:00Z"))

    def test_entity_definitions_and_doctypes_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "aaaa">]><rss version="2.0"><channel><item><title>&a;</title><link>https://x.example/</link></item></channel></rss>'
        with self.assertRaises(feeds.FeedError) as cm:
            feeds.parse_feed(bomb)
        self.assertEqual(cm.exception.code, "FEED_REFUSED")

    def test_things_that_are_not_feeds_are_rejected_with_a_plain_reason(self):
        for body, code in ((b"<html><body>hi</body></html>", "FEED_UNREADABLE"), (b"not xml at all", "FEED_UNREADABLE"), (b'<rss version="2.0"><channel></channel></rss>', "FEED_EMPTY")):
            with self.assertRaises(feeds.FeedError) as cm:
                feeds.parse_feed(body)
            self.assertEqual(cm.exception.code, code)

    def test_the_item_count_is_bounded(self):
        many = b'<rss version="2.0"><channel>' + b"".join(b"<item><title>T%d</title><link>https://n.example/%d</link></item>" % (i, i) for i in range(30)) + b"</channel></rss>"
        f = feeds.parse_feed(many, max_items=10)
        self.assertEqual((len(f["items"]), f["skipped"]), (10, 20))

    def test_urls_without_http_or_with_credentials_are_not_kept(self):
        for bad in ("ftp://x.example/a", "file:///etc/passwd", "https://user:pw@x.example/", "javascript:alert(1)", "", "   ", "http://" + "a" * 3000):
            self.assertIsNone(feeds.normalise_url(bad), bad)
        self.assertEqual(feeds.normalise_url("HTTPS://News.Example/a?b=1&utm_campaign=z&fbclid=q#f"), "https://news.example/a?b=1")

    def test_the_same_item_gets_the_same_fingerprint_and_a_different_item_does_not(self):
        a = feeds.item_ids("g1", "https://x/1", "T", None)
        self.assertEqual(a, feeds.item_ids("g1", "https://x/other", "Other", None))              # the feed's own id wins
        self.assertNotEqual(a, feeds.item_ids("g2", "https://x/1", "T", None))
        self.assertEqual(feeds.item_ids("", "", "Same", "d"), feeds.item_ids("", "", "Same", "d"))
        self.assertEqual(feeds.content_hash("Same  Title!", "A b"), feeds.content_hash("same title", "a  b"))


class Topics(unittest.TestCase):
    def test_a_topic_is_named_with_the_words_that_matched(self):
        out = topics.classify("Central bank raises interest rate as inflation climbs", "Markets fell after the announcement.")
        top = out["topics"][0]
        self.assertEqual(top["topic"], "economy")
        self.assertIn("inflation", top["matched"])
        self.assertIn("guide", out["note"])

    def test_text_that_matches_nothing_gets_no_topic_rather_than_a_guess(self):
        got = topics.classify("Quiet afternoon", "Nothing much happened.")["topics"]
        self.assertEqual([(t["topic"], t["strength"], t["matched"]) for t in got], [("general", "none", [])])

    def test_every_label_is_a_known_topic(self):
        for key in topics.LEXICON:
            self.assertIn(key, topics.TOPIC_LABELS)


class Entities(unittest.TestCase):
    def test_names_places_and_organisations_are_found_by_cue_and_capitalisation(self):
        out = entities.extract("Dr Rao said the Reserve Bank of India raised rates by 0.25 percent in Mumbai on Friday.")
        got = {(e["text"], e["kind"]) for e in out["entities"]}
        self.assertIn(("Rao", "person"), got)
        self.assertIn(("Reserve Bank of India", "organisation"), got)
        self.assertIn(("Mumbai", "place"), got)
        self.assertNotIn("Friday", [e["text"] for e in out["entities"]])                        # a weekday is not a name
        self.assertIn("not looked up", out["note"])

    def test_a_sentence_start_is_not_a_name(self):
        self.assertEqual(entities.extract("The river rose quickly. Heavy rain fell overnight.")["entities"], [])


class Tone(unittest.TestCase):
    def test_tone_follows_the_words_and_says_what_it_matched(self):
        neg = tone.score("Floods devastate village, dozens killed", "Rescue crews warn of further danger.")
        pos = tone.score("Team wins title in joyful celebration", "")
        flat = tone.score("Committee meets on Tuesday", "The agenda has four items.")
        self.assertEqual((neg["label"], pos["label"], flat["label"]), ("negative", "positive", "neutral"))
        self.assertIn("killed", neg["basis"]["negative"])
        self.assertIn("not whether it is true", neg["note"])

    def test_tone_is_labelled_as_wording_not_truth(self):
        self.assertIn("not whether it is true", tone.score("Anything", "at all")["note"])


class Statements(unittest.TestCase):
    def kind(self, title, lead=""):
        return derive.derive(title, lead)["statement"]["kind"]

    def test_the_kind_of_statement_follows_the_wording(self):
        self.assertEqual(self.kind("Minister says tax cut will boost jobs", "The cut would raise employment by 3 percent."), "prediction")
        self.assertEqual(self.kind("The plan could fail", "Reportedly it may not work."), "uncertain")
        self.assertEqual(self.kind("Opinion: the budget is a disgrace", "In my view it fails."), "opinion")
        self.assertEqual(self.kind("Bridge reopens after repairs", "The bridge reopened on Monday."), "stated")

    def test_numbers_and_negation_are_carried_for_comparison(self):
        st = derive.derive("Rate not changed", "The bank did not change the rate of 6.5 percent.")["statement"]
        self.assertTrue(st["negated"])
        self.assertIn("6.5", st["numbers"])


class Clustering(unittest.TestCase):
    def setUp(self):
        self.arts = [art("1", "River floods northern village", "The river rose by 2.4 metres and 1,200 residents were evacuated.", "a", 0),
                     art("2", "Floods hit northern village as river rises", "Officials evacuated 1,200 residents after the river rose 2.4 metres.", "b", 0.2),
                     art("3", "River flood: village evacuated", "The river rose 2.4 metres and 1,200 residents left the village.", "c", 0.3),
                     art("4", "Central bank holds rates", "The central bank left rates unchanged at 6.5 percent.", "a", 1),
                     art("5", "Tech firm unveils new phone", "The company unveiled a new smartphone with a faster chip.", "d", 2)]

    def test_articles_about_one_thing_group_and_unrelated_ones_do_not(self):
        res = cluster.cluster(self.arts)
        self.assertEqual(sorted(sorted(g) for g in res["groups"]), [["1", "2", "3"], ["4"], ["5"]])
        self.assertEqual(res["method"], "tfidf-agglomerative/1")
        self.assertEqual(res["parameters"]["threshold"], 0.3)

    def test_the_same_words_far_apart_in_time_are_not_one_event(self):
        a = art("1", "River floods northern village", "The river rose by 2.4 metres and 1,200 residents were evacuated.", "a", 0)
        b = art("2", "River floods northern village", "The river rose by 2.4 metres and 1,200 residents were evacuated.", "b", 30)
        self.assertEqual(sorted(sorted(g) for g in cluster.cluster([a, b])["groups"]), [["1"], ["2"]])

    def test_the_result_does_not_depend_on_input_order(self):
        base = sorted(sorted(g) for g in cluster.cluster(self.arts)["groups"])
        rng = random.Random(3)
        for _ in range(5):
            shuffled = list(self.arts)
            rng.shuffle(shuffled)
            self.assertEqual(sorted(sorted(g) for g in cluster.cluster(shuffled)["groups"]), base)

    def test_a_large_input_is_bounded_and_says_how_many_were_left_out(self):
        many = [art(str(i), f"Story number {i} about topic {i}", f"Unique words alpha{i} beta{i} gamma{i}.", "a", i % 3) for i in range(60)]
        res = cluster.cluster(many, max_articles=40)
        self.assertEqual(res["skipped"], 20)
        self.assertLessEqual(sum(len(g) for g in res["groups"]), 40)

    def test_no_articles_is_not_an_error(self):
        self.assertEqual(cluster.cluster([])["groups"], [])


class EventSummaries(unittest.TestCase):
    def test_three_outlets_with_matching_plain_facts_are_confirmed_which_means_agreement_not_verification(self):
        members = [art("1", "River floods northern village", "The river rose by 2.4 metres and 1,200 residents were evacuated.", "a", 0),
                   art("2", "Floods hit northern village as river rises", "Officials evacuated 1,200 residents after the river rose 2.4 metres.", "b", 0.2),
                   art("3", "River flood: village evacuated", "The river rose 2.4 metres and 1,200 residents left the village.", "c", 0.3)]
        s = events.summarise(members)
        self.assertEqual((s["status"], s["source_count"], s["article_count"]), ("confirmed", 3, 3))
        self.assertIn("no disagreement", s["basis"])
        self.assertIn("not proof", s["note"].lower())

    def test_two_outlets_are_only_reported(self):
        members = [art("1", "River floods village", "The river rose by 2.4 metres.", "a"), art("2", "River floods village", "The river rose 2.4 metres.", "b")]
        self.assertEqual(events.summarise(members)["status"], "reported")

    def test_different_numbers_from_different_outlets_make_it_disputed(self):
        members = [art("1", "River floods village", "The river rose by 2.4 metres.", "a"), art("2", "River floods village", "The river rose by 3.1 metres.", "b")]
        s = events.summarise(members)
        self.assertEqual(s["status"], "disputed")
        self.assertEqual(s["disagreements"][0]["reason"], "different numbers")

    def test_one_outlet_repeating_itself_is_still_one_source(self):
        members = [art("1", "River floods village", "The river rose by 2.4 metres.", "a", 0), art("2", "River floods village update", "The river rose by 2.4 metres.", "a", 1)]
        s = events.summarise(members)
        self.assertEqual((s["source_count"], s["status"]), (1, "unverified"))

    def test_hedged_or_forecast_wording_cannot_make_an_event_confirmed(self):
        members = [art(str(i), "Bank may raise rates", "Analysts expect the bank will raise rates by 0.25 percent.", p) for i, p in enumerate("abc")]
        self.assertNotEqual(events.summarise(members)["status"], "confirmed")

    def test_a_members_hash_ignores_order_but_not_membership(self):
        self.assertEqual(events.members_hash(["a", "b"]), events.members_hash(["b", "a"]))
        self.assertNotEqual(events.members_hash(["a", "b"]), events.members_hash(["a", "c"]))

    def test_the_timeline_is_in_publication_order_and_says_when_dates_are_missing(self):
        m = [art("2", "Second", "Later.", "b", 2), art("1", "First", "Earlier.", "a", 0)]
        m.append({**art("3", "Undated", "No date.", "c"), "published": None})
        tl = events.timeline(m)
        self.assertEqual([x["article_id"] for x in tl], ["1", "2", "3"])
        self.assertEqual([x["first_reported"] for x in tl], [True, False, False])
        self.assertIsNone(tl[2]["time"])                                                         # the undated one is listed last and shows no invented time


class Trends(unittest.TestCase):
    NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)

    def test_a_large_rise_in_coverage_is_called_rising_and_a_small_sample_is_not_called_anything(self):
        recent = [art(f"r{i}", "Floods hit district", "River flood evacuates villagers.", "a", 12 + i * 0.1) for i in range(8)]
        earlier = [art("p0", "Floods hit district", "River flood evacuates villagers.", "a", 5)]
        out = trends.trends(recent + earlier, window_days=7, now=self.NOW)
        row = next(t for t in out["topics"] if t["name"] == "disasters")
        self.assertEqual((row["recent"], row["previous"], row["label"]), (8, 1, "rising"))
        self.assertTrue(out["caveats"])
        few = trends.trends(recent[:2], window_days=7, now=self.NOW)
        self.assertTrue(all(t["label"] == "too few to say" for t in few["topics"]))

    def test_the_rate_rule_needs_both_enough_articles_and_a_large_difference(self):
        self.assertEqual(trends.rate_change(3, 1)["label"], "too few to say")
        self.assertEqual(trends.rate_change(6, 5)["label"], "steady")
        self.assertEqual(trends.rate_change(1, 9)["label"], "falling")
        self.assertEqual(trends.rate_change(9, 1)["label"], "rising")

    def test_articles_without_a_date_are_counted_apart_not_placed_in_a_week(self):
        a = {**art("1", "Floods", "River flood.", "a"), "published": None}
        out = trends.trends([a], window_days=7, now=self.NOW)
        self.assertEqual((out["undated_articles"], out["recent_articles"], out["previous_articles"]), (1, 0, 0))

    def test_daily_series_counts_and_fills_gaps_with_real_zeros(self):
        arts = [art("1", "Floods", "River flood.", "a", 0), art("2", "Floods again", "River flood again.", "a", 0.5), art("3", "More floods", "River flood more.", "a", 3)]
        s = trends.daily_series(arts, topic=None, entity=None, measure="count")
        self.assertEqual(s["2026-11-01"], 2.0)
        filled = trends.fill_days(s, "2026-11-01", "2026-11-04", 0.0)
        self.assertEqual([filled[d] for d in sorted(filled)], [2.0, 0.0, 0.0, 1.0])


class Influence(unittest.TestCase):
    DAYS = [(datetime(2026, 9, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(70)]

    def make(self, lag=2, noise=0.5, seed=1):
        rng = random.Random(seed)
        news = {d: float(rng.randint(0, 6)) for d in self.DAYS}
        series = {d: (news[self.DAYS[i - lag]] * 3 if i >= lag else 0.0) + rng.random() * noise for i, d in enumerate(self.DAYS)}
        return news, series

    def test_a_planted_delay_is_found_and_called_a_temporal_pattern_only(self):
        news, series = self.make()
        r = influence.analyse(news, series, max_lag=5)
        self.assertEqual(r["best"]["lag"], 2)
        self.assertGreater(r["best"]["rho"], 0.9)
        self.assertEqual(r["evidence"], "temporal")
        self.assertEqual(r["causal_status"], "not established")
        self.assertTrue(r["why_not_causal"])

    def test_independent_series_show_no_association(self):
        rng = random.Random(5)
        news = {d: float(rng.randint(0, 6)) for d in self.DAYS}
        series = {d: rng.random() for d in self.DAYS}
        r = influence.analyse(news, series, max_lag=5)
        self.assertEqual(r["evidence"], "none")
        self.assertEqual(r["causal_status"], "not established")

    def test_two_series_that_merely_trend_together_are_flagged_not_trusted(self):
        rng = random.Random(8)
        news = {d: i * 0.1 + rng.random() * 0.2 for i, d in enumerate(self.DAYS)}
        series = {d: i * 2.0 + rng.random() for i, d in enumerate(self.DAYS)}
        r = influence.analyse(news, series, max_lag=5)
        self.assertFalse(r["robust_to_trend"])
        self.assertNotEqual(r["evidence"], "temporal")

    def test_too_little_overlap_gives_no_result_instead_of_a_number(self):
        r = influence.analyse({d: 1.0 for d in self.DAYS[:10]}, {d: 2.0 for d in self.DAYS[:10]}, max_lag=3)
        self.assertEqual((r["evidence"], r["status"]), ("none", "insufficient_data"))
        self.assertIsNone(r.get("best"))
        self.assertTrue(r["warnings"])

    def test_the_same_input_always_gives_the_same_result_and_hash(self):
        news, series = self.make()
        a, b = influence.analyse(news, series, max_lag=5), influence.analyse(news, series, max_lag=5)
        self.assertEqual(influence.result_hash(a), influence.result_hash(b))
        a["best"]["rho"] = 0.0
        self.assertNotEqual(influence.result_hash(a), influence.result_hash(b))

    def test_adjusted_probabilities_never_fall_below_the_raw_ones(self):
        q = influence.bh_qvalues([0.001, 0.02, 0.5, 0.04])
        self.assertTrue(all(qi >= pi - 1e-12 for qi, pi in zip(q, [0.001, 0.02, 0.5, 0.04])))
        self.assertTrue(all(0 <= qi <= 1 for qi in q))

    def test_lag_range_is_bounded(self):
        self.assertEqual(influence.MAX_LAG, 14)


if __name__ == "__main__":
    unittest.main()
