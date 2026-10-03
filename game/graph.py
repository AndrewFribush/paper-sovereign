"""
The cost-flood graph — main doc §4.

The prototype previously carried one scalar `base_freight` per province, which is a
stand-in for the design's central object rather than the object itself. This is the
object: a multimodal network, node-split by mode, with transfer edges priced, flooded
from every port and railhead at once.

Four properties the scalar could not produce:

  1. **Water flattens the map.** Overland carriage was roughly an order of magnitude
     dearer per ton-mile than water. A river province three hundred miles inland can
     sit on the same isoline as the coast, while a province eighty miles inland with
     a ridge between is further from the world market than either.
  2. **A railway extends a finger, not a radius.** Development follows the track;
     provinces adjacent to but not on the line stay poor.
  3. **Transshipment is where the economics live.** Every mode change is forced through
     a transfer edge carrying break-of-bulk cost, so a line that stops short of a port
     is worth far less than one that reaches it — connectivity beats mileage.
  4. **Blockade is edge deletion.** Cut the ports and the flood no longer reaches the
     interior; import prices rise inland first and worst, with no naval subsystem.

Multi-source Dijkstra from every port and railhead at once gives every province its
cheapest access and the cost of getting there in a single pass — hinterlands, not routes.
"""
from __future__ import annotations
import heapq

# cost per unit of distance, by mode. The pre-rail water/road ratio is the single
# most load-bearing number in the whole transport model.
MODE_COST = {"road": 1.00, "river": 0.30, "rail": 0.26, "sea": 0.12}

# break-of-bulk: hand labour, wharfage, warehousing, spoilage, theft, delay.
# On short hauls this dominated the line haul entirely.
TRANSFER = {
    ("road", "river"): 0.45, ("river", "road"): 0.45,
    ("road", "rail"):  0.40, ("rail", "road"):  0.40,
    ("road", "sea"):   0.55, ("sea", "road"):   0.55,
    ("river", "sea"):  0.30, ("sea", "river"):  0.30,
    ("river", "rail"): 0.40, ("rail", "river"): 0.40,
    ("rail", "sea"):   0.25, ("sea", "rail"):   0.25,
}

# Month multipliers. Baltic ports ice, rivers freeze or run too low, and unmetalled
# roads in a wet spring were close to impassable for a loaded wagon.
SEASON = {
    "road":  [1.9, 1.9, 1.7, 1.4, 1.0, 1.0, 1.0, 1.0, 1.1, 1.3, 1.7, 1.9],
    "river": [3.0, 3.0, 1.6, 1.0, 1.0, 1.0, 1.0, 1.1, 1.0, 1.0, 1.4, 2.6],
    "sea":   [1.5, 1.5, 1.2, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 1.3, 1.5],
    "rail":  [1.1, 1.1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.1],
}


class Network:
    """Node-split multimodal graph. A node is (province_key, mode)."""

    def __init__(self, provs, edges, ports):
        self.provs = {p.key: p for p in provs}
        self.edges = edges          # (a, b, mode, distance)
        self.ports = set(ports)
        self._cache: dict = {}
        self._pw: dict = {}

    # -- construction -----------------------------------------------------
    def _adj(self, month: int, blockaded: set, lost: set):
        adj: dict = {}

        def link(u, v, w):
            adj.setdefault(u, []).append((v, w))
            adj.setdefault(v, []).append((u, w))

        modes_at: dict = {}
        for a, b, mode, dist in self.edges:
            if a in lost or b in lost:
                continue
            m = mode
            if m == "rail" and not (self.provs[a].railed and self.provs[b].railed):
                m = "road"          # the line is not there yet; the road still is
            w = dist * MODE_COST[m] * SEASON[m][month % 12]
            link((a, m), (b, m), w)
            modes_at.setdefault(a, set()).add(m)
            modes_at.setdefault(b, set()).add(m)

        for key in self.ports:
            if key in lost or key in blockaded:
                continue
            modes_at.setdefault(key, set()).add("sea")

        # transfer edges: every mode change inside a province is priced explicitly
        for key, modes in modes_at.items():
            ms = sorted(modes)
            for i, m1 in enumerate(ms):
                for m2 in ms[i + 1:]:
                    link((key, m1), (key, m2), TRANSFER.get((m1, m2), 0.5))
        return adj, modes_at

    # -- the flood --------------------------------------------------------
    def flood(self, month: int = 6, blockaded=frozenset(), lost=frozenset()):
        """Cheapest cost from every province to the world market, in one pass.

        Sources are the sea nodes of unblockaded ports, at cost zero. This is the
        catchment: what each province pays to reach a buyer, not a route to one.
        """
        key = (month, frozenset(blockaded), frozenset(lost),
               frozenset(k for k, p in self.provs.items() if p.railed))
        if key in self._cache:
            return self._cache[key]

        adj, modes_at = self._adj(month, set(blockaded), set(lost))
        dist: dict = {}
        pq = []
        for pk in self.ports:
            if pk in lost or pk in blockaded:
                continue
            n = (pk, "sea")
            dist[n] = 0.0
            heapq.heappush(pq, (0.0, n))
        while pq:
            d, n = heapq.heappop(pq)
            if d > dist.get(n, 1e18):
                continue
            for v, w in adj.get(n, ()):
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))

        out = {}
        for pk in self.provs:
            if pk in lost:
                continue
            best = min((dist.get((pk, m), 1e18) for m in modes_at.get(pk, ())),
                       default=1e18)
            out[pk] = 40.0 if best > 1e17 else best      # unreachable: prohibitive
        self._cache = {key: out}
        return out

    def pairwise(self, month: int = 6, blockaded=frozenset(), lost=frozenset()):
        """Cheapest cost between every pair of provinces.

        The flood answers "what does this province pay to reach a buyer". Arbitrage
        asks a different question — "what does it cost to move a ton from A to B" —
        and the two are not the same. Summing two access costs routes everything
        through the world market, which makes the interior look integrated when it
        is not, and leaves a railway nothing to improve. At this province count an
        all-pairs Dijkstra is free; at three thousand it is the N² problem the design
        already flags.
        """
        key = ("pw", month, frozenset(blockaded), frozenset(lost),
               frozenset(k for k, p in self.provs.items() if p.railed))
        if key in self._pw:
            return self._pw[key]
        adj, modes_at = self._adj(month, set(blockaded), set(lost))
        out: dict = {}
        for src in self.provs:
            if src in lost:
                continue
            dist = {}
            pq = []
            for m in modes_at.get(src, ()):
                dist[(src, m)] = 0.0
                heapq.heappush(pq, (0.0, (src, m)))
            while pq:
                d, n = heapq.heappop(pq)
                if d > dist.get(n, 1e18):
                    continue
                for v, w in adj.get(n, ()):
                    nd = d + w
                    if nd < dist.get(v, 1e18):
                        dist[v] = nd
                        heapq.heappush(pq, (nd, v))
            for dst in self.provs:
                if dst in lost:
                    continue
                best = min((dist.get((dst, m), 1e18) for m in modes_at.get(dst, ())),
                           default=1e18)
                out[(src, dst)] = 40.0 if best > 1e17 else best
        self._pw = {key: out}
        return out

    def invalidate(self):
        self._cache = {}
        self._pw = {}


def build_network(provs):
    """The map. Adjacency, modes and distances are content, not mechanism."""
    P = {p.key: p for p in provs}
    # (a, b, mode, distance)
    E = [
        ("weald",  "cap",    "road", 2.2), ("weald",  "blackm", "road", 2.4),
        ("hollin", "cap",    "road", 2.4), ("hollin", "ironby", "road", 2.2),
        ("cap",    "blackm", "road", 3.2), ("cap",    "ironby", "road", 3.2),
        ("cap",    "stitch", "road", 2.0), ("blackm", "stitch", "road", 3.1),
        ("ironby", "stitch", "road", 3.1), ("blackm", "marsh",  "road", 2.6),
        ("stitch", "marsh",  "road", 4.1), ("ironby", "far",    "road", 2.4),
        ("stitch", "far",    "road", 4.3),
        # the river: it is why the capital is where it is
        ("weald",  "cap",    "river", 2.6), ("cap", "stitch", "river", 2.3),
        ("stitch", "marsh",  "river", 4.4),
        # the line, once built
        ("weald",  "cap",    "rail", 2.2), ("hollin", "cap",  "rail", 2.4),
        ("cap",    "stitch", "rail", 2.0), ("cap",    "blackm", "rail", 3.2),
        ("cap",    "ironby", "rail", 3.2), ("stitch", "marsh", "rail", 4.1),
        ("ironby", "far",    "rail", 2.4),
        # -- the second ring -------------------------------------------------
        ("north",  "weald",  "road", 2.1), ("north",  "cap",    "road", 2.6),
        ("north",  "hollin", "road", 3.0), ("high",   "ironby", "road", 2.2),
        ("high",   "far",    "road", 2.8), ("high",   "hollin", "road", 3.4),
        ("loam",   "weald",  "road", 1.9), ("loam",   "blackm", "road", 1.8),
        ("loam",   "cap",    "road", 2.7), ("loam",   "marsh",  "road", 2.9),
        ("salt",   "stitch", "road", 2.2), ("salt",   "far",    "road", 3.0),
        ("salt",   "ironby", "road", 3.4), ("dun",    "marsh",  "road", 2.2),
        ("dun",    "blackm", "road", 2.4), ("thorn",  "cap",    "road", 1.8),
        ("thorn",  "stitch", "road", 1.7), ("thorn",  "ironby", "road", 2.6),
        ("thorn",  "hollin", "road", 2.9), ("thorn",  "blackm", "road", 3.0),
        # coastwise and river
        ("north",  "cap",    "river", 2.8), ("dun",    "marsh",  "river", 2.4),
        ("salt",   "stitch", "river", 2.4), ("loam",   "cap",    "river", 3.0),
        # the line, where it could go
        ("north",  "cap",    "rail", 2.6), ("thorn",  "cap",    "rail", 1.8),
        ("thorn",  "stitch", "rail", 1.7), ("loam",   "cap",    "rail", 2.7),
        ("high",   "ironby", "rail", 2.2), ("salt",   "stitch", "rail", 2.2),
        ("dun",    "marsh",  "rail", 2.2),
    ]
    E = [e for e in E if e[0] in P and e[1] in P]
    return Network(provs, E, ports=("cap", "marsh", "north", "dun", "salt"))
