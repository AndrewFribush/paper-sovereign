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


def thresholds():
    """Every hardcoded gate, and how often it actually fires.

    A threshold calibrated against a broken system dies silently when you fix it —
    the dearth trigger sat at 3.4x reference while prices were oscillating noise, and
    once the market cleared it became unreachable and the crisis stopped existing with
    nothing failing. So: measure every gate's firing rate. 0% means dead content, 100%
    means the gate is not a gate.
    """
    from game.sim import Game, GOODS, price_of, welfare

    hits = Counter()
    tot = Counter()

    def gate(name, cond, live=True):
        tot[name] += 1
        if cond:
            hits[name] += 1

    for seed in range(1, 13):
        g = Game(seed)
        g.collect()
        while not g.game_over:
            if g.crisis:
                g.choose(g.crisis.choices[0].key)
                continue
            t = g.treasury
            for k, v in dict(census=.18, army=.26, normal=.14, granary=.12,
                             land=.12, railway=.10, schools=.08).items():
                g.budget[k] = t * v
            g.end_turn()
            g.notice = []
            if not g.game_over:
                g.collect()
            live = [p for p in g.provs if p.key not in g.lost_provinces]
            ref = GOODS["grain"].ref_price
            mean_p = st.mean(price_of(p, "grain") for p in live)
            urban = sum(p.pop * p.bourgeoisie for p in live) / max(1e-6, sum(p.pop for p in live))

            gate("crisis: dearth cond", mean_p > ref * 2.0 and g.mean_welfare() < 0.78)
            gate("crisis: cholera cond", g.year >= 1654 and urban > 0.38)
            gate("crisis: sedition cond",
                 g.mean_literacy() > 0.20 and st.mean(p.unrest for p in live) > 0.16)
            gate("riot: unrest > .78", any(p.unrest > 0.78 for p in live))
            gate("diagnose: 'dear' (1.45x)", any(price_of(p, "grain") > ref * 1.45 for p in live))
            gate("ui: province red (1.6x)", any(price_of(p, "grain") > ref * 1.6 for p in live))
            gate("ui: belief stale (>12y)",
                 any((o := g.beliefs.pop.get(p.key)) and o.age(g.year) > 12 for p in live))
            gate("ui: belief ancient (>25y)",
                 any((o := g.beliefs.pop.get(p.key)) and o.age(g.year) > 25 for p in live))
            gate("sight: province invisible", any(p.bourgeoisie <= 0.15 for p in live))
            gate("nudge: consent binds 3+",
                 sum(1 for l in LINES if g.preview(l.key)[0] == "consent") >= 3)
            gate("supply: clamped high", any(
                (price_of(p, q) / GOODS[q].ref_price) ** (0.35 if q == "grain" else 0.55) > 1.6
                for p in live for q in GOOD_KEYS))
            gate("supply: clamped low", any(
                (price_of(p, q) / GOODS[q].ref_price) ** (0.35 if q == "grain" else 0.55) < 0.55
                for p in live for q in GOOD_KEYS))
            gate("price: at floor", any(
                price_of(p, q) <= GOODS[q].ref_price * GOODS[q].floor * 1.001
                for p in live for q in GOOD_KEYS))
            gate("price: at ceiling", any(
                price_of(p, q) >= GOODS[q].ref_price * GOODS[q].ceiling * 0.999
                for p in live for q in GOOD_KEYS))
            gate("welfare: below .5", any(welfare(p) < 0.5 for p in live))

    print("\nTHRESHOLDS  (0% is dead content; 100% means the gate is not a gate)")
    # Two exemptions, both principled:
    #  - a safety clamp SHOULD read 0%. It is a backstop; reaching it is the failure.
    #  - the belief-age gates are per-province display styling, and one province is
    #    never censused, so "any province is stale" is 100% by construction.
    EXEMPT_LOW = {"price: at ceiling", "price: at floor", "supply: clamped high",
                  "supply: clamped low"}
    EXEMPT_HIGH = {"ui: belief stale (>12y)", "ui: belief ancient (>25y)"}
    bad = []
    for name in sorted(tot):
        r = 100 * hits[name] / max(1, tot[name])
        flag = ""
        if r < 1.0 and name not in EXEMPT_LOW:
            flag = "  <-- DEAD"
            bad.append(name)
        elif r < 1.0:
            flag = "  (backstop, correctly never reached)"
        elif r > 97.0 and name not in EXEMPT_HIGH:
            flag = "  <-- ALWAYS TRUE"
            bad.append(name)
        elif r > 97.0:
            flag = "  (per-province styling; aggregate is 100% by construction)"
        print(f"  {r:5.1f}%  {name}{flag}")
    if not bad:
        print("\n  every gate fires sometimes and not always")
    return bad


def links():
    """Every link's trajectory across a run.

    Legibility hit its 1.0 cap by year 3 in every playstyle, which meant it stopped
    binding anything, could not gate the fiscal ladder, and made the late game look
    frozen for reasons that had nothing to do with the late game. No check caught it,
    because every check looked at peaks and outcomes rather than at trajectories.

    A link that saturates is a link that has stopped existing. A link that never rises
    is a wall the player cannot climb.
    """
    from game.sim import Game

    LINKS = ["agents", "reach", "consent", "legibility", "compliance", "substrate"]
    traj = {k: {} for k in LINKS}
    binds = Counter()
    for name, mix in STYLES.items():
        for seed in range(1, 6):
            g = Game(seed)
            g.collect()
            while not g.game_over:
                if g.crisis:
                    g.choose(g.crisis.choices[0].key)
                    continue
                for l in LINES:
                    b, _ = g.preview(l.key)
                    binds[b] += 1
                for k in LINKS:
                    # take the value on whichever line actually uses that link
                    for l in LINES:
                        if k in l.links:
                            traj[k].setdefault(g.turn, []).append(g.link_value(l.key, k))
                            break
                t = g.treasury
                for kk, v in mix.items():
                    g.budget[kk] = t * v
                g.end_turn()
                g.notice = []
                if not g.game_over:
                    g.collect()

    print("\nLINKS  (a link that saturates has stopped existing; one that never rises is a wall)")
    print(f"  {'link':12s} {'yr1':>6} {'yr5':>6} {'yr10':>6} {'yr20':>6} {'sat%':>6} {'binds%':>7}")
    total_binds = sum(binds.values())
    bad = []
    for k in LINKS:
        row = [st.median(traj[k].get(y, [0])) for y in (0, 4, 9, 19)]
        allv = [v for ys in traj[k].values() for v in ys]
        sat = 100 * sum(1 for v in allv if v > 0.97) / max(1, len(allv))
        bpc = 100 * binds[k] / max(1, total_binds)
        flag = ""
        if sat > 55:
            flag = "  <-- SATURATED"; bad.append(k)
        elif row[3] < 0.25 and row[0] < 0.25:
            flag = "  <-- NEVER RISES"; bad.append(k)
        elif bpc < 1.0:
            flag = "  <-- never binds"
        print(f"  {k:12s} {row[0]:6.2f} {row[1]:6.2f} {row[2]:6.2f} {row[3]:6.2f} "
              f"{sat:5.0f}% {bpc:6.1f}%{flag}")
    if not bad:
        print("\n  every link moves across a run and none is stuck at its cap")
    return bad


if __name__ == "__main__":
    main()
    thresholds()
    links()
