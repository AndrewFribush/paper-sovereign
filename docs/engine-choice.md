# Scaling

The fourteen-province slice uses a Python simulation and a pygame interface. The simulation imports only the standard library, so the interface can be replaced independently.

The original synthetic-world benchmark measured growth of **O(n^2.08)** in tick time. Pairwise market settlement is the main scaling problem. `game.bench` rebuilds synthetic worlds and reports fresh timings; `game.readme_check` checks that the measured exponent remains between 1.9 and 2.25. Absolute timings and fitted exponents vary with the machine and load.

The much larger 3,000-province map discussed during development is not implemented here. Changing the language alone would not remove the pairwise work. Any change to settlement would also need to preserve or deliberately revise the model's calibrated behavior, with the existing checks measuring the result.
