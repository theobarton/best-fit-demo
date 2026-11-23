"""Shared SerpAPI cache (search_cache table). Run: python3 -m unittest discover tests

Supabase and SerpAPI are both faked — these tests make no network calls and spend no credits."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import recommender as R  # noqa: E402


class FakeQuery:
    def __init__(self, table):
        self.table, self.filters, self.row = table, [], None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r[col] == val)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: r[col] >= val)
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=None):
        self.row = row
        return self

    def execute(self):
        if self.table.fail:
            raise ConnectionError("supabase unreachable")
        if self.row is not None:
            self.table.rows[(self.row["kind"], self.row["query"], self.row["params_hash"])] = dict(self.row)
            return mock.Mock(data=[self.row])
        return mock.Mock(data=[r for r in self.table.rows.values() if all(f(r) for f in self.filters)])


class FakeSupabase:
    def __init__(self):
        self.rows, self.fail = {}, False

    def table(self, name):
        assert name == "search_cache"
        return FakeQuery(self)


class FakeSerp:
    """Stands in for serpapi.GoogleSearch. Records every request (= one credit)."""
    calls = []
    response = None  # callable(params) -> dict

    def __init__(self, params):
        self.params = params
        FakeSerp.calls.append(params)

    def get_dict(self):
        return FakeSerp.response(self.params)


def shopping_ok(params):
    if params.get("start", 0) > 0:
        return {"shopping_results": []}  # results ran out after page 1
    return {"shopping_results": [{"product_id": f"p{i}", "title": f"Brooks Ghost 16 #{i}"} for i in range(3)]}


def organic_ok(params):
    return {"organic_results": [{"link": "https://www.rei.com/product/123/brooks-ghost-16",
                                 "title": "Brooks Ghost 16 Road-Running Shoes - Men's | REI Co-op"}]}


OUT_OF_CREDITS = {"error": "Your account has run out of searches."}


class SearchCacheTest(unittest.TestCase):
    def setUp(self):
        self.sb = FakeSupabase()
        FakeSerp.calls = []
        FakeSerp.response = shopping_ok
        patches = [
            mock.patch.object(db, "get_cache_client", lambda: self.sb),
            mock.patch.object(R, "GoogleSearch", FakeSerp),
            mock.patch.object(R, "SHARED_CACHE", True),
            mock.patch.object(R, "_log", lambda msg: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        R._cache_state.update(down_until=0.0, off=None)
        R._search_errors.clear()
        self.restart()

    def restart(self):
        """Simulate a Render restart / another process: the in-process lru layer is gone."""
        R._search_cached.cache_clear()
        R._sweep_cached.cache_clear()

    def test_repeated_shopping_search_makes_zero_serpapi_calls(self):
        first = R.shopping_search("Brooks Ghost 16", "key")
        self.assertEqual(len(first), 3)
        spent = len(FakeSerp.calls)
        self.assertGreater(spent, 0)
        self.assertEqual(len(self.sb.rows), 1)

        self.restart()
        # Different user, different casing/spacing — same normalised query.
        second = R.shopping_search("  brooks   GHOST 16 ", "key")
        self.assertEqual(second, first)
        self.assertEqual(len(FakeSerp.calls), spent, "repeat search must not call SerpAPI")

    def test_memory_layer_needs_no_supabase(self):
        R.shopping_search("brooks ghost 16", "key")
        self.sb.fail = True
        spent = len(FakeSerp.calls)
        self.assertEqual(len(R.shopping_search("brooks ghost 16", "key")), 3)
        self.assertEqual(len(FakeSerp.calls), spent)

    def test_repeated_sweep_makes_zero_serpapi_calls(self):
        FakeSerp.response = organic_ok
        first = R.retailer_sweep("Brooks Ghost 16 men's", ["rei.com", "zappos.com"], "key")
        self.assertEqual([p["source"] for p in first], ["REI"])
        self.assertEqual(len(FakeSerp.calls), 1)

        self.restart()
        second = R.retailer_sweep("brooks ghost 16 MEN'S", ["rei.com", "zappos.com"], "key")
        self.assertEqual(second, first)
        self.assertEqual(len(FakeSerp.calls), 1)

    def test_errors_are_not_cached(self):
        FakeSerp.response = lambda params: OUT_OF_CREDITS
        self.assertEqual(R.shopping_search("brooks ghost 16", "key"), [])
        self.assertEqual(R.retailer_sweep("brooks ghost 16", ["rei.com"], "key"), [])
        self.assertEqual(self.sb.rows, {}, "a SerpAPI error must never be written to the shared cache")

        # Credits renew: the very same searches go to SerpAPI and now return results.
        FakeSerp.calls = []
        FakeSerp.response = shopping_ok
        self.assertEqual(len(R.shopping_search("brooks ghost 16", "key")), 3)
        self.assertGreater(len(FakeSerp.calls), 0)

    def test_truncated_pagination_is_not_shared(self):
        FakeSerp.response = lambda params: shopping_ok(params) if params["start"] == 0 else OUT_OF_CREDITS
        with mock.patch.object(R, "SHOPPING_RESULTS", 150):
            self.assertEqual(len(R.shopping_search("brooks ghost 16", "key")), 3)
        self.assertEqual(self.sb.rows, {})

    def test_genuine_empty_result_is_cached(self):
        FakeSerp.response = lambda params: {"error": "Google hasn't returned any results for this query."}
        self.assertEqual(R.shopping_search("asdfqwer zxcv", "key"), [])
        self.restart()
        spent = len(FakeSerp.calls)
        self.assertEqual(R.shopping_search("asdfqwer zxcv", "key"), [])
        self.assertEqual(len(FakeSerp.calls), spent)

    def test_stale_rows_are_refetched(self):
        R.shopping_search("brooks ghost 16", "key")
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        for row in self.sb.rows.values():
            row["fetched_at"] = old
        self.restart()
        spent = len(FakeSerp.calls)
        R.shopping_search("brooks ghost 16", "key")
        self.assertGreater(len(FakeSerp.calls), spent, "a 25h-old shopping row is past the 24h TTL")
        self.assertGreater(next(iter(self.sb.rows.values()))["fetched_at"], old)

    def test_sweep_ttl_is_longer(self):
        FakeSerp.response = organic_ok
        R.retailer_sweep("brooks ghost 16", ["rei.com"], "key")
        for row in self.sb.rows.values():
            row["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        self.restart()
        R.retailer_sweep("brooks ghost 16", ["rei.com"], "key")
        self.assertEqual(len(FakeSerp.calls), 1, "a 48h-old sweep row is inside the 72h TTL")

    def test_supabase_down_fails_open(self):
        self.sb.fail = True
        self.assertEqual(len(R.shopping_search("brooks ghost 16", "key")), 3)
        self.assertGreater(R._cache_state["down_until"], 0)

    def test_unconfigured_supabase_turns_cache_off(self):
        def missing():
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        with mock.patch.object(db, "get_cache_client", missing):
            self.assertEqual(len(R.shopping_search("brooks ghost 16", "key")), 3)
        self.assertTrue(R._cache_state["off"])

    def test_page_stats_count_each_layer_across_threads(self):
        R.shopping_search("cached elsewhere", "key")  # seeds the shared cache, outside any page
        self.restart()
        R.shopping_search("warm in memory", "key")
        FakeSerp.calls = []

        stats = {}
        token = R._stats_var.set(stats)
        try:
            # parallel_search runs each query in its own worker thread.
            R.parallel_search(["warm in memory", "Cached  Elsewhere", "brand new query"], "key")
        finally:
            R._stats_var.reset(token)
        self.assertEqual(stats["lookups"], 3)
        self.assertEqual(stats["shared_hits"], 1)
        self.assertEqual(stats["fetched"], 2)       # 1 shared hit + 1 SerpAPI
        self.assertEqual(stats["serpapi_calls"], len(FakeSerp.calls))
        self.assertIn("1 memory cache, 1 shared cache, 1 SerpAPI", R.search_stats_line(stats))


if __name__ == "__main__":
    unittest.main()
