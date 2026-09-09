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

from game.sim import (Game, GOODS, GOOD_KEYS, LINES, N_TURNS,
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
    pops, inds, lits, treas = [], [], [], []
    for seed in SEEDS:
        g, S = play(seed)
        pops.append(S[-1]["pop"] / S[0]["pop"])
        inds.append(S[-1]["industry"])
        lits.append(g.mean_literacy())
        treas.append(max(s["treasury"] for s in S))
    check("population grows < 2x over the run", max(pops) < 2.0, f"max {max(pops):.2f}x")
    check("industry stock stays bounded", max(inds) < 8.0, f"max {max(inds):.2f}")
    check("literacy stays plausible for the period", max(lits) < 0.60,
          f"max {max(lits)*100:.1f}%")
    check("treasury does not diverge", max(treas) < 5000, f"max £{max(treas):,.0f}")


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
        ps = [("cap", "ironby"), ("cap", "blackm"), ("cap", "hollin")]
        return st.mean(g.pair_cost(x, y) for x, y in ps
                       if x not in g.lost_provinces and y not in g.lost_provinces)
    ca, cb = st.mean(pairs_cost(g) for g in a), st.mean(pairs_cost(g) for g in b)
    check("and the line lowers freight between what it connects", ca < cb * 0.9,
          f"{ca:.2f} vs {cb:.2f}")
    # And that the lower freight reaches the price level. Measured on the pairs the
    # line connects, as the excess over parity — which is the form the design states
    # the claim in (the Anglo-American wheat gap fell from ~60% to ~15%).
    def gap(g, S):
        out = []
        for s in S[12:]:
            for x, y in (("cap", "ironby"), ("cap", "blackm"), ("cap", "hollin")):
                if x in g.lost_provinces or y in g.lost_provinces:
                    continue
                px, py = price_of(g.by_key[x], "grain"), price_of(g.by_key[y], "grain")
                out.append(max(px, py) / max(0.01, min(px, py)))
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
                if abs(px - py) > g.pair_cost(x.key, y.key) * 2.6 * 1.15:
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
    check("funding the census sharpens the state's belief", es < eb * 0.6,
          f"{es*100:.1f}% vs {eb*100:.1f}% error")

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
    invariants(); bounds(); signal(); welfare_shape()
    conservation(); mechanism(); thesis(); persistence(); ui_smoke()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
