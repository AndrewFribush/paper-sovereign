# Verification and limits

The default `./verify.sh` runs the following existing modules in a temporary copy of the package.

| Module | Coverage |
|---|---|
| `game.checks` | 48 assertions over 20 seeds and three budget mixes, covering bounds, mechanisms, strategy differences, persistence, and UI rendering |
| `game.deadcontent` | Unreachable systems, permanently closed gates, crisis choices, and endings |
| `game.adversarial` | Extreme allocations, institutional choices, taxes, and repeated save/load |
| `game.inputs` | 23 assertions that the controls do what the interface says, including rejection of five damaged saves |
| `game.shots` | Fourteen rendered frames; every text rectangle must stay inside the window |
| `game.readme_check` | Ten checks of measured documentation claims and test counts |
| `game.bench` | Synthetic-world timings and scaling estimates |

`./verify.sh --mutations` runs `game.mutants` separately. It restores each injected regression after measurement, checks that the verification code did not change during the run, and reports undetected mutations. Removing the treasury clamp is explicitly marked as an expected miss because appropriations have already been limited to available funds. The runner's output, rather than the presence of the test file, establishes current mutation coverage.

One model issue remains recorded by `game.checks`: sufficiently heavy land-improvement spending drives unrest to its 1.0 cap. The test reports it as a known open finding and checks that it has not worsened. Ordinary allocations can pass the suite while that finding remains. The market algorithm's roughly quadratic growth is a separate limit on map size.

This file keeps the original `BUILD-LOG.md` path because the unchanged documentation check reads the assertion counts here. It contains the package's current verification contract rather than the private development log.
