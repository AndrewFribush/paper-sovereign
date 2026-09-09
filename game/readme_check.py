"""Check that the README's factual claims are still true.

The README made five specific claims about the game, and by this morning every one
of them was wrong: the initial belief error had moved from 12% to 17%, the province
you lose first had changed, the guns-only ending had changed, the railway's effect
on price dispersion had changed by an order of magnitude, and the schools example
named a friction message the game no longer produces. Nothing had broken — the game
had improved, and the README had not been re-measured since.

Documentation nobody checks is documentation that lies. Each claim below names the
exact sentence in the README *and* the measurement behind it, so the two cannot
drift apart silently: if the prose changes the anchor goes missing, and if the game
changes the number stops matching.

    python -m game.readme_check
"""
import os, statistics as st, sys

from game.sim import Game, GOODS, N_TURNS, price_of

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
SEEDS = range(1, 9)
FAILS: list[str] = []


def check(name, anchor_ok, value_ok, detail=""):
    """Two halves, reported apart. "The README no longer says this" and "the game no
    longer does this" need different fixes, and a single boolean cannot tell you which
    one you are looking at."""
    ok = anchor_ok and value_ok
    if ok:
        note = detail
    elif not anchor_ok and not value_ok:
        note = f"the README no longer says this AND the number moved ({detail})"
    elif not anchor_ok:
        note = "the README no longer says this — if the prose changed, re-measure and update both"
    else:
        note = f"the game no longer does this: {detail}"
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {note}" if note else ""))
    if not ok:
        FAILS.append(name)


def play(seed, mix):
    """Answering a crisis does not end the year, so it must not consume an iteration
    here either — counting it as a turn cut runs short and moved every number this
    file measures away from the ones the README was written against."""
    g = Game(seed)
    g.collect()
    guard = 0
    while not g.game_over and guard < 400:
        guard += 1
        if g.crisis:
            g.choose(g.crisis.choices[0].key)
            continue
        t = g.treasury
        for k in g.budget:
            g.budget[k] = 0.0
        for k, v in mix.items():
            g.budget[k] = t * v
        g.end_turn()
        g.notice = []
        if not g.game_over:
            g.collect()
    return g


def main():
    print("DOCS  (are the claims in README.md and docs/ still true?)")
    text = open(README).read()

    def anchored(fragment):
        return fragment in text

    # 1. the initial belief error
    err = st.mean(abs(g.believed_pop() - g.true_pop()) / g.true_pop()
                  for g in (Game(s) for s in SEEDS) if not g.collect())
    # Tolerances are tight on purpose. A band wide enough to be comfortable is a band
    # wide enough to hide the drift it exists to catch — this file passed at 3.40x
    # against a README saying 3.98x until the loop below was fixed.
    claim = "you are wrong by about 16%"
    check("the stated initial belief error", anchored(claim), 0.150 <= err <= 0.175,
          f"measured {err*100:.1f}%, README says 16%")

    # 2. which province is lost first
    firsts = {}
    mix = dict(census=.16, army=.26, railway=.13, granary=.13, normal=.13, schools=.10, land=.09)
    for s in SEEDS:
        g = Game(s); g.collect(); first = None
        for _ in range(N_TURNS):
            if g.game_over: break
            if g.crisis: g.choose(g.crisis.choices[0].key); continue
            t = g.treasury
            for k in g.budget: g.budget[k] = 0.0
            for k, v in mix.items(): g.budget[k] = t * v
            g.end_turn(); g.notice = []
            if not g.game_over: g.collect()
            if g.lost_provinces and first is None:
                first = g.lost_provinces[0]
        if first: firsts[first] = firsts.get(first, 0) + 1
    top = max(firsts, key=firsts.get) if firsts else None
    name = Game(1).by_key[top].name if top else "none"
    check("the province the README says you lose first",
          anchored("**Highfell** is the one you lose first"), name == "Highfell",
          f"measured {name} in {firsts.get(top, 0)} of {len(list(SEEDS))} seeds")

    # 3. the three provinces that never report
    g = Game(7); g.collect()
    silent = sorted(p.name for p in g.provs if not g.beliefs.get_price(p.key, "grain"))
    check("the three provinces that report nothing",
          anchored("Marshend, Cauldfell, Highfell"),
          silent == ["Cauldfell", "Highfell", "Marshend"],
          ", ".join(silent))

    # 4. guns-only
    runs = [play(s, {"army": 1.0}) for s in SEEDS]
    held = min(len(g.provs) - len(g.lost_provinces) for g in runs)
    cash = st.mean(g.treasury for g in runs)
    lit = st.mean(g.mean_literacy() for g in runs)
    check("guns-only keeps the whole country and little else",
          anchored("fourteen provinces and ends broke"),
          held == 14 and abs(cash - 198) < 60 and abs(lit - 0.209) < 0.02,
          f"holds {held}, £{cash:,.0f}, literacy {lit*100:.1f}%")

    # 5. the railway's effect on price dispersion
    def spread(mix_):
        out = []
        for s in SEEDS:
            g = play(s, mix_)
            live = [p for p in g.provs if p.key not in g.lost_provinces]
            ps = [price_of(p, "grain") for p in live]
            out.append(max(ps) / max(0.01, min(ps)))
        return st.mean(out)
    with_rail = spread({"railway": .7, "army": .3})
    without = spread({"granary": .7, "army": .3})
    check("the railway closes the price spread by the stated amount",
          anchored("from 3.76x to 1.67x"),
          abs(without - 3.76) < 0.20 and abs(with_rail - 1.67) < 0.12,
          f"measured {without:.2f}x -> {with_rail:.2f}x")

    # 6. the schools example, message and number
    g = Game(7); g.collect()
    for k in g.budget:
        g.budget[k] = 0.0
    g.budget["schools"] = 60.0
    g.end_turn()
    r = next((x for x in g.results if x.line == "schools"), None)
    check("the schools example spends what the README says, for the stated reason",
          anchored("£60 for schools in year one and £24 gets spent")
          and anchored("there is no press in the province to print\n  a primer"),
          r is not None and abs(r.spent - 24) <= 2
          and "no press in the province" in (r.friction or ""),
          f"£{r.spent:.0f} spent — {r.friction}" if r else "no result")

    # docs/economy.md's central claim is that the supply response holds the market at
    # its equilibrium. The number saying where it holds is therefore the one number in
    # that document that must not drift, and it had: the paragraph said 13-15 against
    # a reference of 10, from before the elasticities were derived. It is 10.7.
    econ = open(os.path.join(os.path.dirname(README), "docs", "economy.md")).read()
    covers, prices = [], []
    mix = dict(census=.16, army=.26, railway=.13, granary=.13, normal=.13, schools=.10, land=.09)
    for s in SEEDS:
        g = Game(s); g.collect()
        while not g.game_over:
            if g.crisis: g.choose(g.crisis.choices[0].key); continue
            t = g.treasury
            for k, v in mix.items(): g.budget[k] = t * v
            g.end_turn(); g.notice = []
            if not g.game_over: g.collect()
            live = [p for p in g.provs if p.key not in g.lost_provinces]
            covers.append(st.mean(p.stocks["grain"] / max(1e-6, p.consumption("grain"))
                                  for p in live) / GOODS["grain"].target_cover)
            prices.append(st.mean(price_of(p, "grain") for p in live))
    med_c, med_p = st.median(covers), st.median(prices)
    check("the market still settles where economy.md says it settles",
          "cover sits at **1.01 of target**" in econ and "**10.7 against a" in econ,
          abs(med_c - 1.01) < 0.06 and abs(med_p - 10.7) < 0.8,
          f"median cover {med_c:.2f} of target, median price {med_p:.1f}")

    # docs/engine-choice.md rests its whole recommendation on one measured number.
    # Absolute times are machine-dependent and have risen 35% as the simulation grew,
    # so asserting those would fail on someone else's laptop for no reason. The
    # exponent is the finding, and the exponent does not care what machine it is on.
    from game.bench import measure_exponent
    exponent = measure_exponent()
    engine = open(os.path.join(os.path.dirname(README), "docs", "engine-choice.md")).read()
    check("the engine argument's exponent still holds",
          "O(n^2.0" in engine, 1.9 <= exponent <= 2.25,
          f"measured O(n^{exponent:.2f}); the port advice rests on this being ~2")

    # The BUILD-LOG's summary table states how many assertions each harness makes,
    # and that table had drifted within an hour of being written. Count them.
    import subprocess
    log = open(os.path.join(os.path.dirname(README), "docs", "BUILD-LOG.md")).read()
    counted = {}
    for mod in ("checks", "inputs"):
        # verify.sh has already run these and exports what each asserted; re-running
        # them here cost sixteen seconds on every verification for no new information.
        env = os.environ.get(f"VICKY_COUNT_{mod}")
        if env and env.isdigit():
            counted[mod] = int(env)
            continue
        r = subprocess.run([sys.executable, "-m", f"game.{mod}"],
                           capture_output=True, text=True)
        counted[mod] = sum(1 for l in r.stdout.split("\n")
                           if l.startswith("  PASS") or l.startswith("  FAIL"))
    stated_ok = (f"{counted['checks']} assertions over 20 seeds" in log
                 and f"{counted['inputs']} assertions that the controls" in log)
    check("the BUILD-LOG states the right number of assertions",
          True, stated_ok,
          f"checks makes {counted['checks']}, inputs makes {counted['inputs']}"
          + ("" if stated_ok else " — the summary table says otherwise"))

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        print("The README says something the game no longer does. Re-measure, then edit both.")
        return 1
    print("every number in the docs is one the game still produces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
