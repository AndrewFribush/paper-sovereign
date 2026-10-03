# Development history

The standalone public repository began with an October 3, 2026 snapshot. It also contains 53 filtered development commits from September 9, 2026. Their author and committer dates come from the original repository; they describe development dates, not earlier public releases.

The retained history covers the twelve `game/*.py` files. All 53 commits were made on September 9. Historical files still use the original Vicky name; the Paper Sovereign rename, public documentation, screenshots, and packaging belong to the October 3 snapshot.

Historical file paths and contents are preserved. Commit messages retain their technical descriptions, with private session links and two unrelated document/planning sections removed.

Each imported commit changes a retained file. Commits outside that scope and merges with no additional retained changes are omitted. Original author and committer identities and timestamps are preserved. Filtering changes commit IDs; [history-map.json](history-map.json) maps every retained source commit to its imported counterpart.

The import joins the historical branch to the existing public history with an October 3 merge. Existing public commits and the current code remain intact. Older snapshots may depend on parts of the original application that are outside this extraction; they were inspected as history, not built or tested as standalone packages.

The history includes mistakes and their repairs. In particular, imported commit `a7f543d8d0fa549516025dff3c145448d29bfc54` captured an instrument-cost mutation; the following retained commit restores the formula. Historical checkouts are not a series of verified releases.

[Browse the imported history](https://github.com/AndrewFribush/paper-sovereign/commits/5f1d18bb665435bd7eb19208cc095f72865d3bad). The extraction provenance describes the current runnable package.
