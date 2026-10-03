# Provenance

Paper Sovereign is Andrew Fribush's economic and political simulation prototype, previously called Vicky. It was developed with AI coding and review assistance. Its design grew from an interest in the economic simulation of Victoria 2 and in the practical limits of state knowledge and administration.

This package was prepared on 2026-10-03 from the development snapshot based on commit `93c9f5e7201ef9ee94bb174ffb5f156f74ee3932`, plus the approved Paper Sovereign rename. It contains the playable slice rather than the larger proposed game.

All twelve `game/*.py` files are byte-identical to that renamed snapshot. Their SHA-256 digests are in [provenance/game-sha256.txt](provenance/game-sha256.txt). Simulation behavior, test scenarios, assertions, tolerances, and mutation definitions are unchanged.

The packaging adaptations are:

- A public README, pinned dependency declaration, and ignore rules provide setup and play instructions.
- Three short current-state notes replace the full development documents. They retain the measured claims and assertion counts used by the unchanged documentation check.
- The verification script is retained as `scripts/run-checks.sh`, with one diagnostic correction: a failed module's name comes from `$1` after the display label has been shifted away, rather than `$2`. Its check commands are unchanged. The new top-level `verify.sh` copies the code and required documentation into a temporary directory before running it and gives each run its own temporary-file directory. `--mutations` runs the existing mutation module in the same isolation.
- The screenshots come from the renamed game's existing `game.shots` renderer.
- A GitHub Actions workflow installs the pinned dependency and runs the default verification. Its actions are pinned to commit SHAs and its token has read-only contents permission.

Some source comments refer to numbered sections of the original design notes; those full notes are not included. Raw conversations, transcripts, private research and business plans, local tool settings, saved games, and development Git history are excluded. The package includes no external game's code or assets.
