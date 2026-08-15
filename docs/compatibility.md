# Compatibility

## Reference baseline

- Python 3.11 or newer on a POSIX host.
- Hermes Agent standalone plugin API with `register_hook`.

## Defensive hook registration

The plugin registers ten observer hooks. Registration is defensive: a hook this Hermes build
lacks is skipped, so the plugin loads on builds where the hook set differs. The cost is a
missing field rather than a failed load.

## Hook dependencies and card impact

| Hook | Card fields populated by the hook | Fields left empty if absent |
|---|---|---|
| `on_session_start` | `platform`, `skills_exposed_count`, `skills_exposed_unused` | `platform` reports `unknown`; skill-exposure fields report zero or empty. |
| `on_session_end` | `boundaries`, non-final card writes | `boundaries` stays zero; no card is written until a terminal hook. |
| `on_session_finalize` | Final card write and accumulator release | No final card; memory is not released for this id. |
| `on_session_reset` | Final card write and accumulator release | No final card; memory is not released for this id. |
| `pre_tool_call` | `tool_calls` | `tool_calls` stays empty. |
| `post_tool_call` | `tool_errors` | `tool_errors` stays empty. |
| `post_api_request` | `api_calls`, `api_seconds`, `tokens`, `context_peak`, `models` | All report zero or empty. |
| `api_request_error` | `errors` | `errors` stays empty. |
| `on_skill_lifecycle` | `skills_activated` | `skills_activated` stays empty. |
| `subagent_start` | `subagents` | `subagents` stays zero. |

Profile attribution is independent of hooks: it is derived from the Hermes home path via the
framework's `get_active_profile_name()` helper when available.

## Evidence level

The hook set was read from the installed framework's plugin registry. Profile attribution
was verified against an installed framework across four profile homes. No clean-host golden
path is claimed; run the deterministic suite and one live profile smoke on the target host
before enabling unattended use.
