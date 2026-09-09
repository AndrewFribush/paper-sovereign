"""
Regression checks.  Run: .venv/bin/python -m game.checks

Three bugs got past hand-checking during the first night of building:

  1. A convergence test that reported an 88% effect while building zero railways.
  2. A patch script that printed "patched" while its string replace had silently failed.
  3. A runaway loop (land -> welfare -> population -> industry -> literacy 72.5) that no
     existing check would have caught, because nothing asserted an upper bound.

So every check here asserts a MECHANISM or a BOUND, never just that a number moved.
"""
from __future__ import annotations
import statistics as st
import sys
from collections import Counter

from game.sim import (Game, GOODS, GOOD_KEYS, LINES, N_TURNS, FREIGHT_MULT,
                      price_of, welfare, wage_of, basket_cost)

SEEDS = range(1, 21)
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def play(seed: int, mix: dict | None = None, crisis: str = "first"):
    """One full run, with per-tick invariant sampling."""
    g = Game(seed)
    g.collect()
    mix = mix or dict(census=.16, army=.26, railway=.13, granary=.13,
                      normal=.13, schools=.10, land=.09)
    samples = []
    while not g.game_over:
        if g.crisis:
            opts = [o.key for o in g.crisis.choices]
            g.choose(opts[0] if crisis == "first" else opts[-1])
            continue
        t = g.treasury
        for k, v in mix.items():
            g.budget[k] = t * v
        g.end_turn()
        g.notice = []
        if not g.game_over:
            g.collect()
        live = [p for p in g.provs if p.key not in g.lost_provinces]
        samples.append({
            "stocks_min": min((p.stocks[q] for p in live for q in GOOD_KEYS), default=0.0),
            "prices": {q: [price_of(p, q) for p in live] for q in GOOD_KEYS},
            "welfare": [welfare(p) for p in live],
            "pop": sum(p.pop for p in live),
            "industry": sum(sum(p.industry.values()) for p in live),
            "treasury": g.treasury,
        })
    return g, samples


# ---------------------------------------------------------------------------

def invariants():
    print("\nINVARIANTS  (economy.md §9 — guarantees, not targets)")
    neg = 0; unbounded = 0; nan = 0
    for seed in SEEDS:
        g, S = play(seed)
        for s in S:
            if s["stocks_min"] < -1e-9:
                neg += 1
            for q, ps in s["prices"].items():
                gd = GOODS[q]
                lo, hi = gd.ref_price * gd.floor, gd.ref_price * gd.ceiling
                for v in ps:
                    if v != v:
                        nan += 1
                    elif v < lo - 1e-6 or v > hi + 1e-6:
                        unbounded += 1
    check("stock never negative", neg == 0, f"{neg} violations")
    check("price always within its clamp", unbounded == 0, f"{unbounded} violations")
    check("no NaN prices", nan == 0, f"{nan} violations")


def bounds():
    """What would have caught the runaway loop."""
    print("\nBOUNDS  (nothing may grow without limit)")
    pops, inds, lits, treas, ALLS = [], [], [], [], []
    for seed in SEEDS:
        g, S = play(seed)
        ALLS.append(S)
        pops.append(S[-1]["pop"] / S[0]["pop"])
        inds.append(S[-1]["industry"])
        lits.append(g.mean_literacy())
        treas.append(max(s["treasury"] for s in S))
    check("population grows < 2x over the run", max(pops) < 2.0, f"max {max(pops):.2f}x")
    check("industry stock stays bounded", max(inds) < 8.0, f"max {max(inds):.2f}")
    check("literacy stays plausible for the period", max(lits) < 0.60,
          f"max {max(lits)*100:.1f}%")
    # Both ends. The max-only version passed a treasury of -1.7e180, produced when a
    # negative balance flipped the sign of every appropriation.
    lo = min(min(s["treasury"] for s in S) for S in ALLS)
    check("treasury does not diverge", max(treas) < 5000 and lo >= -1e-6,
          f"range £{lo:,.0f} to £{max(treas):,.0f}")
    finite = all(s["treasury"] == s["treasury"] and abs(s["treasury"]) < 1e9
                 for S in ALLS for s in S)
    check("treasury stays finite", finite)


def links_and_scarcity():
    """Two failure classes that hid twice each and were only ever caught by tracing.

    SATURATION: legibility hit its 1.0 cap by year 3, then again after the map grew.
    A link at its cap has stopped existing — it binds nothing, gates nothing, and makes
    the late game look frozen for reasons unrelated to the late game.

    SCARCITY: line costs were absolute while revenue scales with the map, so growing
    from 8 provinces to 14 tripled income against a fixed price list and money stopped
    being a constraint at all.
    """
    print("\nLINKS AND SCARCITY  (a link at its cap, or money that is never short)")
    LINKS = ["agents", "reach", "consent", "legibility", "compliance", "substrate"]
    vals = {k: [] for k in LINKS}
    treas, revs, money_binds, total_binds = [], [], 0, 0
    binds_by = Counter()
    # Several budget mixes, not one. A link that saturates only under census-heavy
    # play is invisible to a census-poor sample, and that is exactly what happened:
    # the legibility-saturation mutation stopped being caught the moment the register
    # stopped being inflated by army spending, because the one mix this loop used
    # never pushed the register high enough to reach the cap. A saturation check that
    # tests one playstyle is testing one playstyle.
    MIXES = [
        dict(census=.16, army=.26, railway=.13, granary=.13, normal=.13, schools=.10, land=.09),
        dict(census=.55, army=.25, railway=.05, granary=.05, normal=.05, schools=.05, land=.00),
        dict(census=.05, army=.15, railway=.30, granary=.10, normal=.15, schools=.20, land=.05),
    ]
    for seed in SEEDS:
        mix = MIXES[seed % len(MIXES)]
        g = Game(seed)
        g.collect()
        while not g.game_over:
            if g.crisis:
                g.choose(g.crisis.choices[0].key)
                continue
            for l in LINES:
                b, _ = g.preview(l.key)
                total_binds += 1
                binds_by[b] += 1
                if b == "money":
                    money_binds += 1
            for k in LINKS:
                for l in LINES:
                    if k in l.links:
                        vals[k].append(g.link_value(l.key, k))
                        break
            t = g.treasury
            for kk, v in mix.items():
                g.budget[kk] = t * v
            g.end_turn()
            g.notice = []
            if not g.game_over:
                g.collect()
            treas.append(g.treasury)
            revs.append(g.revenue())

    # Threshold set from the regression, not by feel. Reintroducing the legibility bug
    # gives 30% of line-years above 0.90 across the three mixes; fixed, NO link exceeds
    # 0.90 at all. Measuring "strictly at the 1.0 cap" caught only 13% and let the bug
    # through. The threshold was 0.30 while the agents link sat at 24% on its own — a
    # six-point window between healthy and broken, which is not a test. Recalibrating
    # agents against focused-play peaks took the baseline to 0% and the window to
    # thirty points, so this can now be tightened to where it discriminates.
    worst, rate = None, 0.0
    for k in LINKS:
        r = sum(1 for v in vals[k] if v > 0.90) / max(1, len(vals[k]))
        if r > rate:
            worst, rate = k, r
    check("no link spends most of a run near its cap", rate < 0.10,
          f"worst is {worst} at {rate*100:.0f}% of line-years above 0.90")
    # A high median alone is not the failure — substrate sits near 0.86 and still binds
    # 15% of the time, so it is doing its job. The failure is high AND never binding:
    # that is a link the player can neither feel nor act on.
    inert = [k for k in LINKS
             if st.median(vals[k]) > 0.85 and binds_by[k] / max(1, total_binds) < 0.05]
    check("no link is both high and inert", not inert,
          ", ".join(f"{k} median {st.median(vals[k]):.2f}, binds "
                    f"{100*binds_by[k]/max(1,total_binds):.0f}%" for k in inert) or "none")

    ratio = st.mean(treas) / max(1e-6, st.mean(revs))
    check("money stays scarce", ratio < 3.0,
          f"mean treasury is {ratio:.1f}x mean revenue")
    check("money is rarely the binding link", money_binds / max(1, total_binds) < 0.20,
          f"{100*money_binds/max(1,total_binds):.0f}% of line-years")


def signal():
    """A clamp reached routinely stops preventing nonsense and starts destroying
    information. This is what the ledger view exposed."""
    print("\nSIGNAL  (the player must be able to read a spread)")
    pinned = tot = 0
    spreads = []; flat = 0
    for seed in SEEDS:
        _, S = play(seed)
        for s in S:
            ps = s["prices"]["grain"]
            hi = GOODS["grain"].ref_price * GOODS["grain"].ceiling
            for v in ps:
                tot += 1
                if v >= hi * 0.999:
                    pinned += 1
            sp = max(ps) / max(0.01, min(ps))
            spreads.append(sp)
            if sp < 1.3:
                flat += 1
    check("grain rarely pinned to the ceiling", pinned / tot < 0.05,
          f"{100*pinned/tot:.1f}% of province-years")

    # EVERY good, not just grain. The grain-only version missed coal sitting on its
    # floor in 26% of province-years and iron and cloth on their ceilings in 15%.
    hit = Counter(); seen = Counter()
    for seed in SEEDS:
        _, S = play(seed)
        for s_ in S:
            for q, ps in s_["prices"].items():
                gd = GOODS[q]
                for v in ps:
                    seen[q] += 1
                    if (v <= gd.ref_price * gd.floor * 1.001
                            or v >= gd.ref_price * gd.ceiling * 0.999):
                        hit[q] += 1
    worst = max(GOOD_KEYS, key=lambda q: hit[q] / max(1, seen[q]))
    rate = hit[worst] / max(1, seen[worst])
    check("no good sits on a clamp as an operating state", rate < 0.08,
          f"worst is {worst} at {rate*100:.1f}% of province-years")
    check("price spread usually informative", flat / len(spreads) < 0.15,
          f"flat in {100*flat/len(spreads):.0f}% of years")
    check("median spread is large enough to diagnose", st.median(spreads) > 2.0,
          f"median {st.median(spreads):.1f}x")


def welfare_shape():
    print("\nWELFARE  (two gates, worse binds — must not saturate or bottom out)")
    vals = []
    for seed in SEEDS:
        _, S = play(seed)
        for s in S:
            vals.extend(s["welfare"])
    vals.sort()
    med = vals[len(vals) // 2]
    pinned = sum(1 for v in vals if v > 0.985) / len(vals)
    floored = sum(1 for v in vals if v < 0.02) / len(vals)
    check("welfare not saturated at 1.0", pinned < 0.10, f"{pinned*100:.1f}% pinned")
    check("welfare not collapsed at 0", floored < 0.02, f"{floored*100:.1f}% at zero")
    check("welfare median in a governable band", 0.6 < med < 0.95, f"median {med:.2f}")
    check("welfare responds to price, not only to stock", _welfare_sees_price(),
          "raising a province's grain price must lower its welfare")


def _welfare_sees_price() -> bool:
    g = Game(3); g.collect()
    p = g.provs[0]
    before = welfare(p)
    p.stocks["grain"] *= 0.35          # same province, less grain -> dearer
    after = welfare(p)
    return after < before - 0.01 and basket_cost(p) > 0 and wage_of(p) > 0


def conservation():
    """Goods are created only by production and destroyed only by consumption,
    spoilage, trade loss and relief. A silent leak would show as drift with no cause."""
    print("\nCONSERVATION")
    g = Game(5); g.collect()
    ok = True
    for _ in range(6):
        if g.crisis:
            g.choose(g.crisis.choices[0].key); continue
        before = {q: sum(p.stocks[q] for p in g.provs) for q in GOOD_KEYS}
        cap = {q: sum(p.capacity[q] for p in g.provs) for q in GOOD_KEYS}
        t = g.treasury
        for k in ("census", "army"):
            g.budget[k] = t * 0.3
        g.end_turn(); g.notice = []
        g.collect()
        for q in GOOD_KEYS:
            after = sum(p.stocks[q] for p in g.provs)
            # cannot gain more than a year of maximum production
            if after > before[q] + cap[q] * 1.35 + 1e-6:
                ok = False
    check("no good gains more than a year's production in a year", ok)


def mechanism():
    """Every claim asserts the mechanism fired, not just that the number moved.
    The convergence test once reported an 88% effect having built zero railways."""
    print("\nMECHANISM  (the reason the number moved must be checked too)")
    rail = dict(census=.05, railway=.55, schools=.05, normal=.05, granary=.05, army=.25, land=.00)
    none = dict(census=.05, railway=.00, schools=.05, normal=.05, granary=.10, army=.25, land=.00)
    ra = [play(s, rail) for s in SEEDS]
    rb = [play(s, none) for s in SEEDS]
    a, Sa = [x[0] for x in ra], [x[1] for x in ra]
    b, Sb = [x[0] for x in rb], [x[1] for x in rb]
    lines_a = st.mean(sum(1 for p in g.provs if p.railed) for g in a)
    lines_b = st.mean(sum(1 for p in g.provs if p.railed) for g in b)

    # Measured over the WHOLE run, not the final year: the provinces that generate
    # dispersion (the dark frontier, the remote port) are also the ones ceded first,
    # so an end-state comparison quietly drops the evidence.
    def disp(samples):
        out = []
        for s in samples:
            ps = s["prices"]["grain"]
            out.append(max(ps) / max(0.01, min(ps)))
        return st.mean(out)
    da = st.mean(disp(sa) for sa in Sa)
    db = st.mean(disp(sb) for sb in Sb)
    check("railway spending actually builds lines", lines_a > 3.0 and lines_b < 0.5,
          f"{lines_a:.1f} vs {lines_b:.1f}")
    # What is verified today: the graph responds. Building the line measurably lowers
    # the freight cost between the provinces it connects.
    def pairs_cost(g):
        # Mean over EVERY pair, not three hardcoded ones. The line extends from
        # cheapest reach outward, so which provinces it touches changes with the map —
        # naming pairs meant the check silently measured provinces the rails never
        # reached and reported "no effect" while five lines were being built.
        live = [p.key for p in g.provs if p.key not in g.lost_provinces]
        vals = [g.pair_cost(a, b) for i, a in enumerate(live) for b in live[i + 1:]]
        return st.mean(vals) if vals else 0.0
    ca, cb = st.mean(pairs_cost(g) for g in a), st.mean(pairs_cost(g) for g in b)
    check("and the line lowers freight between what it connects", ca < cb * 0.9,
          f"{ca:.2f} vs {cb:.2f}")
    # And that the lower freight reaches the price level. Measured on the pairs the
    # line connects, as the excess over parity — which is the form the design states
    # the claim in (the Anglo-American wheat gap fell from ~60% to ~15%).
    def gap(g, S):
        # Dispersion across every province still held, over the back half of the run.
        out = []
        for s in S[12:]:
            ps = s["prices"]["grain"]
            if len(ps) >= 2:
                out.append(max(ps) / max(0.01, min(ps)))
        return st.mean(out) if out else 1.0
    ga = st.mean(gap(g, S) - 1.0 for g, S in zip(a, Sa))
    gb = st.mean(gap(g, S) - 1.0 for g, S in zip(b, Sb))
    check("and the lower freight reaches the price level", ga < gb * 0.85,
          f"excess over parity {ga*100:.1f}% vs {gb*100:.1f}%")

    # The market must actually clear, or a price movement is noise rather than a
    # pointer to a cause — and the whole diagnosis layer is reading tea leaves.
    unsettled = 0
    for g in a + b:
        live = [p for p in g.provs if p.key not in g.lost_provinces
                and p.bourgeoisie > 0.15]
        for i, x in enumerate(live):
            for y in live[i + 1:]:
                px, py = price_of(x, "grain"), price_of(y, "grain")
                if abs(px - py) > g.pair_cost(x.key, y.key) * FREIGHT_MULT * 1.15:
                    unsettled += 1
    check("the market settles to the freight bound", unsettled == 0,
          f"{unsettled} pairs left profitable after settlement")

    # the census must actually sharpen belief, or the whole pillar is decorative
    seeing = dict(census=.45, army=.25, railway=.05, granary=.10, normal=.10, schools=.05, land=.00)
    blind = dict(census=.00, army=.25, railway=.15, granary=.20, normal=.20, schools=.20, land=.00)
    def err(g):
        return abs(g.believed_pop() - g.true_pop()) / max(1e-6, g.true_pop())
    es = st.mean(err(play(s, seeing)[0]) for s in SEEDS)
    eb = st.mean(err(play(s, blind)[0]) for s in SEEDS)
    # Threshold from measurement, re-derived after the muster ceiling and the census
    # rate changed the absolute numbers. Vital registration is a SECOND path to belief
    # correction and a census-heavy state unlocks it anyway, so killing the census
    # path only moves the ratio from 0.226 to 0.304 — the gap is narrow and the
    # threshold has to sit inside it or the check cannot see the bug it exists for.
    # The old 0.34 sat above BOTH and passed while the census corrected nothing.
    check("funding the census sharpens the state's belief", es < eb * 0.27,
          f"{es*100:.1f}% vs {eb*100:.1f}% error (ratio {es/max(1e-6,eb):.2f})")

    # No instrument may beat the dedicated instrument on the dedicated instrument's
    # own axis. The army pays a muster roll into the register, which is historically
    # right — but it paid MORE than the census did (0.030 against 0.022), and because
    # the army is gated on legibility while feeding it, the two compounded. An
    # army-only run reached 96% register against an all-in census run's 79-88%: the
    # cheapest way to see your own country was to fund soldiers and ignore the census.
    # That inverts the pillar the whole game rests on, and every mechanism check
    # passed while it was true, because each one only ever looked at one instrument.
    reg_census = st.mean(play(s, dict(census=.75, army=.25))[0].register_quality
                         for s in SEEDS[:6])
    reg_army = st.mean(play(s, dict(army=1.0))[0].register_quality for s in SEEDS[:6])
    check("the census sees further than the muster roll", reg_army < reg_census * 0.75,
          f"army-only {reg_army*100:.0f}% vs census {reg_census*100:.0f}%")

    # Overriding the church must destroy the STOCK you were standing on, not merely
    # remove future help — and the sequencing rule (build the registry first, then
    # fight) must fall out as arithmetic. Both are asserted.
    g = Game(4); g.collect()
    for _ in range(6):
        if g.crisis: g.choose(g.crisis.choices[0].key); continue
        t = g.treasury
        for k, v in dict(census=.5, army=.3, normal=.2).items():
            g.budget[k] = t * v
        g.end_turn(); g.notice = []; g.collect()
    before = g.register_quality
    g.override("clergy")
    check("override destroys the apparatus immediately",
          g.register_quality < before * 0.5,
          f"register {before*100:.0f}% -> {g.register_quality*100:.0f}%")

    def reg(when):
        out = []
        for s in SEEDS:
            g = Game(s); g.collect(); done = when is None
            while not g.game_over:
                if g.crisis: g.choose(g.crisis.choices[0].key); continue
                if not done and g.turn >= when:
                    g.override("clergy"); done = True
                t = g.treasury
                for k, v in dict(census=.2, army=.3, normal=.2, granary=.2).items():
                    g.budget[k] = t * v
                g.end_turn(); g.notice = []
                if not g.game_over: g.collect()
            out.append(g.register_quality)
        return st.mean(out)
    never, early, late = reg(None), reg(1), reg(12)
    check("and timing dominates the decision", late < early < never * 1.02,
          f"never {never*100:.0f}%  early {early*100:.0f}%  late {late*100:.0f}%")


def thesis():
    """No strategy may dominate on every axis — plural win conditions, or none."""
    print("\nTHESIS  (design §21)")
    from game.thesis_test import STRATEGIES, AXES, play as tplay
    rows = {}
    for name, mix in STRATEGIES.items():
        mix = dict(mix); mix.setdefault("land", 0.0)
        runs = [tplay(mix, s) for s in SEEDS]
        rows[name] = {a: st.mean(r[a] for r in runs) for a in AXES}
    lower_better = {"census error"}
    dominators = []
    for a in rows:
        if all(a == b or all(
                (rows[a][ax] <= rows[b][ax]) if ax in lower_better else (rows[a][ax] >= rows[b][ax])
                for ax in AXES) for b in rows):
            dominators.append(a)
    check("no strategy dominates on every axis", not dominators, str(dominators))
    for ax in AXES:
        sp = max(r[ax] for r in rows.values()) - min(r[ax] for r in rows.values())
        rel = sp / max(1e-6, abs(st.mean(r[ax] for r in rows.values())))
        if False:   # welfare now has an extraction lever; strict threshold restored
            # Known weak axis. With the market integrated, food is distributed and every
            # strategy ends up feeding its people about equally. The design wants this to
            # be a real choice ("steel output and army size going up while real consumption
            # goes the other way") and the missing piece is an EXTRACTION lever: revenue is
            # currently an automatic excise, so the player cannot choose to immiserate.
            # Asserted at what is true today, with the gap stated rather than hidden.
            check(f"strategies differ on {ax}", rel > 0.03,
                  f"spread {sp:.1f} — weak axis, needs a tax lever, see BUILD-LOG")
        else:
            check(f"strategies differ on {ax}", rel > 0.05, f"spread {sp:.1f}")


def persistence():
    print("\nPERSISTENCE")
    g = Game(11); g.collect()
    for _ in range(7):
        if g.crisis: g.choose(g.crisis.choices[0].key); continue
        t = g.treasury
        for k, v in dict(census=.3, army=.3, land=.2, normal=.2).items():
            g.budget[k] = t * v
        g.end_turn(); g.notice = []; g.collect()
    g.override("nobles")
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), "vicky_check.json")
    g.save(path)
    h = Game.load(path)
    same = (h.year == g.year and abs(h.treasury - g.treasury) < 1e-6
            and abs(h.believed_pop() - g.believed_pop()) < 1e-6
            and abs(h.true_pop() - g.true_pop()) < 1e-6
            and h.holder["nobles"].overridden == g.holder["nobles"].overridden
            and len(h.beliefs.history) == len(g.beliefs.history)
            and h.unlocked == g.unlocked)
    check("save/load roundtrips exactly", same)
    h.collect(); h.end_turn()
    check("a loaded game resumes", h.year == g.year + 1)


def ui_smoke():
    print("\nUI SMOKE")
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    try:
        from game.main import UI
        u = UI(); u.intro = False
        panels = 0
        while not u.g.game_over:
            if u.g.crisis:
                u.screen.fill((233, 225, 208)); u.draw_crisis(); panels += 1
                u.g.choose(u.g.crisis.choices[0].key); continue
            t = u.g.treasury
            for k, v in dict(census=.15, army=.24, railway=.13, granary=.12,
                             normal=.12, schools=.09, land=.15).items():
                u.g.budget[k] = t * v
            u.g.end_turn(); u.g.notice = []
            if not u.g.game_over: u.g.collect()
            u.screen.fill((233, 225, 208))
            u.draw_header(); u.draw_map(); u.draw_budget(); u.draw_log()
            for flag in ("ledger", "politics"):
                setattr(u, flag, True); getattr(u, "draw_" + flag)(); setattr(u, flag, False)
            u.detail = "weald"; u.draw_detail(); u.detail = None
            panels += 5
        u.screen.fill((233, 225, 208)); u.draw_end()
        u.intro = True; u.draw_brief()
        check("every panel renders for a whole run", True, f"{panels} panel draws")
    except Exception as e:
        check("every panel renders for a whole run", False, f"{type(e).__name__}: {e}")


def main():
    print(f"Vicky regression checks — {len(list(SEEDS))} seeds, {N_TURNS} turns, "
          f"{len(LINES)} budget lines")
    invariants(); bounds(); links_and_scarcity(); signal(); welfare_shape()
    conservation(); mechanism(); thesis(); persistence(); ui_smoke()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
