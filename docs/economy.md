# Economy

Each province has goods stocks, productive capacity, population, and local demand. Prices depend on stock cover. Production responds to prices, with lower elasticity for grain than for manufactured goods. Trade moves goods between provinces subject to freight costs from the transport graph.

The balanced-strategy reference measurement shows that cover sits at **1.01 of target** in the median year, with a grain price of **10.7 against a reference of 10**. `game.readme_check` recomputes the median cover and price across eight seeds and checks them against the original tolerances.

The model maintains nonnegative stocks and bounded prices. `game.checks` also requires prices to vary enough to carry information, since a price that spends most of the game at its cap cannot distinguish different shortages. Railway tests check changes in connectivity, freight, and prices together.

The welfare calculation uses both goods availability and purchasing power after tax. Land improvement can increase output while raising grievance. Heavy land-improvement spending still saturates unrest; that limitation is recorded in the verification note.
