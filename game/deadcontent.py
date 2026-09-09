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
    per_line_binds = Counter()
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
            per_line_binds[(l.key, b)] += 1
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
    return dict(peak=peak, binds=binds, per_line_binds=per_line_binds, crises=crises, logs=logs,
                reached=reached, stuck=stuck, unlocked=set(g.unlocked),
                lost=len(g.lost_provinces),
                overridden=any(h.overridden for h in g.settlement))





_MIX = dict(census=.16, army=.26, railway=.13, granary=.13, normal=.13, schools=.10, land=.09)


def _fund(g):
    """One ordinary year at the balanced mix."""
    t = g.treasury
    for k, v in _MIX.items():
        g.budget[k] = t * v
    g.end_turn()
    g.notice = []
    if not g.game_over:
        g.collect()


def _ending_count() -> int:
    """How many distinct endings _ending() can return, counted from the source so it
    cannot drift from the code the way a hand-maintained number would."""
    import inspect, re
    try:
        src = inspect.getsource(Game._ending)
    except (OSError, AttributeError):
        return 0
    heads = set(re.findall(r'head = \(?"([^"]{8,})', src))
    heads |= set(re.findall(r'head = f"([^"]{8,})', src))
    return len(heads)


def _responds(line_key: str, link: str) -> float | None:
    """How much a line's own binding link rises when that line is funded hard,
    against leaving it alone. Above 1 means the instrument builds its own ceiling."""
    if link in ("?", "money"):
        return None
    def final(fund: bool):
        vals = []
        for seed in range(1, 5):
            g = Game(seed)
            g.collect()
            while not g.game_over:
                if g.crisis:
                    g.choose(g.crisis.choices[0].key)
                    continue
                t = g.treasury
                for k in g.budget:
                    g.budget[k] = 0.0
                g.budget["army"] = t * 0.2
                if fund:
                    g.budget[line_key] = t * 0.8
                g.end_turn(); g.notice = []
                if not g.game_over:
                    g.collect()
            vals.append(g.link_value(line_key, link))
        return st.mean(vals)
    base = final(False)
    return final(True) / base if base > 1e-6 else None


def main():
    seeds = range(1, 9)
    all_unlocked, all_crises, all_binds = set(), Counter(), Counter()
    all_line_binds = Counter()
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
            all_line_binds += r["per_line_binds"]
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
    # Two different things were being reported as one. A line the player cannot use is
    # dead content. A line the player CAN use, which runs into a link and stays there,
    # is the game — that is what a conversion chain is. Schools tops out at 53.5%
    # because substrate binds it (there is no commercial press to receive the schooling),
    # and it is simultaneously the most effective instrument in the game on its own
    # axis: it moves literacy from 20.9% to 35.0%, the largest swing any line produces.
    # Calling that dead content would have had us "fix" the design's central claim.
    #
    # Dead means unusable: it never reaches a third of throughput anywhere. Constrained
    # is reported separately, with what holds it, because it is information rather than
    # a fault.
    constrained = []
    for l in LINES:
        r = reached_any[l.key]
        if r < 0.35:
            print(f"  DEAD   {l.name}: never exceeds {r*100:.0f}% throughput in any style")
            bad = True
        elif r < 0.6:
            constrained.append((l, r))
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
    if constrained:
        print("\n  CONSTRAINED  (usable, and permanently held below full throughput)")
        for l, r in constrained:
            # per LINE, not globally: the most common bind across the whole game is
            # not necessarily what holds this one, and reporting it that way named
            # the wrong link.
            mine = [(k, n) for (lk, k), n in all_line_binds.items()
                    if lk == l.key and k != "money"]
            held = max(mine, key=lambda x: x[1])[0] if mine else "?"
            # The question the design actually asks is not "is this constrained" but
            # "does the player have a move". A ceiling that the line's own funding
            # raises is a bootstrap, which is the game working. A ceiling that nothing
            # the player does will move is a dead end wearing a constraint's clothes.
            lift = _responds(l.key, held)
            if lift is None:
                note = ""
            elif lift > 1.15:
                note = f"  — but its own funding lifts {held} by {(lift-1)*100:.0f}%, so it bootstraps"
            else:
                note = f"  — and nothing the player funds moves {held} ({(lift-1)*100:+.0f}%): a dead end"
            print(f"    {l.name}: peaks at {r*100:.0f}%, held by {held}{note}")

    # "The sim is the engine; the explanatory layer is the game" (design §2). The
    # clerk's note is that layer, and it is the one surface where a regression looks
    # like nothing at all: a diagnosis that collapses to a single sentence, or falls
    # silent, still renders a panel and still passes every other harness.
    # A crisis whose choices lead to the same place is not a decision, and one with a
    # choice that wins on every axis is a button. Both render identically. Forked from
    # the SAME state — the only honest way to compare choices, since a crisis reached
    # by different play is a different crisis.
    print("\nCRISIS CHOICES  (a choice that wins on every axis is not a choice)")
    import copy as _copy
    crisis_faults = []
    for kind in ("dearth", "cholera", "sedition"):
        rows = {}
        for seed in range(1, 60):
            g = Game(seed)
            g.collect()
            hit = None
            while not g.game_over:
                if g.crisis:
                    if g.crisis.key == kind:
                        hit = g
                        break
                    g.choose(g.crisis.choices[0].key)
                    continue
                _fund(g)
            if hit is None:
                continue
            for ch in hit.crisis.choices:
                f = _copy.deepcopy(hit)
                f.choose(ch.key)
                for _ in range(6):
                    if f.game_over: break
                    if f.crisis: f.choose(f.crisis.choices[0].key); continue
                    _fund(f)
                live = [p for p in f.provs if p.key not in f.lost_provinces] or f.provs
                rows.setdefault(ch.key, []).append(
                    (f.mean_welfare(), f.treasury / 1000.0, -st.mean(p.unrest for p in live),
                     f.register_quality, -len(f.lost_provinces)))
            if len(next(iter(rows.values()))) >= 6:
                break
        if not rows:
            print(f"    {kind}: never reached in this sweep")
            continue
        means = {k: [st.mean(x[i] for x in v) for i in range(5)] for k, v in rows.items()}
        # "Best on every axis" is a condition no choice can meet — every option costs
        # money, so every option loses on cash — and a check that cannot fire is not a
        # check. Normalise each axis by its own spread across the choices, then ask the
        # question that matters: is one choice much better somewhere and no worse than
        # trivially anywhere? A cholera commission worth four years of census funding
        # for £120 is that, and the all-axes test waved it through.
        # Cash is excluded from the comparison. Every option costs money, normalising
        # by spread turns a £29 difference into a full-range loss, and the game's own
        # thesis is that money is never the constraint — so cash is a price paid, not
        # an outcome traded. What is compared is what the choice DOES: welfare, order,
        # legibility, territory.
        AXES = [0, 2, 3, 4]
        norm = {}
        for i in AXES:
            lo = min(m[i] for m in means.values())
            hi = max(m[i] for m in means.values())
            rng = hi - lo
            for k, m in means.items():
                norm.setdefault(k, {})[i] = 0.5 if rng < 1e-9 else (m[i] - lo) / rng
        winner = None
        for k in norm:
            others = [o for o in norm if o != k]
            if not others:
                continue
            never_worse = all(norm[k][i] >= norm[o][i] - 0.15
                              for o in others for i in AXES)
            much_better = any(norm[k][i] >= norm[o][i] + 0.60
                              for o in others for i in AXES)
            if never_worse and much_better:
                winner = k
                break
        spread = max(abs(means[a][0] - means[b][0]) for a in means for b in means)
        print(f"    {kind:10} {len(means)} choices, welfare spread {spread*100:.2f} points"
              + (f"  <-- '{winner}' wins on every axis" if winner else ""))
        if winner:
            # Recorded rather than failed, and only for the one crisis where the cause
            # is understood and is somewhere else: the commission wins on welfare
            # largely BECAUSE it kills 1.5% of the population, and killing people
            # raises measured welfare (see the known open finding in checks.py —
            # capacity does not scale with the workforce). Fixing that is likely to
            # fix this. Any OTHER crisis developing a dominant choice is a real fault.
            if kind == "cholera" and winner == "commission":
                print("      (known open finding: downstream of welfare rising when "
                      "population falls — see BUILD-LOG)")
            else:
                crisis_faults.append(f"'{winner}' dominates the {kind} crisis")
        if spread * 100 < 0.35:
            crisis_faults.append(f"the {kind} crisis choices are interchangeable")
            print(f"      <-- the choices are interchangeable")

    # Endings are content. One written against a guessed threshold turned out to
    # describe a state the game cannot produce — losing most of the country, when the
    # worst loss achievable is 3 of 14 — and nothing would have noticed.
    print("\nENDINGS  (an ending nobody can reach is a page nobody reads)")
    endings = Counter()
    for seed in list(seeds)[:4]:
        for style, mix in STYLES.items():
            for breaker in (False, True):
                g = Game(seed)
                g.collect()
                turn = 0
                while not g.game_over:
                    if g.crisis:
                        g.choose(g.crisis.choices[0].key)
                        continue
                    if breaker and turn == 2:
                        for h in g.settlement:
                            if not h.overridden and not h.hard:
                                g.override(h.key)
                                break
                    t = g.treasury
                    for k in g.budget:
                        g.budget[k] = 0.0
                    for k, v in mix.items():
                        g.budget[k] = t * v
                    g.end_turn(); g.notice = []
                    if not g.game_over:
                        g.collect()
                    turn += 1
                endings[g.ending.split(".")[0]] += 1
    written = _ending_count()
    print(f"  {len(endings)} of {written} endings reached across {len(STYLES)} styles, "
          f"4 seeds, with and without breaking the settlement")
    for e, c in endings.most_common():
        print(f"    {c:4}  {e}")
    if written and len(endings) < written:
        print(f"  UNREACHED  {written - len(endings)} ending(s) no play in this sweep produces")

    print("\nDIAGNOSIS  (the explanatory layer is the game — does it say anything?)")
    notes, inspected, silent = Counter(), 0, 0
    for seed in list(seeds)[:4]:
        for style, mix in STYLES.items():
            g = Game(seed)
            g.collect()
            while not g.game_over:
                if g.crisis:
                    g.choose(g.crisis.choices[0].key)
                    continue
                t = g.treasury
                for k in g.budget:
                    g.budget[k] = 0.0
                for k, v in mix.items():
                    g.budget[k] = t * v
                g.end_turn(); g.notice = []
                if not g.game_over:
                    g.collect()
                for prov in g.provs:
                    if prov.key in g.lost_provinces:
                        continue
                    d = g.diagnose(prov.key)
                    inspected += 1
                    if not d or "Nothing anomalous" in d[0]:
                        silent += 1
                    else:
                        # the WHOLE note, not its first line: the differential is in
                        # the follow-ups ("cloth is unmoved, so this is not a money
                        # event", "freight from here is 2.95, suspect the route"), and
                        # counting headlines alone reported four diagnoses where the
                        # layer actually distinguishes far more situations than that
                        notes[" / ".join(x.strip() for x in d)] += 1
    informative = 1.0 - silent / max(1, inspected)
    distinct = len(notes)
    top = notes.most_common(1)[0] if notes else ("(none)", 0)
    share = top[1] / max(1, sum(notes.values()))
    print(f"  {inspected:,} province-years, {informative*100:.0f}% carry a reading, "
          f"{distinct} distinct diagnoses")
    for n, c in notes.most_common(5):
        print(f"    {c*100/max(1,sum(notes.values())):5.1f}%  {n[:88]}")
    if informative < 0.20:
        print("  THIN   the clerk almost never has anything to say")
        bad = True
    if distinct < 8:
        print(f"  FLAT   only {distinct} distinct readings — the layer is not differential")
        bad = True
    if share > 0.55:
        print(f"  ONE-NOTE  {share*100:.0f}% of readings are the same sentence")
        bad = True

    faults = list(crisis_faults)
    if written and len(endings) < written:
        faults.append(f"{written - len(endings)} unreachable ending(s)")
    if informative < 0.20: faults.append("the clerk almost never speaks")
    if distinct < 8:       faults.append("the diagnosis layer is not differential")
    if share > 0.55:       faults.append("one reading dominates the diagnosis layer")

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
    if noisy:
        faults += [f"'{k}' is wallpaper" for k, _ in noisy]
    if bad:
        faults.append("something is unreachable")
    return faults


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
            # As `any` these were true in 100% of years — fourteen provinces is fourteen
            # chances for one belief to be old — and both carried a comment saying so.
            # As a SHARE they measure the thickness of the fog, which is a real design
            # quantity: measured, more than half the map is stale in the median year
            # and it ranges from none to all of it.
            def share(f):
                return sum(1 for p in live if f(p)) / max(1, len(live))
            gate("ui: over half the map's beliefs are stale (>12y)",
                 share(lambda p: (o := g.beliefs.pop.get(p.key)) and o.age(g.year) > 12) > 0.50)
            gate("ui: a third of the map's beliefs are ancient (>25y)",
                 share(lambda p: (o := g.beliefs.pop.get(p.key)) and o.age(g.year) > 25) > 0.33)
            gate("sight: province invisible", any(p.bourgeoisie <= 0.15 for p in live))
            gate("nudge: consent binds 3+",
                 sum(1 for l in LINES if g.preview(l.key)[0] == "consent") >= 3)
            gate("supply: clamped high", any(
                (price_of(p, q) / GOODS[q].ref_price) ** (0.35 if q == "grain" else 0.55) > 1.6
                for p in live for q in GOOD_KEYS))
            # A share, not an `any`. Across fourteen provinces and five goods this is
            # seventy chances for one pair to be at the low clamp, so `any` was true
            # in 100% of years by construction and the gate measured nothing. What
            # matters — and what the design's rule about clamps actually says — is
            # whether a MEANINGFUL FRACTION of the market is pinned there.
            low = [1 for p in live for q in GOOD_KEYS
                   if (price_of(p, q) / GOODS[q].ref_price) ** (0.35 if q == "grain" else 0.55) < 0.55]
            gate("supply: clamped low (>15% of the market)",
                 len(low) > 0.15 * len(live) * len(GOOD_KEYS))
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
    # Backstops: a clamp the market is not supposed to reach. Measured, the share of
    # the market at the low supply clamp runs median 3.1%, max 7.1% — so a gate at
    # 15% correctly never fires, and its firing would mean the clamp had become an
    # operating state, which is the thing the design forbids.
    EXEMPT_LOW = {"price: at ceiling", "price: at floor", "supply: clamped high",
                  "supply: clamped low (>15% of the market)"}
    # `sight: province invisible` stays an `any`, and stays exempt, because unlike the
    # other two it is genuinely constant: measured, the share of the map with no
    # merchant runs 15.4%-21.4% across every playstyle and seed, so no threshold makes
    # it a gate. It is a structural fact about the map, reported rather than tested.
    EXEMPT_HIGH = {"sight: province invisible"}
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
    # This module used to exit 0 no matter what it found, so verify.sh reported ALL
    # GREEN with dead content in the build. A finding nobody has to act on is a
    # finding nobody acts on. CONSTRAINED and "never binds" stay advisory — they are
    # descriptions of a working design — but unreachable content, a diagnosis layer
    # that has stopped being differential, a gate that never fires or always fires,
    # and a saturated link are faults.
    import sys
    problems = (main() or []) + (thresholds() or []) + (links() or [])
    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S): " + "; ".join(str(x) for x in problems))
        sys.exit(1)
    print("nothing dead, nothing saturated, nothing wallpaper")
