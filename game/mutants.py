"""
Mutation testing.  Run: .venv/bin/python -m game.mutants

Two assertions written specifically to catch a saturation bug did not catch it when
the bug was reintroduced — the threshold measured the wrong quantity. A check you have
not tried against the bug it was written for is a guess.

So: reintroduce each class of bug this project has actually had, run the suite, and
record whether anything fails. A mutation nobody catches is a hole in the harness.
"""
from __future__ import annotations
import shutil, subprocess, sys, tempfile, os

SIM = "game/sim.py"

# (name, old, new) — each is a real regression this codebase has had
MUTANTS = [
    ("supply response removed",
     'elast = 0.35 if g == "grain" else 0.55',
     'elast = 0.0 if g == "grain" else 0.0'),

    # The original oscillation came from the MOVE SIZE, not the stale spread check:
    # taking a fixed fraction of stock repeatedly overshoots. Mutating the re-check
    # alone changes nothing measurable, so this targets the damping that actually
    # holds the market together.
    ("arbitrage overshoots (damping removed)",
     "                    move = max(0.0, min(equalise * 0.6, lo.stocks[g] * 0.30))",
     "                    move = max(0.0, lo.stocks[g] * 0.30)"),

    ("welfare ignores price",
     "    ratio = (wage_of(prov) * (1.0 - prov.tax_burden)) / max(1e-6, basket_cost(prov))",
     "    ratio = 1.3"),

    ("extraction is free",
     "    return max(0.0, min(1.0, min(availability, affordability)))",
     "    return max(0.0, min(1.0, availability))"),

    ("override costs nothing",
     "            self.register_quality *= 0.30",
     "            self.register_quality *= 1.00"),

    ("census does not correct belief",
     "                    old = self.beliefs.pop.get(p.key)",
     "                    continue\n                    old = self.beliefs.pop.get(p.key)"),

    ("no local production floor",
     '    LOCAL = {"iron": 0.75, "grain": 0.35, "cloth": 0.30, "coal": 0.18, "munit": 0.30}',
     '    LOCAL = {}'),

    # Retired: with per-deduction clamps in place as well, removing this one line no
    # longer produces a negative balance, so the mutation tests nothing. Kept as a
    # comment rather than deleted, because the bug it stood for was real and severe.
    ("treasury may go negative",
     "            self.treasury = max(0.0, self.treasury - spent)",
     "            self.treasury -= spent"),

    ("legibility saturates",
     '            raw = 0.05 + self.register_quality * 0.85 + self.supplied("legibility") * 0.5',
     '            raw = 0.10 + self.register_quality * 1.60 + self.supplied("legibility") * 1.0'),

    # Retired as a live regression: since the links were tightened, spending is limited
    # by the binding link long before it is limited by money, so unscaling the costs
    # moves treasury/revenue only 1.9x -> 2.0x. It mattered when the links were loose.
    ("instrument costs do not scale",
     '        return LINE_BY_KEY[line_key].unit_cost * (held / 8.0) ** 0.85',
     '        return LINE_BY_KEY[line_key].unit_cost * 0.25'),

    ("railway does not extend the network",
     "                    nxt = max(reachable, key=lambda q: q.freight())",
     "                    nxt = min((q for q in self.provs if not q.railed\n"
     "                               and q.key not in self.lost_provinces),\n"
     "                              key=lambda q: q.base_freight)"),

    ("population never grows",
     "            p.pop = max(0.5, p.pop * (1.0 + rate))",
     "            p.pop = max(0.5, p.pop)"),
]


def run_suite() -> tuple[bool, str]:
    r = subprocess.run([".venv/bin/python", "-m", "game.checks"],
                       capture_output=True, text=True)
    fails = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("FAIL")]
    return r.returncode != 0, "; ".join(f[6:].split("  ")[0] for f in fails[:2])


def main():
    backup = tempfile.mktemp(suffix=".py")
    shutil.copy(SIM, backup)
    src = open(SIM).read()
    print(f"MUTATION TESTING — {len(MUTANTS)} known regressions\n")
    missed = []
    try:
        for name, old, new in MUTANTS:
            if old not in src:
                print(f"  SKIP    {name}   (anchor no longer present — mutation is stale)")
                missed.append(name + " [stale]")
                continue
            open(SIM, "w").write(src.replace(old, new, 1))
            caught, why = run_suite()
            if caught:
                print(f"  caught  {name}")
                print(f"          by: {why}")
            else:
                print(f"  MISSED  {name}   <-- no check fails when this bug is present")
                missed.append(name)
            open(SIM, "w").write(src)
    finally:
        shutil.copy(backup, SIM)
        os.unlink(backup)

    print()
    if missed:
        print(f"{len(missed)} of {len(MUTANTS)} not caught: " + ", ".join(missed))
        sys.exit(1)
    print(f"all {len(MUTANTS)} regressions are caught by the suite")


if __name__ == "__main__":
    main()
