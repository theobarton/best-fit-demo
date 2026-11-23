"""Exhaustive check of engine v5 model selection. Run: python3 scripts/sweep_v5.py
Catalog only, no network. Covers every activity x every answer option of every activity question,
and every activity x a grid of body/foot profiles (sex, width, arch, waterproof, priorities, injuries).
For each result it checks the rules a shopper relies on and prints any violation, then catalog coverage.
Exit code 1 if any violation."""

import ast
import collections
import itertools
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import recommender as R  # noqa: E402

R._log = lambda msg: None


def _literal(name):
    tree = ast.parse(open(os.path.join(ROOT, "app.py")).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


ACTIVITY_QUESTIONS = _literal("ACTIVITY_QUESTIONS")
GENERIC_QUESTIONS = _literal("GENERIC_QUESTIONS")
ACTIVITIES = list(ACTIVITY_QUESTIONS)
SPECS = {(c["brand"], c["model"]): c for c in R.load_catalog()}

BASE = {"sex": "Man", "shoe_size": "US 10", "width": "Standard / Medium (D/B)", "arch": "Neutral",
        "injuries": [], "weight": "160–180 lbs (72–81 kg)", "priorities": [], "waterproof": "Doesn't matter"}
GRID = {
    "sex": ["Man", "Woman", "Non-Binary"],
    "width": ["Standard / Medium (D/B)", "Wide (2E)", "Extra Wide (4E+)", "Narrow"],
    "arch": ["I don't know", "Neutral", "High Arch", "Flat / Low Arch"],
    "waterproof": ["Doesn't matter", "Nice to have", "Required for most activities"],
    "priorities": [[], ["Price / Value"], ["Lightweight", "Support / Stability"]],
}
INJURY_SETS = [["Plantar Fasciitis"], ["Achilles Tendonitis"], ["Bunions"], ["Ankle Instability / Chronic Sprains"],
               ["Overpronation / Collapsed Arches", "Knee Pain (Runner's Knee / PFPS)"]]

violations = []
stats = collections.Counter()
picked = collections.Counter()
relaxed_log = collections.Counter()
short = collections.Counter()


def check(u, act, answers, label):
    stats["runs"] += 1
    cat = R._v5_activity(u, act, answers, "")["footwear"]
    prods = cat["products"]
    p = R.build_fit_profile(u, act, answers)
    relaxed = set(cat.get("relaxed") or [])
    for r in relaxed:
        relaxed_log[(act, r)] += 1

    def bad(msg, kind=None):
        violations.append((kind or msg, f"[{act}] {label}: {msg} -> {[x['title'] for x in prods]}"))

    if len(prods) < 3:
        stats["fewer than 3 distinct prices (shown as fewer tiers)"] += 1
        short[(act, len(prods))] += 1
        if len(prods) < 2:
            return bad(f"{len(prods)} picks", "fewer than 2 picks")
    if [x["tier"] for x in prods] != ["good", "better", "best"][-len(prods):]:
        bad("tiers out of order")
    if len({x["title"] for x in prods}) != len(prods):
        bad("duplicate model")
    prices = [x["price_usd"] for x in prods]
    if any(a >= b for a, b in zip(prices, prices[1:])):
        bad(f"prices not rising {prices}", "prices not rising")
    if sum(x["recommended"] for x in prods) != 1:
        bad("not exactly one recommended tier")
    for x in prods:
        spec = SPECS[(x["brand"], x["model"])]
        picked[(x["brand"], x["model"])] += 1
        if act not in spec["activities"]:
            bad(f"{x['title']} not tagged for {act}", "model from another activity")
        if not 50 <= x["fit_score"] <= 99:
            bad(f"{x['title']} fit score {x['fit_score']}", "fit score out of range")
        if p["stability"] == "required" and "stability support" not in relaxed \
                and spec.get("stability") == "neutral":  # n/a = skates, cleats, climbing shoes
            bad(f"{x['title']} is {spec.get('stability')} for a stability-required profile", "stability must-have broken")
        if p["stability"] == "avoid" and spec.get("stability") == "motion control":
            bad(f"{x['title']} is motion control for a high arch", "motion control for high arch")
        if p["wide"] and "wide" not in relaxed and not spec.get("wide_available"):
            bad(f"{x['title']} not made in wide", "wide must-have broken")
        if p["vegan"] and "vegan" not in relaxed and spec.get("vegan") is False:
            bad(f"{x['title']} not vegan", "vegan must-have broken")
        for rule in p["tag_rules"]:
            if rule["level"] == "must" and rule["desc"] not in relaxed:
                have = {t for c in R.load_catalog() if act in c.get("activities", []) for t in c.get("tags", [])}
                if have & set(rule["tags"]) and not set(spec.get("tags") or []) & set(rule["tags"]):
                    bad(f"{x['title']} misses must-have '{rule['desc']}'", "activity must-have broken")
        topics = [t for t in (R._line_topic(c) for c, _ in x["checks"]) if t]
        if len(topics) != len(set(topics)):
            bad(f"{x['title']} repeats a check line {x['checks']}", "repeated check line")
        if any("n/a" in c.lower() for c, _ in x["checks"]):
            bad(f"{x['title']} shows an n/a line {x['checks']}", "card shows 'n/a'")
        if not x["checks"]:
            bad(f"{x['title']} has no check lines", "card has no ✓ lines")
        fails = [c for c, ok in x["checks"] if not ok]
        if fails and x["fit_score"] > 94:
            bad(f"{x['title']} shows {x['fit_score']} next to a ✗", "high score next to ✗")


t0 = time.perf_counter()
# 1. Every activity x every option of every question (one answer changed at a time), on 3 base bodies.
bodies = [BASE, {**BASE, "sex": "Woman", "width": "Wide (2E)", "arch": "Flat / Low Arch",
                 "injuries": ["Plantar Fasciitis"], "waterproof": "Required for most activities"},
          {**BASE, "arch": "High Arch", "width": "Extra Wide (4E+)", "injuries": ["Bunions"]}]
for act in ACTIVITIES:
    qs = ACTIVITY_QUESTIONS.get(act) or GENERIC_QUESTIONS
    for bi, u in enumerate(bodies):
        check(u, act, {}, f"body{bi} no answers")
        for q in qs:
            for opt in q.get("options", []):
                val = [opt] if q["type"] == "multiselect" else opt
                check(u, act, {q["label"]: val}, f"body{bi} {q['label']}={opt}")
        # every multiselect with all its options at once
        for q in qs:
            if q["type"] == "multiselect":
                check(u, act, {q["label"]: q["options"]}, f"body{bi} {q['label']}=ALL")
n_answers = stats["runs"]

# 2. Every activity x the full body/foot grid, and x each injury set.
for act in ACTIVITIES:
    for combo in itertools.product(*GRID.values()):
        u = {**BASE, **dict(zip(GRID, combo))}
        check(u, act, {}, f"grid {dict(zip(GRID, combo))}")
    for inj in INJURY_SETS:
        check({**BASE, "injuries": inj}, act, {}, f"injuries {inj}")
dt = time.perf_counter() - t0

print(f"{stats['runs']} recommendations ({n_answers} answer-option runs, {stats['runs'] - n_answers} profile runs) "
      f"across {len(ACTIVITIES)} activities in {dt:.1f}s ({dt / stats['runs'] * 1000:.2f} ms each)")
print(f"violations: {len(violations)}")
by_kind = collections.defaultdict(list)
for kind, v in violations:
    by_kind[kind].append(v)
for kind, vs in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
    print(f"  ✗ {kind}: {len(vs)}")
    for v in vs[:4]:
        print("      e.g.", v)

never = sorted(k for k in SPECS if k not in picked)
print(f"\ncatalog coverage: {len(SPECS) - len(never)}/{len(SPECS)} models picked at least once")
for b, m in never:
    print(f"  never picked: {b} {m} ({', '.join(SPECS[(b, m)]['activities'][:3])})")
print(f"\nresults with fewer than 3 tiers: {sum(short.values())}")
for (act, n), c in short.most_common(10):
    print(f"  {act}: {n} tiers x{c}")
print("\nconstraints relaxed most often (disclosed to the shopper):")
for (act, r), n in relaxed_log.most_common(12):
    print(f"  {act}: '{r}' x{n}")
sys.exit(1 if violations else 0)
