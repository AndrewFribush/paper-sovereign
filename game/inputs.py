"""Drive the real event handlers, not the draw calls.

`ui_smoke` in checks.py calls draw_* directly, so every key binding, panel toggle,
province hit-test and button rectangle in the game was reachable only by a person
with a mouse. This plays a whole game through UI.handle() with synthetic events and
asserts the state actually changed — a key that does nothing and a key that works
look identical to a smoke test that only asks whether anything crashed.

    python -m game.inputs
"""
import os, sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
from game.main import UI, LINES

FAILS: list[str] = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def key(ui, k, mod=0):
    return ui.handle(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod))


def click(ui, pos, button=1):
    return ui.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button))


def main():
    print("INPUT  (the handlers, driven as a player drives them)")
    ui = UI()

    # the brief must be dismissable, and only by the keys it advertises
    ui.frame()
    check("the brief does not advance on an unrelated key",
          key(ui, pygame.K_a) and ui.intro)
    key(ui, pygame.K_RETURN)
    check("ENTER leaves the brief", not ui.intro)

    # every panel toggle, by the key the footer advertises
    ui.frame()
    for k, flag in ((pygame.K_t, "ledger"), (pygame.K_p, "politics"),
                    (pygame.K_x, "exchequer")):
        key(ui, k)
        on = getattr(ui, flag)
        key(ui, pygame.K_ESCAPE)
        off = not getattr(ui, flag)
        check(f"{flag} opens and closes", on and off)
    key(ui, pygame.K_F1)
    check("F1 shows the truth", ui.inspector)
    key(ui, pygame.K_F1)

    # the appropriations column: select a line, then move money with the arrows
    ui.frame()
    key(ui, pygame.K_DOWN)
    check("DOWN selects the next line", ui.sel == 1)
    before = ui.g.budget[LINES[1].key]
    key(ui, pygame.K_RIGHT)
    step = ui.g.budget[LINES[1].key] - before
    check("RIGHT appropriates to the selected line", step > 0, f"+£{step:,.0f}")
    key(ui, pygame.K_RIGHT, mod=pygame.KMOD_SHIFT)
    big = ui.g.budget[LINES[1].key] - before - step
    check("SHIFT moves in larger steps", big > step, f"+£{big:,.0f} vs +£{step:,.0f}")
    key(ui, pygame.K_LEFT)
    check("LEFT takes it back", ui.g.budget[LINES[1].key] < before + step + big)
    key(ui, pygame.K_UP)
    check("UP selects the previous line", ui.sel == 0)

    # clicking a province opens its detail, and clicking away closes it
    ui.frame()
    live = [p for p in ui.g.provs if p.key not in ui.g.lost_provinces]
    target = live[3]
    r = ui.prov_rects[target.key]
    click(ui, r.center)
    check("clicking a province opens it", ui.detail == target.key,
          f"{ui.detail!r} vs {target.key!r}")
    ui.frame()
    click(ui, (r.center[0], r.center[1] + 200))
    check("clicking away closes it", ui.detail is None)

    # the appropriations column responds to the mouse, left up and right down
    ui.frame()
    col_x, row_y = 900, 128 + 2 * 66 + 20
    b = ui.g.budget[LINES[2].key]
    click(ui, (col_x, row_y), button=1)
    up = ui.g.budget[LINES[2].key]
    click(ui, (col_x, row_y), button=3)
    down = ui.g.budget[LINES[2].key]
    check("left-click appropriates, right-click withdraws",
          up > b and down < up, f"£{b:,.0f} -> £{up:,.0f} -> £{down:,.0f}")

    # the exchequer's rate buttons must move the rate they sit beside
    ui.exchequer = True
    ui.frame()
    moved = False
    for (sign, tk), rect in ui.tax_btn.items():
        if sign != "+":
            continue
        was = ui.g.tax[tk]
        click(ui, rect.center)
        if ui.g.tax[tk] > was:
            moved = True
        break
    check("an exchequer rate button raises its own rate", moved)
    ui.exchequer = False
    key(ui, pygame.K_ESCAPE)

    # the settlement: paying must cost money and buy consent
    ui.politics = True
    ui.frame()
    paid = None
    for (kind, hk), rect in ui.btn.items():
        if kind == "pay" and ui.g.can_pay(hk):
            before_t = ui.g.treasury
            before_c = ui.g.holder[hk].consent
            click(ui, rect.center)
            paid = (ui.g.treasury < before_t, ui.g.holder[hk].consent > before_c)
            break
    check("paying a veto-holder costs money and buys consent",
          paid is not None and all(paid), str(paid))
    ui.politics = False
    key(ui, pygame.K_ESCAPE)

    # save and load must round-trip through the keys that advertise them
    ui.frame()
    key(ui, pygame.K_s)
    year_at_save = ui.g.year
    key(ui, pygame.K_RETURN)      # end a turn so the loaded state differs
    ui.g.notice = []
    key(ui, pygame.K_l)
    check("S saves and L loads it back", ui.g.year == year_at_save,
          f"{ui.g.year} vs {year_at_save}")

    # A damaged or foreign save must not end the run in progress. This is the one
    # place a player can hand the game a file, and every malformed one used to come
    # back as a raw exception through the L key, which had no guard at all.
    import json, tempfile
    from game.sim import Game
    ui.frame()
    ui.g.save("save.json")
    good = json.load(open("save.json"))
    bad_saves = {
        "damaged":        "",
        "not a save":     '{"hello": "world"}',
        "truncated":      json.dumps(good)[: len(json.dumps(good)) // 2],
        "another map":    json.dumps({**good, "provs": good["provs"][:-1]}),
        "bad value":      json.dumps({**good, "scalars": {"treasury": "lots"}}),
    }
    survived, refused = True, 0
    year_before = ui.g.year
    # Identity, not field equality: a save of the same turn with one province cut out
    # loads with the same year and the same treasury, so comparing those counted a
    # silent partial load as a refusal. Refusing means keeping the game in hand.
    for name, text in bad_saves.items():
        open("save.json", "w").write(text)
        held = ui.g
        try:
            key(ui, pygame.K_l)
        except Exception:
            survived = False
            break
        if ui.g is held:
            refused += 1
    check("a damaged save is refused without ending the run",
          survived and refused == len(bad_saves),
          f"{refused} of {len(bad_saves)} refused" + ("" if survived else "  (one raised)"))
    open("save.json", "w").write(json.dumps(good))
    key(ui, pygame.K_l)
    check("and a good save still loads after a bad one", ui.g.year == year_before)

    # play the rest out through the handlers alone, answering crises by key and by
    # click in turn, and confirm the game actually ends
    crises_by_key = crises_by_click = 0
    click_ignored = [False]
    guard = 0
    while not ui.g.game_over and guard < 400:
        guard += 1
        ui.frame()
        if ui.g.crisis:
            if crises_by_key <= crises_by_click:
                key(ui, pygame.K_1)
                crises_by_key += 1
            else:
                btn = ui.crisis_btn
                if btn:
                    # counting the click is not evidence the click did anything: a
                    # handler that ignores the mouse leaves the crisis standing and
                    # the next pass answers it by key, so the counter still reads
                    # "one by click". Require the crisis to actually be gone.
                    was = ui.g.crisis
                    click(ui, next(iter(btn.values())).center)
                    if ui.g.crisis is not was:
                        crises_by_click += 1
                    else:
                        click_ignored[0] = True
                        key(ui, pygame.K_1)
                else:
                    key(ui, pygame.K_1)
            continue
        if ui.g.notice:
            key(ui, pygame.K_RETURN)
            continue
        key(ui, pygame.K_RETURN)
    check("a whole game plays out through the handlers", ui.g.game_over,
          f"{guard} events")
    check("crises answer to both the keyboard and the mouse",
          crises_by_key > 0 and crises_by_click > 0 and not click_ignored[0],
          f"{crises_by_key} by key, {crises_by_click} by click"
          + ("  (a click was ignored)" if click_ignored[0] else ""))

    # the end screen must still draw, and ESC must quit from it
    ui.frame()
    check("ESC quits from the end screen", key(ui, pygame.K_ESCAPE) is False)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
        return 1
    print("every handler does what the interface says it does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
