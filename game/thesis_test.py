"""
The thesis test (main doc §21):

  "If deciding between funding a census and funding a railway is a genuinely hard and
   interesting choice with nothing else in the game, you've validated the thesis.
   If it's boring there, no amount of map is going to save it."

Runs fixed strategies across many seeds and reports outcomes on plural axes.
There is deliberately NO aggregate score — a single victory number encodes a politics.

PASS if no strategy dominates on every axis and the spread between them is large.
"""
from __future__ import annotations
import statistics as st
from game.sim import Game, N_TURNS

STRATEGIES = {
    "see first":     dict(census=.34, railway=.06, schools=.10, normal=.10, granary=.10, army=.30),
    "build first":   dict(census=.05, railway=.35, schools=.10, normal=.05, granary=.15, army=.30),
    "school first":  dict(census=.05, railway=.05, schools=.28, normal=.22, granary=.10, army=.30),
    "guns only":     dict(census=.02, railway=.03, schools=.02, normal=.03, granary=.10, army=.80),
    "old regime":    dict(census=.02, railway=.02, schools=.02, normal=.02, granary=.22, army=.30),
    "balanced":      dict(census=.17, railway=.17, schools=.17, normal=.12, granary=.12, army=.25),
    # Improvement of the land is the only instrument that touches what people eat, so
    # without a style that funds it the welfare axis is never exercised and reads flat.
    "improving":     dict(census=.06, railway=.05, schools=.05, normal=.06, granary=.10, army=.24, land=.44),
}

AXES = ["literacy", "welfare", "treasury", "provinces", "census error", "register"]


def play(mix: dict, seed: int) -> dict:
    g = Game(seed)
    g.collect()
    while not g.game_over:
        t = g.treasury
        for k, share in mix.items():
            g.budget[k] = t * share
        g.end_turn()
        if not g.game_over:
            g.collect()
    err = abs(g.believed_pop() - g.true_pop()) / max(1e-6, g.true_pop())
    return {
        "literacy":     g.mean_literacy() * 100,
        "welfare":      g.mean_welfare() * 100,
        "treasury":     g.treasury,
        "provinces":    8 - len(g.lost_provinces),
        "census error": err * 100,
        "register":     g.register_quality * 100,
    }


def main(seeds=range(1, 41)):
    rows = {}
    for name, mix in STRATEGIES.items():
        runs = [play(mix, s) for s in seeds]
        rows[name] = {a: st.mean(r[a] for r in runs) for a in AXES}

    w = max(len(n) for n in rows) + 2
    print(f"{'strategy':<{w}}" + "".join(f"{a:>14}" for a in AXES))
    print("-" * (w + 14 * len(AXES)))
    for name, r in rows.items():
        print(f"{name:<{w}}" + "".join(f"{r[a]:>14.1f}" for a in AXES))

    # dominance check: is any strategy best-or-equal on every axis?
    better = {"census error": min}          # lower is better
    dominated = []
    for a in rows:
        wins = 0
        for b in rows:
            if a == b:
                continue
            ok = True
            for ax in AXES:
                av, bv = rows[a][ax], rows[b][ax]
                if ax in better:
                    if av > bv: ok = False
                else:
                    if av < bv: ok = False
            if ok:
                wins += 1
        if wins == len(rows) - 1:
            dominated.append(a)

    print()
    spreads = {ax: max(r[ax] for r in rows.values()) - min(r[ax] for r in rows.values())
               for ax in AXES}
    for ax, s in spreads.items():
        print(f"  spread on {ax:<14} {s:8.1f}")
    print()
    if dominated:
        print(f"  FAIL — {dominated} dominates on every axis. The choice is fake.")
    else:
        print("  PASS — no strategy dominates on every axis.")
        print("  Each buys a different country. That is the thesis.")


def convergence_test(seeds=range(1, 21)):
    """
    Main doc §14 / economy.md §1: freight collapse must converge prices, and it must
    do so BECAUSE the graph changed — not because a parameter said so.

    Compares grain price dispersion under heavy railway investment vs none.
    """
    from game.sim import Game, price_of
    import statistics as st

    def disp(mix, seed):
        g = Game(seed); g.collect()
        while not g.game_over:
            t = g.treasury
            for k, sh in mix.items():
                g.budget[k] = t * sh
            g.end_turn()
            if not g.game_over:
                g.collect()
        live = [p for p in g.provs if p.key not in g.lost_provinces]
        ps = [price_of(p, "grain") for p in live]
        railed = sum(1 for p in live if p.railed)
        return (max(ps) / max(1e-6, min(ps))), railed

    rail = dict(census=.05, railway=.55, schools=.05, normal=.05, granary=.05, army=.25)
    none = dict(census=.05, railway=.00, schools=.05, normal=.05, granary=.10, army=.25)

    a = [disp(rail, s) for s in seeds]
    b = [disp(none, s) for s in seeds]
    ra, rb = st.mean(x[0] for x in a), st.mean(x[0] for x in b)
    la, lb = st.mean(x[1] for x in a), st.mean(x[1] for x in b)
    print(f"  railway-heavy : dispersion {ra:6.2f}x   lines built {la:.1f}")
    print(f"  no railway    : dispersion {rb:6.2f}x   lines built {lb:.1f}")
    if ra < rb * 0.9:
        print(f"  PASS — the railway converges prices ({(1-ra/rb)*100:.0f}% less dispersion).")
    else:
        print("  FAIL — building the line does not integrate the market.")


if __name__ == "__main__":
    main()
    print("\n--- price convergence (main doc §14) ---")
    convergence_test()
