"""
Vicky — thesis slice simulation core.

Pure Python, no dependencies. The renderer sits on top of this and is swappable.

Implements the load-bearing claims from the design docs:
  - economy.md      : price is READ OFF conserved stocks, never solved for
  - conversion.md   : budget -> outcome via a delivery chain; throughput = MIN over links
  - main doc  §2    : two gates, fog as the medium
  - main doc  §5/§7 : several belief layers, never reconciled

The thesis being tested (main doc §21):
  "If deciding between funding a census and funding a railway is a genuinely hard and
   interesting choice with nothing else in the game, you've validated the thesis."
"""

from __future__ import annotations
import json, math, os, random
from statistics import mean as st_mean

from game.graph import build_network
from dataclasses import dataclass, field

RED_FLAG = 9.99
START_YEAR = 1650

# Equilibrium stock is  carry*(P-C)/(1-carry).  For grain to sit near one year of
# cover (its target) in a normal year, production must exceed consumption by
# (1-carry)/carry.  These two constants are solved together, not tuned separately:
# at carry .88 a ~15% surplus holds ~1.0y cover; one bad harvest takes it to ~0.73y
# (price ~1.8x), two in a row to ~0.42y (price ~5x). That is a dearth, not a clamp.
# Converts a graph freight cost into the price gap a trade must beat. Swept: at 2.6
# the market over-integrates (median grain spread 1.5x, flat in 16% of years); at 6.0
# it saturates. Below this, distance stops mattering and the player has no spread to
# read; above it, nothing changes.
# How far a muster roll alone can carry the register. Past this the state
# knows its soldiers and nothing else, and only a civil census helps.
MUSTER_CEILING = 0.45
FREIGHT_MULT = 6.0

GRAIN_CARRY = 0.88
GRAIN_SURPLUS = 1.15
N_TURNS = 20

# ---------------------------------------------------------------------------
# Goods.  elasticity is the ONE parameter per good that does the characterisation
# work (economy.md §2): high = a small shortfall produces a violent price move.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Good:
    key: str
    name: str
    ref_price: float
    elasticity: float     # price response to scarcity
    target_cover: float   # normal working inventory, in years of consumption
    floor: float          # hard clamp, multiple of ref_price
    ceiling: float
    carry: float          # fraction of stock surviving a year (spoilage, vermin, wastage)

    @property
    def surplus(self) -> float:
        """Production/consumption ratio that holds exactly target_cover in equilibrium.

        stock* = carry*(P-C)/(1-carry).  Setting stock* = target_cover*C and solving
        gives P/C = 1 + target_cover*(1-carry)/carry.  Derived, never tuned."""
        return 1.0 + self.target_cover * (1.0 - self.carry) / self.carry

GOODS = {
    g.key: g for g in [
        Good("grain", "Grain", 10.0, 1.9, 1.0, 0.30, 14.0, 0.88),  # the moral-economy good
        # Industrial clamps were far narrower than grain's (3.5x against 14x) while
        # five provinces produce no coal, five no iron and four no cloth — a province
        # that can only import drifts much further than one that can also grow its own.
        # Measured at the old bounds: coal on its floor 26% of province-years, iron and
        # cloth on their ceilings 15%. A clamp reached that often is an operating state,
        # not a backstop, and it flattens exactly the spread the player reads.
        # A remote coalfield with no way to ship is genuinely near-worthless where it
        # sits, so the floor has to be low enough for a glut to read as a glut rather
        # than as a clamp. Highfell and Cauldfell both sit on coal they cannot move.
        Good("coal",  "Coal",   6.0, 1.0, 0.6, 0.09, 9.0, 0.90),
        # Iron is concentrated in two provinces and one of them (Highfell) is stranded
        # behind the worst freight on the map — so national supply is structurally short
        # of what the surplus formula assumes, and the shortage is real. The ceiling has
        # to be far enough out that "dear because it cannot get here" reads as a price
        # rather than as a clamp. This is the stranded-resource story the railway exists
        # to solve, and it should be legible as one.
        Good("iron",  "Iron",  14.0, 1.1, 0.7, 0.20, 22.0, 0.94),
        Good("cloth", "Cloth", 20.0, 0.7, 0.5, 0.30, 7.0, 0.92),
        # Pillar 3: war is a second kind of demand on the network, not a die roll.
        # Munitions are the good that makes an army an economic object — it eats
        # them every year and eats far more when it fights, so a war two provinces
        # away shows up as a price in a city that never sees a soldier.
        # A war triples demand against fixed arsenals, so the wartime price is meant to be
        # violent — the ceiling has to be far enough out that the spike is a signal rather
        # than a clamp. At 8.0 it pinned in 19% of province-years.
        Good("munit", "Munitions", 26.0, 1.3, 0.8, 0.25, 22.0, 0.97),
    ]
}
GOOD_KEYS = list(GOODS)


# ---------------------------------------------------------------------------
# Provinces
# ---------------------------------------------------------------------------

@dataclass
class Province:
    key: str
    name: str
    x: int
    y: int
    pop: float                       # TRUTH. the state does not know this.
    literacy: float                  # TRUTH, 0..1
    base_freight: float              # cost to reach the capital, pre-rail
    water: bool                      # navigable water flattens freight
    bourgeoisie: float               # 0..1 — provinces with none are invisible to capital
    capacity: dict                   # good -> annual output at full capacity
    stocks: dict = field(default_factory=dict)
    railed: bool = False
    industry: dict = field(default_factory=dict)   # capital stock per good
    unrest: float = 0.0
    grievance: float = 0.0   # struck stakes: commons, gleaning, customary right
    tax_burden: float = 0.0  # share of the wage the state takes here, set each tick
    munitions_demand: float = 0.0  # what the army stationed here eats
    # who obstructs here, and how hard (0..1). politics-and-discretion.md
    clergy_strength: float = 0.0
    noble_strength: float = 0.0

    # set each tick from the cost flood; base_freight survives only as the fallback
    # used before a network exists and as the seed for map content.
    flood_cost: float = 0.0

    def freight(self) -> float:
        if self.flood_cost > 0.0:
            return max(0.05, self.flood_cost)
        f = self.base_freight
        if self.water:
            f *= 0.35
        if self.railed:
            f *= 0.30
        return max(0.05, f)

    def consumption(self, good: str) -> float:
        """Annual need. Grain scales with pop; industrial goods with pop and bourgeoisie."""
        if good == "grain":
            return self.pop * 1.0
        if good == "cloth":
            return self.pop * 0.22
        if good == "coal":
            return self.pop * 0.30 * (0.4 + self.bourgeoisie)
        if good == "iron":
            # Iron was scarce and used sparingly in 1650 — nails, tools, a plough share,
            # not construction. At 0.14 national demand outran what two ironfields could
            # supply and every province sat on the iron ceiling in 15% of province-years.
            return self.pop * 0.085 * (0.3 + self.bourgeoisie)
        if good == "munit":
            # A standing army eats munitions every year whether or not it fights, and
            # that upkeep is the continuous drag: it raises demand nationally, which
            # raises the price, which is money the country spends on powder instead of
            # bread. Spread by POPULATION, not by freight — concentrating it on the
            # nearest province emptied the capital's magazine and pinned its price.
            return 0.05 * self.pop + self.munitions_demand
        return 0.0


def build_world(rng: random.Random) -> list[Province]:
    """One country, eight provinces, deliberately varied so price spreads are readable."""
    P = Province
    provs = [
        P("cap",   "Aldermarch",  4, 2, 24.0, 0.34, 0.15, True,  0.85,
          {"grain": 8.0, "munit": 3.2,  "cloth": 14.0, "coal": 0.0,  "iron": 2.0}, clergy_strength=0.35, noble_strength=0.20),
        P("weald", "Weald",       2, 1, 14.0, 0.16, 0.55, True,  0.30,
          {"munit": 0.0, "grain": 30.0, "cloth": 1.0,  "coal": 0.0,  "iron": 0.8}, clergy_strength=0.55, noble_strength=0.70),
        P("hollin","Hollinghay",  6, 1, 16.0, 0.13, 0.70, False, 0.22,
          {"munit": 0.0, "grain": 32.0, "cloth": 0.5,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.60, noble_strength=0.75),
        P("blackm","Blackmoor",   2, 3,  9.0, 0.19, 1.10, False, 0.35,
          {"grain": 4.0, "munit": 2.2,  "cloth": 0.0,  "coal": 18.0, "iron": 1.0}, clergy_strength=0.30, noble_strength=0.45),
        P("ironby","Ironby",      8, 2,  8.0, 0.21, 1.25, False, 0.40,
          {"grain": 3.5, "munit": 5.0,  "cloth": 0.0,  "coal": 3.0,  "iron": 14.0}, clergy_strength=0.30, noble_strength=0.50),
        P("stitch","Stitchford",  4, 4, 11.0, 0.28, 0.85, True,  0.60,
          {"munit": 0.0, "grain": 3.0,  "cloth": 16.0, "coal": 0.0,  "iron": 0.9}, clergy_strength=0.25, noble_strength=0.25),
        P("marsh", "Marshend",    2, 5,  7.0, 0.09, 1.40, True,  0.10,
          {"munit": 0.0, "grain": 13.0,  "cloth": 0.0,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.70, noble_strength=0.60),
        P("far",   "Cauldfell",  10, 3,  5.0, 0.05, 2.60, False, 0.03,   # the dark province
          {"munit": 0.0, "grain": 6.0,  "cloth": 0.0,  "coal": 2.0,  "iron": 0.0}, clergy_strength=0.75, noble_strength=0.85),
        # -- the second ring -------------------------------------------------
        P("north", "Northport",   4, 0, 10.0, 0.31, 0.30, True,  0.66,
          {"munit": 1.1, "grain": 4.0,  "cloth": 7.0,  "coal": 0.0,  "iron": 0.7}, clergy_strength=0.30, noble_strength=0.25),
        P("high",  "Highfell",   10, 1,  4.0, 0.07, 2.30, False, 0.09,
          # A coal province, stranded behind the worst freight on the map. Giving it a
          # third of the nation's iron as well put that iron out of reach and left every
          # other province pinned to the iron ceiling in 22% of province-years — the
          # stranded-resource story reads better on one good than on two.
          {"munit": 0.0, "grain": 2.0,  "cloth": 0.0,  "coal": 11.0, "iron": 1.5}, clergy_strength=0.65, noble_strength=0.80),
        P("loam",  "Loam",        0, 4, 12.0, 0.14, 0.75, False, 0.19,
          {"munit": 0.0, "grain": 26.0, "cloth": 1.0,  "coal": 0.0,  "iron": 0.6}, clergy_strength=0.60, noble_strength=0.72),
        P("salt",  "Saltmere",    8, 4,  6.0, 0.17, 0.60, True,  0.40,
          {"munit": 0.0, "grain": 7.0,  "cloth": 2.0,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.45, noble_strength=0.35),
        P("dun",   "Dunwick",     0, 2,  5.0, 0.11, 0.55, True,  0.24,
          {"munit": 0.0, "grain": 5.0,  "cloth": 1.0,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.55, noble_strength=0.40),
        P("thorn", "Thornhill",   6, 3,  9.0, 0.22, 1.05, False, 0.46,
          {"munit": 0.7, "grain": 4.0,  "cloth": 11.0, "coal": 0.0,  "iron": 1.0}, clergy_strength=0.35, noble_strength=0.45),
    ]
    # No province produced literally none of anything. There was a village smith, a
    # weaver at the cottage, someone digging the outcrop. Without a floor, a province
    # with zero capacity that arbitrage cannot reach — Marshend and Cauldfell have no
    # merchant, so nobody trades to them — holds zero stock forever and its price reads
    # the ceiling every single year. That is a constant, not information, and it was
    # 15.7% of province-years for iron and cloth.
    # Per-good local production floor. Normalisation scales total capacity to match
    # total demand, so a national shortage is impossible by construction — what bites
    # is DISTRIBUTION, when a good is made in two provinces and needed in fourteen.
    # Iron is the case: smithing happened everywhere, so its floor is high; coal and
    # cloth are genuinely regional.
    LOCAL = {"iron": 0.75, "grain": 0.35, "cloth": 0.30, "coal": 0.18, "munit": 0.30}

    # every good's capacity is scaled to the surplus its own carry and target imply
    for g in GOOD_KEYS:
        need = sum(p.consumption(g) for p in provs)
        have = sum(p.capacity[g] for p in provs)
        if have > 0 and need > 0:
            k = need * GOODS[g].surplus / have
            for p in provs:
                p.capacity[g] *= k
        # AFTER normalisation, not before: applying the floor first inflates `have`,
        # so the scaling immediately undoes it and the floor does nothing at all.
        for p in provs:
            p.capacity[g] = max(p.capacity[g], p.consumption(g) * LOCAL.get(g, 0.30))
    for p in provs:
        for g in GOOD_KEYS:
            p.industry[g] = 0.0
    for p in provs:
        for g in GOOD_KEYS:
            # start each province near its target cover so year 1 is not a crisis
            p.stocks[g] = p.consumption(g) * GOODS[g].target_cover * rng.uniform(0.85, 1.15)
    return provs


# ---------------------------------------------------------------------------
# Prices — economy.md §2.  Read off stocks. Bounded by construction.
# ---------------------------------------------------------------------------

def price_of(prov: Province, good: str) -> float:
    g = GOODS[good]
    cons = prov.consumption(good)
    if cons <= 1e-6:
        return g.ref_price
    cover = prov.stocks[good] / cons
    if cover <= 1e-6:
        ratio = g.ceiling
    else:
        ratio = (g.target_cover / cover) ** g.elasticity
    ratio = max(g.floor, min(g.ceiling, ratio))
    return g.ref_price * ratio


def wage_of(prov: Province) -> float:
    """Money wage. Sticky downward, which is why a price spike against a flat wage is
    an entitlement signal rather than a supply signal (main doc §9)."""
    # calibrated against the basket at PREVAILING prices, not reference prices:
    # grain trades near 3x its reference in an ordinary year, so a wage set against
    # the reference makes the whole country read as permanently half-starving.
    base = 8.0 + 12.0 * prov.bourgeoisie + 5.5 * prov.literacy
    return base * (0.85 + 0.30 * min(1.5, sum(prov.capacity.values()) / max(1.0, prov.pop)))


def basket_cost(prov: Province) -> float:
    """What a year of one person's needs costs at THIS province's prices."""
    return sum((prov.consumption(g) / max(1e-6, prov.pop)) * price_of(prov, g)
               for g in GOOD_KEYS)


def welfare(prov: Province) -> float:
    """0..1. TRUTH — never shown to the player except under the inspector.

    Two gates, and the worse one binds (the same Liebig semantics as the delivery
    chain). AVAILABILITY: is the stuff physically there. AFFORDABILITY: can a wage
    buy it at the price it is actually trading at.

    The second gate is the entitlement mechanism. Famines are frequently not supply
    failures — the goods are present and a class can no longer buy them at any price.
    Without it, welfare cannot see a province where grain is at six times normal, and
    every price lever in the game (relief, the granary, the railway that collapses
    freight) becomes invisible to how people actually live.
    """
    total, met = 0.0, 0.0
    for g in GOOD_KEYS:
        need = prov.consumption(g)
        if need <= 1e-6:
            continue
        w = 3.0 if g == "grain" else 1.0
        total += w
        met += w * min(1.0, prov.stocks[g] / max(1e-6, need * 0.55))
    availability = met / total if total else 1.0

    # what the state takes is not available to buy bread with. This is the whole
    # guns-versus-butter trade: without it, extraction is free and every strategy
    # feeds its people identically.
    ratio = (wage_of(prov) * (1.0 - prov.tax_burden)) / max(1e-6, basket_cost(prov))
    affordability = 1.0 - math.exp(-1.9 * ratio)

    return max(0.0, min(1.0, min(availability, affordability)))


# ---------------------------------------------------------------------------
# The belief layer — main doc §5.  SEVERAL layers, never reconciled.
# ---------------------------------------------------------------------------

@dataclass
class Obs:
    value: float
    year: int
    source: str

    def age(self, now: int) -> int:
        return now - self.year


class Beliefs:
    """
    What the state thinks it knows. Deliberately NOT a model of the world —
    a bag of independently-sourced numbers with different ages and biases.
    """
    def __init__(self):
        self.pop: dict[str, Obs] = {}
        self.price: dict[tuple[str, str], Obs] = {}
        self.unrest: dict[str, Obs] = {}
        self.output: dict[str, Obs] = {}      # governor's report — adversarial
        # what the state has on file, year by year. the player reads SPREADS, not levels.
        self.history: dict[tuple[str, str], list] = {}
        self.wage_hist: dict[str, list] = {}

    def push(self, key, good, year, value):
        self.history.setdefault((key, good), []).append((year, value))

    def series(self, key, good):
        return self.history.get((key, good), [])

    def get_pop(self, key):    return self.pop.get(key)
    def get_price(self, k, g): return self.price.get((k, g))


# ---------------------------------------------------------------------------
# Conversion chains — conversion.md.  THROUGHPUT IS THE MINIMUM.
# ---------------------------------------------------------------------------

LINK_NAMES = {
    "money":      "Money",
    "agents":     "Trained men",
    "reach":      "Reach",
    "consent":    "Consent",
    "legibility": "Legibility",
    "compliance": "Compliance",
    "substrate":  "Substrate",
}

# Diegetic failure text — P1-7. Opaque about outcome, transparent about mechanism.
FRICTION = {
    ("census", "agents"):      "no clerks who can keep a register could be found",
    ("census", "reach"):       "the enumerators did not reach the outer hundreds",
    ("census", "consent"):     "the landowners will not have their people counted",
    ("railway", "agents"):     "no engineer would take the contract",
    ("railway", "substrate"):  "iron could not be had at any price the works would pay",
    ("railway", "consent"):    "the landowners refuse the right of way",
    ("schools", "agents"):     "no qualified masters presented themselves",
    ("schools", "consent"):    "the bishop declines to license the school",
    ("schools", "compliance"): "the schoolhouse stands empty; the harvest wants the children",
    ("schools", "substrate"):  "there is no press in the province to print a primer",
    ("normal", "agents"):      "the seminary cannot find tutors",
    ("granary", "reach"):      "the grain could not be carted before it spoiled",
    ("army", "legibility"):    "the muster rolls do not answer; the class cannot be found",
    ("army", "consent"):       "the estates will not vote the levy",
    ("land", "agents"):        "no surveyor could be found to lay out the fields",
    ("land", "consent"):       "the landowners will not bear the cost of the ditches",
    ("land", "compliance"):    "the commoners have pulled down the new hedges",
}


@dataclass
class Line:
    key: str
    name: str
    blurb: str
    links: list          # which link types this instrument needs
    unit_cost: float     # £ per unit of throughput


LINES = [
    Line("census",  "Census & survey",
         "Sharpens what you know. Nothing else.",
         ["money", "agents", "reach", "consent"], 40),
    Line("railway", "Railway",
         "Collapses freight. Prices converge; the interior joins the market.",
         ["money", "agents", "substrate", "consent"], 90),
    Line("schools", "Schools",
         "Literacy, in forty years. Feeds every other chain.",
         ["money", "agents", "consent", "compliance", "substrate"], 35),
    Line("normal",  "Normal schools",
         "Trains the masters. Does nothing for thirty years.",
         ["money", "agents"], 30),
    Line("granary", "State granary",
         "Buys grain into reserve. Releasable in a dearth.",
         ["money", "reach"], 25),
    Line("army",    "Army",
         "Tilly does not care what else you were funding.",
         ["money", "legibility", "consent"], 55),
    Line("land",    "Improvement of the land",
         "Drainage, enclosure, new rotations. More bread, and a grievance.",
         ["money", "agents", "consent", "compliance"], 45),
]
LINE_BY_KEY = {l.key: l for l in LINES}


# ---------------------------------------------------------------------------
# The settlement — politics-and-discretion.md.
# Discretion is a SHAPE, not a pool: the set of things you can do without consent.
# The join with conversion.md: you hold a veto BECAUSE you perform a function the
# state cannot. Override removes that function from every chain it supplied.
# ---------------------------------------------------------------------------

@dataclass
class VetoHolder:
    key: str
    name: str
    domain: list          # which budget lines it can block
    consent: float        # 0..1
    supplies: dict        # link -> strength it contributes to EVERY line using that link
    price: str            # what it wants, in words
    price_cost: float     # £ to buy a step of consent
    concession: str       # what paying permanently costs you
    overridden: bool = False
    hard: bool = False    # a hard veto cannot be overridden at all

    def blocks(self, line_key: str) -> bool:
        return line_key in self.domain


def build_settlement() -> list:
    return [
        VetoHolder("clergy", "The Church", ["schools", "census"], 0.75,
                   {"legibility": 0.26, "compliance": 0.22},
                   "the schools licensed, and the tithe left alone", 55,
                   "you may never preach against them"),
        VetoHolder("nobles", "The Landowners", ["railway", "census", "land"], 0.70,
                   {"reach": 0.24, "compliance": 0.10},
                   "their privileges confirmed, and no survey of their rents", 65,
                   "their exemption becomes customary"),
        VetoHolder("estates", "The Estates", ["army"], 0.80,
                   {"money": 0.0},
                   "a standing right to vote the levy", 70,
                   "they will expect to be asked again"),
    ]


# ---------------------------------------------------------------------------
# Statistical categories — main doc §5. Some things are not unknown, they are
# UNASKABLE: the concept required to collect them has not been invented. Each
# unlock opens a class of question the state could not previously pose, gives a
# new instrument, and usually delivers an unpleasant surprise about the number.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The fiscal ladder — main doc §5. "Fiscal capacity is gated by information capacity,
# and the historical sequencing falls out on its own. States taxed trade first because
# trade was visible... A player who wants progressive taxation in 1700 should find the
# instrument simply unavailable, not merely unpopular."
#
# So each tier is gated on the legibility link that already exists, and each falls on
# a different part of the country. Extraction is the missing choice: with revenue
# automatic, nobody can squeeze their population for state power, and the design's
# "two visible numbers" — state power and how people live — cannot diverge.
# ---------------------------------------------------------------------------

@dataclass
class Tax:
    key: str
    name: str
    blurb: str
    needs: float          # legibility required before the instrument exists at all
    yield_per: float      # revenue per point of rate
    incidence: str        # who actually pays


TAXES = [
    Tax("excise", "Excise and customs",
        "Goods at the port and the mill gate. You need to see almost nothing.",
        0.00, 138.0, "everyone, and hardest on the poor"),
    Tax("land", "Land tax",
        "Assessed on the cadastre. Survey once, then it stays roughly true.",
        0.46, 170.0, "the countryside"),
    Tax("income", "Income tax",
        "Continuous, adversarial, and it needs a literate inspectorate.",
        0.68, 236.0, "where the money is"),
]


@dataclass
class Category:
    key: str
    name: str
    needs: str            # institution gating it
    level: float
    blurb: str
    reveal: list          # what the state learns, and wishes it had not

CATEGORIES = [
    Category("returns", "Trade returns", "clerks", 0.58,
             "Prices reported from provinces with no merchant of their own.",
             ["THE BOARD OF TRADE IS ESTABLISHED", "",
              "Until now you have read prices only where somebody had a reason",
              "to quote them. The dark provinces were dark because nobody there",
              "was trading on their own account.", "",
              "Your clerks will now collect returns from every district you hold.",
              "You will not like all of them."]),
    Category("vital", "Vital registration", "register", 0.52,
             "Births and burials. The population figure stops drifting between counts.",
             ["CIVIL REGISTRATION BEGINS", "",
              "Baptisms, marriages and burials were the church's books, and you",
              "read them only by its leave. Now they are yours.", "",
              "Your population figure will no longer go stale between counts —",
              "it will be corrected every year, by arithmetic you own."]),
    Category("cost", "Cost of living", "clerks", 0.80,
             "Wages against prices. You can finally see who is being priced out.",
             ["A COST-OF-LIVING INDEX IS CONSTRUCTED", "",
              "You have always been able to see that grain was dear. You have",
              "never been able to see what that meant for a labourer's week.", "",
              "Now you can. The number has a name, so it can be demanded to fall,",
              "and you have created a public that knows to demand it."]),
]


# ---------------------------------------------------------------------------
# Crises — main doc §5. "Blindness costs nothing in ordinary years. Then there is a
# famine, a cholera epidemic, a mobilization, or 1848, and the state needs to know
# within weeks... and the apparatus takes a decade to build."
#
# This is the answer to measured late-game convergence: every other instrument is a
# slow stock, so once built the ordering never changes. A crisis is a SHORT FUSE that
# late capacity can answer and early capacity cannot — and surviving one ratchets the
# state upward, so the century's capacity growth is paid for in disasters.
# ---------------------------------------------------------------------------

@dataclass
class Choice:
    key: str
    label: str
    hint: str


class Budget(dict):
    """A budget that refuses money for a line that does not exist.

    A plain dict accepts any key, so `budget["rail"] = 900` — the line is called
    "railway" — appropriated nothing, reported nothing, and read as a strategy that
    simply did not work. It cost several measurements before anyone noticed the
    railway was never being built. Money assigned to nothing is always a mistake.
    """

    def __setitem__(self, key, value):
        if key not in self:
            raise KeyError(
                f"no budget line {key!r}; the lines are {sorted(self)}")
        dict.__setitem__(self, key, value)


@dataclass
class Crisis:
    key: str
    title: str
    lines: list
    choices: list


@dataclass
class ChainResult:
    line: str
    spent: float
    appropriated: float
    throughput: float
    binding: str          # link key that bound
    friction: str         # diegetic message, or ""


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

class Game:
    def __init__(self, seed: int = 7):
        self.rng = random.Random(seed)
        self.year = START_YEAR
        self.turn = 0
        self.provs = build_world(self.rng)
        self.by_key = {p.key: p for p in self.provs}
        self.beliefs = Beliefs()
        self.treasury = 900.0
        self.reserve_grain = 0.0
        self.rail_progress = 0.0   # a line takes years; progress carries over

        # institutional stocks — these ARE the chain links
        self.clerks = 0.30           # legibility apparatus
        self.masters = 0.22          # trained teachers
        self.engineers = 0.15
        self.register_quality = 0.10 # conscription register
        self.army = 1.0
        self.credit = 1.0            # sovereign credit (main doc §10)

        # politics — the settlement (politics-and-discretion.md)
        self.settlement = build_settlement()
        self.holder = {h.key: h for h in self.settlement}

        self.budget: dict[str, float] = Budget({l.key: 0.0 for l in LINES})
        self.tax: dict[str, float] = {"excise": 0.45, "land": 0.0, "income": 0.0}
        self.log: list[str] = []
        self.pending: list[str] = []
        self.results: list[ChainResult] = []
        self.spent_total = {l.key: 0.0 for l in LINES}
        self.wasted_total = {l.key: 0.0 for l in LINES}
        self.threat = 0.35           # the Tilly clock
        self.war_in = 8              # years until the neighbour is ready
        self.lost_provinces: list[str] = []
        self.network = build_network(self.provs)
        self.blockaded: set = set()
        self._pairs: dict = {}
        self._reflood()
        self.admin_load = 1.0
        self.taught = False
        self.notice: list = []   # a thing the player must actually be made to read
        self.unlocked: set = set()
        self.crisis = None
        self.price_fixed_until = -1
        self.crises_survived = 0
        self.crisis_cool = 0
        self.game_over = False
        self.ending = ""

        self._apply_tax_burden()
        self._initial_survey()

    # -- setup ------------------------------------------------------------
    def _initial_survey(self):
        """A stale, partial, systematically-biased picture. The frontier undercounts."""
        for p in self.provs:
            age = self.rng.randint(9, 34)
            bias = 1.0 - min(0.55, p.base_freight * 0.20)      # remote => undercount
            noise = self.rng.uniform(0.93, 1.07)
            self.beliefs.pop[p.key] = Obs(p.pop * bias * noise, self.year - age, "survey")
        # Merchants have been quoting all along — the state simply holds old returns.
        # Without this the opening decision is made with no price data at all, which
        # reads as an empty screen rather than as a state that cannot see.
        for p in self.provs:
            if p.bourgeoisie <= 0.15:
                continue
            age = self.rng.randint(1, 4)
            for g in GOOD_KEYS:
                drift = self.rng.uniform(0.82, 1.22)
                self.beliefs.price[(p.key, g)] = Obs(price_of(p, g) * drift,
                                                     self.year - age, "market")
                self.beliefs.push(p.key, g, self.year - age, price_of(p, g) * drift)
            self.beliefs.wage_hist.setdefault(p.key, []).append(
                (self.year - age, wage_of(p)))
        self.log.append(f"{self.year}. You inherit a treasury, an army, and a map you did not draw.")

    # -- links ------------------------------------------------------------
    def pair_cost(self, a: str, b: str) -> float:
        """Freight between two markets — what arbitrage actually pays."""
        return self._pairs.get((a, b), 40.0)

    def _reflood(self, month: int = 6):
        """One multi-source Dijkstra gives every province its access cost. Called
        whenever the network changes: a line completes, a port is blockaded, a
        province is lost, or the season turns."""
        self.network.invalidate()
        costs = self.network.flood(month=month, blockaded=self.blockaded,
                                   lost=set(self.lost_provinces))
        self._pairs = self.network.pairwise(month=month, blockaded=self.blockaded,
                                            lost=set(self.lost_provinces))
        for k, c in costs.items():
            self.by_key[k].flood_cost = c

    def link_value(self, line_key: str, link: str) -> float:
        """0..1 strength of one link for one instrument."""
        if link == "money":
            return 1.0
        if link == "agents":
            # Calibrated against each institution's MEASURED peak so the link lands near
            # 0.85 when the apparatus is fully built, not at its cap. Census was
            # 0.15 + clerks*1.5 against clerks that peak at 0.83, so it pinned to 1.0
            # the moment the clerks existed and stopped being a constraint — the same
            # failure as legibility, in a link nobody had looked at.
            # Re-derived against peaks measured under FOCUSED play, not under one
            # balanced mix. The old numbers came from clerks peaking at 0.83 and
            # masters at ~0.6, which is what a spread budget reaches. Pour everything
            # into one instrument and clerks reach 0.97, masters 0.985 — so census
            # pinned at 1.0 and both teaching lines pinned above it, and the link
            # stopped constraining exactly the player who had specialised into it.
            # Each now lands at 0.85 when its institution is maximally built.
            # Concave, not linear. A linear curve forces a choice between pinning at
            # 1.0 for the specialist who maxes the institution and throttling the
            # generalist who half-builds it: calibrated for one, it fails the other.
            # Both failures were live — census pinned at 1.0 under census-heavy play,
            # and re-calibrating it flat throttled schools far enough to starve the
            # press that schools depend on, which fed back into schools again.
            # x**0.45 gives fast early returns and a flat top: each lands at ~0.85
            # when its institution is maximally built and still near 0.69 at 0.6.
            def agents_of(x: float, base: float, span: float) -> float:
                return min(1.0, base + span * max(0.0, x) ** 0.45)
            if line_key == "census":  return agents_of(self.clerks, 0.10, 0.75)
            if line_key in ("railway", "land"): return agents_of(self.engineers, 0.08, 1.09)
            if line_key in ("schools",): return agents_of(self.masters, 0.05, 0.80)
            if line_key == "normal":  return agents_of(self.masters, 0.26, 0.59)
            return 1.0
        if link == "reach":
            # How much of your country you can actually get to, weighted by where the
            # people are. The previous form summed 1/freight, which is dominated by the
            # ports already at the minimum — building the entire rail network moved it
            # from 0.67 to 0.73, so the design's central claim that a railway transforms
            # the state's reach could not show up anywhere.
            live = [p for p in self.provs if p.key not in self.lost_provinces]
            if not live:
                return 0.12
            pop = sum(p.pop for p in live)
            acc = sum(p.pop * (1.0 / (1.0 + p.freight())) for p in live) / max(1e-6, pop)
            return max(0.12, min(1.0, (acc + self.supplied("reach"))
                                 / max(1.0, self.admin_load ** 0.8)))
        if link == "consent":
            v = 1.0
            for h in self.settlement:
                if h.blocks(line_key):
                    v = min(v, 1.0 if h.overridden else h.consent)
            return v
        if link == "legibility":
            # the parish register was the census apparatus long before the state had one
            # Was 0.10 + register*1.6 + supplied, which reached the 1.0 cap by year 3 in
            # every playstyle — so legibility stopped binding anything, and it could not
            # gate the fiscal ladder at all (all three tax tiers were available by 1651).
            # Seeing your own country is meant to be the hard, slow thing.
            # divided by administrative load: a register that covers eight counties
            # does not cover fourteen, and on the larger map this hit its cap by 1664.
            raw = 0.05 + self.register_quality * 0.85 + self.supplied("legibility") * 0.5
            return max(0.05, min(1.0, raw / max(1.0, self.admin_load ** 0.7)))
        if link == "compliance":
            if line_key == "land":
                # the moral economy: commons, gleaning and wood-gathering were a real
                # fraction of poor subsistence, and a hungry province defends them hardest
                unrest = sum(p.unrest * p.pop for p in self.provs) / sum(p.pop for p in self.provs)
                hunger = 1.0 - self.mean_welfare()
                return max(0.08, min(1.0, 0.95 - unrest * 0.8 - hunger * 0.9
                                     + self.supplied("compliance")))
            # Households only spare children where there is a return to reading. This was
            # 0.18 + lit*1.3 + supplied, which evaluates to 1.08 before the game starts —
            # over its own cap, so the gate never existed.
            lit_return = sum(p.bourgeoisie * p.pop for p in self.provs) / sum(p.pop for p in self.provs)
            return min(1.0, 0.06 + lit_return * 0.95 + self.supplied("compliance") * 0.45)
        if link == "substrate":
            # Both arms were generous enough that substrate never bound anything once
            # iron supply was fixed — median 0.86 and 0% binding, a link the player
            # could neither feel nor act on. A line needs rails, not merely iron; a
            # school needs a press in the province, which is a commercial thing.
            live = [p for p in self.provs if p.key not in self.lost_provinces] or self.provs
            if line_key == "railway":
                iron = sum(p.stocks["iron"] for p in live)
                need = sum(p.consumption("iron") for p in live)
                return max(0.08, min(1.0, iron / max(1e-6, need * 1.35)))
            if line_key == "schools":
                press = sum(p.bourgeoisie * p.literacy for p in live) / max(1, len(live))
                return max(0.06, min(1.0, 0.12 + press * 3.4))
            return 1.0
        return 1.0

    def relieve(self, key: str) -> str:
        """Open the reserve at one province. The lever the design says actually works —
        it moves the world rather than overwriting the readout. You choose on the
        prices you BELIEVE, and the grain still has to get there."""
        p = self.by_key[key]
        if key in self.lost_provinces:
            return "That province is no longer yours."
        want = p.consumption("grain") * 0.80
        give = min(self.reserve_grain, want)
        if give <= 0.5:
            return "The reserve is empty."
        self.reserve_grain -= give
        # reach: what leaves the magazine is not what arrives
        arrived = give * max(0.25, 1.0 - p.freight() * 0.22)
        p.stocks["grain"] += arrived
        p.unrest = max(0.0, p.unrest - 0.40)
        loss = (give - arrived) / give
        msg = f"Relief opened at {p.name}: {give:,.0f} qr sent"
        if loss > 0.12:
            msg += f", {arrived:,.0f} arrived — {loss*100:.0f}% lost on the road"
        self.pending.append(msg + ".")
        return msg

    def supplied(self, link: str) -> float:
        """What the settlement contributes to a link. Overridden holders contribute nothing."""
        return sum(h.supplies.get(link, 0.0) for h in self.settlement if not h.overridden)

    def can_pay(self, key: str) -> bool:
        h = self.holder[key]
        return not h.overridden and h.consent < 0.98 and self.treasury >= h.price_cost

    def pay(self, key: str):
        """capacity -> discretion. Cheap now, and it narrows your future permanently."""
        h = self.holder[key]
        if not self.can_pay(key):
            return
        self.treasury -= h.price_cost
        h.consent = min(1.0, h.consent + 0.16)
        h.price_cost *= 1.35          # they learn what you will pay
        self.pending.append(f"You buy {h.name}: {h.concession}.")

    # What each holder is, beyond the links they supply. The panel used to read this
    # off h.supplies alone, so the Estates — who supply no LINK at all — displayed
    # "Supplies: nothing", which told the player that overriding them was free. It is
    # not: it costs 38% of your sovereign credit. A holder can be a stock you stand on
    # without that stock being one of the eight links, and the player has to be able to
    # see it before deciding. One source of truth, used by the panel and by override().
    STANDS_ON = {
        "clergy": "the parish registers, which are your census and your muster roll",
        "nobles": "the county carting and the local courts",
        "estates": "your credit with the people who lend to you",
    }

    def stands_on(self, key: str) -> str:
        """What breaking with this holder destroys, in the player's words."""
        h = self.holder[key]
        links = ", ".join(LINK_NAMES[k].lower() for k in h.supplies if h.supplies[k] > 0)
        extra = self.STANDS_ON.get(h.key, "")
        if links and extra:
            return f"{links}; and {extra}"
        return links or extra or "nothing you can name"

    def override(self, key: str):
        """You win the political fight and lose the ability to execute what you won."""
        h = self.holder[key]
        if h.overridden or h.hard:
            return
        h.overridden = True
        lost = ", ".join(LINK_NAMES[k].lower() for k in h.supplies if h.supplies[k] > 0)
        self.pending.append(f"You override {h.name}. They will not obstruct you again.")
        if lost:
            self.pending.append(f"  They also stop supplying {lost}.")
        # You do not merely lose their future help — you lose the stock you were
        # standing on. The parish register WAS the census before civil registration
        # existed; breaking with the church destroys the apparatus, not just the ally.
        # Hence the sequencing rule: build the registry office first, then fight.
        if h.key == "clergy":
            self.register_quality *= 0.30
            self.clerks *= 0.55
            self.pending.append("  The parish registers close. Your muster rolls go with them.")
        if h.key == "nobles":
            for p in self.provs:
                p.base_freight *= 1.30
            self.pending.append("  The county carting and the local courts are no longer yours.")
        if h.key == "estates":
            self.credit *= 0.62
            self.pending.append("  You have shown that lending to you is unsafe. The spread widens.")
        for p in self.provs:
            p.unrest = min(1.0, p.unrest + 0.22)

    def unit_cost(self, line_key: str) -> float:
        """What a unit of an instrument costs in THIS country.

        Line costs were absolute while revenue scales with the map, so growing from 8
        provinces to 14 tripled income against a fixed price list and money stopped
        being scarce — the treasury piled up and every line ran to its non-money link.
        A census of fourteen counties costs more than a census of eight.
        """
        held = sum(1 for p in self.provs if p.key not in self.lost_provinces)
        return LINE_BY_KEY[line_key].unit_cost * (held / 8.0) ** 0.85

    def preview(self, line_key: str) -> tuple[str, float]:
        """Binding link and its value — shown BEFORE committing. Transparent about mechanism."""
        line = LINE_BY_KEY[line_key]
        worst, wv = "money", 1.0
        for lk in line.links:
            if lk == "money":
                continue
            v = self.link_value(line_key, lk)
            if v < wv:
                worst, wv = lk, v
        return worst, wv

    # -- the tick ---------------------------------------------------------
    def end_turn(self):
        if self.game_over:
            return
        self.results = []
        self.log = list(self.pending)
        self.pending = []
        y = self.year

        # A negative treasury made this scale factor negative, which flipped the sign of
        # every appropriation, made `spent` negative, and ADDED money on every line —
        # compounding to -1.7e180 over twenty years. Crisis options deduct without
        # checking affordability, so the treasury can and does go below zero.
        self.treasury = max(0.0, self.treasury)
        appropriated = sum(self.budget.values())
        if appropriated > self.treasury:
            scale = max(0.0, self.treasury) / max(1e-6, appropriated)
            for k in self.budget:
                self.budget[k] *= scale
            self.log.append("The treasury would not bear it; appropriations were cut back.")

        # 1. conversion: money -> outcome, throughput = MIN over links
        for line in LINES:
            appro = self.budget[line.key]
            if appro <= 0:
                continue
            binding, bv = self.preview(line.key)
            wanted = appro / self.unit_cost(line.key)
            through = wanted * bv
            spent = through * self.unit_cost(line.key)
            self.treasury = max(0.0, self.treasury - spent)
            waste = appro - spent
            fr = ""
            if waste > appro * 0.12:
                fr = FRICTION.get((line.key, binding), "the work did not proceed")
            self.spent_total[line.key] += spent
            self.wasted_total[line.key] += waste
            self.results.append(ChainResult(line.key, spent, appro, through, binding, fr))
            self._apply(line.key, through)

        for k in self.budget:
            self.budget[k] = 0.0

        # 2. world tick
        self._apply_tax_burden()
        self._reflood()
        self._economy(y)
        self._events(y)
        self._reports(y)
        self._early_lesson(y)
        self._categories(y)
        self._crisis(y)
        self._tilly(y)

        # 3. decay
        self.clerks *= 0.97
        self.masters *= 0.985
        self.engineers *= 0.97
        # Technical men are produced by the country, not appropriated. A province with
        # literacy, a bourgeoisie and capital throws them off at some rate — which is
        # why funding schools and industry eventually unlocks the works you could not
        # previously build. Without this, `engineers` is gated on the very lines it
        # gates, and the binding constraint never moves for the whole run.
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        if live:
            pool = sum(p.literacy * p.bourgeoisie * p.pop for p in live) / \
                   max(1e-6, sum(p.pop for p in live))
            target = min(0.95, pool * 3.4)
            if target > self.engineers:
                self.engineers += (target - self.engineers) * 0.16
        self.register_quality *= 0.965     # operating expenditure: goes stale fast

        self.turn += 1
        self.year += 1
        if self.turn >= N_TURNS:
            self.game_over = True
            self.ending = self._ending()

    def _ending(self) -> str:
        """Name what this run was, without ranking it.

        No score: a single number would encode a politics (design §16). So the
        ending states the run's dominant *fact* — what happened, in the terms the
        run itself made salient — and different facts are not comparable to each
        other. A country you could see and a country you kept are two different
        achievements, and the game does not say which is better.

        Losing a province or two is the ordinary price of not funding the army,
        which is a legitimate strategy and not the defining fact of a reign. Only
        losing most of the country is. Measured: all-in on army holds 14 of 14;
        no army at all loses exactly 2. So the headline comes from what you built,
        and territory is a clause appended to it.
        """
        lost = len(self.lost_provinces)
        n = max(1, len(self.provs))
        overrode = [h for h in self.settlement if h.overridden]

        if lost >= n * 0.35:
            return "Most of it is not yours any more. What is left, you hold."

        if overrode and self.register_quality < 0.25:
            head = ("You broke what stood in your way and found nothing behind it. "
                    "The country is quiet and unread.")
        elif overrode:
            head = "Nobody obstructs you now. You built the apparatus to replace them."
        elif self.register_quality >= 0.55:
            head = "You can see your own country. Your successors inherit the file."
        # Measured across the strategy space: literacy runs 20.9% with no schooling
        # at all to 28.2% for an all-in schools run over twenty years — schools have
        # a forty-year fuse and this is a twenty-year game. 0.26 is the line that
        # actually separates 'you funded schools' from 'you did not'. A guessed 0.35
        # was above the reachable maximum and made this ending dead content.
        elif self.mean_literacy() >= 0.26:
            head = "They can read. Whatever they do with that is not yours to decide."
        elif self.mean_welfare() >= 0.72:
            head = "They are fed and housed. The state that did it remains a rumour to them."
        else:
            head = f"{N_TURNS} years. Nothing broke, and nothing much moved."

        if lost == 1:
            head += " One county went to the neighbour."
        elif lost:
            head += f" {lost} counties went to the neighbour."
        return head

    def _apply(self, key: str, through: float):
        if key == "census":
            self.clerks = min(1.0, self.clerks + through * 0.05)
            # legibility is one apparatus: counting people is what makes a muster roll possible
            # Swept after the muster ceiling went in, because the army had been
            # inflating legibility and every instrument was drawing on it. There is
            # a cliff between 0.022 and 0.028 (belief-error ratio 0.48 -> 0.24): below
            # it the census never gets over the decay and the pillar is decorative.
            # 0.034 sits mid-plateau rather than on the edge, and leaves the register
            # at ~67% on an all-in census run — high, not saturated.
            self.register_quality = min(1.0, self.register_quality + through * 0.034)
            # a census is a snapshot, and it only reaches where reach allows
            for p in self.provs:
                if p.key in self.lost_provinces:
                    continue
                chance = min(0.9, through * 0.055 / max(0.5, p.freight()))
                if self.rng.random() < chance:
                    err = self.rng.uniform(0.97, 1.03)
                    old = self.beliefs.pop.get(p.key)
                    new = Obs(p.pop * err, self.year, "census")
                    self.beliefs.pop[p.key] = new
                    if old and old.value > 0:
                        d = (new.value - old.value) / old.value
                        if abs(d) > 0.14:
                            self.pending.append(
                                f"The return from {p.name} is in: {new.value*1000:,.0f} souls, not "
                                f"{old.value*1000:,.0f}. You were {abs(d)*100:.0f}% out for "
                                f"{old.age(self.year)} years.")
        elif key == "railway":
            self.engineers = min(1.0, self.engineers + through * 0.02)
            self.rail_progress += through
            # The line EXTENDS. A rail edge carries traffic only if BOTH ends are
            # railed, so railing the cheapest provinces independently activated two
            # edges out of five lines built and the network gained nothing — which is
            # the design's own point that connectivity beats mileage. Grow outward
            # from what is already connected, taking the province with most to gain.
            railset = {p.key for p in self.provs if p.railed}
            while self.rail_progress >= 2.5:
                if not railset:
                    nxt = min((p for p in self.provs if p.key not in self.lost_provinces),
                              key=lambda q: q.base_freight)
                else:
                    adj = set()
                    for a, b, m, _d in self.network.edges:
                        if m != "rail":
                            continue
                        if a in railset:
                            adj.add(b)
                        if b in railset:
                            adj.add(a)
                    reachable = [q for q in self.provs if q.key in adj and not q.railed
                                 and q.key not in self.lost_provinces]
                    if not reachable:
                        break
                    nxt = max(reachable, key=lambda q: q.freight())
                self.rail_progress -= 2.5
                nxt.railed = True
                railset.add(nxt.key)
                self.log.append(f"The line reaches {nxt.name}.")
                self._reflood()
        elif key == "schools":
            for p in self.provs:
                gain = through * 0.004 * (0.4 + p.bourgeoisie)
                p.literacy = min(0.95, p.literacy + gain)
        elif key == "normal":
            self.masters = min(1.0, self.masters + through * 0.035)
        elif key == "granary":
            bought = through * 2.0
            self.reserve_grain += bought
            for p in self.provs:
                take = min(p.stocks["grain"] * 0.05, bought / len(self.provs))
                p.stocks["grain"] -= take
        elif key == "land":
            # The one instrument that touches what people actually eat. It also strikes
            # the moral economy: enclosure takes commons, gleaning and wood-gathering,
            # which were a real fraction of poor subsistence (main doc §10). More bread
            # in aggregate, and a specific grievance in the provinces it is done to.
            for p in self.provs:
                if p.key in self.lost_provinces:
                    continue
                share = through * 0.0035 * (0.5 + p.capacity["grain"] / 30.0)
                p.capacity["grain"] *= 1.0 + share
                # the grievance is specific and local: this province lost its commons.
                # It also feeds back — unrest lowers the compliance link, so enclosure
                # gets harder the more of it you have already done.
                p.grievance = min(1.0, p.grievance + share * 22.0)
            self.engineers = min(1.0, self.engineers + through * 0.012)
        elif key == "army":
            # An army is men AND magazines. Procurement is what lets you fight a war
            # you could not supply out of current production — the design's stockpile
            # against a demand shock you can see coming.
            # Measured: at the old rate the army peaked at 1.85 against a threat that
            # reaches 3.92, so no level of spending could hold the country and the Tilly
            # clock was a countdown rather than a choice.
            self.army += through * 0.38
            buy = through * 3.2   # enough that a decade of funding is a real magazine
            live = [p for p in self.provs if p.key not in self.lost_provinces]
            for p in sorted(live, key=lambda q: q.freight())[:3]:
                p.stocks["munit"] += buy / 3.0
            # A muster roll is a register — historically the first one most states had.
            # But it is a NARROW register: men of fighting age, in places you already
            # reach. Two bugs lived here. It paid 0.03 against the census's 0.022, so
            # the instrument whose purpose is elsewhere out-registered the dedicated
            # one; and because the army is *gated on* legibility while *feeding* it,
            # the two compounded into a self-gating loop — army-only runs reached 96%
            # register against all-in census's 79-88%, inverting the game's thesis.
            # The ceiling is the fix that matters: a muster roll can only tell you so
            # much about a country, and past that you need an actual civil census.
            self.register_quality = min(MUSTER_CEILING,
                                        self.register_quality + through * 0.010) \
                if self.register_quality < MUSTER_CEILING else self.register_quality

    def _economy(self, y: int):
        # climate: a slow global multiplier with excursions (main doc §20)
        climate = 1.0
        if y in (1657, 1658, 1665, 1672, 1685, 1694, 1695):
            climate = self.rng.uniform(0.62, 0.80)
            self.log.append("A cold, wet year. The harvest is short across the country.")
        # population grows, so every count starts going stale the day it is taken
        # vital registration keeps the population figure from drifting between counts
        if "vital" in self.unlocked:
            for p in self.provs:
                if p.key in self.lost_provinces:
                    continue
                ob = self.beliefs.pop.get(p.key)
                if not ob:
                    continue
                # A registrar is a person in an office in a place. Registration reaches
                # where the state reaches — without this it refreshed Cauldfell, which
                # has no merchant, no census and barely a road, and every province on
                # the ledger read "1y" the moment the category unlocked.
                if self.rng.random() > 1.0 / (1.0 + p.freight() * 0.9):
                    continue
                drift = 0.30 * (p.pop - ob.value)
                self.beliefs.pop[p.key] = Obs(ob.value + drift, self.year, "registrar")
        for p in self.provs:
            if p.key in self.lost_provinces:
                continue
            w = welfare(p)
            # capped: welfare feeds population feeds industry feeds bourgeoisie feeds
            # welfare. Uncapped, that loop runs away and the country doubles.
            rate = min(0.022, 0.004 + 0.011 * p.bourgeoisie + 0.010 * (w - 0.6))
            p.pop = max(0.5, p.pop * (1.0 + rate))
            # land comes into cultivation with the people. The squeeze the player must answer
            # is the climate excursion, not a structural Malthusianism they have no lever on.
            for _g in GOOD_KEYS:
                p.capacity[_g] *= 1.0 + rate   # land brought into cultivation

        for p in self.provs:
            for g in GOOD_KEYS:
                out = p.capacity[g] * (climate if g == "grain" else 1.0)
                # SUPPLY RESPONSE. Without it, equilibrium cover is
                #   carry*(P/C - 1)/(1 - carry)
                # which at carry .88 multiplies any drift in the production ratio by
                # 7.3, and price responds as cover^-1.9. A 1% shift in P/C then moves
                # the price level enormously — the knife-edge that turns into the
                # "one good's price hitting infinity in 1847" the design warns about.
                # High prices call forth supply: more land under plough, more shifts
                # worked, marginal seams reopened. It is the negative feedback that
                # makes the whole price model robust instead of merely bounded.
                gd = GOODS[g]
                elast = 0.35 if g == "grain" else 0.55
                out *= min(1.6, max(0.55, (price_of(p, g) / gd.ref_price) ** elast))
                out *= self.rng.uniform(0.92, 1.08)
                p.stocks[g] += out
                p.stocks[g] = max(0.0, p.stocks[g] - p.consumption(g))
                p.stocks[g] *= GOODS[g].carry   # spoilage / carrying loss

        self._invest()

        # arbitrage: capitalists move goods where the spread beats freight.
        # sight is limited — provinces with no bourgeoisie are invisible to capital.
        for g in GOOD_KEYS:
            # Every pair, not just the global extremes. Trading only min-against-max
            # makes the residual dispersion a function of ONE pair's freight cost, so
            # a line built between any other two provinces changes nothing between
            # them — the transport graph works and the price model cannot feel it.
            # At this province count all-pairs is free; at scale this is the greedy
            # priority pass the design specifies instead of a global solver.
            # Trade reaches wherever the graph reaches. A missing bourgeoisie makes a
            # province invisible to INVESTMENT (main doc §8 — finding out requires a
            # person with capital standing there) and to price REPORTING, both of which
            # are gated elsewhere. It does not stop a carter. Gating arbitrage on it was
            # a misreading, and it left provinces that produce none of a good and cannot
            # be traded to holding zero stock forever, pinned to the ceiling in 15% of
            # province-years — a constant, not a signal. Freight cost is what limits
            # trade to the remote, and it does so on its own.
            seen = [p for p in self.provs if p.key not in self.lost_provinces]
            # SEQUENTIAL settlement, not simultaneous. Executing every profitable
            # trade against prices computed before any of them moved makes a cheap
            # province a source in several pairs at once: it is drained repeatedly,
            # overshoots into being the dearest, and becomes the destination for
            # everything on the next pass. Measured, before this fix, one province
            # swung 13.3 -> 70.7 -> 12.0 -> 79.6 -> 88.0 across six iterations.
            #
            # That is the "oscillating prices" failure named in the very first answer
            # of the source conversation, and while it was happening no transport
            # improvement could show up in prices, because the prices were noise.
            #
            # So: re-check each trade against LIVE prices immediately before it
            # executes, and never move more than closes the gap.
            for _ in range(6):
                cands = []
                for i, a in enumerate(seen):
                    for b in seen[i + 1:]:
                        pa, pb = price_of(a, g), price_of(b, g)
                        lo, hi = (a, b) if pa < pb else (b, a)
                        if abs(pa - pb) > self.pair_cost(lo.key, hi.key) * FREIGHT_MULT:
                            cands.append((abs(pa - pb), lo, hi))
                if not cands:
                    break
                cands.sort(key=lambda t: -t[0])
                did = False
                for _sp, lo, hi in cands:
                    spread = price_of(hi, g) - price_of(lo, g)
                    cost = self.pair_cost(lo.key, hi.key) * FREIGHT_MULT
                    if spread <= cost:
                        continue          # an earlier trade already closed this one
                    # the move that equalises cover, damped so it cannot overshoot
                    cl, ch = lo.consumption(g), hi.consumption(g)
                    equalise = (lo.stocks[g] * ch - hi.stocks[g] * cl) / max(1e-6, cl + ch)
                    move = max(0.0, min(equalise * 0.6, lo.stocks[g] * 0.30))
                    if move <= 1e-6:
                        continue
                    lo.stocks[g] -= move
                    hi.stocks[g] += move * 0.96
                    did = True
                if not did:
                    break

        # unrest follows unmet need, weighted to grain
        for p in self.provs:
            w = welfare(p)
            p.grievance *= 0.94        # a generation to forget an enclosure
            p.unrest = max(0.0, min(1.0, p.unrest * 0.75 + (0.75 - w) * 1.1
                                    + p.grievance * 0.9))

    def _invest(self):
        """Increasing returns. Main doc §14: agglomeration is not a new system, it is
        labour pooling + supplier networks + spillover, which are the three networks
        already built with the distance term set to nearly zero. So the whole term is
        EXISTING CONCENTRATION, and it compounds — which is what makes the century's
        industrial geography path-dependent rather than a function of the trend."""
        INDUSTRIAL = ("coal", "iron", "cloth")
        pool = sum(p.pop for p in self.provs if p.key not in self.lost_provinces) * 0.011
        cands = []
        for p in self.provs:
            if p.key in self.lost_provinces:
                continue
            congestion = 1.0 + 1.9 * sum(p.industry.values())      # rent, wages, coal, cholera
            for g in INDUSTRIAL:
                if p.capacity[g] <= 0.01:
                    continue
                ob = self.beliefs.get_price(p.key, g)
                signal = (ob.value if ob else GOODS[g].ref_price) / GOODS[g].ref_price
                cluster = 1.0 + 2.4 * p.industry[g]               # the compounding term
                # a capitalist cannot invest where nobody is standing (main doc §8)
                sight = p.bourgeoisie
                # early flows are near-random and then lock in (main doc §13, on
                # corridors — the same logic governs where an industry settles). Without
                # this the largest starting bourgeoisie wins every seed, which is a trend,
                # not a path.
                luck = self.rng.uniform(0.55, 1.65)
                score = signal * cluster * (0.25 + sight) * luck / congestion
                if score > 0.01:
                    cands.append((score, p, g))
        tot = sum(c[0] for c in cands)
        if tot <= 0:
            return
        for score, p, g in cands:
            p.industry[g] += pool * (score / tot) * 0.055
            p.capacity[g] = p.capacity[g] * (1.0 + pool * (score / tot) * 0.045)
        # industry makes a bourgeoisie, and a bourgeoisie makes the province visible
        for p in self.provs:
            if p.key in self.lost_provinces:
                continue
            ind = sum(p.industry.values())
            p.bourgeoisie = min(0.95, p.bourgeoisie + ind * 0.010)

    def _events(self, y: int):
        rioting = [p for p in self.provs
                   if p.key not in self.lost_provinces
                   and p.unrest > 0.78 and self.rng.random() < 0.30]
        if rioting:
            names = ", ".join(p.name for p in rioting[:3])
            more = f" and {len(rioting)-3} others" if len(rioting) > 3 else ""
            self.log.append(f"Bread riots in {names}{more}. The magistrates ask for grain.")
            for p in rioting:
                # an unanswered riot is a standing claim, not a mood
                p.grievance = min(1.0, p.grievance + 0.10)
            self.holder["nobles"].consent = max(0.05, self.holder["nobles"].consent - 0.05)
        if self.rng.random() < 0.12 and not self.holder['clergy'].overridden:
            self.holder['clergy'].consent = max(0.05, self.holder['clergy'].consent - 0.08)
            self.log.append("A pastoral letter warns against the schools of the state.")

    def _reports(self, y: int):
        """Channels with different latencies and biases. Never reconciled."""
        for p in self.provs:
            # prices: honest, but only where there is trade and reach
            if self.year <= self.price_fixed_until:
                continue          # an administered price carries no information
            visible = p.bourgeoisie > 0.15 or "returns" in self.unlocked
            if visible and self.rng.random() < 0.9 - p.freight() * 0.2:
                for g in GOOD_KEYS:
                    self.beliefs.price[(p.key, g)] = Obs(price_of(p, g), y, "market")
                    self.beliefs.push(p.key, g, y, price_of(p, g))
                self.beliefs.wage_hist.setdefault(p.key, []).append((y, self.wage(p)))
            # governor's report: adversarial, inflated, and it hides unrest
            if self.rng.random() < 0.8:
                infl = 1.0 + 0.10 + p.noble_strength * 0.22
                self.beliefs.output[p.key] = Obs(p.stocks["grain"] * infl, y, "governor")
                self.beliefs.unrest[p.key] = Obs(p.unrest * (1.0 - p.noble_strength * 0.55), y, "governor")

    def _early_lesson(self, y: int):
        """Year 2: the receipts contradict the register. Costs the player almost nothing
        and teaches the one rule the whole game runs on."""
        if self.turn != 1 or self.taught:
            return
        worst, gap = None, 0.0
        for p in self.provs:
            o = self.beliefs.pop.get(p.key)
            if not o or o.value <= 0:
                continue
            d = (p.pop - o.value) / o.value
            if abs(d) > abs(gap):
                worst, gap = p, d
        if not worst or abs(gap) < 0.08:
            return
        self.taught = True
        o = self.beliefs.pop[worst.key]
        implied = o.value * (1.0 + gap * self.rng.uniform(0.55, 0.8))
        self.notice = [
            "THE RECEIPTS DO NOT AGREE WITH THE REGISTER",
            "",
            f"The excise returns from {worst.name} have come in.",
            "",
            f"Your register says {o.value*1000:,.0f} souls. It is a {o.source} of {o.year},",
            f"{self.year - o.year} years old, and nobody has counted them since.",
            "",
            f"The receipts imply nearer {implied*1000:,.0f}.",
            "",
            "You are not being told the true figure. You are being told",
            "that your figure is wrong, by a channel that had no reason to lie:",
            "the money actually collected.",
            "",
            "Reports are fast and self-serving. Receipts are slow and roughly honest.",
            "Prices are residue — what is left behind by people who can see what you cannot.",
            "",
            "A census would settle it. So would a railway. You cannot afford both.",
        ]

    def _categories(self, y: int):
        """A category is not a bonus. It is a question you could not previously ask."""
        for c in CATEGORIES:
            if c.key in self.unlocked:
                continue
            have = {"clerks": self.clerks, "register": self.register_quality}[c.needs]
            if have >= c.level:
                self.unlocked.add(c.key)
                self.notice = list(c.reveal)
                return

    # -- crises -----------------------------------------------------------
    def _crisis(self, y: int):
        if self.crisis or self.notice or self.turn < 3 or self.turn < self.crisis_cool:
            return
        self.crisis_cool = self.turn + 3
        urban = sum(p.pop * p.bourgeoisie for p in self.provs) / max(1e-6, self.true_pop())
        # Calibrated against the settled market, not the oscillating one. Before the
        # arbitrage fix grain routinely read 3-6x reference because prices were noise;
        # the threshold was set there, and once the market cleared it became
        # unreachable and this crisis silently stopped existing. Measured now:
        # median 1.18x, p90 2.19x, per-run peak 3.02x.
        dearth = (st_mean([price_of(p, "grain") for p in self.provs])
                  > GOODS["grain"].ref_price * 2.0
                  and self.mean_welfare() < 0.78)
        unrest = st_mean([p.unrest for p in self.provs])

        if dearth and self.rng.random() < 0.60:
            self.crisis = Crisis("dearth", "THE PRICE OF BREAD", [
                "Grain has run away from wages in most of the country.",
                "The magistrates write daily. The assize is being ignored.",
                "",
                "You have to decide what a government is for."],
                [Choice("reserve", "Open the reserve everywhere",
                        f"{self.reserve_grain:,.0f} qr in hand. Reach decides what arrives."),
                 Choice("buy", "Buy abroad on credit",
                        f"£{200 * (2.0 - self.credit):,.0f}, and it lands late."),
                 Choice("fix", "Fix the price of bread by proclamation",
                        "Instant relief. You will not see a price for three years."),
                 Choice("none", "Let it run", "The market clears. Some of them will not.")])
            return

        if y >= 1654 and urban > 0.38 and self.rng.random() < 0.22:
            self.crisis = Crisis("cholera", "A SICKNESS IN THE PORTS", [
                "It came up the river with the coasting trade and it is in the towns.",
                "Nobody can tell you how many are dying, because nobody counts the dead.",
                "",
                "You are being asked to act on a number you do not have."],
                [Choice("quarantine", "Quarantine the ports",
                        "Halts the contagion where reach allows. Trade stops with it."),
                 Choice("commission", "A sanitary commission",
                        "£120 and clerks you may not have. It will also start counting."),
                 Choice("none", "Trust to the season", "It has passed before.")])
            return

        if self.mean_literacy() > 0.20 and unrest > 0.16 and self.rng.random() < 0.28:
            self.crisis = Crisis("sedition", "THE PRESS AND THE MEETINGS", [
                "The reading rooms have become something else. There are petitions,",
                "and the petitions have printers, and the printers have subscribers.",
                "",
                "Your schoolmasters taught them to read. You paid for it."],
                [Choice("concede", "Concede, and be seen to concede",
                        "Unrest falls. Someone in the settlement gains a permanent hold."),
                 Choice("suppress", "Suppress the press and the associations",
                        "Needs an army. Costs consent, and you go blinder."),
                 Choice("none", "Do nothing and hope it passes", "It sometimes does.")])
            return

    def choose(self, key: str):
        """Resolve the standing crisis. Capacity decides whether the option works."""
        c = self.crisis
        if not c:
            return
        self.crisis = None
        out = []
        if key == "reserve":
            sent = self.reserve_grain
            arrived = 0.0
            for p in self.provs:
                if p.key in self.lost_provinces: continue
                give = sent / max(1, len(self.provs))
                a = give * max(0.25, 1.0 - p.freight() * 0.22)
                p.stocks["grain"] += a; p.unrest = max(0.0, p.unrest - 0.35); arrived += a
            self.reserve_grain = 0.0
            out = [f"{sent:,.0f} qr sent, {arrived:,.0f} arrived. Reach decided the rest."]
            if sent < 20: out.append("It was not enough. It was never going to be.")
        elif key == "buy":
            cost = 200 * (2.0 - self.credit)
            if self.treasury >= cost:
                self.treasury = max(0.0, self.treasury - cost)
                for p in self.provs:
                    if p.key not in self.lost_provinces:
                        p.stocks["grain"] += p.consumption("grain") * 0.35
                        p.unrest = max(0.0, p.unrest - 0.30)
                out = [f"£{cost:,.0f} spent. The grain lands late, and it lands."]
            else:
                self.credit *= 0.80
                out = ["You could not raise it. The refusal is now public,",
                       "and it will price into everything you borrow after this."]
        elif key == "fix":
            self.price_fixed_until = self.year + 3
            for p in self.provs:
                p.unrest = max(0.0, p.unrest - 0.45)
            out = ["The proclamation is obeyed, and the riots stop.",
                   "Your price returns stop with them. For three years you are",
                   "reading a number the state set, not one the country made."]
        elif key == "quarantine":
            r = self.link_value("census", "reach")
            for p in self.provs:
                p.pop *= 1.0 - 0.030 * (1.0 - r)
            self.treasury = max(0.0, self.treasury - 60)
            out = [f"The cordon holds where you could reach ({r*100:.0f}%).",
                   "Trade stops. So, mostly, does the sickness."]
        elif key == "commission":
            if self.treasury >= 120 and self.clerks > 0.25:
                self.treasury = max(0.0, self.treasury - 120)
                self.register_quality = min(1.0, self.register_quality + 0.22)
                for p in self.provs: p.pop *= 0.985
                self.unlocked.add("vital")
                out = ["The commission reports. The mortality was worse than believed.",
                       "It also leaves you a register of the dead — which is a register."]
            else:
                for p in self.provs: p.pop *= 0.965
                out = ["You had neither the money nor the clerks. It ran its course."]
        elif key == "concede":
            h = self.rng.choice([x for x in self.settlement if not x.overridden] or self.settlement)
            h.consent = min(1.0, h.consent + 0.20)
            h.price_cost *= 1.5
            for p in self.provs: p.unrest = max(0.0, p.unrest - 0.35)
            out = [f"It is settled. {h.name} were seen to obtain it,",
                   "and they will expect to be asked again."]
        elif key == "suppress":
            if self.army >= 1.4:
                for p in self.provs: p.unrest = max(0.0, p.unrest - 0.45)
                for h in self.settlement:
                    if not h.overridden: h.consent = max(0.05, h.consent - 0.10)
                self.clerks *= 0.85
                out = ["The meetings stop. So do the reports about the meetings.",
                       "Your picture of the country improves and the country does not."]
            else:
                for p in self.provs: p.unrest = min(1.0, p.unrest + 0.20)
                out = ["You did not have the men. The attempt was noticed."]
        else:
            for p in self.provs: p.unrest = min(1.0, p.unrest + 0.12)
            out = ["Nothing was done. It passed, or it did not."]

        if key != "none":
            self.crises_survived += 1
            # the ratchet: what a crisis forces you to build, you keep
            self.clerks = min(1.0, self.clerks + 0.05)
        self.notice = [c.title + " — RESOLVED", ""] + out

    def _army_upkeep(self):
        """Standing demand from the army, spread by where the people are."""
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        pop = sum(p.pop for p in live)
        for p in live:
            p.munitions_demand = self.army * 0.55 * (p.pop / max(1e-6, pop))

    def _munitions(self, at_war: bool):
        """Spread the army's demand over the provinces that can actually supply it.

        The army is a province-sized consumer that produces nothing (main doc §16).
        In peace it eats a trickle; at war it eats an order of magnitude more, and the
        shortfall is what decides whether the line holds — not a die roll.
        """
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        if not live:
            return 1.0
        want = self.army * (2.2 if at_war else 0.8)
        # Drawn from the national magazine, weighted toward what the army can reach.
        # Depleting the stock is what raises the price everywhere the market reaches —
        # so a war two provinces away shows up in a city that never sees a soldier.
        wt = {p.key: p.stocks["munit"] / (1.0 + p.freight()) for p in live}
        tot = sum(wt.values())
        if tot <= 1e-9:
            for p in live:
                p.munitions_demand = 0.0
            return 0.15
        got = 0.0
        for p in live:
            # never strip a magazine bare: the draw is weighted toward the closest
            # province, and emptying the capital every year pinned its price to the
            # ceiling in 125 province-years against 20 everywhere else.
            take = min(p.stocks["munit"] * 0.55, want * wt[p.key] / tot)
            p.stocks["munit"] -= take
            got += take
        return max(0.15, min(1.0, got / max(1e-6, want)))

    def _tilly(self, y: int):
        self._army_upkeep()
        self._munitions(at_war=False)
        self.war_in -= 1
        if self.war_in == 2:
            self.log.append("The neighbour is drilling. Your envoys advise it will be two years.")
        if self.war_in <= 0:
            held = sum(1 for p in self.provs if p.key not in self.lost_provinces)
            need = (1.0 + self.threat * 2.0 + (y - START_YEAR) * 0.055) * (held / 8.0) ** 0.55
            supplied = self._munitions(at_war=True)
            effective = self.army * (0.55 + 0.45 * supplied)
            if supplied < 0.85:
                self.log.append(
                    f"The magazines are at {supplied*100:.0f}%. The army fights on what it has.")
            if effective >= need:
                self.log.append("WAR. The line holds. The neighbour comes to terms.")
                self.treasury += 120
                self.holder['estates'].consent = min(1.0, self.holder['estates'].consent + 0.08)
            else:
                # The frontier is what you cannot hold: the province that costs most to
                # reach is the one that falls. Taking `provs[-1]` was list order, which
                # after the map grew meant losing a central textile town to an invasion
                # from the east.
                lost = max((p for p in self.provs if p.key not in self.lost_provinces),
                           key=lambda q: q.freight())
                self.lost_provinces.append(lost.key)
                self.log.append(f"WAR. The army was not enough. {lost.name} is ceded.")
                self._reflood()
                self.treasury = max(0.0, self.treasury - 150)
                self.credit *= 0.8
                self.holder['estates'].consent = max(0.05, self.holder['estates'].consent - 0.15)
            self.army *= 0.80
            self.threat += 0.12
            self.war_in = self.rng.randint(6, 9)

    # -- save / load ------------------------------------------------------
    SCALARS = ("year turn treasury reserve_grain rail_progress clerks masters engineers "
               "register_quality army credit threat war_in game_over ending taught admin_load "
               "price_fixed_until crises_survived crisis_cool").split()
    # `unlocked` is a set, handled separately in to_dict/load

    def to_dict(self) -> dict:
        return {
            "scalars": {k: getattr(self, k) for k in self.SCALARS},
            "lost": self.lost_provinces,
            "tax": self.tax,
            "unlocked": sorted(self.unlocked),
            "provs": [{"key": p.key, "pop": p.pop, "literacy": p.literacy,
                       "base_freight": p.base_freight, "railed": p.railed,
                       "unrest": p.unrest, "grievance": p.grievance, "tax_burden": p.tax_burden, "flood_cost": p.flood_cost, "stocks": p.stocks, "capacity": p.capacity,
                       "industry": p.industry, "bourgeoisie": p.bourgeoisie}
                      for p in self.provs],
            "settlement": [{"key": h.key, "consent": h.consent,
                            "price_cost": h.price_cost, "overridden": h.overridden}
                           for h in self.settlement],
            "beliefs": {
                "pop": {k: [o.value, o.year, o.source] for k, o in self.beliefs.pop.items()},
                "price": {f"{k}|{g}": [o.value, o.year, o.source]
                          for (k, g), o in self.beliefs.price.items()},
                "unrest": {k: [o.value, o.year, o.source] for k, o in self.beliefs.unrest.items()},
                "history": {f"{k}|{g}": v for (k, g), v in self.beliefs.history.items()},
                "wage_hist": self.beliefs.wage_hist,
            },
        }

    def save(self, path="save.json"):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)
        return f"Saved to {path}."

    class BadSave(Exception):
        """A save file that cannot be trusted. Carries what to tell the player."""

    @classmethod
    def load(cls, path="save.json"):
        """Return a Game, or raise BadSave with a sentence a player can read.

        Every malformed file used to come out of here as a raw exception —
        JSONDecodeError on a truncated write, KeyError on a save from a different
        map — straight through the L key, which had no guard. A corrupt save file
        killed the process and took the player's run with it. Worse, a save missing
        a province loaded *silently* as a partial world.
        """
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError) as ex:
            raise cls.BadSave(f"The save file is damaged and cannot be read ({ex.__class__.__name__}).")
        if not isinstance(d, dict):
            raise cls.BadSave("That file is not a saved game.")
        for req in ("scalars", "lost", "provs", "settlement", "beliefs"):
            if not isinstance(d.get(req), (dict, list)):
                raise cls.BadSave(f"That save is missing its {req}; it may be from another version.")
        g = cls(7)
        keys = {p.key for p in g.provs}
        saved = {pd.get("key") for pd in d["provs"] if isinstance(pd, dict)}
        if saved != keys:
            missing = ", ".join(sorted(keys - saved)) or "none"
            extra = ", ".join(sorted(saved - keys)) or "none"
            raise cls.BadSave(
                f"That save is of a different country (missing: {missing}; unknown: {extra}).")
        try:
            return cls._restore(g, d)
        except cls.BadSave:
            raise
        except Exception as ex:
            raise cls.BadSave(
                f"That save could not be read back ({type(ex).__name__}); it may be from another version.")

    @staticmethod
    def _restore(g, d):
        # Type-check against the live game rather than trusting the file. A scalar of
        # the wrong type ({"treasury": "lots"}) used to load cleanly and then crash a
        # dozen operations later, in arithmetic that had nothing to do with the cause.
        for k, v in d["scalars"].items():
            if not hasattr(g, k):
                raise Game.BadSave(f"That save mentions {k!r}, which this version does not have.")
            want = type(getattr(g, k))
            if want in (int, float):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise Game.BadSave(f"That save has a bad value for {k!r}.")
            elif want is bool:
                if not isinstance(v, bool):
                    raise Game.BadSave(f"That save has a bad value for {k!r}.")
            elif want is str:
                if not isinstance(v, str):
                    raise Game.BadSave(f"That save has a bad value for {k!r}.")
            setattr(g, k, v)
        g.lost_provinces = d["lost"]
        g.tax = d.get("tax", g.tax)
        g.unlocked = set(d.get("unlocked", []))
        for pd in d["provs"]:
            p = g.by_key[pd["key"]]
            for k in ("pop", "literacy", "base_freight", "railed", "unrest", "grievance", "tax_burden", "flood_cost", "stocks", "capacity",
                      "industry", "bourgeoisie"):
                setattr(p, k, pd[k])
        for hd in d["settlement"]:
            h = g.holder[hd["key"]]
            h.consent, h.price_cost, h.overridden = hd["consent"], hd["price_cost"], hd["overridden"]
        b = g.beliefs
        b.pop = {k: Obs(*v) for k, v in d["beliefs"]["pop"].items()}
        b.price = {tuple(k.split("|")): Obs(*v) for k, v in d["beliefs"]["price"].items()}
        b.unrest = {k: Obs(*v) for k, v in d["beliefs"]["unrest"].items()}
        b.history = {tuple(k.split("|")): [tuple(x) for x in v]
                     for k, v in d["beliefs"]["history"].items()}
        b.wage_hist = {k: [tuple(x) for x in v] for k, v in d["beliefs"]["wage_hist"].items()}
        return g

    def wage(self, p) -> float:
        return wage_of(p)

    def diagnose(self, key: str) -> list[str]:
        """Differential diagnosis (main doc §9). Reads only what the STATE has on file —
        never ground truth. Arranges the numbers the market hands you for free."""
        p = self.by_key[key]
        out = []
        gp = self.beliefs.get_price(key, "grain")
        if not gp:
            return ["No return from this province. Nothing to read."]
        ref = GOODS["grain"].ref_price
        others = [self.beliefs.get_price(k, "grain") for k in self.by_key if k != key]
        others = [o.value for o in others if o]
        national = sum(others) / len(others) if others else ref

        high_here = gp.value > ref * 1.45
        high_all = national > ref * 1.45
        if high_here and high_all:
            out.append("Grain is dear everywhere: a general harvest failure, not a local one.")
        elif high_here:
            out.append("Grain is dear HERE and not elsewhere — a local failure, or the road is cut.")
            fr = p.freight()
            if fr > 0.9:
                out.append(f"  Freight from here is {fr:.2f}. Suspect the route before the field.")
            else:
                out.append("  Freight is cheap, so the route is open. Suspect the harvest.")
        w = self.beliefs.wage_hist.get(key, [])
        if high_here and len(w) >= 3 and abs(w[-1][1] - w[-3][1]) < 0.4:
            out.append("  Wages have not moved. Supply is not the whole story — a class is")
            out.append("  being priced out. Relief, not a bounty on imports.")
        cl = self.beliefs.get_price(key, "cloth")
        if cl and gp.value > ref * 1.45 and cl.value < GOODS["cloth"].ref_price * 1.15:
            out.append("  Cloth is unmoved, so this is not a money event.")
        ub = self.beliefs.unrest.get(key)
        if ub and ub.value > 0.3:
            out.append("The magistrates report the district disturbed.")
        if not out:
            out.append("Nothing anomalous on file for this province.")
        return out

    # -- income -----------------------------------------------------------
    def tax_available(self, key: str) -> bool:
        """An instrument you cannot see well enough to use does not exist yet."""
        t = next(x for x in TAXES if x.key == key)
        return self.link_value("army", "legibility") >= t.needs

    def _apply_tax_burden(self):
        """Set each province's share of its wage taken by the state. Incidence differs
        by tier, which is the whole point — the same revenue hurts different people."""
        for p in self.provs:
            if p.key in self.lost_provinces:
                continue
            b = 0.0
            b += self.tax["excise"] * 0.30 * (1.25 - 0.5 * p.bourgeoisie)   # regressive
            b += self.tax["land"] * 0.26 * (1.30 - p.bourgeoisie)           # the countryside
            b += self.tax["income"] * 0.30 * (0.35 + p.bourgeoisie)         # where money is
            p.tax_burden = max(0.0, min(0.72, b))

    def revenue(self) -> float:
        """Excise on trade, plus whatever tiers the state can see well enough to levy."""
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        if not live:
            return 0.0
        trade = sum(sum(p.capacity[g] for g in GOOD_KEYS) * (0.25 + p.bourgeoisie * 0.7)
                    / max(0.5, p.freight() ** 0.4) for p in live)
        scale = trade / 90.0
        r = 0.0
        for t in TAXES:
            if not self.tax_available(t.key):
                continue
            rate = self.tax[t.key]
            # each tier saturates: you cannot get everything by raising one lever
            r += t.yield_per * scale * (1.0 - math.exp(-2.1 * rate))
        return r * (0.7 + 0.3 * self.credit)

    def _old_revenue(self) -> float:
        """Excise on trade — the cheap fiscal tier. You tax what moves, not what you cannot see."""
        r = 0.0
        for p in self.provs:
            if p.key in self.lost_provinces:
                continue
            traded = sum(p.capacity[g] for g in GOOD_KEYS) * (0.25 + p.bourgeoisie * 0.7)
            r += traded * 1.15 / max(0.5, p.freight() ** 0.4)
        return r * (0.7 + 0.3 * self.credit)

    def collect(self):
        self.treasury += self.revenue()

    # -- readouts ---------------------------------------------------------
    def epitaph(self) -> list:
        """Retrospective legibility (main doc §19). Uncertainty about the future is a
        game; mystery about the past is a bug report. So at the end, and only at the
        end, the state is told what was actually there."""
        out = []
        for p in self.provs:
            if p.key in self.lost_provinces:
                out.append((p.name, "ceded", "", RED_FLAG))
                continue
            o = self.beliefs.pop.get(p.key)
            if not o:
                out.append((p.name, "never counted", f"{p.pop*1000:,.0f} were there", RED_FLAG))
                continue
            err = (p.pop - o.value) / max(1e-6, o.value)
            note = f"{o.value*1000:,.0f} on file ({o.source} {o.year})"
            truth = f"{p.pop*1000:,.0f} actually"
            out.append((p.name, note, truth, abs(err)))
        return out

    def believed_pop(self) -> float:
        return sum(o.value for k, o in self.beliefs.pop.items() if k not in self.lost_provinces)

    def true_pop(self) -> float:
        return sum(p.pop for p in self.provs if p.key not in self.lost_provinces)

    def mean_literacy(self) -> float:
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        return sum(p.literacy * p.pop for p in live) / max(1e-6, sum(p.pop for p in live))

    def mean_welfare(self) -> float:
        live = [p for p in self.provs if p.key not in self.lost_provinces]
        return sum(welfare(p) for p in live) / max(1, len(live))
