# Verification

Recorded on 2026-10-03 on macOS, using Python 3.14.8 and pygame-ce 2.5.8 from an existing local environment. No dependency installation was performed during packaging.

`./verify.sh` passed in a separate copy of this package in 48 seconds. Its seven modules completed successfully: regression checks, reachable-content checks, adversarial play, input handling, rendered-frame bounds, documentation claims, and scaling. The [recorded output](provenance/verification.log) preserves the results; only the generated screenshot directory path has been replaced with a placeholder.

The regression module passed 48 assertions and reported its existing known open finding for unrest saturation under heavy land improvement. The input module passed 23 assertions, including refusal of five damaged saves. The documentation module passed all ten checks. The renderer produced fourteen frames with no text outside the window.

A sentinel `save.json` and file hashes confirmed that the verification launcher left the calling copy's save, Python modules, and other package files unchanged. No bytecode directory or mutation lock remained there. The launcher gives each run a separate working directory and `TMPDIR`.

All twelve packaged Python modules match the renamed development snapshot byte-for-byte. The included screenshots were inspected, and all relative Markdown links resolve. Shell syntax checks passed for `play.sh`, `verify.sh`, and `scripts/run-checks.sh`.

The final verification script includes a correction to the name printed when a module fails. That diagnostic-only correction received a shell syntax check; the recorded full run precedes it. No check commands or Python modules changed.

The optional twelve-mutation campaign was not rerun for this package. The GitHub Actions workflow is configured but has not run; the local result does not certify that separate CI environment.
