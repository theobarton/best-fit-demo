"""
Run every recommendation engine live on fixed test profiles and save the picks side by side.

    python3 scripts/compare_engines.py                 # all profiles
    python3 scripts/compare_engines.py flat_wide_runner  # one profile
    python3 scripts/compare_engines.py --engines v1,v2   # subset of engines

Writes scripts/out/compare_<profile>.json. Uses real SerpAPI + OpenAI credits
(roughly 15 SerpAPI searches per engine per activity; v0 ~3–6).
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

import recommender as R  # noqa: E402

PROFILES = {
    "flat_wide_runner": {
        "about": "Heavier woman, flat feet + plantar fasciitis, 2E wide, marathon training, wants max cushion, budget-minded",
        "user": {"age": "35–44", "sex": "Woman", "weight": "200–225 lbs (90–102 kg)", "height": "5'3\" – 5'6\" (160–168 cm)",
                 "shoe_size": "US 9.5  |  EU 42", "width": "Wide (2E)", "arch": "Flat / Low Arch",
                 "injuries": ["Flat Feet", "Plantar Fasciitis"], "priorities": ["Comfort", "Price / Value"],
                 "waterproof": "Doesn't matter"},
        "answers": {"Road Running": {"Primary goal": "Long distance (10K–Marathon)",
                                     "Cushion preference": "Max cushion (Hoka-style)",
                                     "Heel drop": "No preference",
                                     "Current or favorite shoe": "Brooks Adrenaline GTS 22"}},
    },
    "high_arch_backpacker": {
        "about": "Man, high arches + bunions + Achilles tendonitis + weak ankles, heavy pack, rocky terrain, Gore-Tex required",
        "user": {"age": "25–34", "sex": "Man", "weight": "160–180 lbs (72–82 kg)", "height": "5'9\" – 6'0\" (175–183 cm)",
                 "shoe_size": "US 10.5  |  EU 43", "width": "Standard / Medium (D/B)", "arch": "High Arch",
                 "injuries": ["High Arches", "Bunions", "Achilles Tendonitis", "Weak Ankles (general)"],
                 "priorities": ["Durability", "Support / Stability"], "waterproof": "Required for most activities"},
        "answers": {"Backpacking": {"Typical pack weight": "Heavy (50+ lbs)",
                                    "Boot height preference": "High cut (full ankle support)",
                                    "Waterproofing": "Required (Gore-Tex)",
                                    "Primary terrain": "Rocky / technical"}},
    },
    "safety_toe_nurse": {
        "about": "Man, extra-wide 4E, diabetic foot + neuropathy, 10+ hr hospital shifts, needs composite toe, vegan",
        "user": {"age": "45–54", "sex": "Man", "weight": "250–275 lbs (113–125 kg)", "height": "6'0\" – 6'3\" (183–190 cm)",
                 "shoe_size": "US 12  |  EU 45", "width": "Extra Wide (4E+)", "arch": "Neutral",
                 "injuries": ["Diabetic Foot", "Neuropathy"], "priorities": ["Comfort", "Support / Stability"],
                 "waterproof": "Nice to have"},
        "answers": {"Work / Standing All Day": {"Work environment": "Hospital / healthcare",
                                                "Safety requirements": "Steel toe / composite toe",
                                                "Top comfort priority": "Wide toe box",
                                                "Hours on feet per shift": "10+ hours",
                                                "Vegan materials?": "Yes — vegan only"}},
    },
}

# Count SerpAPI calls per engine.
_calls = {"n": 0}
_RealSearch = R.GoogleSearch


class _CountingSearch(_RealSearch):
    def get_dict(self):
        _calls["n"] += 1
        return super().get_dict()


R.GoogleSearch = _CountingSearch


def tiers(products):
    """Mirror app.render_product_tiers: best fits only, then budget / value / premium by price."""
    scored = [p for p in products if p.get("fit_score") is not None]
    if scored:
        best = max(p["fit_score"] for p in scored)
        pool = [p for p in scored if p["fit_score"] >= best - 15] or scored[:3]
    else:
        pool = products
    trusted = [p for p in pool if R.is_trusted(p)]
    if len(trusted) >= 3:
        pool = trusted
    priced = sorted([p for p in pool if R.parse_price(p.get("price")) is not None],
                    key=lambda p: R.parse_price(p.get("price")))
    pool = priced + [p for p in pool if R.parse_price(p.get("price")) is None]
    picks, brands = [], set()
    for p in pool:
        b = re.sub(r"^((wo)?men'?s|unisex|adults?'?)\s+", "", (p.get("title") or "").lower()).split(" ")[0]
        if b not in brands:
            picks.append(p)
            brands.add(b)
    if len(picks) >= 3:
        picks = [picks[0], picks[len(picks) // 2], picks[-1]]
    return [{k: p.get(k) for k in ("title", "price", "source", "fit_score", "why", "link", "thumbnail")}
            for p in picks[:3]]


def run(profile_key, engines):
    prof = PROFILES[profile_key]
    out = {"profile": profile_key, "about": prof["about"], "user": prof["user"],
           "answers": prof["answers"], "engines": {}}
    for eng in engines:
        _calls["n"] = 0
        t0 = time.time()
        try:
            res = R.recommend(eng, prof["user"], prof["answers"],
                              os.getenv("OPENAI_API_KEY"), os.getenv("SERPAPI_KEY"))
            err = None
        except Exception as e:
            res, err = {}, str(e)
        entry = {"seconds": round(time.time() - t0, 1), "serp_calls": _calls["n"], "error": err, "activities": {}}
        for act, d in res.items():
            entry["activities"][act] = {
                "engine": d.get("engine"), "error": d.get("error"),
                "categories": {cat: {"query": c["query"], "candidates": len(c["products"]),
                                     "picks": tiers(c["products"])}
                               for cat, c in d.get("categories", {}).items()},
            }
        out["engines"][eng] = entry
        print(f"  {profile_key} {eng}: {entry['serp_calls']} SerpAPI calls, {entry['seconds']}s"
              + (f"  ERROR {err}" if err else ""), flush=True)
    os.makedirs(os.path.join(ROOT, "scripts", "out"), exist_ok=True)
    with open(os.path.join(ROOT, "scripts", "out", f"compare_{profile_key}.json"), "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    engines = list(R.ENGINES)
    for a in sys.argv[1:]:
        if a.startswith("--engines="):
            engines = a.split("=", 1)[1].split(",")
    for key in args or list(PROFILES):
        run(key, engines)
