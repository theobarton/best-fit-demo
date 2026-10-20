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

import json
import os
import re
import sys
import threading
import time
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
    "v4": "v4 · Spec Match + Verify (recommended)",
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
        elif "current" in l and "shoe" in l:
            p["current_shoe"] = v
    return p


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


SHOPPING_RESULTS = int(os.getenv("FITFXR_SHOPPING_RESULTS", "150"))
_SHOPPING_PAGE = 100  # requested page size; Google currently serves ~40/page, so 150 ≈ 4 credits


@lru_cache(maxsize=512)
def _search_cached(query, serpapi_key):
    """Page through Google Shopping until SHOPPING_RESULTS listings (or results run out)."""
    out, seen = [], set()
    while len(out) < SHOPPING_RESULTS:
        try:
            page = _serp({
                "engine": "google_shopping", "q": query, "api_key": serpapi_key,
                "num": _SHOPPING_PAGE, "start": len(out), "gl": "us", "hl": "en",
            }).get("shopping_results", [])
        except Exception:
            if not out:
                raise  # nothing to show — let the caller decide (and don't cache the failure)
            break
        fresh = [r for r in page if (r.get("product_id") or r.get("title")) not in seen]
        if not fresh:
            break  # results ran out (or Google repeated the page)
        seen.update(r.get("product_id") or r.get("title") for r in fresh)
        out.extend(fresh)
    return tuple(out[:SHOPPING_RESULTS])


def shopping_search(query, serpapi_key):
    """Google Shopping via SerpAPI. Cached per process so retries/refines don't burn quota."""
    if not query:
        return []
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

    threads = [threading.Thread(target=work, args=(i, it), daemon=True) for i, it in enumerate(items)]
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
    # No parentheses around the site: list — Google silently drops the filter if they're there.
    sites = " OR ".join(f"site:{d}" for d in domains)
    organic = _serp({
        "engine": "google", "q": f"{query.replace(chr(39), '')} {sites}", "api_key": serpapi_key,
        "num": 10, "gl": "us", "hl": "en",
    }).get("organic_results", [])
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
    if not query or not domains:
        return []
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


DEFAULT_ENGINE = "v4"
_DISPATCH = {"v0": recommend_v0, "v1": recommend_v1, "v2": recommend_v2, "v3": recommend_v3, "v4": recommend_v4}


SEARCH_DOWN_NOTE = ("Live product search is temporarily unavailable, so we couldn't check stores just now. "
                    "Please try again in a little while. Your answers are saved.")


def recommend(engine, user_data, activity_answers, openai_key, serpapi_key):
    """activity_answers: {primary_activity: {question_label: answer}}"""
    client = OpenAI(api_key=openai_key)
    _search_errors.clear()
    results = _DISPATCH.get(engine, _DISPATCH[DEFAULT_ENGINE])(user_data, activity_answers, client, serpapi_key)
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
