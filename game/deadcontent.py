"""
Dead-content detector.  Run: .venv/bin/python -m game.deadcontent

One traced playthrough found three systems that could never fire — engineers gated on
themselves, a statistical category above its own ceiling, and riots so frequent they
were wallpaper. Aggregate statistics had hidden all three for twenty ticks.

This generalises that catch: play every distinct style, and report anything the player
can never reach, anything that never moves, and anything that fires so often it stops
meaning anything.
"""
from __future__ import annotations
import statistics as st
from collections import Counter

from game.sim import Game, LINES, LINE_BY_KEY, CATEGORIES, LINK_NAMES, GOOD_KEYS

STYLES = {
    "seeing":     dict(census=.42, army=.24, normal=.14, granary=.10, land=.05, schools=.05),
    "building":   dict(railway=.40, land=.20, army=.24, census=.06, normal=.05, schools=.05),
    "schooling":  dict(normal=.30, schools=.28, census=.10, army=.24, granary=.08),
    "soldiering": dict(army=.66, census=.12, granary=.12, normal=.05, schools=.05),
    "feeding":    dict(granary=.34, land=.30, army=.22, census=.08, schools=.06),
    "old regime": dict(army=.26, granary=.10, census=.03, normal=.03, schools=.03),
    "political":  dict(census=.16, army=.26, normal=.14, granary=.12, land=.12, schools=.08),
}


def play(style: str, mix: dict, seed: int):
    g = Game(seed)
    g.collect()
    peak = Counter()
    binds = Counter()
    crises = Counter()
    logs = Counter()
    reached = {l.key: 0.0 for l in LINES}
    stuck = {}
    prev = {}
    while not g.game_over:
        if g.crisis:
            crises[g.crisis.key] += 1
            opts = [o.key for o in g.crisis.choices]
            g.choose(opts[0])
            continue
        if style == "political":
            for h in g.settlement:
                if g.can_pay(h.key) and h.consent < 0.6:
                    g.pay(h.key)
        for l in LINES:
            b, v = g.preview(l.key)
            binds[b] += 1
            reached[l.key] = max(reached[l.key], v)
        t = g.treasury
        for k, v in mix.items():
            g.budget[k] = t * v
        g.end_turn()
        for line in g.log:
            key = line.split(".")[0][:34]
            logs[key] += 1
        g.notice = []
        if not g.game_over:
            g.collect()
        now = {"clerks": g.clerks, "masters": g.masters, "engineers": g.engineers,
               "register": g.register_quality, "army": g.army, "credit": g.credit}
        for k, v in now.items():
            peak[k] = max(peak[k], v)
            if k in prev and abs(v - prev[k]) < 1e-4:
                stuck[k] = stuck.get(k, 0) + 1
        prev = now
    return dict(peak=peak, binds=binds, crises=crises, logs=logs,
                reached=reached, stuck=stuck, unlocked=set(g.unlocked),
                lost=len(g.lost_provinces),
                overridden=any(h.overridden for h in g.settlement))


def main():
    seeds = range(1, 9)
    all_unlocked, all_crises, all_binds = set(), Counter(), Counter()
    reached_any = {l.key: 0.0 for l in LINES}
    peak_any = Counter()
    log_freq = Counter()
    print(f"Dead-content sweep — {len(STYLES)} styles x {len(list(seeds))} seeds\n")
    print(f"  {'style':12s} {'lines>50%':>10} {'cats':>5} {'crises':>7} {'lost':>5}  dominant binds")
    for name, mix in STYLES.items():
        rs = [play(name, mix, s) for s in seeds]
        for r in rs:
            all_unlocked |= r["unlocked"]
            all_crises += r["crises"]
            all_binds += r["binds"]
            log_freq += r["logs"]
            for k, v in r["reached"].items():
                reached_any[k] = max(reached_any[k], v)
            for k, v in r["peak"].items():
                peak_any[k] = max(peak_any[k], v)
        good = st.mean(sum(1 for v in r["reached"].values() if v > 0.5) for r in rs)
        cats = st.mean(len(r["unlocked"]) for r in rs)
        cri = st.mean(sum(r["crises"].values()) for r in rs)
        lost = st.mean(r["lost"] for r in rs)
        top = ", ".join(f"{k}" for k, _ in Counter(
            {k: sum(r["binds"][k] for r in rs) for k in LINK_NAMES}).most_common(2) if _)
        print(f"  {name:12s} {good:10.1f} {cats:5.1f} {cri:7.1f} {lost:5.1f}  {top}")

    print("\nUNREACHABLE  (nothing here should be permanently out of the player's grasp)")
    bad = False
    for l in LINES:
        if reached_any[l.key] < 0.6:
            print(f"  DEAD   {l.name}: never exceeds {reached_any[l.key]*100:.0f}% throughput in any style")
            bad = True
    for k in ("clerks", "masters", "engineers", "register"):
        if peak_any[k] < 0.35:
            print(f"  DEAD   institution '{k}' never exceeds {peak_any[k]:.2f}")
            bad = True
    for c in CATEGORIES:
        if c.key not in all_unlocked:
            print(f"  DEAD   category '{c.name}' never unlocked in any style")
            bad = True
    for k in ("dearth", "cholera", "sedition"):
        if all_crises[k] == 0:
            print(f"  DEAD   crisis '{k}' never fired in any style")
            bad = True
    for k in LINK_NAMES:
        if all_binds[k] == 0 and k != "money":
            print(f"  UNUSED link '{k}' never binds anything")
    if not bad:
        print("  nothing unreachable")

    print("\nWALLPAPER  (a message the player stops reading is worse than none)")
    total_years = len(STYLES) * len(list(seeds)) * 20
    noisy = [(k, v) for k, v in log_freq.most_common(6) if v > total_years * 0.45]
    if noisy:
        for k, v in noisy:
            print(f"  NOISY  '{k}' fires in {100*v/total_years:.0f}% of years")
    else:
        print("  no message fires in more than 45% of years")
        for k, v in log_freq.most_common(4):
            print(f"         '{k}' {100*v/total_years:.0f}%")


if __name__ == "__main__":
    main()
