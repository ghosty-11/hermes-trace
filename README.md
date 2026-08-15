# hermes-trace

[![Support this work](https://img.shields.io/badge/Support-EVM-6f42c1?logo=ethereum&logoColor=white)](#support-development)

`hermes-trace` is a Hermes Agent observer plugin that records per-session evidence of what an agent actually did. A running use-count cannot say which skills were available and not chosen, nor what a session cost; this plugin closes that gap by writing bounded, append-only artefacts next to the running profile.

## Status

Reference implementation of the hermes-trace observer. The deterministic suite covers profile attribution from the home path, accumulation across turn boundaries, finalize semantics, handler totality on hostile input, bounded capture, and configurability. Integration with a live Hermes deployment, retention behaviour, and operator review workflows are acceptance work for each deployment.

## What it records

The plugin writes two artefacts under the configured trace directory (`trace/` under `$HERMES_HOME` by default, overridable under the `trace:` config block):

- `events/YYYY-MM-DD.jsonl` — append-only, one JSON object per observed event. Each object carries `ts`, `kind`, `session_id`, `profile`, and event-specific bounded fields.
- `cards/<session_id>.yaml` — a compact TraceCard per session, summarising cumulative state at the last turn boundary or finalisation.

TraceCard fields, in order:

1. `session_id`
2. `profile`
3. `platform`
4. `final`
5. `boundaries`
6. `session_seconds`
7. `api_calls`
8. `api_seconds`
9. `tokens` (object with `input`, `output`, `cached`)
10. `context_peak`
11. `models`
12. `subagents`
13. `skills_activated`
14. `skills_exposed_count`
15. `skills_exposed_unused`
16. `tool_calls`
17. `tool_errors`
18. `errors`

## Boundary

`hermes-trace` is an observer. It registers only observer hooks, returns `None` from every handler, never gates a turn, makes no network calls, holds no lock across work, and caps its own disk footprint. It reads skill and config state; it writes only under the trace directory.

Honest limits:

- `session_seconds` is measured from the first event this process observed for the session id, so it is a floor, not the session's true age. A restart or a plugin loaded mid-session cannot recover unobserved work.
- `on_session_end` is a turn boundary, not a terminal event. On a persistent chat surface it may fire many times for one session id; the plugin accumulates across it and only releases state on `on_session_finalize` or `on_session_reset`.
- `tool_errors` is a heuristic on result text, not a status code. It may misclassify ambiguous outcomes.
- Profile attribution comes from the Hermes home path via the framework's `get_active_profile_name()`, never from a `HERMES_PROFILE` environment variable.

Disk footprint is bounded by the `trace:` config keys: `max_event_file_mb`, `keep_event_days`, `max_cards`, `max_field_chars`, and `max_tools_tracked`.

## Layout

- `plugin/`: Hermes observer plugin and manifest.
- `tests/`: behavioural tests that drive the plugin through the hook surface.
- `docs/`: installation, operations, specification, and compatibility guidance.
- `schemas/`: normative JSON Schema for trace events and TraceCards.

## Documentation

- [Installation](docs/installation.md)
- [Operations and retention](docs/operations.md)
- [Specification](docs/specification.md)
- [Compatibility](docs/compatibility.md)
- [Trace event schema](schemas/trace-event.schema.json)
- [Trace card schema](schemas/trace-card.schema.json)

## Verification

```sh
python3 -m unittest discover -s tests -v
```

The deterministic suite currently contains 18 tests. It asserts profile attribution from the Hermes home path, accumulation of totals across `on_session_end` turn boundaries, finalisation semantics, totality of every handler on hostile input, bounded capture of long values, and configurability of the trace directory and event stream.

## Support and security

Read [Support](SUPPORT.md) before filing an issue and report vulnerabilities through [Security](SECURITY.md). Do not publish trace files, tool arguments, tool results, skill names, model names, session identifiers, or deployment paths in either channel.

## Support development

If this package saves you time, you find it useful, or you want to help me cover the token costs of continued development, you can support the work with an EVM donation:

```text
0x9600c9bc632175941608a1b551cb0f018f0f40b4
```

Networks: Ethereum, Base, Polygon, and other EVM-compatible networks. Verify the address and selected network before sending; unsupported assets or networks may be unrecoverable.

## Provenance

This plugin was developed while operating a real self-hosted Hermes deployment. The two card/vocabulary influences are the Skill-BOM pattern (arXiv:2606.20631) and ClawTrace (arXiv:2604.23853).

It is an independent project and is not an official Nous Research project.

Licensed under the [MIT License](LICENSE).

<sub>Made with love, with help from AI agents.</sub>
