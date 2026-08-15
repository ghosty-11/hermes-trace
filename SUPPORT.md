# Issue reports and support

Questions, corrections, compatibility reports, and bounded implementation defects are welcome through [GitHub issues](https://github.com/ghosty-11/hermes-trace/issues). Read [SECURITY.md](SECURITY.md) first for anything that could expose trace contents, tool arguments, tool results, skill names, model names, session identifiers, or deployment paths.

## Before opening an issue

1. Search existing issues and read the relevant specification, installation, operations, and compatibility sections.
2. Pin the repository revision and record current Hermes Agent, OMP, Python, and OS versions.
3. Re-run the deterministic suite and the smallest temporary-directory scenario that demonstrates the problem.
4. Replace profile names, session identifiers, paths, tool arguments, tool results, and config values with fictional data. Use generic example names such as `research`, `scribe`, or `assistant`.

A useful report includes the observed result, expected contract, exact public-safe reproduction, bounded command output, and what remains uncertain. Never attach trace files, cards, complete environment dumps, unbounded transcripts, or third-party personal data.

Issues are reviewed as time permits; there is no support SLA. This repository owns the observer plugin, schemas, and tests. Stack-level module selection belongs to Hermes Stackbook, cross-harness messaging belongs to hermes-mailbox, coding delegation belongs to hermes-omp-broker, and harness-specific defects may belong to the Hermes Agent or OMP upstream projects.
