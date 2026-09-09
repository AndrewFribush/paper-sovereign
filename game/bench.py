"""
Scaling benchmark.  Run: .venv/bin/python -m game.bench

`engine-choice.md` argues pygame/CPython is wrong for the shipped game. That argument
should be a number, not a claim. This builds synthetic worlds of increasing size and
times a tick, so the port decision rests on measurement.

The design's target is ~3000 provinces on a daily or weekly tick.
"""
from __future__ import annotations
import random, time, statistics as st

import game.sim as S
from game.sim import Game, GOODS, GOOD_KEYS, Province
from game.graph import Network


def synth(n: int, seed: int = 1):
    """A world of n provinces on a ring-plus-chords graph, sized like the real one."""
    rng = random.Random(seed)
    provs = []
    for i in range(n):
        p = Province(f"p{i}", f"P{i}", i % 40, i // 40,
                     pop=rng.uniform(4, 26), literacy=rng.uniform(0.05, 0.35),
                     base_freight=rng.uniform(0.15, 2.6), water=rng.random() < 0.3,
                     bourgeoisie=rng.uniform(0.03, 0.9),
                     capacity={g: rng.uniform(0.5, 30) for g in GOOD_KEYS})
        provs.append(p)
    for p in provs:
        for g in GOOD_KEYS:
            p.industry[g] = 0.0
            p.capacity[g] = max(p.capacity[g], p.consumption(g) * 0.30)
    for g in GOOD_KEYS:
        need = sum(p.consumption(g) for p in provs)
        have = sum(p.capacity[g] for p in provs)
        k = need * GOODS[g].surplus / have
        for p in provs:
            p.capacity[g] *= k
            p.stocks[g] = p.consumption(g) * GOODS[g].target_cover
    edges = []
    for i in range(n):
        edges.append((f"p{i}", f"p{(i+1) % n}", "road", rng.uniform(1.5, 4.0)))
        if i % 7 == 0:
            edges.append((f"p{i}", f"p{(i + n // 3) % n}", "road", rng.uniform(2.0, 5.0)))
        if i % 5 == 0:
            edges.append((f"p{i}", f"p{(i+1) % n}", "river", rng.uniform(1.5, 4.0)))
        if i % 3 == 0:
            edges.append((f"p{i}", f"p{(i+1) % n}", "rail", rng.uniform(1.5, 4.0)))
    ports = tuple(f"p{i}" for i in range(0, n, max(1, n // 8)))
    return provs, Network(provs, edges, ports)


def time_tick(n: int, ticks: int = 3):
    g = Game(1)
    provs, net = synth(n)
    g.provs = provs
    g.by_key = {p.key: p for p in provs}
    g.network = net
    g.lost_provinces = []
    g.blockaded = set()
    g._reflood()
    g._apply_tax_burden()
    g.collect()
    parts = {}
    t0 = time.perf_counter()
    for _ in range(ticks):
        for k, v in dict(census=.2, army=.3, granary=.2, normal=.3).items():
            g.budget[k] = g.treasury * v
        a = time.perf_counter(); g._reflood(); parts["graph"] = parts.get("graph", 0) + time.perf_counter() - a
        a = time.perf_counter(); g._economy(g.year); parts["economy"] = parts.get("economy", 0) + time.perf_counter() - a
        a = time.perf_counter(); g._invest(); parts["invest"] = parts.get("invest", 0) + time.perf_counter() - a
        g.year += 1
        g.notice = []
    total = (time.perf_counter() - t0) / ticks
    return total, {k: v / ticks for k, v in parts.items()}


def measure_exponent(sizes=(64, 160)) -> float:
    """The growth exponent alone, on a small ladder.

    Absolute timings are machine-dependent; the exponent is not, and the exponent is
    what the engine recommendation actually rests on. Kept small so a docs check can
    afford to run it.
    """
    import math
    (n1, t1), (n2, t2) = [(n, time_tick(n)[0]) for n in sizes]
    return math.log(t2 / t1) / math.log(n2 / n1)


def main():
    print("SCALING  (per simulated tick, single-threaded CPython)\n")
    print(f"  {'provinces':>10} {'tick':>10} {'graph':>9} {'economy':>9} {'invest':>9}")
    rows = []
    for n in (8, 24, 64, 160, 400):
        t, parts = time_tick(n)
        rows.append((n, t))
        print(f"  {n:>10} {t*1000:>8.1f}ms {parts['graph']*1000:>7.1f}ms "
              f"{parts['economy']*1000:>7.1f}ms {parts['invest']*1000:>7.1f}ms")

    # fit an exponent on the top half: t = k * n^a
    import math
    (n1, t1), (n2, t2) = rows[-3], rows[-1]
    a = math.log(t2 / t1) / math.log(n2 / n1)
    k = t2 / (n2 ** a)
    print(f"\n  measured growth ≈ O(n^{a:.2f})")
    for target, label in ((3000, "the design's target map"),):
        est = k * target ** a
        print(f"\n  extrapolated to {target} provinces: {est:.2f}s per tick")
        for per_year, cadence in ((1, "yearly"), (12, "monthly"), (52, "weekly"), (365, "daily")):
            years = 250
            secs = est * per_year * years
            print(f"    {cadence:8s} ticks, 250 years: {secs/3600:8.1f} hours of compute for one playthrough")
    print("\n  A game is unplayable much above ~50ms per tick at the cadence the player")
    print("  advances time. See docs/engine-choice.md — the sim is stdlib-only so the")
    print("  port is transcription, not redesign.")


if __name__ == "__main__":
    main()
