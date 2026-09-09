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
        Good("coal",  "Coal",   6.0, 1.0, 0.6, 0.45, 3.5, 0.90),
        Good("iron",  "Iron",  14.0, 1.1, 0.7, 0.45, 3.5, 0.94),
        Good("cloth", "Cloth", 20.0, 0.7, 0.5, 0.55, 2.8, 0.92),
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
            return self.pop * 0.14 * (0.3 + self.bourgeoisie)
        return 0.0


def build_world(rng: random.Random) -> list[Province]:
    """One country, eight provinces, deliberately varied so price spreads are readable."""
    P = Province
    provs = [
        P("cap",   "Aldermarch",  6, 2, 24.0, 0.34, 0.15, True,  0.85,
          {"grain": 8.0,  "cloth": 14.0, "coal": 0.0,  "iron": 2.0}, clergy_strength=0.35, noble_strength=0.20),
        P("weald", "Weald",       4, 1, 14.0, 0.16, 0.55, True,  0.30,
          {"grain": 30.0, "cloth": 1.0,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.55, noble_strength=0.70),
        P("hollin","Hollinghay",  8, 1, 16.0, 0.13, 0.70, False, 0.22,
          {"grain": 32.0, "cloth": 0.5,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.60, noble_strength=0.75),
        P("blackm","Blackmoor",   3, 3,  9.0, 0.19, 1.10, False, 0.35,
          {"grain": 4.0,  "cloth": 0.0,  "coal": 18.0, "iron": 1.0}, clergy_strength=0.30, noble_strength=0.45),
        P("ironby","Ironby",      9, 3,  8.0, 0.21, 1.25, False, 0.40,
          {"grain": 3.5,  "cloth": 0.0,  "coal": 3.0,  "iron": 11.0}, clergy_strength=0.30, noble_strength=0.50),
        P("stitch","Stitchford",  6, 4, 11.0, 0.28, 0.85, True,  0.60,
          {"grain": 3.0,  "cloth": 16.0, "coal": 0.0,  "iron": 0.0}, clergy_strength=0.25, noble_strength=0.25),
        P("marsh", "Marshend",    2, 5,  7.0, 0.09, 1.40, True,  0.10,
          {"grain": 13.0,  "cloth": 0.0,  "coal": 0.0,  "iron": 0.0}, clergy_strength=0.70, noble_strength=0.60),
        P("far",   "Cauldfell",  10, 5,  5.0, 0.05, 2.60, False, 0.03,   # the dark province
          {"grain": 6.0,  "cloth": 0.0,  "coal": 2.0,  "iron": 0.0}, clergy_strength=0.75, noble_strength=0.85),
    ]
    # every good's capacity is scaled to the surplus its own carry and target imply
    for g in GOOD_KEYS:
        need = sum(p.consumption(g) for p in provs)
        have = sum(p.capacity[g] for p in provs)
        if have > 0 and need > 0:
            k = need * GOODS[g].surplus / have
            for p in provs:
                p.capacity[g] *= k
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

    ratio = wage_of(prov) / max(1e-6, basket_cost(prov))
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

@dataclass
class Category:
    key: str
    name: str
    needs: str            # institution gating it
    level: float
    blurb: str
    reveal: list          # what the state learns, and wishes it had not

CATEGORIES = [
    Category("returns", "Trade returns", "clerks", 0.72,
             "Prices reported from provinces with no merchant of their own.",
             ["THE BOARD OF TRADE IS ESTABLISHED", "",
              "Until now you have read prices only where somebody had a reason",
              "to quote them. The dark provinces were dark because nobody there",
              "was trading on their own account.", "",
              "Your clerks will now collect returns from every district you hold.",
              "You will not like all of them."]),
    Category("vital", "Vital registration", "register", 0.80,
             "Births and burials. The population figure stops drifting between counts.",
             ["CIVIL REGISTRATION BEGINS", "",
              "Baptisms, marriages and burials were the church's books, and you",
              "read them only by its leave. Now they are yours.", "",
              "Your population figure will no longer go stale between counts —",
              "it will be corrected every year, by arithmetic you own."]),
    Category("cost", "Cost of living", "clerks", 0.92,
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

        self.budget: dict[str, float] = {l.key: 0.0 for l in LINES}
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

        self._initial_survey()

    # -- setup ------------------------------------------------------------
    def _initial_survey(self):
        """A stale, partial, systematically-biased picture. The frontier undercounts."""
        for p in self.provs:
            age = self.rng.randint(9, 34)
            bias = 1.0 - min(0.55, p.base_freight * 0.20)      # remote => undercount
            noise = self.rng.uniform(0.93, 1.07)
            self.beliefs.pop[p.key] = Obs(p.pop * bias * noise, self.year - age, "survey")
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
            if line_key == "census":  return min(1.0, 0.15 + self.clerks * 1.5)
            if line_key in ("railway", "land"): return min(1.0, 0.10 + self.engineers * 1.8)
            if line_key in ("schools",): return min(1.0, 0.05 + self.masters * 1.7)
            if line_key == "normal":  return min(1.0, 0.30 + self.masters * 0.8)
            return 1.0
        if link == "reach":
            tot = sum(1.0 / p.freight() for p in self.provs)
            best = sum(1.0 / (p.base_freight * 0.25) for p in self.provs)
            return max(0.12, min(1.0, (tot / best + self.supplied("reach")) / max(1.0, self.admin_load ** 0.8)))
        if link == "consent":
            v = 1.0
            for h in self.settlement:
                if h.blocks(line_key):
                    v = min(v, 1.0 if h.overridden else h.consent)
            return v
        if link == "legibility":
            # the parish register was the census apparatus long before the state had one
            return min(1.0, 0.10 + self.register_quality * 1.6 + self.supplied("legibility"))
        if link == "compliance":
            if line_key == "land":
                # the moral economy: commons, gleaning and wood-gathering were a real
                # fraction of poor subsistence, and a hungry province defends them hardest
                unrest = sum(p.unrest * p.pop for p in self.provs) / sum(p.pop for p in self.provs)
                hunger = 1.0 - self.mean_welfare()
                return max(0.08, min(1.0, 0.95 - unrest * 0.8 - hunger * 0.9
                                     + self.supplied("compliance")))
            # households only spare children where there is a return to reading
            lit_return = sum(p.bourgeoisie * p.pop for p in self.provs) / sum(p.pop for p in self.provs)
            return min(1.0, 0.18 + lit_return * 1.3 + self.supplied("compliance"))
        if link == "substrate":
            if line_key == "railway":
                iron = sum(p.stocks["iron"] for p in self.provs)
                need = sum(p.consumption("iron") for p in self.provs)
                return max(0.10, min(1.0, iron / max(1e-6, need * 1.2)))
            if line_key == "schools":
                return min(1.0, 0.25 + sum(p.bourgeoisie for p in self.provs) / len(self.provs))
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

        appropriated = sum(self.budget.values())
        if appropriated > self.treasury:
            scale = self.treasury / max(1e-6, appropriated)
            for k in self.budget:
                self.budget[k] *= scale
            self.log.append("The treasury would not bear it; appropriations were cut back.")

        # 1. conversion: money -> outcome, throughput = MIN over links
        for line in LINES:
            appro = self.budget[line.key]
            if appro <= 0:
                continue
            binding, bv = self.preview(line.key)
            wanted = appro / line.unit_cost
            through = wanted * bv
            spent = through * line.unit_cost
            self.treasury -= spent
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
        self.register_quality *= 0.965     # operating expenditure: goes stale fast

        self.turn += 1
        self.year += 1
        if self.turn >= N_TURNS:
            self.game_over = True
            self.ending = "The century turns. You hand on what you built."

    def _apply(self, key: str, through: float):
        if key == "census":
            self.clerks = min(1.0, self.clerks + through * 0.05)
            # legibility is one apparatus: counting people is what makes a muster roll possible
            self.register_quality = min(1.0, self.register_quality + through * 0.022)
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
            # the line is pushed outward from the capital: cheapest reach first,
            # because that is where the cost gradient actually lets it go
            for p in sorted(self.provs, key=lambda q: q.base_freight):
                if p.railed or p.key in self.lost_provinces:
                    continue
                if self.rail_progress >= 2.5:
                    self.rail_progress -= 2.5
                    p.railed = True
                    self.log.append(f"The line reaches {p.name}.")
                    self._reflood()
                else:
                    break
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
            self.army += through * 0.085
            self.register_quality = min(1.0, self.register_quality + through * 0.03)

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
                o = self.beliefs.pop.get(p.key)
                if o:
                    drift = 0.30 * (p.pop - o.value)
                    self.beliefs.pop[p.key] = Obs(o.value + drift, self.year, "registrar")
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
            seen = [p for p in self.provs
                    if p.bourgeoisie > 0.15 and p.key not in self.lost_provinces]
            for _ in range(4):
                trades = []
                for i, a in enumerate(seen):
                    for b in seen[i + 1:]:
                        pa, pb = price_of(a, g), price_of(b, g)
                        lo, hi = (a, b) if pa < pb else (b, a)
                        spread = abs(pa - pb)
                        cost = self.pair_cost(lo.key, hi.key) * 2.6
                        if spread > cost:
                            trades.append((spread - cost, lo, hi))
                if not trades:
                    break
                trades.sort(key=lambda t: -t[0])
                for _profit, lo, hi in trades:
                    move = min(lo.stocks[g] * 0.22, hi.consumption(g) * 0.55)
                    if move <= 0:
                        continue
                    lo.stocks[g] -= move
                    hi.stocks[g] += move * 0.96

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
        for p in self.provs:
            if p.unrest > 0.62 and self.rng.random() < 0.45:
                self.log.append(f"Bread riots in {p.name}. The magistrates ask for grain.")
                if True:
                    self.holder['nobles'].consent = max(0.05, self.holder['nobles'].consent - 0.04)
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
        dearth = (st_mean([price_of(p, "grain") for p in self.provs]) > GOODS["grain"].ref_price * 3.4
                  and self.mean_welfare() < 0.86)
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
                self.treasury -= cost
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
            self.treasury -= 60
            out = [f"The cordon holds where you could reach ({r*100:.0f}%).",
                   "Trade stops. So, mostly, does the sickness."]
        elif key == "commission":
            if self.treasury >= 120 and self.clerks > 0.25:
                self.treasury -= 120
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

    def _tilly(self, y: int):
        self.war_in -= 1
        if self.war_in == 2:
            self.log.append("The neighbour is drilling. Your envoys advise it will be two years.")
        if self.war_in <= 0:
            need = 1.0 + self.threat * 2.4 + (y - START_YEAR) * 0.075
            if self.army >= need:
                self.log.append("WAR. The line holds. The neighbour comes to terms.")
                self.treasury += 120
                self.holder['estates'].consent = min(1.0, self.holder['estates'].consent + 0.08)
            else:
                lost = [p for p in self.provs if p.key not in self.lost_provinces][-1]
                self.lost_provinces.append(lost.key)
                self.log.append(f"WAR. The army was not enough. {lost.name} is ceded.")
                self._reflood()
                self.treasury = max(0, self.treasury - 150)
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
            "unlocked": sorted(self.unlocked),
            "provs": [{"key": p.key, "pop": p.pop, "literacy": p.literacy,
                       "base_freight": p.base_freight, "railed": p.railed,
                       "unrest": p.unrest, "grievance": p.grievance, "flood_cost": p.flood_cost, "stocks": p.stocks, "capacity": p.capacity,
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

    @classmethod
    def load(cls, path="save.json"):
        if not os.path.exists(path):
            return None
        with open(path) as f:
            d = json.load(f)
        g = cls(7)
        for k, v in d["scalars"].items():
            setattr(g, k, v)
        g.lost_provinces = d["lost"]
        g.unlocked = set(d.get("unlocked", []))
        for pd in d["provs"]:
            p = g.by_key[pd["key"]]
            for k in ("pop", "literacy", "base_freight", "railed", "unrest", "grievance", "flood_cost", "stocks", "capacity",
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
    def revenue(self) -> float:
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
