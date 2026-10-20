"""Render scripts/out/compare_*.json (from compare_engines.py) into a side-by-side HTML page.

    python3 scripts/render_comparison.py   →  scripts/out/engine_comparison.html

VERDICTS are hand-written notes from reviewing the Oct 16 2026 live run; profiles without
one just show the picks.
"""
import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "scripts", "out")
E = lambda s: html.escape(str(s)) if s is not None else ""  # noqa: E731

TITLES = {
    "flat_wide_runner": "Flat-footed, wide, heavier marathoner",
    "high_arch_backpacker": "High-arch backpacker with bunions + Achilles",
    "safety_toe_nurse": "Extra-wide diabetic nurse needing composite toe",
}
VERDICTS = {
    "flat_wide_runner": {
        "v0": ("bad", "Used eBay/Depop listings in sizes 7.5 and 8 for $19–35. This is today's live behaviour."),
        "v1": ("mid", "Right category (stability + wide), but picked 2020-era models (Ariel '20, 1540v3), and every gear item was rejected."),
        "v2": ("good", "Wave Horizon, Hurricane and Gaviota 6: all max-cushion stability shoes, from Academy, Foot Locker and Fleet Feet. (Rerun after fixes.)"),
        "v3": ("good", "Gaviota plus the current Adrenaline GTS 25, the successor to her GTS 22. The budget slot came from an unknown seller (since fixed)."),
        "v4": ("mid", "Not run live: the Mac went to sleep mid-run. Same model picks as v2, plus the listing check."),
    },
    "high_arch_backpacker": {
        "v0": ("mid", "Salomon Quest 4D is a good boot, but there was one pick only, and the insoles were wrong."),
        "v1": ("mid", "Quest 5 GTX at REI is excellent. It also kept an Asolo its own note says lacks Gore-Tex, which was a must-have."),
        "v2": ("good", "Best boots for a 50+ lb load on rock: Quest 4 GTX, Nucleo High II GTX, Kailash Plus GTX. It invented a 'crampon' requirement nobody asked for."),
        "v3": ("mid", "The catalog is too thin here. Moab and Targhee are light hikers, not 50 lb pack boots, and one was an old Targhee II."),
        "v4": ("good", "Salomon Quest GTX + Lowa Renegade GTX: both high-cut Gore-Tex load-haulers, and the invented crampon rule is gone. The ski poles shown were from before the gear fix; on a re-check the judge now drops all five."),
    },
    "safety_toe_nurse": {
        "v0": ("bad", "A $19.99 eBay Skechers with no safety toe."),
        "v1": ("mid", "Only one shoe survived, but it meets every must-have (composite toe, extra wide). The gear was spot on: diabetic and neuropathy socks."),
        "v2": ("bad", "GPT claimed the NB 626v2 and Skechers UNO SR are composite toe. They aren't. Gear included a $1,295 standing desk."),
        "v3": ("mid", "Honest 'closest match: not composite toe'. The catalog has no safety-toe shoes yet."),
        "v4": ("good", "Only verified safety-toe shoes made it through (Reebok Sublite Work, incl. Alloy Toe at Kohl's). The fake 'composite toe' shoes and the Skechers slip-on were rejected. Gear was empty because of a judge bug, since fixed: insoles and work pants now come through."),
    },
}
ENG = {"v0": "v0 · Current", "v1": "v1 · Rerank", "v2": "v2 · Spec Match", "v3": "v3 · Catalog", "v4": "v4 · Spec + Verify ★"}
GRADE = {"good": "Good fit", "mid": "Partial", "bad": "Misfit"}

CSS = """
:root{--bg:#F5F6FA;--card:#fff;--ink:#1B2F6E;--text:#1f2433;--muted:#667;--line:#e3e6ef;--good:#1a7f4b;--goodbg:#e6f5ec;--mid:#9a6400;--midbg:#fdf3dc;--bad:#b42318;--badbg:#fde8e6;--accent:#E8A020}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1320;--card:#181d2e;--ink:#c9d4ff;--text:#e4e7f0;--muted:#9aa3b8;--line:#2a3148;--good:#5fd39a;--goodbg:#13301f;--mid:#f0c060;--midbg:#33290f;--bad:#ff8a7f;--badbg:#3a1714}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,'Helvetica Neue',Helvetica,sans-serif;padding:24px 16px 48px}
main{max-width:1560px;margin:0 auto}h1{color:var(--ink);margin:0 0 4px;font-size:clamp(24px,4vw,34px)}h2{color:var(--ink);margin:0;font-size:21px}
.lede{color:var(--muted);margin:0 0 20px;max-width:820px}.rec{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:14px 18px;margin:0 0 28px}
.rec b{color:var(--ink)}section{margin:0 0 36px}.about{margin:2px 0 8px;color:var(--muted);font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}.chips span{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}@media(max-width:1200px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:1000px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.grid{grid-template-columns:1fr}}
.col{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column}.col.good{border-top:4px solid var(--good)}.col.mid{border-top:4px solid var(--mid)}.col.bad{border-top:4px solid var(--bad)}.col.star{box-shadow:0 0 0 2px var(--accent)}
.eh{display:flex;justify-content:space-between;align-items:center;gap:8px}h3{margin:0;font-size:15px;color:var(--ink)}
.grade{font-size:11.5px;font-weight:700;border-radius:6px;padding:2px 8px;white-space:nowrap}.grade.good{color:var(--good);background:var(--goodbg)}.grade.mid{color:var(--mid);background:var(--midbg)}.grade.bad{color:var(--bad);background:var(--badbg)}
.note{font-size:13px;margin:8px 0 4px}h4{margin:10px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}h4 small{text-transform:none;letter-spacing:0;font-weight:400}
ul{list-style:none;margin:0;padding:0}li{padding:7px 0;border-top:1px solid var(--line);font-size:13px}li a{color:var(--text);text-decoration:none;font-weight:600;overflow-wrap:anywhere}li a:hover{text-decoration:underline}
.meta{color:var(--muted);font-size:12px}.fit{color:var(--good);font-weight:700}.why{font-size:12px;font-style:italic;color:var(--muted)}.gear li{font-size:12.5px}.empty{color:var(--muted);font-style:italic}
.stats{margin-top:auto;padding-top:10px;font-size:11.5px;color:var(--muted)}
"""


def card(p):
    fit = f'<span class="fit">{p["fit_score"]}% fit</span>' if p.get("fit_score") is not None else ""
    why = f'<div class="why">{E(p["why"])}</div>' if p.get("why") else ""
    return (f'<li><a href="{E(p.get("link") or "#")}" target="_blank" rel="noopener">{E(p["title"])}</a>'
            f'<div class="meta"><b>{E(p.get("price") or "price at store")}</b> · {E(p.get("source") or "?")} {fit}</div>{why}</li>')


def section(key):
    d = json.load(open(os.path.join(OUT, f"compare_{key}.json")))
    u = d["user"]
    act = list(d["answers"])[0]
    chips = [u["sex"], u["weight"].split(" (")[0], u["width"], u["arch"], *u["injuries"],
             *(f"{k}: {v}" for k, v in d["answers"][act].items())]
    cols = []
    for eng in ENG:
        if eng not in d["engines"]:
            continue
        e = d["engines"][eng]
        a = e["activities"].get(act, {})
        cats = a.get("categories", {})
        g, note = VERDICTS.get(key, {}).get(eng, ("mid", ""))
        foot = cats.get("footwear", {})
        gear = [p for c in ("gear_1", "gear_2") for p in cats.get(c, {}).get("picks", [])][:3]
        routed = f' · ran as {E(a["engine"])}' if a.get("engine") not in (None, eng) else ""
        cols.append(f'''<div class="col {g}{' star' if eng == 'v4' else ''}">
  <div class="eh"><h3>{ENG[eng]}</h3><span class="grade {g}">{GRADE[g]}</span></div>
  <p class="note">{E(note)}</p>
  <h4>Footwear <small>{foot.get("candidates", 0)} candidates</small></h4>
  <ul>{"".join(card(p) for p in foot.get("picks", [])) or '<li class="empty">No footwear survived the filters</li>'}</ul>
  <h4>Gear</h4>
  <ul class="gear">{"".join(card(p) for p in gear) or '<li class="empty">None</li>'}</ul>
  <div class="stats">{e["serp_calls"]} SerpAPI credits · {e["seconds"]}s{routed}</div>
</div>''')
    return (f'<section><h2>{E(TITLES.get(key, key))}</h2><p class="about">{E(act)}</p>'
            f'<div class="chips">{"".join(f"<span>{E(c)}</span>" for c in chips)}</div>'
            f'<div class="grid">{"".join(cols)}</div></section>')


def main():
    keys = [k for k in TITLES if os.path.exists(os.path.join(OUT, f"compare_{k}.json"))]
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Engine Comparison</title><style>{CSS}</style></head><body><main>
<h1>FITFXR engine head-to-head</h1>
<p class="lede">Three hard survey profiles, run live against Google Shopping + GPT-4o on Oct 16, 2026 (40 results per query to save free-plan credits). Each column shows what the results page would show: three footwear tiers, then gear.</p>
<div class="rec"><b>Default is now v4 · Spec + Verify.</b> It takes v2's model picks (the best of v1–v3), then a GPT check confirms every must-have on each <i>real listing</i> (yes / no / unknown). Any "no" is dropped, and unconfirmed must-haves lower the score. Live, it fixed v2's two failures: the fake "composite toe" shoes and the invented crampon requirement. v0–v3 stay selectable for comparison.</div>
{"".join(section(k) for k in keys)}
</main></body></html>'''
    path = os.path.join(OUT, "engine_comparison.html")
    with open(path, "w") as f:
        f.write(page)
    print(path)


if __name__ == "__main__":
    main()
