"""
Vicky — thesis slice. pygame front end.

One country, one screen, twenty turns.
    F1  toggle the ground-truth inspector (real values beside believed ones)
    UP/DOWN or click   select a budget line
    LEFT/RIGHT         move money
    ENTER              end the year
    ESC                quit

Rendering rule from main doc §19: a believed number must NEVER be typographically
identical to a known number. Believed values are dimmer, italicised by colour, and
always carry a source and a date.
"""
from __future__ import annotations
import sys, os
import pygame

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from game.sim import (Game, LINES, LINE_BY_KEY, GOODS, GOOD_KEYS, TAXES,
                      price_of, welfare, LINK_NAMES, N_TURNS)
from statistics import mean as st_mean

W, H = 1280, 800
INK        = (34, 30, 26)
PARCH      = (233, 225, 208)
PARCH_DK   = (214, 203, 181)
KNOWN      = (28, 26, 22)      # a number you actually know
BELIEVED   = (126, 110, 84)    # a number you were told.  never the same colour.
STALE      = (158, 142, 116)
TRUTH      = (150, 40, 40)     # inspector only
GOLD       = (150, 112, 40)
RED        = (150, 46, 40)
GREEN      = (58, 100, 52)
RULE       = (188, 175, 152)


class UI:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Vicky — a government that cannot see its own country")
        self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        self.f_h1 = pygame.font.SysFont("Georgia,Times New Roman,serif", 30)
        self.f_h2 = pygame.font.SysFont("Georgia,Times New Roman,serif", 20)
        self.f    = pygame.font.SysFont("Georgia,Times New Roman,serif", 16)
        self.f_sm = pygame.font.SysFont("Georgia,Times New Roman,serif", 13)
        self.f_mono = pygame.font.SysFont("Menlo,Consolas,monospace", 14)
        self.g = Game(7)
        self.g.collect()
        self.sel = 0
        self.inspector = False
        self.hover_prov = None
        self.detail = None
        self.politics = False
        self.ledger = False
        self.exchequer = False
        self.intro = True
        self.crisis_btn: dict = {}
        self.btn: dict = {}
        self.prov_rects: dict = {}

    # -- helpers ----------------------------------------------------------
    def t(self, s, x, y, font=None, col=INK):
        font = font or self.f
        self.screen.blit(font.render(str(s), True, col), (x, y))

    def tr(self, s, x, y, font=None, col=INK):
        font = font or self.f
        surf = font.render(str(s), True, col)
        self.screen.blit(surf, (x - surf.get_width(), y))

    def rule(self, x, y, w):
        pygame.draw.line(self.screen, RULE, (x, y), (x + w, y), 1)

    # -- panels -----------------------------------------------------------
    def draw_header(self):
        g = self.g
        self.t(f"{g.year}", 28, 20, self.f_h1)
        self.t(f"Year {g.turn + 1} of {N_TURNS}", 120, 30, self.f_sm, STALE)

        self.t("TREASURY", 260, 22, self.f_sm, STALE)
        self.t(f"£{g.treasury:,.0f}", 260, 38, self.f_h2, KNOWN)
        self.t("REVENUE", 400, 22, self.f_sm, STALE)
        self.t(f"£{g.revenue():,.0f}", 400, 38, self.f_h2, KNOWN)
        self.t("ARMY", 530, 22, self.f_sm, STALE)
        self.t(f"{g.army:.1f}", 530, 38, self.f_h2, KNOWN)
        self.t("RESERVE", 620, 22, self.f_sm, STALE)
        self.t(f"{g.reserve_grain:,.0f} qr", 620, 38, self.f_h2, KNOWN)

        # the headline number the state believes about itself
        self.t("SUBJECTS", 760, 22, self.f_sm, STALE)
        bp = g.believed_pop()
        self.t(f"{bp*1000:,.0f}", 760, 38, self.f_h2, BELIEVED)
        if self.inspector:
            self.t(f"actually {g.true_pop()*1000:,.0f}", 760, 62, self.f_sm, TRUTH)

        self.t("WAR EXPECTED", 950, 22, self.f_sm, STALE)
        yrs = max(0, g.war_in)
        col = RED if yrs <= 2 else INK
        self.t(f"in {yrs} year{'s' if yrs != 1 else ''}", 950, 38, self.f_h2, col)
        need = 1.0 + g.threat * 3.2 + (g.year - 1650) * 0.10
        self.t(f"they will bring ~{need:.1f}", 950, 62, self.f_sm, STALE)

        self.rule(24, 84, W - 48)

    def draw_map(self):
        g = self.g
        x0, y0 = 30, 104
        self.t("THE COUNTRY", x0, y0, self.f_sm, STALE)
        self.t("as reported", x0 + 120, y0, self.f_sm, BELIEVED)
        self.prov_rects = {}
        cw, ch = 118, 74
        for p in g.provs:
            gx = x0 - 6 + p.x * 54
            gy = y0 + 24 + p.y * 82
            r = pygame.Rect(gx, gy, cw, ch)
            self.prov_rects[p.key] = r
            lost = p.key in g.lost_provinces
            bg = (206, 196, 176) if lost else PARCH_DK
            pygame.draw.rect(self.screen, bg, r, border_radius=3)
            if p.railed:
                pygame.draw.line(self.screen, GOLD, (r.left + 6, r.bottom - 5),
                                 (r.right - 6, r.bottom - 5), 3)
            pygame.draw.rect(self.screen, RULE, r, 1, border_radius=3)
            if lost:
                self.t(p.name[:11], r.x + 7, r.y + 5, self.f_sm, STALE)
                self.t("ceded", r.x + 7, r.y + 22, self.f_sm, RED)
                continue
            self.t(p.name[:11], r.x + 7, r.y + 4, self.f, KNOWN)

            ob = g.beliefs.get_pop(p.key)
            if ob:
                age = ob.age(g.year)
                col = BELIEVED if age < 12 else STALE
                self.t(f"{ob.value*1000:,.0f}", r.x + 7, r.y + 22, self.f_sm, col)
                self.t(f"{ob.source[:6]} {ob.year}", r.x + 7, r.y + 36, self.f_sm, STALE)
            else:
                self.t("numerous", r.x + 7, r.y + 22, self.f_sm, STALE)

            pb = g.beliefs.get_price(p.key, "grain")
            if pb:
                col = RED if pb.value > GOODS["grain"].ref_price * 1.6 else BELIEVED
                self.t(f"gr {pb.value:,.1f}", r.x + 7, r.y + 50, self.f_sm, col)
            else:
                self.t("no return", r.x + 7, r.y + 50, self.f_sm, STALE)

            ub = g.beliefs.unrest.get(p.key)
            if ub and ub.value > 0.35:
                pygame.draw.circle(self.screen, RED, (r.right - 12, r.y + 12), 4)

            if self.inspector:
                self.tr(f"{p.pop*1000:,.0f}", r.right - 6, r.y + 22, self.f_sm, TRUTH)
                self.tr(f"{price_of(p,'grain'):,.1f}", r.right - 6, r.y + 36, self.f_sm, TRUTH)
                self.tr(f"w{welfare(p):.2f}", r.right - 6, r.y + 50, self.f_sm, TRUTH)

    def spark(self, series, x, y, w, h, col):
        """A price series as the state has it on file. Gaps are gaps."""
        if len(series) < 2:
            self.t("no series", x, y + 2, self.f_sm, STALE); return
        vals = [v for _, v in series][-14:]
        lo, hi = min(vals), max(vals)
        rng = max(1e-6, hi - lo)
        pts = [(x + i * w / max(1, len(vals) - 1), y + h - (v - lo) / rng * h)
               for i, v in enumerate(vals)]
        pygame.draw.lines(self.screen, col, False, pts, 2)
        self.tr(f"{vals[-1]:,.1f}", x + w + 42, y + h / 2 - 8, self.f_sm, col)

    def draw_detail(self):
        g = self.g
        key = self.detail
        p = g.by_key[key]
        panel = pygame.Rect(24, 96, 654, 528)
        pygame.draw.rect(self.screen, PARCH_DK, panel, border_radius=4)
        pygame.draw.rect(self.screen, RULE, panel, 1, border_radius=4)
        x, y = panel.x + 18, panel.y + 14
        self.t(p.name, x, y, self.f_h1, KNOWN)
        self.tr("click again to close", panel.right - 16, y + 12, self.f_sm, STALE)
        y += 44

        ob = g.beliefs.get_pop(key)
        if ob:
            self.t("Subjects", x, y, self.f_sm, STALE)
            self.t(f"{ob.value*1000:,.0f}", x + 90, y - 3, self.f_h2, BELIEVED)
            self.t(f"{ob.source}, {ob.year} — {ob.age(g.year)} years old",
                   x + 210, y, self.f_sm, STALE)
            if self.inspector:
                self.tr(f"actually {p.pop*1000:,.0f}", panel.right - 16, y, self.f_sm, TRUTH)
        else:
            self.t("Subjects", x, y, self.f_sm, STALE)
            self.t("numerous", x + 90, y - 3, self.f_h2, STALE)
        y += 30
        self.t("Freight to the capital", x, y, self.f_sm, STALE)
        self.t(f"{p.freight():.2f}" + ("  — railed" if p.railed else ""), x + 210, y, self.f_sm, KNOWN)
        y += 26
        self.rule(x, y, panel.width - 36); y += 12

        self.t("PRICES ON FILE", x, y, self.f_sm, STALE)
        self.t("what the market reported, and when", x + 150, y, self.f_sm, BELIEVED)
        y += 22
        for gk in GOOD_KEYS:
            gd = GOODS[gk]
            pb = g.beliefs.get_price(key, gk)
            self.t(gd.name, x, y + 6, self.f, KNOWN)
            if pb:
                dear = pb.value > gd.ref_price * 1.45
                col = RED if dear else BELIEVED
                self.t(f"{pb.value:,.1f}", x + 84, y + 3, self.f_h2, col)
                self.t(f"({pb.year})", x + 150, y + 8, self.f_sm, STALE)
                self.t(f"usual {gd.ref_price:,.0f}", x + 200, y + 8, self.f_sm, STALE)
            else:
                self.t("no return", x + 84, y + 6, self.f, STALE)
            self.spark(g.beliefs.series(key, gk), x + 300, y, 240, 26,
                       RED if (pb and pb.value > gd.ref_price * 1.45) else BELIEVED)
            if self.inspector:
                self.tr(f"{price_of(p, gk):,.1f}", panel.right - 16, y + 6, self.f_sm, TRUTH)
            y += 34

        wh = g.beliefs.wage_hist.get(key, [])
        self.t("Wages", x, y + 6, self.f, KNOWN)
        if wh:
            self.t(f"{wh[-1][1]:,.1f}", x + 84, y + 3, self.f_h2, BELIEVED)
        self.spark(wh, x + 300, y, 240, 26, GOLD)
        y += 40
        self.rule(x, y, panel.width - 36); y += 12

        self.t("THE CLERK'S NOTE", x, y, self.f_sm, STALE)
        y += 20
        for line in g.diagnose(key):
            col = INK if not line.startswith("  ") else STALE
            self.t(line, x, y, self.f_sm, col)
            y += 17

        self.btn = {}
        rr = pygame.Rect(x, panel.bottom - 46, 200, 30)
        can = g.reserve_grain > 0.5
        pygame.draw.rect(self.screen, PARCH if not can else PARCH_DK, rr, border_radius=3)
        pygame.draw.rect(self.screen, RULE if not can else GOLD, rr, 1, border_radius=3)
        self.t(f"Open the reserve here", rr.x + 14, rr.y + 6, self.f, KNOWN if can else STALE)
        self.btn[("relief", key)] = rr
        self.t(f"{g.reserve_grain:,.0f} qr in the magazine  ·  "
               f"{(1.0 - p.freight() * 0.22) * 100:.0f}% of it would arrive here",
               rr.right + 16, rr.y + 8, self.f_sm, STALE)

    def draw_notice(self):
        s = pygame.Surface((W, H)); s.set_alpha(232); s.fill(PARCH)
        self.screen.blit(s, (0, 0))
        box = pygame.Rect(210, 130, 860, 540)
        pygame.draw.rect(self.screen, PARCH_DK, box, border_radius=4)
        pygame.draw.rect(self.screen, GOLD, box, 2, border_radius=4)
        x, y = box.x + 44, box.y + 40
        for i, line in enumerate(self.g.notice):
            if i == 0:
                self.t(line, x, y, self.f_h2, KNOWN); y += 40
                self.rule(x, y - 12, box.width - 88)
            else:
                self.t(line, x, y, self.f, INK if line else STALE)
                y += 24 if line else 10
        self.t("ENTER to go on", x, box.bottom - 42, self.f_sm, GOLD)

    def draw_crisis(self):
        g = self.g
        c = g.crisis
        s = pygame.Surface((W, H)); s.set_alpha(236); s.fill(PARCH)
        self.screen.blit(s, (0, 0))
        box = pygame.Rect(190, 110, 900, 580)
        pygame.draw.rect(self.screen, PARCH_DK, box, border_radius=4)
        pygame.draw.rect(self.screen, RED, box, 2, border_radius=4)
        x, y = box.x + 46, box.y + 38
        self.t(c.title, x, y, self.f_h1, KNOWN); y += 48
        self.rule(x, y - 8, box.width - 92); y += 10
        for line in c.lines:
            self.t(line, x, y, self.f, INK if line else STALE)
            y += 25 if line else 12
        y += 16
        self.crisis_btn = {}
        for i, ch in enumerate(c.choices):
            r = pygame.Rect(x, y, box.width - 92, 46)
            pygame.draw.rect(self.screen, PARCH, r, border_radius=3)
            pygame.draw.rect(self.screen, RULE, r, 1, border_radius=3)
            self.t(f"{i+1}", r.x + 14, r.y + 11, self.f_h2, GOLD)
            self.t(ch.label, r.x + 44, r.y + 6, self.f, KNOWN)
            self.t(ch.hint, r.x + 44, r.y + 26, self.f_sm, STALE)
            self.crisis_btn[ch.key] = r
            y += 52
        self.t("Press 1-4, or click.", x, box.bottom - 34, self.f_sm, STALE)

    def draw_brief(self):
        self.screen.fill(PARCH)
        x = 150
        self.t("VICKY", x, 96, self.f_h1, KNOWN)
        self.t("a government that cannot see its own country", x, 138, self.f_h2, STALE)
        self.rule(x, 176, 900)
        lines = [
            ("", 0),
            ("You are a state, in 1650. You do not build things. You fund them,", 0),
            ("and whether the money becomes anything depends on a chain of people", 0),
            ("and institutions you mostly do not own.", 0),
            ("", 0),
            ("The rule everything runs on:", 1),
            ("What exists where is common knowledge. Everyone knows Newcastle has coal.", 2),
            ("How many, how much, and at what price are measurements — and measurements", 2),
            ("cost money, arrive late, and are made by people with their own interests.", 2),
            ("", 0),
            ("So the numbers you are shown are not the numbers that are true.", 1),
            ("They carry a source and a date. Read both.", 2),
            ("", 0),
            ("Press F1 at any moment to see what is actually happening underneath.", 1),
            ("It is not cheating. It is there so you can tell a lie from a bug.", 2),
            ("", 0),
        ]
        y = 200
        for text, kind in lines:
            f = self.f_h2 if kind == 1 else self.f
            col = KNOWN if kind <= 1 else STALE
            self.t(text, x + (24 if kind == 2 else 0), y, f, col)
            y += 26 if text else 12
        self.rule(x, y + 8, 900)
        y += 26
        keys = [("ENTER", "begin, and end each year"), ("click a province", "prices on file, and the clerk's note"),
                ("T", "the ledger — every province at once"), ("P", "the settlement — who can block you"),
                ("F1", "the truth")]
        for k, v in keys:
            self.t(k, x, y, self.f, GOLD); self.t(v, x + 150, y, self.f_sm, STALE); y += 22
        self.t("Press ENTER to begin.", x, y + 22, self.f_h2, KNOWN)

    def draw_ledger(self):
        """The spread, all at once. Diagnosis is reading ACROSS provinces and goods —
        one province at a time cannot show you 'here' versus 'everywhere'."""
        g = self.g
        panel = pygame.Rect(24, 96, 654, 528)
        pygame.draw.rect(self.screen, PARCH_DK, panel, border_radius=4)
        pygame.draw.rect(self.screen, RULE, panel, 1, border_radius=4)
        x, y = panel.x + 16, panel.y + 12
        self.t("THE LEDGER", x, y, self.f_h1, KNOWN)
        self.tr("T to close", panel.right - 16, y + 12, self.f_sm, STALE)
        y += 40
        self.t("Everything the state has on file, side by side. Dearest first.",
               x, y, self.f_sm, STALE)
        y += 24

        cols = ([("Province", 0), ("Subjects", 132), ("age", 210)]
                + [(GOODS[g].name[:5], 262 + i * 60) for i, g in enumerate(GOOD_KEYS)]
                + [("Wage", 262 + len(GOOD_KEYS) * 60)])
        for label, dx in cols:
            self.t(label, x + dx, y, self.f_sm, STALE)
        y += 16
        self.rule(x, y, panel.width - 32); y += 8

        live = [p for p in g.provs if p.key not in g.lost_provinces]
        def grain(p):
            o = g.beliefs.get_price(p.key, "grain")
            return o.value if o else -1.0
        live.sort(key=grain, reverse=True)

        for p in live:
            self.t(p.name, x, y, self.f, KNOWN)
            ob = g.beliefs.get_pop(p.key)
            if ob:
                self.t(f"{ob.value*1000:,.0f}", x + 132, y, self.f_sm, BELIEVED)
                a = ob.age(g.year)
                self.t(f"{a}y", x + 210, y, self.f_sm, RED if a > 25 else STALE)
            else:
                self.t("numerous", x + 132, y, self.f_sm, STALE)
            for gi, gk in enumerate(GOOD_KEYS):
                dx = 262 + gi * 60
                o = g.beliefs.get_price(p.key, gk)
                ref = GOODS[gk].ref_price
                if not o:
                    self.t("—", x + dx, y, self.f_sm, STALE)
                    continue
                r = o.value / ref
                col = RED if r > 1.45 else (GOLD if r > 1.15 else
                                            (GREEN if r < 0.8 else BELIEVED))
                self.t(f"{o.value:,.1f}", x + dx, y, self.f_sm, col)
                if g.year - o.year > 1:
                    self.t(f"'{o.year % 100:02d}", x + dx + 40, y + 2, self.f_sm, STALE)
            wh = g.beliefs.wage_hist.get(p.key, [])
            self.t(f"{wh[-1][1]:,.1f}" if wh else "—", x + 262 + len(GOOD_KEYS) * 60, y,
                   self.f_sm, BELIEVED if wh else STALE)
            if self.inspector:
                self.tr(f"{price_of(p,'grain'):,.0f}", panel.right - 14, y, self.f_sm, TRUTH)
            y += 22

        y += 6
        self.rule(x, y, panel.width - 32); y += 10
        vals = [g.beliefs.get_price(p.key, "grain") for p in live]
        vals = [o.value for o in vals if o]
        if vals:
            lo, hi = min(vals), max(vals)
            self.t("Grain, across the country", x, y, self.f_sm, STALE)
            self.t(f"{lo:,.1f} to {hi:,.1f}", x + 200, y, self.f, KNOWN)
            self.t(f"spread {hi/max(0.01,lo):,.1f}x", x + 320, y, self.f_sm,
                   RED if hi / max(0.01, lo) > 6 else GOLD)
            y += 22
            n = sum(1 for v in vals if v > GOODS["grain"].ref_price * 1.45)
            if n >= len(vals) * 0.6:
                msg = "Dear almost everywhere: a general failure, or the money."
            elif n:
                msg = f"Dear in {n} of {len(vals)} on file: local. Check the roads in."
            else:
                msg = "Nothing on file looks like a dearth."
            self.t(msg, x, y, self.f_sm, INK)
            y += 20
        missing = [p.name for p in live if not g.beliefs.get_price(p.key, "grain")]
        if missing:
            self.t(f"No return at all from: {', '.join(missing)}", x, y, self.f_sm, RED)

    def draw_exchequer(self):
        g = self.g
        panel = pygame.Rect(24, 96, 654, 528)
        pygame.draw.rect(self.screen, PARCH_DK, panel, border_radius=4)
        pygame.draw.rect(self.screen, RULE, panel, 1, border_radius=4)
        x, y = panel.x + 18, panel.y + 14
        self.t("THE EXCHEQUER", x, y, self.f_h1, KNOWN)
        self.tr("X to close", panel.right - 16, y + 12, self.f_sm, STALE)
        y += 42
        self.t("You cannot tax what you cannot see. Each instrument needs an apparatus first.",
               x, y, self.f_sm, STALE)
        y += 14
        self.t(f"Revenue £{g.revenue():,.0f}", x, y + 10, self.f_h2, KNOWN)
        self.t(f"legibility {g.link_value('army','legibility')*100:.0f}%",
               x + 190, y + 16, self.f_sm, GOLD)
        y += 46
        self.tax_btn = {}
        for t in TAXES:
            ok = g.tax_available(t.key)
            box = pygame.Rect(x, y, panel.width - 36, 112)
            pygame.draw.rect(self.screen, PARCH if ok else (222, 214, 196), box, border_radius=3)
            pygame.draw.rect(self.screen, RULE, box, 1, border_radius=3)
            self.t(t.name, x + 14, y + 10, self.f_h2, KNOWN if ok else STALE)
            if not ok:
                self.t(f"unavailable — needs legibility {t.needs*100:.0f}%",
                       x + 300, y + 16, self.f_sm, RED)
            self.t(t.blurb, x + 14, y + 38, self.f_sm, STALE)
            self.t(f"falls on {t.incidence}", x + 14, y + 56, self.f_sm,
                   BELIEVED if ok else STALE)
            rate = g.tax[t.key]
            pygame.draw.rect(self.screen, PARCH_DK, (x + 14, y + 84, 300, 10), border_radius=2)
            if ok:
                pygame.draw.rect(self.screen, GOLD, (x + 14, y + 84, int(300 * rate), 10),
                                 border_radius=2)
            self.t(f"{rate*100:.0f}%", x + 326, y + 78, self.f, KNOWN if ok else STALE)
            if ok:
                minus = pygame.Rect(x + 380, y + 76, 30, 26)
                plus = pygame.Rect(x + 416, y + 76, 30, 26)
                for r, lab in ((minus, "-"), (plus, "+")):
                    pygame.draw.rect(self.screen, PARCH_DK, r, border_radius=3)
                    pygame.draw.rect(self.screen, RULE, r, 1, border_radius=3)
                    self.t(lab, r.x + 11, r.y + 3, self.f, KNOWN)
                self.tax_btn[("-", t.key)] = minus
                self.tax_btn[("+", t.key)] = plus
            y += 122

        burden = st_mean([p.tax_burden for p in g.provs
                          if p.key not in g.lost_provinces] or [0])
        self.rule(x, panel.bottom - 66, panel.width - 36)
        self.t(f"You are taking {burden*100:.0f}% of what your subjects earn.",
               x, panel.bottom - 54, self.f, RED if burden > 0.4 else INK)
        self.t("It is not available to them for bread. That is the whole trade.",
               x, panel.bottom - 30, self.f_sm, STALE)

    def draw_politics(self):
        g = self.g
        panel = pygame.Rect(24, 96, 654, 528)
        pygame.draw.rect(self.screen, PARCH_DK, panel, border_radius=4)
        pygame.draw.rect(self.screen, RULE, panel, 1, border_radius=4)
        x, y = panel.x + 18, panel.y + 14
        self.t("THE SETTLEMENT", x, y, self.f_h1, KNOWN)
        self.tr("P to close", panel.right - 16, y + 12, self.f_sm, STALE)
        y += 42
        self.t("Discretion is not a pool. It is the set of things you can do without asking.",
               x, y, self.f_sm, STALE)
        y += 26
        self.btn = {}
        for h in g.settlement:
            box = pygame.Rect(x, y, panel.width - 36, 138)
            pygame.draw.rect(self.screen, PARCH, box, border_radius=3)
            pygame.draw.rect(self.screen, RULE, box, 1, border_radius=3)
            self.t(h.name, x + 14, y + 10, self.f_h2, KNOWN)
            if h.overridden:
                self.t("OVERRIDDEN — they no longer obstruct, and no longer help",
                       x + 200, y + 16, self.f_sm, RED)
            else:
                pygame.draw.rect(self.screen, PARCH_DK, (x + 200, y + 20, 180, 8), border_radius=2)
                c = RED if h.consent < 0.45 else GOLD
                pygame.draw.rect(self.screen, c, (x + 200, y + 20, int(180 * h.consent), 8),
                                 border_radius=2)
                self.t(f"{h.consent*100:.0f}% consent", x + 392, y + 14, self.f_sm, c)

            blocks = ", ".join(LINE_BY_KEY[k].name for k in h.domain)
            self.t(f"Blocks:   {blocks}", x + 14, y + 40, self.f_sm, INK)
            sup = ", ".join(LINK_NAMES[k].lower() for k, v in h.supplies.items() if v > 0)
            self.t(f"Supplies: {sup or 'nothing'}", x + 14, y + 58, self.f_sm,
                   STALE if h.overridden else GREEN)
            self.t(f"Wants:    {h.price}", x + 14, y + 76, self.f_sm, STALE)

            if not h.overridden:
                pr = pygame.Rect(x + 14, y + 100, 150, 26)
                ok = g.can_pay(h.key)
                pygame.draw.rect(self.screen, PARCH_DK if ok else PARCH, pr, border_radius=3)
                pygame.draw.rect(self.screen, RULE, pr, 1, border_radius=3)
                self.t(f"Pay £{h.price_cost:,.0f}", pr.x + 12, pr.y + 4, self.f_sm,
                       KNOWN if ok else STALE)
                orr = pygame.Rect(x + 178, y + 100, 150, 26)
                pygame.draw.rect(self.screen, PARCH_DK, orr, border_radius=3)
                pygame.draw.rect(self.screen, RED, orr, 1, border_radius=3)
                self.t("Override", orr.x + 12, orr.y + 4, self.f_sm, RED)
                self.btn[("pay", h.key)] = pr
                self.btn[("ovr", h.key)] = orr
                self.t("cheap now, narrows you forever", x + 344, y + 105, self.f_sm, STALE)
            y += 148

    def draw_budget(self):
        g = self.g
        x0, y0 = 700, 104
        self.t("APPROPRIATIONS", x0, y0, self.f_sm, STALE)
        allocated = sum(g.budget.values())
        self.tr(f"£{allocated:,.0f} of £{g.treasury:,.0f}", W - 30, y0, self.f_sm,
                RED if allocated > g.treasury else KNOWN)
        y = y0 + 24
        for i, line in enumerate(LINES):
            sel = (i == self.sel)
            r = pygame.Rect(x0 - 6, y - 4, W - x0 - 24, 62)
            if sel:
                pygame.draw.rect(self.screen, PARCH_DK, r, border_radius=3)
            self.t(line.name, x0, y, self.f_h2 if sel else self.f, KNOWN)
            self.tr(f"£{g.budget[line.key]:,.0f}", W - 30, y, self.f_h2 if sel else self.f, GOLD)
            self.t(line.blurb, x0, y + 22, self.f_sm, STALE)

            binding, bv = g.preview(line.key)
            bar_x, bar_y = x0, y + 42
            pygame.draw.rect(self.screen, PARCH_DK, (bar_x, bar_y, 260, 8), border_radius=2)
            col = RED if bv < 0.4 else (GOLD if bv < 0.75 else GREEN)
            pygame.draw.rect(self.screen, col, (bar_x, bar_y, int(260 * bv), 8), border_radius=2)
            self.t(f"limited by {LINK_NAMES[binding].lower()}  {bv*100:.0f}%",
                   bar_x + 270, y + 38, self.f_sm, col)
            # A railway is all-or-nothing: 2.5 accumulated throughput buys one line and
            # a part-built line is no line. Without showing the accumulation, a player
            # funding it lightly pays for twenty years and sees nothing happen.
            if line.key == "railway":
                need = 2.5
                frac = min(1.0, g.rail_progress / need)
                self.t(f"survey {g.rail_progress:.1f} of {need:.1f} to the next line",
                       bar_x, y + 54, self.f_sm, GOLD if frac > 0.5 else STALE)
                pygame.draw.rect(self.screen, PARCH_DK, (bar_x + 200, y + 58, 90, 6),
                                 border_radius=2)
                pygame.draw.rect(self.screen, GOLD, (bar_x + 200, y + 58, int(90 * frac), 6),
                                 border_radius=2)
            y += 66 if line.key != "railway" else 74

        self.rule(x0, y + 2, W - x0 - 24)
        # When the material constraints are solved, consent becomes the universal
        # binder and the answer stops being money. Say so, once it is true.
        binding = [g.preview(l.key)[0] for l in LINES]
        n_consent = sum(1 for b in binding if b == "consent")
        if n_consent >= 3:
            self.t(f"Consent limits {n_consent} of {len(LINES)} lines. Money will not move them — press P.",
                   x0, y + 10, self.f_sm, RED)
        else:
            self.t("Money above the binding link is not spent.", x0, y + 10, self.f_sm, STALE)

        self.t("INSTITUTIONS", x0, y + 40, self.f_sm, STALE)
        inst = [("Clerks", g.clerks), ("Masters", g.masters), ("Engineers", g.engineers),
                ("Register", g.register_quality)]
        for j, (nm, v) in enumerate(inst):
            xx = x0 + (j % 2) * 260
            yy = y + 60 + (j // 2) * 22
            self.t(nm, xx, yy, self.f_sm, KNOWN)
            pygame.draw.rect(self.screen, PARCH_DK, (xx + 80, yy + 5, 120, 7), border_radius=2)
            pygame.draw.rect(self.screen, GOLD, (xx + 80, yy + 5, int(120 * min(1, v)), 7),
                             border_radius=2)

        cons = [(h.name.replace("The ", ""), 1.0 if h.overridden else h.consent)
                for h in g.settlement]
        self.t("CONSENT   (P for the settlement)", x0, y + 112, self.f_sm, STALE)
        for j, (nm, v) in enumerate(cons):
            xx = x0 + j * 175
            self.t(nm, xx, y + 130, self.f_sm, KNOWN)
            pygame.draw.rect(self.screen, PARCH_DK, (xx, y + 148, 150, 7), border_radius=2)
            c = RED if v < 0.4 else GOLD
            pygame.draw.rect(self.screen, c, (xx, y + 148, int(150 * min(1, v)), 7), border_radius=2)

    def draw_log(self):
        g = self.g
        y0 = H - 190
        self.rule(24, y0 - 10, W - 48)
        self.t("THE YEAR", 30, y0, self.f_sm, STALE)
        y = y0 + 20
        shown = [r for r in g.results if r.friction] + [r for r in g.results if not r.friction]
        for r in shown[:7]:
            ln = LINE_BY_KEY[r.line]
            if r.friction:
                self.t(f"{ln.name}: £{r.appropriated:,.0f} appropriated, £{r.spent:,.0f} spent — "
                       f"{r.friction}.", 30, y, self.f_sm, RED)
            else:
                self.t(f"{ln.name}: £{r.spent:,.0f} spent.", 30, y, self.f_sm, GREEN)
            y += 18
        room = 8 - len(g.results)
        for line in g.log[:max(1, room)]:
            self.t(line, 30, y, self.f_sm, INK)
            y += 18

        self.tr("F1 truth   T ledger   X exchequer   P politics   S/L save   ENTER end year", W - 30, H - 26,
                self.f_sm, STALE)
        if self.inspector:
            self.t("GROUND TRUTH  ·  red = simulation, not the state's belief", 30, H - 26, self.f_sm, TRUTH)

    def draw_end(self):
        g = self.g
        s = pygame.Surface((W, H)); s.set_alpha(240); s.fill(PARCH)
        self.screen.blit(s, (0, 0))
        self.t(f"{g.year}", 60, 40, self.f_h1)
        self.t(g.ending, 150, 50, self.f_h2, STALE)
        self.rule(60, 86, W - 120)

        # -- the country you made -----------------------------------------
        self.t("THE COUNTRY YOU MADE", 60, 100, self.f_sm, STALE)
        rows = [("Literacy", f"{g.mean_literacy()*100:.1f}%"),
                ("Welfare of your people", f"{g.mean_welfare()*100:.0f}%"),
                ("Treasury", f"£{g.treasury:,.0f}"),
                ("Provinces held", f"{8 - len(g.lost_provinces)} of 8"),
                ("Sovereign credit", f"{g.credit:.2f}"),
                ("Register", f"{g.register_quality*100:.0f}%")]
        y = 124
        for label, val in rows:
            self.t(label, 60, y, self.f_sm, INK)
            self.t(val, 250, y - 3, self.f_h2, KNOWN)
            y += 30
        self.t("There is no score. A single number would encode a politics.",
               60, y + 6, self.f_sm, STALE)

        # -- where the money went -----------------------------------------
        self.t("WHERE THE MONEY WENT", 400, 100, self.f_sm, STALE)
        y = 124
        tot = sum(g.spent_total.values()) + sum(g.wasted_total.values())
        for line in LINES:
            sp, wa = g.spent_total[line.key], g.wasted_total[line.key]
            if sp + wa < 1:
                continue
            self.t(line.name, 400, y, self.f_sm, INK)
            self.tr(f"£{sp:,.0f}", 620, y, self.f_sm, KNOWN)
            if wa > 1:
                self.tr(f"£{wa:,.0f} unspent", 740, y, self.f_sm, RED)
            y += 22
        if tot:
            worst = max(LINES, key=lambda l: g.wasted_total[l.key])
            if g.wasted_total[worst.key] > tot * 0.06:
                self.t(f"Most of what you could not spend was on {worst.name.lower()}.",
                       400, y + 8, self.f_sm, STALE)
                self.t("The money was never the constraint.", 400, y + 26, self.f_sm, STALE)

        # -- what you never found out --------------------------------------
        self.rule(60, 400, W - 120)
        self.t("WHAT YOU NEVER FOUND OUT", 60, 414, self.f_sm, STALE)
        self.t("what the register said, and what was there", 340, 414, self.f_sm, TRUTH)
        y = 438
        for name, note, truth, err in g.epitaph():
            col = TRUTH if (err is not None and err != 0 and (err == 9.99 or err > 0.15)) else INK
            self.t(name, 60, y, self.f, KNOWN)
            self.t(note, 220, y + 2, self.f_sm, BELIEVED)
            self.t(truth, 470, y + 2, self.f_sm, col)
            if isinstance(err, float) and err < 9 and err > 0.001:
                self.t(f"{err*100:.0f}% out", 700, y + 2, self.f_sm,
                       TRUTH if err > 0.15 else STALE)
            y += 24

        e = abs(g.believed_pop() - g.true_pop()) / max(1e-6, g.true_pop())
        self.t(f"You governed {g.true_pop()*1000:,.0f} people believing there were "
               f"{g.believed_pop()*1000:,.0f}.", 60, y + 16, self.f_h2, TRUTH)
        self.t(f"After twenty years and everything you spent, you were still {e*100:.1f}% out.",
               60, y + 46, self.f, INK)
        self.t("ESC to quit", 60, H - 34, self.f_sm, STALE)

    # -- input ------------------------------------------------------------
    def step(self, delta):
        g = self.g
        key = LINES[self.sel].key
        g.budget[key] = max(0.0, g.budget[key] + delta)

    def run(self):
        while True:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    return
                if e.type == pygame.KEYDOWN:
                    if self.g.crisis:
                        for i, ch in enumerate(self.g.crisis.choices):
                            if e.key == getattr(pygame, f"K_{i+1}"):
                                self.g.choose(ch.key); break
                        continue
                    if self.g.notice:
                        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            self.g.notice = []
                        continue
                    if self.intro:
                        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                            self.intro = False
                        elif e.key == pygame.K_ESCAPE:
                            return
                        continue
                    if e.key == pygame.K_ESCAPE:
                        if self.detail or self.politics or self.ledger or self.exchequer:
                            self.detail = None
                            self.politics = False
                            self.ledger = False
                            self.exchequer = False
                            continue
                        return
                    if e.key == pygame.K_F1:
                        self.inspector = not self.inspector
                    if e.key == pygame.K_s:
                        self.g.log.insert(0, self.g.save())
                    if e.key == pygame.K_l:
                        loaded = self.g.__class__.load()
                        if loaded:
                            self.g = loaded
                            self.detail = None; self.politics = False
                            self.g.log.insert(0, "Loaded.")
                    if e.key == pygame.K_p:
                        self.politics = not self.politics
                        self.detail = None; self.ledger = False
                    if e.key == pygame.K_t:
                        self.ledger = not self.ledger
                        self.detail = None; self.politics = False; self.exchequer = False
                    if e.key == pygame.K_x:
                        self.exchequer = not self.exchequer
                        self.detail = None; self.politics = False; self.ledger = False
                    if self.g.game_over:
                        continue
                    if e.key in (pygame.K_DOWN, pygame.K_j):
                        self.sel = (self.sel + 1) % len(LINES)
                    if e.key in (pygame.K_UP, pygame.K_k):
                        self.sel = (self.sel - 1) % len(LINES)
                    mult = 100 if (e.mod & pygame.KMOD_SHIFT) else 25
                    if e.key == pygame.K_RIGHT:
                        self.step(mult)
                    if e.key == pygame.K_LEFT:
                        self.step(-mult)
                    if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        self.g.end_turn()
                        if not self.g.game_over:
                            self.g.collect()
                if e.type == pygame.MOUSEBUTTONDOWN and self.g.crisis:
                    for k, r in getattr(self, "crisis_btn", {}).items():
                        if r.collidepoint(e.pos):
                            self.g.choose(k); break
                    continue
                if (e.type == pygame.MOUSEBUTTONDOWN and not self.g.game_over
                        and not self.intro and not self.g.notice):
                    mx, my = e.pos
                    if mx >= 694:                       # the appropriations column
                        for i, line in enumerate(LINES):
                            top = 128 + i * 66
                            if top <= my <= top + 62:
                                self.sel = i
                                if e.button == 1: self.step(25)
                                if e.button == 3: self.step(-25)
                                break
                    elif self.exchequer:
                        for (sign, tk), r in getattr(self, "tax_btn", {}).items():
                            if r.collidepoint(mx, my):
                                d = 0.05 if sign == "+" else -0.05
                                self.g.tax[tk] = max(0.0, min(1.0, self.g.tax[tk] + d))
                                self.g._apply_tax_burden()
                                break
                    elif self.politics:
                        for (kind, hk), r in self.btn.items():
                            if r.collidepoint(mx, my):
                                if kind == "pay": self.g.pay(hk)
                                else: self.g.override(hk)
                                break
                    elif self.detail:
                        hit = None
                        for (kind, hk), r in self.btn.items():
                            if kind == "relief" and r.collidepoint(mx, my):
                                hit = hk
                        if hit:
                            self.g.log.insert(0, self.g.relieve(hit))
                        else:
                            self.detail = None
                    else:
                        for k, r in self.prov_rects.items():
                            if r.collidepoint(mx, my) and k not in self.g.lost_provinces:
                                self.detail = k
                                break

            self.screen.fill(PARCH)
            if self.intro:
                self.draw_brief()
                pygame.display.flip(); self.clock.tick(60); continue
            if self.g.game_over:
                self.draw_end()
            else:
                self.draw_header()
                if self.politics: self.draw_politics()
                elif self.exchequer: self.draw_exchequer()
                elif self.ledger: self.draw_ledger()
                elif self.detail: self.draw_detail()
                else: self.draw_map()
                self.draw_budget(); self.draw_log()
            if self.g.crisis:
                self.draw_crisis()
            elif self.g.notice:
                self.draw_notice()
            pygame.display.flip()
            self.clock.tick(60)


if __name__ == "__main__":
    UI().run()
    pygame.quit()
