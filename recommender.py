"""
FITFXR recommendation engines.

  v0  Current       GPT writes one Google Shopping query per category; results sorted by price.
  v1  Rerank        Wider candidate pool from several queries, then GPT scores every *real*
                    product against the profile and rejects misfits (wrong sex, wrong type,
                    kids sizes, missing must-haves). Each pick gets a fit score + reason.
  v2  Spec Match    The profile is turned into hard requirements (stability, cushion, drop,
                    width, waterproof, ankle support...). GPT proposes specific models *with
                    specs*, a deterministic scorer ranks them, and we search each top model by
                    exact name and keep only listings whose title actually matches.
  v3  Catalog       Same scorer, but candidates come from our own curated catalog.json
                    (consistent, auditable, partner-ready). Activities with thin catalog
                    coverage fall back to v2.
  v4  Spec + Verify v2's model picks, then an LLM judge checks every real listing (exact model/
                    version in the title) against the must-haves and drops anything that
                    fails — v2's spec claims are never trusted on their own. DEFAULT.

Every engine returns the shape app.py renders:
    {activity: {"engine": str, "categories": {cat_key: {"label", "query", "products": [...]}}}}
Products may carry "fit_score" (0–100) and "why"; render_product_tiers uses them when present.
"""

import contextvars
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from functools import lru_cache
from urllib.parse import urlparse

from openai import OpenAI
from serpapi import GoogleSearch

LLM_MODEL = os.getenv("FITFXR_LLM_MODEL", "gpt-4o")

ENGINES = {
    "v0": "v0 · Current (query → price sort)",
    "v1": "v1 · Rerank (GPT scores real listings)",
    "v2": "v2 · Spec Match (requirements → models → verified listings)",
    "v3": "v3 · Catalog (curated specs + live prices)",
    "v4": "v4 · Spec Match + Verify (live search + LLM judge)",
    "v5": "v5 · Tiered Good/Better/Best (instant, recommended)",
}

CATEGORY_LABELS = {
    "footwear": "👟 Footwear",
    "gear_1":   "🎒 Gear & Accessories",
    "gear_2":   "⚡ Also Recommended",
}

# Generic search terms used when an AI query returns nothing.
ACTIVITY_FALLBACK_TERMS = {
    "Road Running": "road running shoes",
    "Trail Running": "trail running shoes",
    "Ultramarathon": "ultra marathon trail running shoes",
    "Cross Country Running": "cross country running shoes",
    "Race Walking": "race walking shoes",
    "Treadmill Running": "treadmill running shoes",
    "Walking / Casual": "walking shoes",
    "Day Hiking": "day hiking boots",
    "Backpacking": "backpacking boots",
    "Mountaineering / Alpine": "mountaineering boots",
    "Rock Climbing": "rock climbing shoes",
    "Water Sports / Kayaking": "water shoes kayaking",
    "Hunting / Fishing": "hunting boots",
    "Cycling / Mountain Biking": "mountain bike shoes SPD",
    "Basketball": "basketball shoes",
    "Tennis": "tennis shoes",
    "Pickleball": "pickleball shoes",
    "Volleyball": "volleyball shoes",
    "Squash / Racquetball": "squash court shoes",
    "Badminton": "badminton shoes",
    "Soccer": "soccer cleats",
    "Football (Cleats)": "football cleats",
    "Baseball / Softball": "baseball cleats",
    "Lacrosse": "lacrosse cleats",
    "Rugby": "rugby boots",
    "Ultimate Frisbee": "cleats ultimate frisbee",
    "Field Hockey": "field hockey turf shoes",
    "Weightlifting / CrossFit": "weightlifting shoes",
    "HIIT Training": "cross training shoes",
    "Yoga / Pilates": "yoga grip socks",
    "Dance / Zumba": "dance aerobics shoes",
    "Spin / Indoor Cycling": "indoor cycling shoes SPD",
    "Rowing / Erg": "rowing training shoes",
    "Downhill Skiing": "downhill ski boots",
    "Cross-Country Skiing": "cross country ski boots",
    "Snowboarding": "snowboard boots",
    "Ice Skating": "ice skates",
    "Snowshoeing": "winter hiking boots waterproof",
    "Skateboarding": "skate shoes vulcanized",
    "Golf": "golf shoes spikeless",
    "Work / Standing All Day": "work shoes slip resistant",
    "Casual / Streetwear": "casual sneakers",
    "Travel / Light Hiking": "travel walking shoes",
    "Cowboy / Western": "western boots",
}


# ── Fit profile ───────────────────────────────────────────────────────────────

_HEAVY_WEIGHTS = ("200–225", "225–250", "250–275", "275–300", "Over 300")
_CUSHION_INJURIES = {
    "Plantar Fasciitis", "Heel Spurs", "Knee Pain (Runner's Knee / PFPS)", "IT Band Syndrome",
    "Shin Splints", "Stress Fractures", "Diabetic Foot", "Neuropathy", "Hip Pain",
    "Lower Back Pain", "Post-Surgery Recovery",
}


def _us_size(s):
    m = re.search(r"US\s*(\d{1,2}(?:\.5)?)", s or "")
    return float(m.group(1)) if m else None


def build_fit_profile(u, activity, answers):
    """Turn raw questionnaire answers into explicit, checkable fit requirements."""
    injuries = set(u.get("injuries") or []) - {"None"}
    arch = u.get("arch")
    weight = u.get("weight") or ""
    p = {
        "activity": activity,
        "gender": {"Man": "men's", "Woman": "women's"}.get(u.get("sex"), ""),
        "us_size": _us_size(u.get("shoe_size")),
        "stability": "any",          # required | avoid | any
        "cushion": "any",            # max | moderate | minimal | any
        "min_drop_mm": None,
        "max_drop_mm": None,
        "wide": u.get("width") in ("Wide (2E)", "Extra Wide (4E+)"),
        "extra_wide": u.get("width") == "Extra Wide (4E+)",
        "narrow": u.get("width") == "Narrow",
        "wide_toebox": bool(injuries & {"Bunions", "Morton's Neuroma"}),
        "ankle_support": bool(injuries & {"Ankle Instability / Chronic Sprains", "Weak Ankles (general)"}),
        "waterproof": {"Required for most activities": "required",
                       "Nice to have": "preferred"}.get(u.get("waterproof"), "any"),
        "heavy": any(w in weight for w in _HEAVY_WEIGHTS),
        "vegan": False,
        "wants_plate": False,
        "goal": "",
        "current_shoe": "",
        "priorities": list(u.get("priorities") or []),
        "injuries": sorted(injuries),
        "answers": answers,
        "competitive": False,
        "tag_rules": [],
    }

    if injuries & {"Overpronation / Collapsed Arches", "Flat Feet"} or arch == "Flat / Low Arch":
        p["stability"] = "required"
    elif injuries & {"High Arches", "Supination / Underpronation"} or arch == "High Arch":
        p["stability"] = "avoid"

    if injuries & _CUSHION_INJURIES or p["heavy"]:
        p["cushion"] = "max"
    # Achilles load drops with a *higher* heel-to-toe offset.
    if "Achilles Tendonitis" in injuries:
        p["min_drop_mm"] = 8

    for label, val in answers.items():
        l, v = label.lower(), str(val)
        if "cushion" in l:
            if v.startswith("Max"):
                p["cushion"] = "max"
            elif v.startswith("Moderate"):
                p["cushion"] = "moderate"
            elif v.startswith("Minimal"):
                p["cushion"] = "minimal"
            elif v.startswith("Carbon"):
                p["wants_plate"] = True
        elif "drop" in l:
            if v.startswith("Zero"):
                p["max_drop_mm"] = 0
            elif v.startswith("Low"):
                p["max_drop_mm"] = 7
            elif v.startswith("Medium"):
                p["min_drop_mm"], p["max_drop_mm"] = max(p["min_drop_mm"] or 0, 8), 11
            elif v.startswith("High"):
                p["min_drop_mm"] = 12
        elif "vegan" in l and v.startswith("Yes"):
            p["vegan"] = True
        elif "goal" in l:
            p["goal"] = v
            if "Racing" in v:
                p["wants_plate"] = True
        elif "current" in l and any(w in l for w in ("shoe", "boot", "cleat", "skate")):
            p["current_shoe"] = v
        elif l == "waterproofing":
            if v.startswith("Required"):
                p["waterproof"] = "required"
            elif v.startswith("Preferred") and p["waterproof"] == "any":
                p["waterproof"] = "preferred"
        if any(w in v for w in ("Competitive", "Elite", "Collegiate", "Professional", "Racing")):
            p["competitive"] = True
    p["tag_rules"] = answer_tag_rules(answers)
    return p


# Survey answers that map to catalog tags (catalog.json "tags"). (match, tags any-of, level, phrase).
# match: substring of the answer, or "=exact". "must" rules exclude models without the tag; "should"
# rules only move the score. A rule is ignored when no model for the activity carries any of its tags.
_ANSWER_TAGS = [
    ("(FG)", ["FG"], "must", "firm-ground (FG) version"),
    ("(AG)", ["AG"], "must", "artificial-grass (AG) version"),
    ("(TF)", ["TF"], "must", "turf (TF) version"),
    ("(IC)", ["IC"], "must", "indoor (IC) version"),
    ("Metal cleats", ["metal"], "must", "metal-cleat version"),
    ("Molded rubber cleats", ["molded"], "must", "molded-cleat version"),
    ("Turf shoes", ["turf"], "must", "turf version"),
    ("Spiked cross country shoe", ["spiked"], "must", "XC spikes"),
    ("Spikeless / trail shoe", ["spikeless"], "should", "spikeless"),
    ("Spiked / cleated", ["spiked"], "must", "spiked"),
    ("Spikeless (versatile", ["spikeless"], "must", "spikeless"),
    ("SPD (", ["2-bolt"], "must", "SPD (2-bolt) cleat compatible"),
    ("SPD-SL", ["3-bolt"], "must", "3-bolt (SPD-SL / Look) compatible"),
    ("Look Keo", ["3-bolt"], "must", "3-bolt (Look Keo) compatible"),
    ("Look Delta", ["3-bolt"], "must", "Look Delta (Peloton) compatible"),
    ("Flat pedals", ["flat-pedal"], "must", "flat-pedal grip"),
    ("Neutral (comfortable", ["flat-downturn"], "should", "flat, all-day profile"),
    ("Moderate downturn", ["moderate-downturn"], "should", "moderate downturn"),
    ("Aggressive downturn", ["aggressive-downturn"], "should", "aggressive downturn"),
    ("Recreational / public", ["rec"], "must", "recreational skate"),
    ("Figure skating", ["figure"], "must", "figure skate"),
    ("Ice hockey", ["hockey"], "must", "hockey skate"),
    ("Soft (65", ["soft-flex"], "should", "soft flex"),
    ("Medium (85", ["med-flex"], "should", "medium flex"),
    ("Stiff (105", ["stiff-flex"], "should", "stiff flex"),
    ("Race stiff", ["race-flex"], "should", "race-stiff flex"),
    ("Soft — park", ["soft-flex"], "should", "soft flex"),
    ("Medium — all-mountain", ["med-flex"], "should", "medium flex"),
    ("Stiff — freeride", ["stiff-flex"], "should", "stiff flex"),
    ("=Classic", ["classic"], "must", "classic-technique boot"),
    ("=Skate skiing", ["skate"], "must", "skate-technique boot"),
    ("C1 (strap-on)", ["C1", "C2", "C3"], "must", "crampon compatible"),
    ("C2 (semi-rigid)", ["C2", "C3"], "must", "semi-rigid (C2) crampon compatible"),
    ("C3 (full rigid)", ["C3"], "must", "rigid (C3) crampon compatible"),
    ("Hospital / healthcare", ["safety-toe"], "avoid", "light shoe (no safety boot)"),
    ("Office / light duty", ["safety-toe"], "avoid", "light shoe (no safety boot)"),
    ("Restaurant / kitchen", ["safety-toe"], "avoid", "light shoe (no safety boot)"),
    ("Retail / sales floor", ["safety-toe"], "avoid", "light shoe (no safety boot)"),
    ("Restaurant / kitchen", ["slip-resistant"], "must", "slip-resistant sole"),
    ("Construction / industrial", ["safety-toe"], "must", "safety toe"),
    ("Warehouse", ["safety-toe"], "should", "safety toe"),
    ("Slip-resistant sole", ["slip-resistant"], "must", "slip-resistant sole"),
    ("Steel toe / composite toe", ["safety-toe"], "must", "safety toe"),
    ("Electrical hazard", ["EH"], "must", "EH-rated"),
    ("Yes — rocky terrain", ["rock-plate"], "should", "rock plate"),
    ("Raised heel", ["lifter"], "should", "raised lifting heel"),
    ("Light insulation", ["insulated"], "should", "insulated"),
    ("Moderate insulation", ["insulated"], "should", "insulated"),
    ("Expedition warmth", ["insulated"], "must", "insulated"),
    ("Low cut", ["low"], "should", "low cut"),
    ("Low-top", ["low"], "should", "low-top"),
    ("Mid cut", ["mid", "high"], "should", "mid-cut ankle support"),
    ("Mid-top", ["mid", "high"], "should", "mid-top"),
    ("High cut", ["high"], "should", "high-cut ankle support"),
    ("High-top", ["high"], "should", "high-top ankle support"),
]


def answer_tag_rules(answers):
    rules, seen = [], set()
    for val in answers.values():
        for v in (val if isinstance(val, (list, tuple)) else [val]):
            v = str(v)
            for match, tags, level, desc in _ANSWER_TAGS:
                hit = v == match[1:] if match.startswith("=") else match in v
                if hit and (desc, level) not in seen:
                    seen.add((desc, level))
                    rules.append({"tags": tags, "level": level, "desc": desc})
    return rules


def must_haves(p):
    """The hard footwear requirements from the body/foot profile, as short checkable phrases."""
    m = []
    if p["gender"]:
        m.append(f"{p['gender']} sizing")
    if p["stability"] == "required":
        m.append("stability or motion-control support (not a neutral shoe)")
    elif p["stability"] == "avoid":
        m.append("neutral / cushioned (not motion control)")
    if p["wide"]:
        m.append("offered in " + ("4E / extra-wide" if p["extra_wide"] else "2E / wide"))
    if p["wide_toebox"]:
        m.append("wide toe box")
    if p["ankle_support"]:
        m.append("ankle support (mid/high cut or very stable heel)")
    if p["waterproof"] == "required":
        m.append("waterproof / Gore-Tex version")
    if p["vegan"]:
        m.append("vegan materials")
    return m


# Activity names that models misread. "Football (Cleats)" was getting soccer boots.
ACTIVITY_CLARIFY = {
    "Football (Cleats)": "American football (gridiron) — American football cleats, NOT soccer boots",
    "Soccer": "soccer / association football — soccer boots, NOT American football cleats",
    "Rugby": "rugby union/league — rugby boots",
    "Field Hockey": "field hockey (turf/grass) — not ice hockey",
    "Squash / Racquetball": "squash or racquetball — non-marking indoor court shoes",
    "Cross Country Running": "cross country running (XC races) — XC spikes or flats",
    "Cross-Country Skiing": "cross-country (Nordic) skiing — XC ski boots",
}


def profile_brief(u, p):
    """Compact natural-language version of the profile for LLM prompts."""
    hard, soft = [], []
    if p["gender"]:
        hard.append(f"{p['gender']} sizing")
    if p["stability"] == "required":
        hard.append("stability / motion-control support (NO neutral shoes)")
    elif p["stability"] == "avoid":
        hard.append("neutral, cushioned shoes only (NO motion control)")
    if p["wide"]:
        hard.append("must be offered in " + ("4E/extra-wide" if p["extra_wide"] else "2E/wide"))
    if p["wide_toebox"]:
        hard.append("wide anatomical toe box")
    if p["ankle_support"]:
        hard.append("ankle support (mid/high cut or very stable heel)")
    if p["waterproof"] == "required":
        hard.append("waterproof / Gore-Tex version")
    if p["vegan"]:
        hard.append("vegan materials")
    if p["cushion"] != "any":
        soft.append(f"{p['cushion']} cushion")
    if p["min_drop_mm"] is not None or p["max_drop_mm"] is not None:
        soft.append(f"heel drop {p['min_drop_mm'] if p['min_drop_mm'] is not None else 0}–"
                    f"{p['max_drop_mm'] if p['max_drop_mm'] is not None else 14}mm")
    if p["wants_plate"]:
        soft.append("plated / race-day responsiveness")
    if p["waterproof"] == "preferred":
        soft.append("waterproof nice to have")
    if p["heavy"]:
        soft.append("heavier runner/athlete — durable, well-cushioned platform")
    return "\n".join(filter(None, [
        f"Activity: {ACTIVITY_CLARIFY.get(p['activity'], p['activity'])}",
        f"Person: age {u.get('age')}, {u.get('sex')}, {u.get('weight')}, {u.get('height')}; "
        f"shoe {u.get('shoe_size')}, width {u.get('width')}, arch {u.get('arch')}",
        f"Injuries/concerns: {', '.join(p['injuries']) or 'none'}",
        f"Activity answers: " + ("; ".join(f"{k}: {v}" for k, v in p["answers"].items()) or "none"),
        f"Priorities: {', '.join(p['priorities']) or 'none'}",
        "MUST HAVE: " + ("; ".join(hard) if hard else "nothing specific"),
        "SHOULD HAVE: " + ("; ".join(soft) if soft else "nothing specific"),
    ]))


# ── Deterministic spec scorer (v2 + v3) ───────────────────────────────────────

def score_spec(spec, p, reqs=()):
    """Score a product spec against a fit profile. Returns (score 0–100, reasons) or
    (None, [why]) when a hard requirement is violated.

    `reqs` are activity-specific requirements derived from the survey answers
    (e.g. crampon_compatible, cleat_type, court_surface, safety_toe); `spec["extras"]`
    holds the candidate's value for each. Unknown values are neutral."""
    s, reasons = 60, []
    extras = spec.get("extras") or {}
    for r in reqs:
        val = extras.get(r.get("key"))
        if val is None or r.get("want") is None:
            continue
        ok = str(val).strip().lower() == str(r["want"]).strip().lower()
        if r.get("level") == "must" and not ok:
            return None, [f"not {r.get('desc') or r['key']}"]
        if ok:
            s += 10 if r.get("level") == "must" else 7
            reasons.append(r.get("desc") or r["key"])
        else:
            s -= 6
    stab = (spec.get("stability") or "n/a").lower()
    supportive = stab in ("stability", "motion control")
    cush = (spec.get("cushion") or "").lower()
    drop = spec.get("drop_mm")

    if p["stability"] == "required":
        if stab == "neutral":
            return None, ["neutral shoe, you need stability"]
        if supportive:
            s += 15
            reasons.append(f"{stab} support for your arches")
    elif p["stability"] == "avoid":
        if stab == "motion control":
            return None, ["motion control fights a high arch"]
        if stab == "neutral":
            s += 8
            reasons.append("neutral platform for your arch")

    if p["wide"] and spec.get("wide_available") is False:
        return None, ["not made in wide"]
    if p["waterproof"] == "required" and not spec.get("waterproof_option"):
        return None, ["no waterproof version"]
    if p["vegan"] and spec.get("vegan") is False:
        return None, ["not vegan"]

    if p["cushion"] != "any" and cush:
        if cush == p["cushion"]:
            s += 12
            reasons.append(f"{cush} cushion")
        elif {p["cushion"], cush} == {"max", "minimal"}:
            s -= 20
        else:
            s -= 4
    if isinstance(drop, (int, float)):
        lo, hi = p["min_drop_mm"], p["max_drop_mm"]
        if (lo is not None and drop < lo) or (hi is not None and drop > hi):
            s -= 15
        elif lo is not None or hi is not None:
            s += 8
            reasons.append(f"{int(drop)}mm drop")
    if p["wide"] and spec.get("wide_available"):
        s += 6
        reasons.append("comes in wide")
    if p["wide_toebox"]:
        if spec.get("wide_toebox"):
            s += 10
            reasons.append("roomy toe box")
        else:
            s -= 8
    if p["ankle_support"]:
        if spec.get("ankle_support"):
            s += 10
            reasons.append("ankle support")
        else:
            s -= 10
    if p["waterproof"] != "any" and spec.get("waterproof_option"):
        s += 4
        if p["waterproof"] == "required":
            reasons.append("waterproof version")
    if p["heavy"]:
        if cush == "max" or spec.get("durable"):
            s += 5
        elif cush == "minimal":
            s -= 8
    if p["wants_plate"]:
        if spec.get("plate") in ("carbon", "nylon", "plate"):
            s += 12
            reasons.append(f"{spec['plate']} plate for speed")
        else:
            s -= 6

    goal = p["goal"]
    weight_cls = (spec.get("weight") or "").lower()
    if goal and ("recovery" in goal.lower() or "Long distance" in goal) and cush == "max":
        s += 5
    if goal and ("Speed" in goal or "Racing" in goal) and weight_cls == "light":
        s += 6
        reasons.append("light and fast")

    price = spec.get("price_usd") or spec.get("typical_price_usd")
    for pr in p["priorities"]:
        if pr == "Lightweight":
            s += 6 if weight_cls == "light" else -4 if weight_cls == "heavy" else 0
        elif pr == "Support / Stability" and (supportive or spec.get("ankle_support")):
            s += 6
        elif pr == "Comfort" and cush == "max":
            s += 5
        elif pr == "Durability" and spec.get("durable"):
            s += 5
        elif pr == "Price / Value" and price:
            s += 6 if price <= 130 else -6 if price >= 180 else 0
            if price <= 130:
                reasons.append("great value")
        elif pr == "Sustainability" and spec.get("vegan"):
            s += 3

    cur = p["current_shoe"].lower()
    if cur and (spec.get("model") or "").lower() in cur:
        s += 6
        reasons.insert(0, "the current version of the shoe you already like")

    return max(0, s), reasons[:3]  # unclamped so strong fits still rank apart; display clamps


# ── Shared helpers ────────────────────────────────────────────────────────────

SERP_TIMEOUT_S = 20  # the SerpAPI client defaults to 60,000 s — one stuck call would hang the page


class SearchUnavailable(Exception):
    """SerpAPI refused the search (out of credits, bad key, rate limit) — not the same as 'no results'."""


# Errors from the current recommend() call, so the UI can say "search is down" instead of
# blaming the user's preferences. Reset per call; appended from worker threads (list.append is atomic).
_search_errors = []


def _serp(params):
    _count("serpapi_calls")
    client = GoogleSearch(params)
    client.timeout = SERP_TIMEOUT_S
    d = client.get_dict()
    err = d.get("error")
    if err:
        if "hasn't returned any results" in err or "no results" in err.lower():
            return d  # a genuine empty result
        # Raise (never return) so lru_cache doesn't cache the failure as "zero results".
        first = not _search_errors
        _search_errors.append(err)
        if first and "run out of searches" in err.lower():
            _log("SERPAPI OUT OF SEARCHES — every product search will fail until credits renew or the plan is upgraded")
        raise SearchUnavailable(err)
    return d


# ── Shared search cache (Supabase search_cache table, see schema.sql) ──────────
# Layer 1 is the in-process lru_cache on _search_cached / _sweep_cached: free, but lost on every
# Render restart and private to one process. Layer 2 is this table, shared by every user and
# restart. Only complete, successful SerpAPI responses are written — SearchUnavailable and partial
# pages never are. The cache fails open: any Supabase problem just means a normal SerpAPI call.
CACHE_TTL_S = {
    "shopping": float(os.getenv("FITFXR_CACHE_TTL_SHOPPING_H", "24")) * 3600,
    "sweep":    float(os.getenv("FITFXR_CACHE_TTL_SWEEP_H", "72")) * 3600,
}
SHARED_CACHE = os.getenv("FITFXR_SHARED_CACHE", "1") != "0"
_CACHE_VERSION = 1           # bump to invalidate every row after a change to what's stored
_CACHE_BACKOFF_S = 60        # after a Supabase error, skip the shared cache for this long
_cache_state = {"down_until": 0.0, "off": None}


def normalize_query(q):
    """Same search from different users -> same cache key ('Brooks  Ghost 16' == 'brooks ghost 16')."""
    return " ".join((q or "").lower().split())


def _params_hash(params):
    return hashlib.sha256(json.dumps({**params, "v": _CACHE_VERSION}, sort_keys=True).encode()).hexdigest()[:32]


def _cache_table():
    import db  # lazy: recommender also runs from scripts/ without Supabase configured
    return db.get_cache_client().table("search_cache")


def _cache_usable():
    return SHARED_CACHE and not _cache_state["off"] and time.monotonic() >= _cache_state["down_until"]


def _cache_failed(op, e):
    if isinstance(e, RuntimeError) and "not set" in str(e):
        _cache_state["off"] = str(e)  # not configured (local script) — stop trying, quietly
        _log(f"shared search cache off: {e}")
        return
    if time.monotonic() >= _cache_state["down_until"]:
        _log(f"shared search cache {op} failed, bypassing it for {_CACHE_BACKOFF_S}s: {e}")
    _cache_state["down_until"] = time.monotonic() + _CACHE_BACKOFF_S


def cache_get(kind, query, params):
    """Fresh cached results (a list) for this search, or None on a miss / cache unavailable."""
    if not _cache_usable():
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=CACHE_TTL_S[kind])
    try:
        rows = (_cache_table().select("results")
                .eq("kind", kind).eq("query", query).eq("params_hash", _params_hash(params))
                .gte("fetched_at", cutoff.isoformat()).limit(1).execute().data)
    except Exception as e:
        _cache_failed("read", e)
        return None
    if not rows:
        return None
    _count("shared_hits")
    return rows[0]["results"]


def cache_put(kind, query, params, results):
    if not _cache_usable():
        return
    try:
        _cache_table().upsert({
            "kind": kind, "query": query, "params_hash": _params_hash(params),
            "results": list(results), "fetched_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="kind,query,params_hash").execute()
    except Exception as e:
        _cache_failed("write", e)


# Per results page (one recommend() call): how each search was answered. A contextvar, so
# concurrent users don't mix counts; run_with_deadline copies it into its worker threads.
_stats_var = contextvars.ContextVar("fitfxr_search_stats", default=None)
_stats_lock = threading.Lock()


def _count(field, n=1):
    stats = _stats_var.get()
    if stats is not None:
        with _stats_lock:
            stats[field] = stats.get(field, 0) + n


def search_stats_line(stats):
    lookups = stats.get("lookups", 0)
    shared = stats.get("shared_hits", 0)
    fetched = stats.get("fetched", 0)
    return (f"searches this page: {lookups} — {lookups - fetched} memory cache, {shared} shared cache, "
            f"{fetched - shared} SerpAPI ({stats.get('serpapi_calls', 0)} SerpAPI requests)")


SHOPPING_RESULTS = int(os.getenv("FITFXR_SHOPPING_RESULTS", "150"))
_SHOPPING_PAGE = 100  # requested page size; Google currently serves ~40/page, so 150 ≈ 4 credits


def _shopping_params():
    return {"engine": "google_shopping", "results": SHOPPING_RESULTS, "num": _SHOPPING_PAGE, "gl": "us", "hl": "en"}


@lru_cache(maxsize=512)
def _search_cached(query, serpapi_key):
    """Shared cache, else page through Google Shopping until SHOPPING_RESULTS listings (or results run out)."""
    _count("fetched")
    hit = cache_get("shopping", query, _shopping_params())
    if hit is not None:
        return tuple(hit)
    out, seen, complete = [], set(), True
    while len(out) < SHOPPING_RESULTS:
        try:
            page = _serp({
                "engine": "google_shopping", "q": query, "api_key": serpapi_key,
                "num": _SHOPPING_PAGE, "start": len(out), "gl": "us", "hl": "en",
            }).get("shopping_results", [])
        except Exception:
            if not out:
                raise  # nothing to show — let the caller decide (and don't cache the failure)
            complete = False  # show what we have, but don't share a truncated result
            break
        fresh = [r for r in page if (r.get("product_id") or r.get("title")) not in seen]
        if not fresh:
            break  # results ran out (or Google repeated the page)
        seen.update(r.get("product_id") or r.get("title") for r in fresh)
        out.extend(fresh)
    out = out[:SHOPPING_RESULTS]
    if complete:
        cache_put("shopping", query, _shopping_params(), out)
    return tuple(out)


def shopping_search(query, serpapi_key):
    """Google Shopping via SerpAPI, behind the in-process and shared caches."""
    query = normalize_query(query)
    if not query:
        return []
    _count("lookups")
    try:
        return [dict(r) for r in _search_cached(query, serpapi_key)]
    except Exception as e:
        if not isinstance(e, SearchUnavailable):
            _log(f"shopping search failed for {query!r}: {e}")
        return []


SEARCH_DEADLINE_S = 30


def run_with_deadline(fn, items, deadline_s, default=None):
    """fn(item) for every item in parallel daemon threads; results in order. Anything still running
    at the deadline yields `default` and is abandoned — a stuck socket can never hold up a request
    (or, as with ThreadPoolExecutor, block interpreter exit waiting on it)."""
    results = [default] * len(items)

    def work(i, item):
        try:
            results[i] = fn(item)
        except Exception as e:
            _log(f"parallel call failed: {e}")

    # Each thread runs in a copy of the caller's context so per-page search stats reach it.
    threads = [threading.Thread(target=contextvars.copy_context().run, args=(work, i, it), daemon=True)
               for i, it in enumerate(items)]
    for t in threads:
        t.start()
    end = time.monotonic() + deadline_s
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    stuck = sum(t.is_alive() for t in threads)
    if stuck:
        _log(f"{stuck}/{len(threads)} calls missed the {deadline_s}s deadline; continuing without them")
    return [r if r is not None else default for r in results]


def parallel_search(queries, serpapi_key):
    return run_with_deadline(lambda q: shopping_search(q, serpapi_key), queries, SEARCH_DEADLINE_S, default=[])


# ── Retailer sweep (30–100 retailers) ─────────────────────────────────────────
# 76 retailers in retailers.json, grouped (run / outdoor / court / team / work / general).
# Two layers:
#   1. Google Shopping already aggregates every merchant; we pull 40 results and rank the
#      ones sold by an allowlisted retailer first (junk marketplaces are dropped).
#   2. Targeted site: sweeps hit the specialty retailers that matter for the activity,
#      5 domains per Google query (more and Google starts ignoring the filter).
#      FITFXR_SWEEP_BATCHES sets batches per query (default 1 = 5 retailers; each batch
#      is one SerpAPI call — raise it on a paid plan).
# Scraping retailers directly is blocked by bot protection and most ToS. Long term, their
# affiliate product feeds (AvantLink / CJ / Rakuten / Impact) are the proper source.
_RETAILERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retailers.json")
with open(_RETAILERS_PATH) as _f:
    _RETAILERS = json.load(_f)

RETAILER_NAMES = {r["domain"]: r["name"] for g in _RETAILERS["groups"].values() for r in g}
_NAME_KEYS = {n.lower() for n in RETAILER_NAMES.values()}
BLOCKED_SOURCES = ("ebay", "temu", "aliexpress", "wish", "dhgate", "poshmark", "mercari", "shein",
                   "depop", "thredup", "vinted", "grailed", "goat", "stockx")

SWEEP_BATCHES = int(os.getenv("FITFXR_SWEEP_BATCHES", "1"))
# Measured Oct 2026: 5 site: filters → 10/10 results on-allowlist; 10 filters → 3/10.
_DOMAINS_PER_BATCH = 5
_NOT_PRODUCT = re.compile(r"/(blog|blogs|news|review|reviews|guide|guides|article|stories|journal|c|catpage|category|collections?|search)\b|catpage|[?&]q=", re.I)


def retailers_for(activity):
    """Domains to sweep for an activity: its specialty groups first, then general retailers."""
    groups = [g for g, acts in _RETAILERS["activity_groups"].items() if activity in acts] + ["general"]
    seen, out = set(), []
    for g in groups:
        for r in _RETAILERS["groups"][g]:
            if r["domain"] not in seen and r["domain"] not in ("amazon.com", "walmart.com", "target.com"):
                seen.add(r["domain"])
                out.append(r["domain"])
    return out


def is_trusted(prod):
    src = (prod.get("source") or "").lower().strip()
    # Short names ("On", "evo", "6pm") must match exactly, or they'd match half the web.
    return any(src == k or (len(k) > 3 and k in src) for k in _NAME_KEYS)


def is_blocked(prod):
    src = (prod.get("source") or "").lower()
    return any(b in src for b in BLOCKED_SOURCES)


def _strip_site_suffix(title, retailer):
    """'Brooks Ghost 16 Men's | REI Co-op' -> 'Brooks Ghost 16 Men's'"""
    m = re.match(r"^(.*\S)\s+[|–-]\s+(.+)$", title or "")  # greedy head = split at last " | " / " - "
    if m and retailer.split()[0].lower() in m.group(2).lower():
        return m.group(1)
    return title


@lru_cache(maxsize=1024)
def _sweep_cached(query, domains, serpapi_key):
    _count("fetched")
    # The raw organic results are cached (not the parsed products), so filter changes apply at once.
    params = {"engine": "google", "domains": sorted(domains), "num": 10, "gl": "us", "hl": "en"}
    organic = cache_get("sweep", query, params)
    if organic is None:
        # No parentheses around the site: list — Google silently drops the filter if they're there.
        sites = " OR ".join(f"site:{d}" for d in domains)
        organic = _serp({
            "engine": "google", "q": f"{query.replace(chr(39), '')} {sites}", "api_key": serpapi_key,
            "num": 10, "gl": "us", "hl": "en",
        }).get("organic_results", [])
        cache_put("sweep", query, params, organic)
    out = []
    for r in organic:
        link = r.get("link", "")
        host = urlparse(link).netloc.lower()
        domain = next((d for d in domains if host == d or host.endswith("." + d)), None)
        if not domain:
            continue
        ext = (r.get("rich_snippet") or {}).get("bottom", {}).get("detected_extensions", {})
        price = ext.get("price")
        title = r.get("title", "")
        # Keep product pages only (price is optional — retailers rarely expose it in snippets).
        if _NOT_PRODUCT.search(urlparse(link).path + "?" + urlparse(link).query) \
                or re.search(r"review|best .* for|\bvs\.?\b", title, re.I):
            continue
        out.append({
            "title": _strip_site_suffix(r.get("title", ""), RETAILER_NAMES[domain]),
            "price": f"${price:,.2f}" if isinstance(price, (int, float)) else None,
            "source": RETAILER_NAMES[domain],
            "link": link,
            "thumbnail": r.get("thumbnail"),
        })
    return tuple(out)


def retailer_sweep(query, domains, serpapi_key):
    query = normalize_query(query)
    if not query or not domains:
        return []
    _count("lookups")
    try:
        return [dict(r) for r in _sweep_cached(query, tuple(domains), serpapi_key)]
    except Exception as e:
        if not isinstance(e, SearchUnavailable):
            _log(f"retailer sweep failed for {query!r}: {e}")
        return []


def search_everywhere(queries, serpapi_key, activity):
    """Google Shopping + activity-targeted retailer sweeps for each query, all in parallel.
    Returns one merged, junk-free product list per query, allowlisted retailers first."""
    domains = retailers_for(activity)[:SWEEP_BATCHES * _DOMAINS_PER_BATCH]
    batches = [domains[i:i + _DOMAINS_PER_BATCH] for i in range(0, len(domains), _DOMAINS_PER_BATCH)]
    jobs = [(i, None) for i in range(len(queries))] + [(i, b) for i in range(len(queries)) for b in batches]

    def run(job):
        i, batch = job
        if batch is None:
            return shopping_search(queries[i], serpapi_key)
        return retailer_sweep(queries[i], batch, serpapi_key)

    found = run_with_deadline(run, jobs, SEARCH_DEADLINE_S, default=[])
    merged = [[] for _ in queries]
    for (i, _), prods in zip(jobs, found):
        merged[i].extend(x for x in prods if not is_blocked(x))
    return [sorted(m, key=lambda x: not is_trusted(x)) for m in merged]


_KID_RE = re.compile(r"\b(kids?|youth|toddler|little kid|big kid|grade school|junior|infant|boys|girls)\b", re.I)
_USED_RE = re.compile(r"\b(used|pre-?owned|refurbished|replacement laces|shoe ?horn)\b", re.I)


_SIZE_RE = re.compile(
    r"\b(?:size|sz|us)\s*:?\s*(\d{1,2}(?:\.5)?)\b"            # "Size 7.5", "US 8"
    r"|\b(\d{1,2}(?:\.5)?)\s*/\s*(?:[a-e]|2e|4e|d|b|medium|wide|regular)\b"  # "6 / B - Regular", "8.5D"
    r"|\b(\d{1,2}(?:\.5)?)(?:d|b|2e|4e)\b"
    r"|\s(\d{1,2}\.5)\s*$"                                  # trailing "... Blue 5.5"
    r"|,\s*(\d{1,2}(?:\.5)?)\s*(?:-|$)"                       # "Blue/White, 11.5 - Women's"
    r"|/\s*(\d{1,2}(?:\.5)?)\s*$", re.I)                       # "Quartz/Eggplant / 8"


def title_size(title):
    """US size pinned in a listing title, if any."""
    m = _SIZE_RE.search(title or "")
    if not m:
        return None
    val = float(next(g for g in m.groups() if g))
    return val if 4 <= val <= 18 else None


def title_ok(title, p, check_size=True):
    """Cheap hard filter: wrong sex, kids sizing, used listings, a pinned size the user doesn't wear
    (footwear only — gear sizes like '15-20mmHg' or 'Large' aren't shoe sizes)."""
    t = (title or "").lower()
    if not t or _KID_RE.search(t) or _USED_RE.search(t):
        return False
    pinned = title_size(title) if check_size else None
    if pinned is not None and p.get("us_size") is not None and abs(pinned - p["us_size"]) > 0.01:
        return False
    if p["gender"] == "men's" and re.search(r"\bwom[ae]n'?s?\b", t):
        return False
    if p["gender"] == "women's" and re.search(r"\bmen'?s?\b", t) and not re.search(r"\bwom[ae]n", t):
        return False
    return True


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def dedupe(products):
    seen, out = set(), []
    for prod in products:
        key = _norm_title(prod.get("title"))[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(prod)
    return out


def _prerank(prod, queries):
    """Cheap relevance score used to shortlist listings before the LLM sees them."""
    t = set(_norm_title(prod.get("title")).split())
    best_overlap = max((len(t & set(_norm_title(q).split())) for q in queries), default=0)
    rating = prod.get("rating") or 0
    return (best_overlap * 3 + 2 * is_trusted(prod) + (rating >= 4.3)
            + ((prod.get("reviews") or 0) > 50) + (parse_price(prod.get("price")) is not None))


def parse_price(price):
    if isinstance(price, (int, float)):
        return float(price)
    nums = re.findall(r"\d+\.?\d*", str(price or "").replace(",", ""))
    return float(nums[0]) if nums else None


def title_matches_model(title, brand, model):
    """True when the listing title really is this brand + model (e.g. not a 'Ghost-style' knockoff)."""
    t = _norm_title(title)
    tokens = [w for w in _norm_title(model).split() if not w.isdigit()]
    brand_first = _norm_title(brand).split()[:1]
    return all(w in t.split() or w in t for w in tokens) and all(b in t for b in brand_first)


def _log(msg):
    print(f"[FITFXR recommender] {msg}", file=sys.stderr, flush=True)


LLM_WALL_CLOCK_S = 60


def llm_json(client, system, user):
    """JSON-mode chat call with hard limits. JSON mode can occasionally stream whitespace until
    the token limit, so output is capped and the whole call has a wall-clock deadline."""
    def call():
        return client.with_options(timeout=45, max_retries=1).chat.completions.create(
            model=LLM_MODEL,
            temperature=0.2,
            max_tokens=3000,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    box = {}

    def runner():
        try:
            box["r"] = call()
        except Exception as e:
            box["e"] = e
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(LLM_WALL_CLOCK_S)
    if t.is_alive():
        raise TimeoutError(f"LLM call exceeded {LLM_WALL_CLOCK_S}s")
    if "e" in box:
        raise box["e"]
    r = box["r"]
    choice = r.choices[0]
    if choice.finish_reason == "length":
        raise ValueError("LLM reply hit the token cap (runaway JSON)")
    return json.loads(choice.message.content)


def _category(label_key, query, products):
    return {"label": CATEGORY_LABELS[label_key], "query": query, "products": products}


# ── v0: current behaviour (unchanged, kept for A/B) ───────────────────────────

_V0_SYSTEM = (
    "You are FITFXR, an expert footwear and outdoor gear AI.\n"
    "For each PRIMARY activity, generate exactly 3 Google Shopping search queries covering different product categories:\n"
    "  1. Primary footwear (shoes/boots/skates/cleats — the most important)\n"
    "  2. A closely related gear item (insoles, socks, bindings, pedals, etc.)\n"
    "  3. A second gear or apparel item that pairs well with the activity\n\n"
    "Return valid JSON only. Structure:\n"
    '{"Road Running": {"footwear": "Brooks Ghost 16 men stability running shoe", '
    '"gear_1": "Superfeet GREEN insoles arch support", '
    '"gear_2": "Balega Hidden Comfort running socks men"}, '
    '"Downhill Skiing": {"footwear": "Salomon S/Pro 120 ski boot men", '
    '"gear_1": "Superfeet Yellow ski boot insoles", '
    '"gear_2": "Smartwool ski socks men medium cushion"}}\n\n'
    "Rules:\n"
    "- Name a specific real brand and model in EVERY query\n"
    "- Footwear must match the activity exactly (boots/cleats/skates/shoes — never generic)\n"
    "- ALWAYS include the user's sex in footwear queries: 'women's' for Woman, 'men's' for Man\n"
    "- ALWAYS apply arch type to footwear queries (e.g. 'high arch support', 'neutral arch')\n"
    "- Apply ALL injury, width, and waterproof requirements to the footwear query\n"
    "- gear_1 and gear_2 must be distinct product types (not two shoe queries)\n"
    "- Every query must find a buyable product on Google Shopping\n"
    "- Return ONLY the JSON object — no markdown, no explanation"
)


def _v0_context(u):
    injuries = u.get("injuries", [])
    notes = []
    if "Overpronation / Collapsed Arches" in injuries or "Flat Feet" in injuries:
        notes.append("CRITICAL: all queries MUST include 'motion control' or 'stability'. No neutral shoes.")
    if "Plantar Fasciitis" in injuries or "Heel Spurs" in injuries:
        notes.append("Prioritize max cushion and arch support in every query.")
    if "Knee Pain (Runner's Knee / PFPS)" in injuries or "IT Band Syndrome" in injuries:
        notes.append("Prioritize shock absorption and cushioning.")
    if "High Arches" in injuries or "Supination / Underpronation" in injuries:
        notes.append("Neutral or cushioned shoes only. No motion control.")
    if "Achilles Tendonitis" in injuries:
        notes.append("Prioritize lower heel drop (4–8mm) and cushioning.")
    if "Bunions" in injuries or "Morton's Neuroma" in injuries:
        notes.append("All queries require wide toe box.")
    if "Ankle Instability / Chronic Sprains" in injuries or "Weak Ankles (general)" in injuries:
        notes.append("Prioritize high-top or ankle-support designs.")
    width_note = ("Wide feet — include 'wide' or '2E'/'4E' in all queries."
                  if u.get('width') in ["Wide (2E)", "Extra Wide (4E+)"] else "")
    wp_note = ("Waterproofing required — include 'waterproof' or 'Gore-Tex' in all queries."
               if u.get('waterproof') == "Required for most activities" else "")
    return "\n".join(filter(None, [
        f"User: Age {u['age']}, {u['sex']}, {u['weight']}, {u['height']}.",
        f"Shoe size: {u['shoe_size']}, Width: {u['width']}, Arch: {u['arch']}.",
        f"Injuries/Concerns: {', '.join(injuries) or 'None'}.",
        f"Waterproof need: {u.get('waterproof', 'N/A')}.",
        f"Priorities: {', '.join(u['priorities']) if u['priorities'] else 'None'}.",
        width_note, wp_note, *notes,
    ]))


def recommend_v0(u, activity_answers, client, serpapi_key):
    lines = []
    for act, answers in activity_answers.items():
        detail = "; ".join(f"{k}: {v}" for k, v in answers.items())
        lines.append(f'  "{act}": details — {detail}' if detail else f'  "{act}"')
    queries = llm_json(client, _V0_SYSTEM,
                       f"{_v0_context(u)}\n\nPRIMARY activities to generate queries for:\n" + "\n".join(lines))
    queries_lower = {k.lower(): v for k, v in queries.items()}

    results = {}
    for act in activity_answers:
        ai_act = queries.get(act) or queries_lower.get(act.lower()) or {}
        if isinstance(ai_act, str):
            ai_act = {"footwear": ai_act}
        generic = ACTIVITY_FALLBACK_TERMS.get(act, f"{act} shoes")
        cats = {}
        try:
            for cat_key in CATEGORY_LABELS:
                q = ai_act.get(cat_key, "")
                fallback = generic if cat_key == "footwear" else ""
                if not q and not fallback:
                    continue
                products = shopping_search(q, serpapi_key) if q else []
                used = q
                if not products:
                    used = fallback or q
                    products = shopping_search(used, serpapi_key)
                cats[cat_key] = _category(cat_key, used, products)
            results[act] = {"engine": "v0", "categories": cats}
        except Exception as e:
            results[act] = {"engine": "v0", "categories": {}, "error": str(e)}
    return results


# ── v1: rerank real listings ──────────────────────────────────────────────────

_V1_QUERY_SYSTEM = (
    "You are FITFXR's gear expert. For the activity below, write Google Shopping queries that "
    "will surface products that genuinely fit this person.\n"
    "Return JSON: {\"footwear\": [q1, q2, q3], \"gear_1\": [q], \"gear_2\": [q]}\n"
    "- footwear: 3 queries, each naming a DIFFERENT specific brand+model sold new in 2025–2026 (no old "
    "versions) that meets every "
    "MUST HAVE; include the sex term and 'wide'/'waterproof' when required.\n"
    "- gear_1: the accessory that most helps THIS person's injuries/feet (e.g. insoles, braces, socks).\n"
    "- gear_2: a second, different item that pairs with the activity.\n"
    "Keep queries short (under 9 words) — Google Shopping punishes long queries."
)

_V1_RERANK_SYSTEM = (
    "You are FITFXR's fit specialist. You get a person's fit profile and REAL product listings "
    "from Google Shopping, grouped by category. Judge every listing.\n"
    "For each id return: fit (0–100: how well it fits THIS person for THIS activity), "
    "reject (true if it violates any MUST HAVE, is the wrong sex, kids sizing, the wrong product "
    "type for the category, a knockoff, or an accessory listed as footwear), and why "
    "(max 12 words, written to the user, naming the specific feature that fits them).\n"
    "MUST HAVEs apply to footwear only. Gear is judged on whether it helps THIS person's feet/activity "
    "and is the item the query asked for (right product, not a variant or unrelated product).\n"
    "Check EVERY activity answer (surface, cleat type, boot flex, crampon compatibility, safety toe, "
    "insulation, pack weight, play style...) — a listing that contradicts one is a misfit. "
    "Use your product knowledge of each model (stability vs neutral, cushion, drop, widths). "
    "Be strict — a mediocre fit should score under 60.\n"
    'Return JSON: {"scores": [{"id": "f0", "fit": 88, "reject": false, "why": "..."}]}'
)


def recommend_v1(u, activity_answers, client, serpapi_key):
    results = {}
    for act, answers in activity_answers.items():
        p = build_fit_profile(u, act, answers)
        brief = profile_brief(u, p)
        try:
            q = llm_json(client, _V1_QUERY_SYSTEM, brief)
            generic = " ".join(filter(None, [p["gender"], "wide" if p["wide"] else "",
                                             ACTIVITY_FALLBACK_TERMS.get(act, f"{act} shoes")]))
            plan = {
                "footwear": [x for x in (q.get("footwear") or []) if x][:3] + [generic],
                "gear_1": [x for x in (q.get("gear_1") or []) if x][:1],
                "gear_2": [x for x in (q.get("gear_2") or []) if x][:1],
            }
            pools = {cat: [] for cat in plan}
            # Interleave so every query (and every retailer) is represented in the pool.
            per_query = search_everywhere(plan["footwear"], serpapi_key, act)
            for row in zip_longest(*per_query):
                pools["footwear"].extend(x for x in row if x)
            gear = [(cat, qq) for cat in ("gear_1", "gear_2") for qq in plan[cat]]
            for (cat, _), prods in zip(gear, parallel_search([qq for _, qq in gear], serpapi_key)):
                pools[cat].extend(x for x in prods if not is_blocked(x))
            # Hundreds of raw listings → cheap pre-rank → top N go to GPT for real scoring.
            for cat in pools:
                limit = 40 if cat == "footwear" else 12
                clean = [x for x in dedupe(pools[cat]) if title_ok(x.get("title"), p, check_size=cat == "footwear")]
                pools[cat] = sorted(clean, key=lambda x: -_prerank(x, plan[cat]))[:limit]

            listing, by_id = [], {}
            for cat, prods in pools.items():
                for i, prod in enumerate(prods):
                    pid = f"{cat[0]}{cat[-1]}{i}"
                    by_id[pid] = prod
                    listing.append({"id": pid, "category": cat, "title": prod.get("title"),
                                    "price": prod.get("price"), "seller": prod.get("source")})

            if listing:
                verdict = llm_json(client, _V1_RERANK_SYSTEM,
                                   f"{brief}\n\nLISTINGS:\n{json.dumps(listing)}")
                for sc in verdict.get("scores", []):
                    prod = by_id.get(sc.get("id"))
                    if prod is None:
                        continue
                    prod["fit_score"] = 0 if sc.get("reject") else int(sc.get("fit", 0))
                    prod["why"] = sc.get("why", "")

            cats = {}
            for cat, qs in plan.items():
                if not qs:
                    continue
                kept = sorted((x for x in pools[cat] if x.get("fit_score", 0) >= 50),
                              key=lambda x: -x["fit_score"])
                cats[cat] = _category(cat, " · ".join(qs), kept)
            results[act] = {"engine": "v1", "categories": cats}
        except Exception as e:
            results[act] = {"engine": "v1", "categories": {}, "error": str(e)}
    return results


# ── v2: spec match ────────────────────────────────────────────────────────────

_SPEC_FIELDS = (
    '{"brand": str, "model": str (model LINE without version number, e.g. "Adrenaline GTS"), '
    '"stability": "neutral"|"stability"|"motion control"|"n/a", "cushion": "max"|"moderate"|"minimal", '
    '"drop_mm": int|null, "wide_available": bool, "waterproof_option": bool, "ankle_support": bool, '
    '"wide_toebox": bool, "weight": "light"|"medium"|"heavy", "durable": bool, "plate": "carbon"|"nylon"|null, '
    '"vegan": bool|null, "typical_price_usd": int}'
)

_V2_SYSTEM = (
    "You are FITFXR's footwear spec database. Given a fit profile, propose CANDIDATES and report "
    "their real specs honestly — specs are scored by code, so do not inflate them to fit.\n"
    "Return JSON: {\"requirements\": [...], \"footwear\": [8 specs], \"gear_1\": [3 items], \"gear_2\": [3 items], \"catalog_extras\": {...}}\n"
    f"Each footwear spec: {_SPEC_FIELDS}\n"
    "Footwear: 8 different current models across at least 4 brands, the right TYPE for the activity "
    "(boots/cleats/skates/court shoes...). Include some that may not fit — the scorer decides. "
    "If the person named a current or discontinued favourite, include it (or its closest current successor).\n"
    "REQUIREMENTS: also return \"requirements\": up to 6 checks derived ONLY from Activity answers the person "
    "actually gave (body/injury/width/waterproof are already handled). Never invent a requirement they did not "
    "answer — e.g. no crampon check unless they were asked about crampons. Each "
    '{"key": snake_case, "desc": "short phrase to the user, e.g. crampon-compatible", '
    '"level": "must"|"should", "want": true|false|"exact enum value"}. '
    "Every footwear spec (and every CATALOG model, if given) must carry \"extras\": {key: value} for each requirement key, "
    "using your honest knowledge (null if unknown).\n"
    'If CATALOG MODELS are listed, return "catalog_extras": {"Brand Model": {key: value}} for each of them.\n'
    'Each gear item: {"name": "Brand Product" (searchable, under 6 words), "why": "max 12 words, to the user"}. '
    "gear_1 = the accessory that most helps THIS person's feet/injuries; gear_2 = a different item that pairs with the activity. "
    "Gear must be something a footwear/outdoor/sporting retailer sells, typically under $150 — no furniture, "
    "massagers or electronics."
)


def _search_models(ranked, p, serpapi_key, top_n=4, start=0):
    """Search each top-ranked model by exact name; keep only listings that really are that model."""
    top = ranked[start:start + top_n]
    queries = [" ".join(filter(None, [
        spec["brand"], spec["model"], p["gender"],
        "wide" if p["wide"] else "",
        "waterproof" if p["waterproof"] == "required" else "",
    ])) for spec, _, _ in top]
    found = search_everywhere(queries, serpapi_key, p["activity"])
    products = []
    for (spec, score, reasons), prods in zip(top, found):
        typical = spec.get("price_usd") or spec.get("typical_price_usd")
        floor = 0.45 * typical if isinstance(typical, (int, float)) else 0
        hits = [x for x in prods
                if title_ok(x.get("title"), p)
                and title_matches_model(x.get("title"), spec["brand"], spec["model"])
                # Way under typical price on a current model = counterfeit, used or mislabelled.
                and (parse_price(x.get("price")) is None or parse_price(x.get("price")) >= floor)]
        for x in dedupe(hits)[:6]:
            x["fit_score"] = min(100, score)
            x["spec_score"] = min(100, score)
            x["why"] = ", ".join(reasons).capitalize() if reasons else ""
            products.append(x)
    return " · ".join(queries), products


def _search_gear(items, p, serpapi_key):
    items = [i for i in (items or []) if i.get("name")][:2]
    found = parallel_search([i["name"] for i in items], serpapi_key)
    products = []
    for item, prods in zip(items, found):
        words = [w for w in _norm_title(item["name"]).split() if w not in {"for", "the", "and", "with", "s"}]
        brand, rest = words[:1], set(words[1:])

        def matches(x):
            # Brand must appear, plus at least half the product words ("Tech Polo" ≠ "Launch Shorts").
            got = set(_norm_title(x.get("title")).split())
            return set(brand) <= got and len(rest & got) >= (len(rest) + 1) // 2
        good = [x for x in prods if title_ok(x.get("title"), p, check_size=False) and not is_blocked(x) and matches(x)]
        for x in sorted(good, key=lambda x: not is_trusted(x))[:5]:
            x["why"] = item.get("why", "")
            x["requested"] = item["name"]
            products.append(x)
    return " · ".join(i["name"] for i in items), dedupe(products)


def rank_specs(specs, p, reqs=()):
    ranked, misses = [], []
    for spec in specs:
        if not spec.get("brand") or not spec.get("model"):
            continue
        score, reasons = score_spec(spec, p, reqs)
        if score is not None:
            ranked.append((spec, score, reasons))
        else:
            misses.append((spec, 30, [f"closest match ({reasons[0]})"]))
    # Nothing met every must-have: show the closest options, clearly labelled, instead of an empty page.
    ranked = ranked or misses
    ranked.sort(key=lambda r: -r[1])
    return ranked


def _v2_activity(u, act, answers, client, serpapi_key, footwear_specs=None, verify=False):
    p = build_fit_profile(u, act, answers)
    brief = profile_brief(u, p)
    if footwear_specs is not None:
        brief += "\n\nCATALOG MODELS: " + "; ".join(f"{s['brand']} {s['model']}" for s in footwear_specs)
    out = llm_json(client, _V2_SYSTEM, brief)
    reqs = [r for r in (out.get("requirements") or []) if isinstance(r, dict) and r.get("key")]
    if footwear_specs is not None:
        cat_extras = {k.lower(): v for k, v in (out.get("catalog_extras") or {}).items()}
        specs = [{**s, "extras": cat_extras.get(f"{s['brand']} {s['model']}".lower(), {})} for s in footwear_specs]
    else:
        specs = out.get("footwear") or []
    ranked = rank_specs(specs, p, reqs)
    query, foot = _search_models(ranked, p, serpapi_key)
    cats = {"footwear": _category("footwear", query, foot)}
    for g in ("gear_1", "gear_2"):
        gq, gp = _search_gear(out.get(g), p, serpapi_key)
        if gq:
            cats[g] = _category(g, gq, gp)
    if not verify:
        return cats

    verify_listings(client, brief, reqs, cats, must_haves(p))
    # Too few footwear listings survived the judge → try the next-best models once.
    if len(cats["footwear"]["products"]) < 3 and len(ranked) > 4:
        q2, more = _search_models(ranked, p, serpapi_key, top_n=3, start=4)
        if more:
            extra = {"footwear": _category("footwear", q2, more)}
            verify_listings(client, brief, reqs, extra, must_haves(p))
            cats["footwear"]["products"] = sorted(
                cats["footwear"]["products"] + extra["footwear"]["products"], key=lambda x: -x["fit_score"])
            cats["footwear"]["query"] += " · " + q2
    if not cats["footwear"]["products"]:
        cats["footwear"]["note"] = ("None of the listings we found met every must-have"
                                    + (f" ({cats['footwear'].get('top_reject')})" if cats["footwear"].get("top_reject") else "")
                                    + ". Try Refine My Search, or loosen a preference.")
    return cats


_VERIFY_SYSTEM = (
    "You are FITFXR's fit checker. Our spec engine shortlisted these REAL store listings for the person below. "
    "Its spec claims are unverified hints — they can be wrong. Judge each LISTING (the exact product, model and "
    "version in its title) using the listing title and your own knowledge of that exact product.\n"
    "reject = true if it violates any MUST HAVE or activity requirement marked must (e.g. claimed composite toe "
    "but this model has none; no Gore-Tex version when Gore-Tex is required; not offered in the needed width), "
    "is the wrong sex, kids sizing, an outdated version sold as clearance junk, a knockoff, or the wrong product "
    "type for its category. MUST HAVEs apply to footwear; gear is judged on helping THIS person and being the "
    "item named.\n"
    "For every FOOTWEAR listing also return checks: one entry per numbered MUST HAVE, in order, each "
    '"yes" | "no" | "unknown" — does THIS exact model/version have it? Answer "no" when you know it '
    "lacks it (e.g. a slip-resistant shoe is not a safety-toe shoe).\n"
    "For GEAR, 'requested' is the KIND of product we searched for, not an exact SKU. Any variant, model or "
    "pack size of that kind is fine (any Dr. Scholl's work insole matches 'Dr. Scholl's Work Insoles'). "
    "Reject gear only when it is a different KIND of product (ski poles for trekking poles, shorts for a "
    "polo, heel cups for full insoles) or does not help this person.\n"
    "fit = 0–100 for THIS person and activity; be strict, a mediocre fit is under 60.\n"
    "why = max 12 words to the user naming the specific feature that fits them (or, if rejected, why not).\n"
    'Return JSON: {"scores": [{"id": "f0", "checks": ["yes", "no"], "fit": 88, "reject": false, "why": "..."}]}'
)


def verify_listings(client, brief, reqs, cats, musts=()):
    """LLM judge pass over the real listings in `cats` (mutates it). Rejected listings are removed;
    footwear fit = average of spec score and judge score. Fails open: on an LLM error the
    unverified results are kept."""
    listing, by_id = [], {}
    for cat, c in cats.items():
        for i, prod in enumerate(c["products"]):
            pid = f"{ {'footwear': 'f', 'gear_1': 'a', 'gear_2': 'b'}[cat] }{i}"
            by_id[pid] = (cat, prod)
            listing.append({"id": pid, "category": cat, "title": prod.get("title"),
                            "price": prod.get("price"), "seller": prod.get("source"),
                            **({"requested": prod["requested"]} if prod.get("requested") else {}),
                            "spec_claim": prod.get("why") or None})
    if not listing:
        return
    must_list = list(musts) + [r.get("desc") or r["key"] for r in reqs if r.get("level") == "must"]
    should = "; ".join(r.get("desc") or r["key"] for r in reqs if r.get("level") != "must") or "none"
    numbered = "\n".join(f"{i + 1}. {m}" for i, m in enumerate(must_list)) or "(none)"
    prompt = (f"{brief}\n\nMUST HAVES (footwear):\n{numbered}\nNice to have: {should}"
              f"\n\nLISTINGS:\n{json.dumps(listing)}")
    try:
        try:
            verdict = llm_json(client, _VERIFY_SYSTEM, prompt)
        except Exception as e:
            _log(f"verify judge failed ({e}); retrying once")
            verdict = llm_json(client, _VERIFY_SYSTEM, prompt)
    except Exception as e:
        _log(f"verify judge failed, showing unverified picks: {e}")
        for _, prod in by_id.values():
            prod["why"] = ((prod.get("why") or "") + " (unverified)").strip()
        return
    judged = {}
    for sc in verdict.get("scores", []):
        if sc.get("id") in by_id:
            judged[sc["id"]] = sc
    if not judged:
        return
    keep = {cat: [] for cat in cats}
    for pid, (cat, prod) in by_id.items():
        sc = judged.get(pid)
        if sc is None:
            continue  # judge skipped it — unverified, leave it out
        checks = [str(c).lower() for c in (sc.get("checks") or [])] if cat == "footwear" else []
        failed = [m for m, c in zip(must_list, checks) if c == "no"]
        if sc.get("reject") or failed:
            if cat == "footwear" and not cats[cat].get("top_reject"):
                cats[cat]["top_reject"] = f"not {failed[0]}" if failed else sc.get("why", "")
            continue
        judge = int(sc.get("fit", 0)) - 12 * checks.count("unknown")  # unconfirmed must-haves cost
        spec = prod.get("spec_score")
        prod["fit_score"] = round((spec + judge) / 2) if spec is not None else judge
        prod["why"] = sc.get("why") or prod.get("why", "")
        prod["verified"] = True
        keep[cat].append(prod)
    for cat in cats:
        cats[cat]["products"] = sorted(keep[cat], key=lambda x: -x["fit_score"])


def recommend_v2(u, activity_answers, client, serpapi_key):
    results = {}
    for act, answers in activity_answers.items():
        try:
            results[act] = {"engine": "v2", "categories": _v2_activity(u, act, answers, client, serpapi_key)}
        except Exception as e:
            results[act] = {"engine": "v2", "categories": {}, "error": str(e)}
    return results


# ── v3: curated catalog ───────────────────────────────────────────────────────

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")
MIN_CATALOG_COVERAGE = 4


@lru_cache(maxsize=1)
def load_catalog():
    with open(_CATALOG_PATH) as f:
        return json.load(f)["products"]


def recommend_v3(u, activity_answers, client, serpapi_key):
    catalog = load_catalog()
    results = {}
    for act, answers in activity_answers.items():
        try:
            specs = [c for c in catalog if act in c.get("activities", [])]
            if len(specs) < MIN_CATALOG_COVERAGE:
                # Thin coverage — let the model propose candidates instead.
                results[act] = {"engine": "v3→v2", "categories": _v2_activity(u, act, answers, client, serpapi_key)}
            else:
                results[act] = {"engine": "v3", "categories": _v2_activity(
                    u, act, answers, client, serpapi_key, footwear_specs=specs)}
        except Exception as e:
            results[act] = {"engine": "v3", "categories": {}, "error": str(e)}
    return results


# ── v5: tiered (Good / Better / Best) ─────────────────────────────────────────
# Three DIFFERENT models at three distinct price points, each the strongest fit for this person
# in its price band — the budget buyer gets a real recommendation, not just the cheapest seller.
# Catalog-only and deterministic: no LLM and no live search on the critical path, so results are
# instant, repeatable and auditable. FITFXR_LIVE_PRICES=1 (with SerpAPI credits) adds live store
# prices/links for the three picks; without it, picks show typical retail and a shop link.

TIERS = [("good", "Good", "Smart budget pick"), ("better", "Better", "Best balance"), ("best", "Best", "Premium pick")]
LIVE_PRICES = os.getenv("FITFXR_LIVE_PRICES", "0") == "1"
SHOP_URL = os.getenv("FITFXR_SHOP_URL", "https://www.google.com/search?tbm=shop&q={q}")
_MIN_STEP = 1.12  # each tier costs at least 12% more than the one below


def load_gear():
    with open(_CATALOG_PATH) as f:
        return json.load(f).get("gear", [])


def fit_display(score):
    """score_spec's open-ended score -> a 50–99 'fit score' for display (monotonic)."""
    return max(50, min(99, round(55 + 0.45 * score)))


def _profile_chips(p):
    chips = []
    if p["stability"] == "required":
        chips.append("Stability support")
    elif p["stability"] == "avoid":
        chips.append("Neutral (no motion control)")
    if p["cushion"] != "any":
        chips.append(f"{p['cushion'].capitalize()} cushion")
    if p["wide"]:
        chips.append("Extra-wide (4E)" if p["extra_wide"] else "Wide (2E)")
    if p["wide_toebox"]:
        chips.append("Roomy toe box")
    if p["ankle_support"]:
        chips.append("Ankle support")
    if p["waterproof"] == "required":
        chips.append("Waterproof")
    if p["min_drop_mm"] is not None or p["max_drop_mm"] is not None:
        chips.append(f"Drop {p['min_drop_mm'] if p['min_drop_mm'] is not None else 0}–"
                     f"{p['max_drop_mm'] if p['max_drop_mm'] is not None else 14}mm")
    if p["wants_plate"]:
        chips.append("Plated / race")
    if p["vegan"]:
        chips.append("Vegan")
    chips += [r["desc"][0].upper() + r["desc"][1:] for r in p["tag_rules"] if r["level"] == "must"]
    return chips


def _checks(spec, p, rules):
    """What this model does for the person's must-haves: [(text, ok)]."""
    out = []
    stab = (spec.get("stability") or "").lower()
    if stab in ("", "n/a"):
        pass  # skates, climbing shoes, ski boots, cleats: arch support isn't a property of this kind of shoe
    elif p["stability"] == "required":
        out.append((f"{stab.capitalize()} support for flat arches / overpronation",
                    stab in ("stability", "motion control")))
    elif p["stability"] == "avoid":
        out.append(("No motion control (right for high arches)", stab != "motion control"))
    if p["cushion"] != "any" and spec.get("cushion"):
        ok = spec["cushion"] == p["cushion"]
        out.append((f"{p['cushion'].capitalize()} cushion" + ("" if ok else f" (this one is {spec['cushion']})"), ok))
    if p["wide"]:
        out.append(("Made in wide sizes", bool(spec.get("wide_available"))))
    if p["wide_toebox"]:
        out.append(("Roomy toe box", bool(spec.get("wide_toebox"))))
    if p["ankle_support"]:
        out.append(("Ankle support", bool(spec.get("ankle_support"))))
    if p["waterproof"] == "required":
        out.append(("Waterproof version", bool(spec.get("waterproof_option"))))
    tags = set(spec.get("tags") or [])
    for r in rules:
        if r["level"] == "must":
            out.append((r["desc"][0].upper() + r["desc"][1:], bool(tags & set(r["tags"]))))
    return out


# What a check / reason line is about, so "Made in wide sizes" and "comes in wide" count as one line.
_LINE_TOPICS = [("toebox", ("toe box",)), ("width", ("wide",)), ("ankle", ("ankle",)),
                ("waterproof", ("waterproof", "gore-tex")), ("cushion", ("cushion",)),
                ("stability", ("stability", "motion control", "neutral", "arch")), ("drop", ("mm drop",)),
                ("vegan", ("vegan",)), ("activity", ("built for ", "made for "))]


def _line_topic(text):
    t = text.lower()
    for topic, words in _LINE_TOPICS:
        if any(w in t for w in words):
            return topic
    return None


def _spec_facts(spec, act):
    """Plain facts about the shoe, for a card that has no must-haves to check (a no-special-needs profile)."""
    out = [(f"Made for {act.lower()}", True)]
    bits = []
    if spec.get("cushion") and spec["cushion"] != "n/a":
        bits.append(f"{spec['cushion']} cushion")
    if isinstance(spec.get("drop_mm"), (int, float)):
        bits.append(f"{int(spec['drop_mm'])}mm drop")
    if spec.get("weight") == "light":
        bits.append("lightweight")
    if spec.get("durable"):
        bits.append("durable build")
    if bits:
        out.append(((", ".join(bits))[0].upper() + ", ".join(bits)[1:], True))
    return out


def _with_reasons(checks, reasons, limit=5):
    """Top up the must-have checks with the scorer's positive reasons, so every card says why it fits.
    A reason about something a check already covers (width, arch support, cushion...) is skipped."""
    seen = {t.lower() for t, _ in checks}
    topics = {_line_topic(t) for t, _ in checks} - {None}
    out = list(checks)
    for r in reasons:
        if len(out) >= limit:
            break
        if r.startswith("closest match"):
            continue
        txt = r[0].upper() + r[1:]
        topic = _line_topic(txt)
        if topic in topics or any(txt.lower() in t or t in txt.lower() for t in seen):
            continue
        out.append((txt, True))
        seen.add(txt.lower())
        if topic:
            topics.add(topic)
    return out


def score_tiered(spec, p, rules):
    """score_spec plus the activity-specific tag rules. (score, reasons) or (None, [why not])."""
    score, reasons = score_spec(spec, p)
    if score is None:
        return None, reasons
    tags = set(spec.get("tags") or [])
    for r in rules:
        has = bool(tags & set(r["tags"]))
        if r["level"] == "avoid":
            score -= 15 if has else 0
            continue
        if r["level"] == "must" and not has:
            return None, [f"not {r['desc']}"]
        if has:
            score += 10 if r["level"] == "must" else 8
            reasons.insert(0, r["desc"])
        elif r["level"] == "should":
            score -= 4
    if p.get("competitive") and (spec.get("plate") or spec.get("weight") == "light"):
        score += 4
    return score, reasons


def pick_tiers(scored):
    """scored: [(spec, score)] — choose (good, better, best): three different models with each price
    at least _MIN_STEP above the previous, maximising total fit, with a bonus for landing in the
    low / middle / top third of the price range and for brand variety. Brute force — n is small."""
    scored = sorted(scored, key=lambda x: (x[0]["price_usd"], -x[1]))
    prices = sorted(sp["price_usd"] for sp, _ in scored)
    if len(scored) < 3:
        return [sp for sp, _ in scored]
    lo, hi = prices[len(prices) // 3], prices[(2 * len(prices)) // 3]
    top = max(sc for _, sc in scored)
    # Keep the search fast and sensible: the 40 strongest fits.
    pool = sorted(scored, key=lambda x: -x[1])[:40]
    pool.sort(key=lambda x: x[0]["price_usd"])

    def quality(sc):
        return sc - 2 * max(0, top - sc - 15)  # steep cost for anything well below the best fit

    best, best_val = None, None
    n = len(pool)
    for i in range(n):
        g, gs = pool[i]
        for j in range(i + 1, n):
            b, bs = pool[j]
            if b["price_usd"] < g["price_usd"] * _MIN_STEP:
                continue
            for k in range(j + 1, n):
                x, xs = pool[k]
                if x["price_usd"] < b["price_usd"] * _MIN_STEP:
                    continue
                val = quality(gs) + quality(bs) + quality(xs)
                val += 10 * (g["price_usd"] <= lo) + 6 * (lo < b["price_usd"] <= hi or b["price_usd"] == lo) \
                    + 10 * (x["price_usd"] >= hi)
                val += 4 * (len({g["brand"], b["brand"], x["brand"]}) - 1)
                if best_val is None or val > best_val:
                    best, best_val = (g, b, x), val
    if best:
        return list(best)
    # Prices too bunched for clean steps: best fits at three different prices, else top three.
    out, used = [], set()
    for sp, _ in sorted(scored, key=lambda x: -x[1]):
        if sp["price_usd"] not in used:
            out.append(sp)
            used.add(sp["price_usd"])
        if len(out) == 3:
            break
    # Fewer than three distinct prices: show what there is (e.g. Better + Best) rather than two "tiers"
    # at the same price.
    return sorted(out, key=lambda sp: sp["price_usd"])


def _shop_link(q):
    from urllib.parse import quote_plus
    return SHOP_URL.format(q=quote_plus(q))


def _query_for(spec, p):
    return " ".join(filter(None, [spec["brand"], spec["model"], p["gender"],
                                  "wide" if p["wide"] and spec.get("wide_available") else "",
                                  "gore-tex" if p["waterproof"] == "required" and spec.get("waterproof_option") else ""]))


def _recommended_tier(p):
    if "Price / Value" in p["priorities"]:
        return "good"
    if p["competitive"] or p["wants_plate"]:
        return "best"
    return "better"


def _attach_live_prices(picks, p, serpapi_key):
    """Optional: best trusted live listing for each pick (in parallel, bounded). Fails soft."""
    def lookup(prod):
        hits = [x for x in shopping_search(prod["query"], serpapi_key)
                if title_matches_model(x.get("title"), prod["brand"], prod["model"])
                and title_ok(x.get("title"), p, check_size=False) and not is_blocked(x)
                and parse_price(x.get("price")) is not None]
        hits.sort(key=lambda x: (not is_trusted(x), parse_price(x.get("price"))))
        return hits[0] if hits else None
    for prod, hit in zip(picks, run_with_deadline(lookup, picks, 12)):
        if hit:
            prod.update(price=hit.get("price"), source=hit.get("source"), live=True,
                        link=hit.get("link") or hit.get("product_link") or prod["link"],
                        thumbnail=hit.get("thumbnail"))


def _pick_gear(p):
    act, inj = p["activity"], set(p["injuries"])
    arch = "flat" if p["stability"] == "required" else "high" if p["stability"] == "avoid" else None

    def rank(g):
        if inj & set(g.get("for_injuries", [])):
            return 0
        if arch and arch in g.get("for_arch", []):
            return 1
        if act in g.get("for_activities", []):
            return 2
        if not (g.get("for_injuries") or g.get("for_arch") or g.get("for_activities")):
            return 3  # general fallback
        return 9
    out = []
    for kind, label in (("insole", "Insole"), ("sock", "Socks")):
        options = sorted((g for g in load_gear() if g["kind"] == kind), key=rank)
        if options and rank(options[0]) < 9:
            g = options[0]
            name = f"{g['brand']} {g['model']}"
            out.append({"title": name, "brand": g["brand"], "model": g["model"], "tier_label": label,
                        "price": f"${g['price_usd']}", "price_usd": g["price_usd"], "why": g["highlight"],
                        "source": "Typical price", "link": _shop_link(name), "requested": name})
    return out


def _v5_activity(u, act, answers, serpapi_key):
    p = build_fit_profile(u, act, answers)
    candidates = [c for c in load_catalog() if act in c.get("activities", [])]
    # Only enforce rules the catalog can answer for this activity (e.g. no cleat tags on running shoes).
    have = {t for c in candidates for t in c.get("tags", [])}
    cushions = {c.get("cushion") for c in candidates} | {"any"}
    rules = [r for r in p["tag_rules"] if have & set(r["tags"])]

    def eligible(prof, rls):
        out = []
        for spec in candidates:
            sc, reasons = score_tiered(spec, prof, rls)
            if sc is not None:
                if spec.get("activities", [None])[0] == act:
                    sc += 4  # purpose-built for this activity, not a crossover pick
                    reasons.append(f"built for {act.lower()}")
                out.append((spec, sc, reasons))
        return out

    # If a must-have leaves fewer than three models, relax the least critical one and say so,
    # rather than returning nothing (e.g. 'waterproof for most activities' + road running).
    relax_steps = [
        ("waterproof", lambda q: {**q, "waterproof": "preferred"} if q["waterproof"] == "required" else None),
        ("vegan", lambda q: {**q, "vegan": False} if q["vegan"] else None),
        ("wide", lambda q: {**q, "wide": False, "extra_wide": False} if q["wide"] else None),
        ("stability support", lambda q: {**q, "stability": "any"} if q["stability"] == "required" else None),
    ]
    prof, rls, relaxed = p, rules, []
    if p["waterproof"] == "required" and not any(c.get("waterproof_option") for c in candidates):
        prof = {**p, "waterproof": "any"}  # the general survey answer doesn't apply (court shoes, cleats...)
    scored = eligible(prof, rls)
    if len(scored) < 3:
        # Prefer relaxing ONE thing (the first, least critical, that is enough); else relax cumulatively.
        single = [(phrase, nxt) for phrase, step in relax_steps if (nxt := step(prof)) is not None]
        for phrase, nxt in single:
            trial = eligible(nxt, rls)
            if len(trial) >= 3:
                prof, scored, relaxed = nxt, trial, [phrase]
                break
        else:
            for phrase, step in relax_steps:
                if len(scored) >= 3:
                    break
                nxt = step(prof)
                if nxt is not None:
                    prof = nxt
                    relaxed.append(phrase)
                    scored = eligible(prof, rls)
    if len(scored) < 3 and any(r["level"] == "must" for r in rls):
        relaxed += [r["desc"] for r in rls if r["level"] == "must"]
        rls = [r for r in rls if r["level"] != "must"]
        scored = eligible(prof, rls)
    closest = bool(relaxed)
    by_id = {id(sp): (sc, rs) for sp, sc, rs in scored}
    picks = pick_tiers([(sp, sc) for sp, sc, _ in scored])

    rec = _recommended_tier(p)
    products = []
    for (key, label, blurb), spec in zip(TIERS[-len(picks):] if len(picks) < 3 else TIERS, picks):
        sc, reasons = by_id[id(spec)]
        name = f"{spec['brand']} {spec['model']}"
        q = _query_for(spec, p)
        check_p = {**p, "waterproof": prof["waterproof"] if p["waterproof"] == "required"
                   and prof["waterproof"] == "any" else p["waterproof"]}
        if p["cushion"] not in cushions:
            check_p["cushion"] = "any"  # no model for this activity offers it (e.g. max-cushion climbing shoes)
        checks = _with_reasons(_checks(spec, check_p, rules), reasons)
        if len(checks) < 2:
            checks = _with_reasons(checks, [t for t, _ in _spec_facts(spec, act)], limit=3)
        fit = fit_display(sc)
        fails = sum(1 for _, ok in checks if not ok)
        if fails:
            fit = min(fit, 94 - 5 * (fails - 1))  # a visible ✗ can't sit next to a near-perfect score
        products.append({
            "title": name, "brand": spec["brand"], "model": spec["model"],
            "tier": key, "tier_label": label, "tier_blurb": blurb, "recommended": key == rec,
            "price": f"${spec['price_usd']}", "price_usd": spec["price_usd"], "source": "Typical price",
            "fit_score": fit, "raw_score": sc,
            "why": spec.get("highlight", ""), "reasons": reasons[:3],
            "checks": checks, "query": q, "link": _shop_link(q), "verified": True,
        })
    if products and not any(x["recommended"] for x in products):
        products[len(products) // 2]["recommended"] = True
    if LIVE_PRICES and serpapi_key and products:
        _attach_live_prices(products, p, serpapi_key)

    cat = _category("footwear", " · ".join(x["title"] for x in products), products)
    cat.update(tiers=True, profile_chips=_profile_chips(p), considered=len(candidates), matched=len(scored))
    if len(products) == 3:
        g, x = products[0], products[2]
        gap = x["fit_score"] - g["fit_score"]
        cat["summary"] = (f"Checked {len(candidates)} {act.lower()} models against your profile. "
                          f"The Good pick saves ${x['price_usd'] - g['price_usd']} vs Best"
                          + (" with almost the same fit." if gap <= 4 else f" for {gap} fewer fit points."))
    if relaxed:
        cat["note"] = (f"Few {act.lower()} models come with {' or '.join(relaxed)}, so these are the "
                       f"best fits without it — check the ✗ items before you buy.")
        cat["relaxed"] = relaxed
    cats = {"footwear": cat}
    gear = _pick_gear(p)
    if gear:
        cats["gear_1"] = {**_category("gear_1", " · ".join(x["title"] for x in gear), gear),
                          "label": "🧦 Complete the fit", "addons": True}
    return cats


def recommend_v5(u, activity_answers, client, serpapi_key):
    results = {}
    for act, answers in activity_answers.items():
        try:
            results[act] = {"engine": "v5", "categories": _v5_activity(u, act, answers, serpapi_key)}
        except Exception as e:
            _log(f"v5 failed for {act}: {e}")
            results[act] = {"engine": "v5", "categories": {}, "error": str(e)}
    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def recommend_v4(u, activity_answers, client, serpapi_key):
    """v2's spec-driven model picks, then an LLM judge verifies every real listing."""
    results = {}
    for act, answers in activity_answers.items():
        try:
            results[act] = {"engine": "v4",
                            "categories": _v2_activity(u, act, answers, client, serpapi_key, verify=True)}
        except Exception as e:
            results[act] = {"engine": "v4", "categories": {}, "error": str(e)}
    return results


DEFAULT_ENGINE = "v5"
_DISPATCH = {"v0": recommend_v0, "v1": recommend_v1, "v2": recommend_v2, "v3": recommend_v3, "v4": recommend_v4,
             "v5": recommend_v5}


SEARCH_DOWN_NOTE = ("Live product search is temporarily unavailable, so we couldn't check stores just now. "
                    "Please try again in a little while. Your answers are saved.")


def recommend(engine, user_data, activity_answers, openai_key, serpapi_key):
    """activity_answers: {primary_activity: {question_label: answer}}"""
    # v5 needs no LLM; don't require an OpenAI key for it.
    client = OpenAI(api_key=openai_key) if engine != "v5" and openai_key else None
    _search_errors.clear()
    stats = {}
    token = _stats_var.set(stats)
    try:
        results = _DISPATCH.get(engine, _DISPATCH[DEFAULT_ENGINE])(user_data, activity_answers, client, serpapi_key)
    finally:
        _stats_var.reset(token)
        _log(f"{engine} · {len(activity_answers)} activit{'y' if len(activity_answers) == 1 else 'ies'} · "
             + search_stats_line(stats))
    if _search_errors:
        # Empty categories were caused by search failures, not by the person's must-haves.
        for res in results.values():
            for cat in res.get("categories", {}).values():
                if not cat.get("products"):
                    cat["note"] = SEARCH_DOWN_NOTE
                    cat["search_down"] = True
    return results


def serpapi_credits_left(serpapi_key):
    """Remaining SerpAPI searches (the account endpoint is free), or None if unknown."""
    try:
        import urllib.request
        with urllib.request.urlopen(f"https://serpapi.com/account.json?api_key={serpapi_key}", timeout=5) as r:
            return json.load(r).get("total_searches_left")
    except Exception:
        return None
