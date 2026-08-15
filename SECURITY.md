# Security policy

This package is a local observability boundary. It records tool names, clipped tool arguments and results, model names, token counts, and skill names. Trace output is as sensitive as the conversations it observes and must not be published, shared, or archived without review.

Reports about unauthorised trace directory access, information leakage via trace files, retention bypass, configuration injection, or unsafe installation guidance are security-relevant.

## Report privately

Use GitHub private vulnerability reporting for this repository when available. If it is not enabled, open a minimal issue requesting a private reporting channel. Do not put trace contents, tool arguments, tool results, session identifiers, skill names, model names, deployment paths, identities, or credentials in a public issue.

## Include

- affected revision and file or component;
- the violated trust boundary and observable result;
- Hermes Agent, OMP, Python, and OS versions that materially affect it;
- a minimal reproduction using fictional profile names such as `research`, `scribe`, or `assistant` and a temporary directory;
- whether trace files, cards, or Git history may already be exposed;
- the narrowest safe mitigation known.

Revoke or isolate exposed credentials and remove or restrict access to exposed trace files before reporting. Preserve evidence privately and use the upstream security channel when the defect belongs to [Hermes Agent](https://github.com/NousResearch/hermes-agent) or [OMP](https://github.com/can1357/oh-my-pi/blob/main/.github/SECURITY.md).

Only the current default branch is maintained. Deployment policy, retention settings, operator authorization, and the sensitivity of captured trace contents remain outside this repository.

## Operational security

The `trace:` config block controls disk footprint and retention. Review these keys before enabling the plugin in a shared or long-running deployment:

- `dir`: trace directory. Empty defaults to `<hermes_home>/trace`.
- `max_event_file_mb`: per-event-file size ceiling.
- `keep_event_days`: retention window for event files.
- `max_cards`: maximum TraceCards to retain.
- `max_field_chars`: per-field string clip limit.
- `max_tools_tracked`: maximum distinct tool names tracked per session.

These caps limit accidental growth; they do not make trace files safe to publish.
