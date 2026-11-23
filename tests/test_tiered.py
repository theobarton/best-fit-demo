"""Engine v5 (Good / Better / Best). Run: python3 -m unittest discover tests — catalog only, no network."""

import os
import re
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recommender as R  # noqa: E402

APP = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")).read()
ACTIVITIES = re.findall(r'^    "([A-Z][^"]*)": \[', APP, re.M)

NEUTRAL = {"sex": "Man", "shoe_size": "US 10", "width": "Standard (D)", "arch": "Normal / Medium Arch",
           "injuries": [], "weight": "175–200 lbs", "priorities": [], "waterproof": "Not important"}
FLAT_WIDE = {"sex": "Woman", "shoe_size": "US 8", "width": "Wide (2E)", "arch": "Flat / Low Arch",
             "injuries": ["Plantar Fasciitis"], "weight": "150–175 lbs", "priorities": ["Price / Value"],
             "waterproof": "Required for most activities"}


def tiers(u, act, answers=None):
    return R._v5_activity(u, act, answers or {}, "")["footwear"]


class TieredEngineTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(R, "_log", lambda msg: None)
        p.start()
        self.addCleanup(p.stop)

    def test_every_activity_gets_three_different_models_in_rising_price(self):
        self.assertGreaterEqual(len(ACTIVITIES), 40)
        for u in (NEUTRAL, FLAT_WIDE):
            for act in ACTIVITIES:
                with self.subTest(act=act, profile=u["arch"]):
                    picks = tiers(u, act)["products"]
                    self.assertEqual([x["tier"] for x in picks], ["good", "better", "best"])
                    self.assertEqual(len({x["title"] for x in picks}), 3)
                    prices = [x["price_usd"] for x in picks]
                    self.assertLess(prices[0], prices[1])
                    self.assertLess(prices[1], prices[2])
                    self.assertEqual(sum(x["recommended"] for x in picks), 1)

    def test_must_haves_are_enforced(self):
        picks = tiers(FLAT_WIDE, "Road Running")["products"]
        specs = {(c["brand"], c["model"]): c for c in R.load_catalog()}
        for x in picks:
            spec = specs[(x["brand"], x["model"])]
            self.assertIn(spec["stability"], ("stability", "motion control"), x["title"])
            self.assertTrue(spec["wide_available"], x["title"])
            self.assertTrue(all(ok for _, ok in x["checks"] if "cushion" not in _.lower()), x["checks"])

    def test_answer_tags_filter_models(self):
        cases = [
            ("Soccer", {"Primary playing surface": "Indoor / futsal (IC)"}, "IC"),
            ("Baseball / Softball", {"Cleat type": "Metal cleats"}, "metal"),
            ("Work / Standing All Day", {"Safety requirements": ["Steel toe / composite toe"]}, "safety-toe"),
            ("Mountaineering / Alpine", {"Crampon compatibility needed?": "C3 (full rigid)"}, "C3"),
            ("Ice Skating", {"Skating type": "Ice hockey (player)"}, "hockey"),
        ]
        specs = {(c["brand"], c["model"]): c for c in R.load_catalog()}
        for act, answers, tag in cases:
            with self.subTest(act=act):
                cat = tiers(NEUTRAL, act, answers)
                for x in cat["products"]:
                    if not cat.get("relaxed"):
                        self.assertIn(tag, specs[(x["brand"], x["model"])]["tags"], x["title"])

    def test_work_environment_steers_safety_boots(self):
        specs = {(c["brand"], c["model"]): c for c in R.load_catalog()}

        def tags(env):
            cat = tiers(NEUTRAL, "Work / Standing All Day", {"Work environment": env})
            return [set(specs[(x["brand"], x["model"])]["tags"]) for x in cat["products"]]
        self.assertFalse(any("safety-toe" in t for t in tags("Hospital / healthcare")))
        self.assertTrue(all("safety-toe" in t for t in tags("Construction / industrial")))
        self.assertTrue(all("slip-resistant" in t for t in tags("Restaurant / kitchen")))

    def test_waterproof_answer_is_ignored_where_it_cannot_apply(self):
        cat = tiers(FLAT_WIDE, "Tennis")
        self.assertNotIn("waterproof", cat.get("relaxed") or [])
        self.assertFalse(any("Waterproof" in t for x in cat["products"] for t, _ in x["checks"]))

    def test_relaxed_constraint_is_disclosed(self):
        cat = tiers(FLAT_WIDE, "Trail Running")  # no stability trail shoes in the catalog
        self.assertEqual(cat["relaxed"], ["stability support"])
        self.assertIn("stability support", cat["note"])
        self.assertTrue(any(not ok for x in cat["products"] for _, ok in x["checks"]))

    def test_price_priority_recommends_good_tier(self):
        self.assertTrue(tiers(FLAT_WIDE, "Road Running")["products"][0]["recommended"])
        self.assertTrue(tiers(NEUTRAL, "Road Running")["products"][1]["recommended"])

    def test_v5_needs_no_api_keys_and_is_fast(self):
        t0 = time.perf_counter()
        res = R.recommend("v5", NEUTRAL, {a: {} for a in ACTIVITIES}, "", "")
        elapsed = time.perf_counter() - t0
        self.assertTrue(all(r["categories"]["footwear"]["products"] for r in res.values()))
        self.assertLess(elapsed, 2.0, f"{len(ACTIVITIES)} activities took {elapsed:.2f}s")

    def test_gear_follows_injuries(self):
        gear = R._v5_activity(FLAT_WIDE, "Road Running", {}, "")["gear_1"]["products"]
        self.assertIn("Powerstep", gear[0]["title"])

    def test_check_lines_do_not_repeat_a_topic(self):
        for u in (NEUTRAL, FLAT_WIDE):
            for act in ACTIVITIES:
                for x in tiers(u, act)["products"]:
                    topics = [R._line_topic(t) for t, _ in x["checks"]]
                    topics = [t for t in topics if t]
                    with self.subTest(act=act, pick=x["title"]):
                        self.assertEqual(len(topics), len(set(topics)), x["checks"])

    def test_cards_never_show_na_and_always_say_why(self):
        for u in (NEUTRAL, FLAT_WIDE):
            for act in ACTIVITIES:
                for x in tiers(u, act)["products"]:
                    with self.subTest(act=act, pick=x["title"]):
                        self.assertTrue(x["checks"])
                        self.assertFalse(any("n/a" in t.lower() for t, _ in x["checks"]), x["checks"])

    def test_no_two_tiers_at_the_same_price(self):
        ans = {"Warmth / insulation": "Expedition warmth"}
        picks = tiers(NEUTRAL, "Mountaineering / Alpine", ans)["products"]
        prices = [x["price_usd"] for x in picks]
        self.assertEqual(len(prices), len(set(prices)), prices)


if __name__ == "__main__":
    unittest.main()
