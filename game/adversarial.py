"""
Adversarial and degenerate play.  Run: .venv/bin/python -m game.adversarial

Everything tested so far has been a reasonable strategy. Players are not reasonable:
they spend nothing, they spend everything on one thing, they max every tax in year one,
they break every institution they can find, and they save and reload constantly.

Looks for crashes, NaN, impossible states, and any single line of play that dominates.
"""
from __future__ import annotations
import random
import statistics as st
import sys
import tempfile, os

from game.sim import Game, LINES, TAXES, GOOD_KEYS, GOODS, price_of, welfare

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def sane(g) -> str | None:
    """Any impossible state, described, or None."""
    if g.treasury != g.treasury or g.treasury < -1e-6 or g.treasury > 1e9:
        return f"treasury {g.treasury}"
    for p in g.provs:
        if p.pop != p.pop or p.pop < 0 or p.pop > 1e6:
            return f"{p.name} pop {p.pop}"
        if not (0.0 <= p.literacy <= 1.0):
            return f"{p.name} literacy {p.literacy}"
        if not (0.0 <= p.unrest <= 1.0):
            return f"{p.name} unrest {p.unrest}"
        if not (0.0 <= p.tax_burden <= 1.0):
            return f"{p.name} tax_burden {p.tax_burden}"
        w = welfare(p)
        if w != w or not (0.0 <= w <= 1.0):
            return f"{p.name} welfare {w}"
        for q in GOOD_KEYS:
            if p.stocks[q] != p.stocks[q] or p.stocks[q] < -1e-6:
                return f"{p.name} {q} stock {p.stocks[q]}"
            v = price_of(p, q)
            gd = GOODS[q]
            if v != v or v < gd.ref_price * gd.floor * 0.99 or v > gd.ref_price * gd.ceiling * 1.01:
                return f"{p.name} {q} price {v}"
    for k in ("clerks", "masters", "engineers", "register_quality", "credit", "army"):
        v = getattr(g, k)
        if v != v or v < 0 or v > 1e6:
            return f"{k} {v}"
    return None


def run(name, seed, act, taxes=None, overrides=(), reload_every=0):
    g = Game(seed)
    g.collect()
    if taxes:
        g.tax.update(taxes)
        g._apply_tax_burden()
    tmp = os.path.join(tempfile.gettempdir(), f"vk_adv_{seed}.json")
    turn = 0
    while not g.game_over:
        if g.crisis:
            g.choose(random.Random(seed + turn).choice(g.crisis.choices).key)
            continue
        if turn in overrides:
            for h in list(g.settlement):
                g.override(h.key)
        act(g, turn)
        if taxes:
            g.tax.update(taxes)
        g.end_turn()
        g.notice = []
        bad = sane(g)
        if bad:
            return f"{name} seed {seed} turn {turn}: {bad}"
        if reload_every and turn % reload_every == 0:
            g.save(tmp)
            g2 = Game.load(tmp)
            if g2 is None or g2.year != g.year:
                return f"{name}: reload mismatch at turn {turn}"
            g = g2
        if not g.game_over:
            g.collect()
        turn += 1
    return None


def main():
    seeds = range(1, 13)
    print("ADVERSARIAL PLAY\n")

    cases = {
        "spend nothing at all": (lambda g, t: None, None, (), 0),
        "everything on one line": (
            lambda g, t: g.budget.update({LINES[t % len(LINES)].key: g.treasury}), None, (), 0),
        "all lines, all treasury": (
            lambda g, t: g.budget.update({l.key: g.treasury for l in LINES}), None, (), 0),
        "budget beyond the treasury": (
            lambda g, t: g.budget.update({l.key: g.treasury * 5 for l in LINES}), None, (), 0),
        "every tax at maximum": (
            lambda g, t: g.budget.update({"army": g.treasury}),
            dict(excise=1.0, land=1.0, income=1.0), (), 0),
        "no tax at all": (
            lambda g, t: g.budget.update({"army": g.treasury * 0.5}),
            dict(excise=0.0, land=0.0, income=0.0), (), 0),
        "break everything in year 1": (
            lambda g, t: g.budget.update({l.key: g.treasury * 0.14 for l in LINES}),
            None, (0,), 0),
        "save and reload every year": (
            lambda g, t: g.budget.update({l.key: g.treasury * 0.14 for l in LINES}),
            None, (), 1),
        "random budget every year": (
            lambda g, t: g.budget.update(
                {l.key: g.treasury * random.Random(t * 31 + i).random() * 0.4
                 for i, l in enumerate(LINES)}), None, (), 0),
        "relieve everywhere, always": (
            lambda g, t: ([g.relieve(p.key) for p in g.provs if p.key not in g.lost_provinces],
                          g.budget.update({"granary": g.treasury})), None, (), 0),
    }

    for name, (act, taxes, ov, rl) in cases.items():
        errs = [e for s in seeds if (e := run(name, s, act, taxes, ov, rl))]
        check(name, not errs, errs[0] if errs else f"{len(list(seeds))} seeds clean")

    # no single line of play may dominate on every axis
    print("\nDOMINANCE  (an extreme should be good at one thing and bad at others)")
    res = {}
    for name, mix in {
        "all army": {"army": 1.0}, "all census": {"census": 1.0},
        "all schools": {"schools": .5, "normal": .5}, "all land": {"land": 1.0},
        "all rail": {"railway": 1.0}, "hoard": {},
    }.items():
        out = []
        for s in seeds:
            g = Game(s); g.collect()
            while not g.game_over:
                if g.crisis:
                    g.choose(g.crisis.choices[0].key); continue
                for k, v in mix.items():
                    g.budget[k] = g.treasury * v
                g.end_turn(); g.notice = []
                if not g.game_over: g.collect()
            # third instance of a count hardcoded to the map's original size; the
            # map has been fourteen provinces for hours
            out.append((g.mean_welfare(), g.mean_literacy(),
                        len(g.provs) - len(g.lost_provinces),
                        -abs(g.believed_pop() - g.true_pop()) / max(1e-6, g.true_pop()),
                        g.treasury / 500.0))
        res[name] = [st.mean(x[i] for x in out) for i in range(5)]
    doms = [a for a in res if all(a == b or all(res[a][i] >= res[b][i] for i in range(5))
                                  for b in res)]
    for n, v in res.items():
        print(f"    {n:12s} welf {v[0]*100:5.1f}  lit {v[1]*100:5.1f}  prov {v[2]:.1f}  "
              f"seen {(1+v[3])*100:5.1f}  cash {v[4]*500:5.0f}")
    check("no extreme dominates every axis", not doms, str(doms))

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS)); sys.exit(1)
    print("survived everything")


if __name__ == "__main__":
    main()
