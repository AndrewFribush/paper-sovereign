"""Render every panel to a PNG so the interface can be looked at, not just asserted about.

The check suite proves the simulation is sound. It cannot tell you that a label runs off
the edge of its column, that two panels disagree about where a number lives, or that the
end screen is unreadable. This drives the real UI headless and saves what it draws.

    python -m game.shots [outdir]

Every panel the game can show gets one frame. Nothing here touches sim state that the
game would not reach on its own — the turns are played, not fabricated.
"""
import os, sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
from game.main import UI, PARCH, LINES


def render(ui):
    """The draw dispatch from UI.run, minus the event loop."""
    ui.screen.fill(PARCH)
    if ui.intro:
        ui.draw_brief()
    elif ui.g.game_over:
        ui.draw_end()
    else:
        ui.draw_header()
        if ui.politics:    ui.draw_politics()
        elif ui.exchequer: ui.draw_exchequer()
        elif ui.ledger:    ui.draw_ledger()
        elif ui.detail:    ui.draw_detail()
        else:              ui.draw_map()
        ui.draw_budget(); ui.draw_log()
    if ui.g.crisis:   ui.draw_crisis()
    elif ui.g.notice: ui.draw_notice()
    pygame.display.flip()


def shot(ui, outdir, name):
    render(ui)
    path = os.path.join(outdir, name + ".png")
    pygame.image.save(ui.screen, path)
    print(f"  {name}")
    return path


def clear(ui):
    ui.politics = ui.exchequer = ui.ledger = False
    ui.detail = None


def advance(ui, n=1):
    """Play turns the way the player does — dismiss whatever the game puts in the way."""
    for _ in range(n):
        ui.g.notice = []
        if ui.g.crisis:
            ui.g.choose(ui.g.crisis.choices[0].key)
        if ui.g.game_over:
            return
        ui.g.end_turn()
        if not ui.g.game_over:
            ui.g.collect()


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "shots"
    os.makedirs(outdir, exist_ok=True)
    ui = UI()
    print(f"PANEL SHOTS -> {outdir}/")

    shot(ui, outdir, "01-brief")
    ui.intro = False
    shot(ui, outdir, "02-map-year-1")

    # A budget that actually spends, so later panels are not all zeroes.
    for line in LINES:
        ui.g.budget[line.key] = max(ui.g.budget[line.key], 60.0)
    advance(ui, 3)
    shot(ui, outdir, "03-map-year-4")

    ui.detail = ui.g.provs[0].key
    shot(ui, outdir, "04-province-detail")
    clear(ui)

    ui.ledger = True;    shot(ui, outdir, "05-ledger");    clear(ui)
    ui.exchequer = True; shot(ui, outdir, "06-exchequer"); clear(ui)
    ui.politics = True;  shot(ui, outdir, "07-politics");  clear(ui)

    ui.inspector = True
    shot(ui, outdir, "08-inspector")
    ui.inspector = False

    # Play forward looking for a crisis and a notice to photograph.
    got_crisis = got_notice = False
    for _ in range(20):
        if ui.g.game_over:
            break
        if ui.g.crisis and not got_crisis:
            shot(ui, outdir, "09-crisis"); got_crisis = True
        if ui.g.notice and not got_notice:
            shot(ui, outdir, "10-notice"); got_notice = True
        ui.g.notice = []
        if ui.g.crisis:
            ui.g.choose(ui.g.crisis.choices[0].key)
            continue
        ui.g.end_turn()
        if not ui.g.game_over:
            ui.g.collect()

    if not got_crisis: print("  (no crisis fired in this run — no shot)")
    if not got_notice: print("  (no notice fired in this run — no shot)")

    while not ui.g.game_over:
        advance(ui)
    shot(ui, outdir, "11-end")
    print(f"\n{len(os.listdir(outdir))} frames written")


if __name__ == "__main__":
    main()
