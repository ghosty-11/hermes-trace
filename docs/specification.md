# Hermes Trace specification

## Artefacts

The plugin writes two plain files under the configured trace directory (`$HERMES_HOME/trace`
by default):

- `events/YYYY-MM-DD.jsonl` — append-only, one JSON object per line.
- `cards/<session_id>.yaml` — a compact TraceCard per session id.

All writes are local, atomic where the filesystem supports rename, and best-effort: the
plugin drops data rather than raise or block a turn.

## Event record

Every event object carries these fields:

| Field | Type | Meaning |
|---|---|---|
| `ts` | number | Unix timestamp in seconds, rounded to milliseconds. |
| `kind` | string | Event kind; one of the values in the next section. |
| `session_id` | string | The Hermes session id. |
| `profile` | string | The Hermes profile name, derived from the home path. |

`kind`-specific fields are listed below. Optional fields may be `null` or an empty value;
required fields are always present.

## Content-free capture

Events are the wide-open artefact, so every field is an allowlisted content-free value,
projected BEFORE any formatting: identifiers (bounded to 128 characters), booleans,
numbers and null. Tool arguments and results appear only as counts and sizes; arbitrary
error text is reduced to its exception class and length; any other object is omitted.
Clipping is a size bound, not a redaction — a clip window over a secret keeps the
secret — so there is no clip window, and `str()`/`default=str` never runs downstream of
the gate. A value that cannot be projected is dropped, not stringified.

### `session_start`

Fired by `on_session_start`.

| Field | Type | Meaning |
|---|---|---|
| `platform` | string or `null` | Chat surface or other platform identifier. |
| `boundary_reason` | string or `null` | Reason the session started this turn, if supplied. |
| `skills_exposed_count` | integer or `null` | Names in this surface's rendered skills index at this moment. `null` when that evidence is unavailable — reported as unknown, never as a configured count. |
| `skills_exposed_def` | integer | Counter definition version (currently `2`): `2` counts rendered-index names; `1` (historical) counted installed-minus-disabled configuration. Compare baselines only within one definition. |

### `session_end`

Fired by `on_session_end`. This is a **turn boundary**, not a terminal event.

| Field | Type | Meaning |
|---|---|---|
| `completed` | boolean or `null` | Whether the turn completed. |

### `session_finalize`

Fired by `on_session_finalize`. Terminal event; writes the final card and releases the
accumulator. No kind-specific fields.

### `session_reset`

Fired by `on_session_reset`. Terminal event; writes the final card and releases the
accumulator. No kind-specific fields.

### `tool_before`

Fired by `pre_tool_call`.

| Field | Type | Meaning |
|---|---|---|
| `tool` | string | Tool name. |
| `args_count` | integer or `null` | Number of arguments. `null` when the args value was not a plain mapping: unknown objects are omitted, never stringified. |
| `args_chars` | integer or `null` | Serialized size of the arguments. Argument contents are never captured in any form. |
| `tool_call_id` | string or `null` | Upstream tool-call identifier. |
| `turn_id` | string or `null` | Upstream turn identifier. |

### `tool_after`

Fired by `post_tool_call`.

| Field | Type | Meaning |
|---|---|---|
| `tool` | string | Tool name. |
| `looks_failed` | boolean | Heuristic signal that the result text begins with an error/failure marker. |
| `result_chars` | integer | Length of the result the model was handed. Result contents are never captured in any form. |
| `result_raw_chars` | integer or `null` | Pre-cap output size the tool reported for itself (`output_total_chars`); `null` when the tool reported none. |
| `tool_call_id` | string or `null` | Upstream tool-call identifier. |

### `api_after`

Fired by `post_api_request`.

| Field | Type | Meaning |
|---|---|---|
| `model` | string | Model identifier. |
| `provider` | string or `null` | Provider identifier, if supplied. |
| `seconds` | number | Request duration, from the hook's `api_duration`. |
| `input` | integer or `null` | New (uncached) prompt tokens. Falls back to the whole prompt when the provider reports no split. |
| `output` | integer or `null` | Completion tokens. |
| `cached` | integer or `null` | Reused prompt tokens. |
| `prompt` | integer or `null` | The whole prompt as reported, cached portion included. Never summed across calls. |
| `finish_reason` | string or `null` | Completion reason, if supplied. |
| `tool_calls` | integer or `null` | Number of tool calls in the assistant turn, if supplied. |

### `api_error`

Fired by `api_request_error`.

| Field | Type | Meaning |
|---|---|---|
| `error_kind` | string | Exception class name (or `str`/`error`). Error text is arbitrary payload and is never captured. |
| `error_chars` | integer | Length of the error text. |
| `model` | string or `null` | Model identifier, if supplied. |

### `skill`

Fired by `on_skill_lifecycle`.

| Field | Type | Meaning |
|---|---|---|
| `skill` | string | Skill name. |
| `phase` | string or `null` | Lifecycle phase, if supplied. |
| `source` | string or `null` | Activation source, if supplied. |

### `subagent_start`

Fired by `subagent_start`.

| Field | Type | Meaning |
|---|---|---|
| `agent` | string or `null` | Subagent name or identifier. |

## TraceCard

A card is a YAML scalar mapping. Fields appear in the order below. JSON-equivalent types
are used in [`schemas/trace-card.schema.json`](../schemas/trace-card.schema.json).

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Session identifier. |
| `profile` | string | Hermes profile name, or `unknown`. |
| `platform` | string | Platform identifier, or `unknown`. |
| `final` | boolean | `true` only after `session_finalize` or `session_reset`. `false` means the session is still open and totals are cumulative so far. |
| `boundaries` | integer | Number of `session_end` turn boundaries the totals span. |
| `session_seconds` | number | Seconds since the first event this process observed for the id. This is a floor, not the session's true age. |
| `api_calls` | integer | Total API requests observed. |
| `api_seconds` | number | Sum of observed API request durations. |
| `tokens` | object | `{input, output, cached}` token sums. `input` counts new tokens only; `cached` counts reused prompt tokens. |
| `context_peak` | integer | High-water mark of the whole prompt: the largest `prompt_tokens` any single request reported, cached portion included. It is the reported value, never a sum and never reconstructed from `input + cached`. |
| `models` | object | Map of model identifier to call count. |
| `subagents` | integer | Number of subagent starts observed. |
| `skills_activated` | object | Map of skill name to activation count. |
| `skills_exposed_def` | integer | Counter definition version (currently `2`); see `session_start`. |
| `skills_exposed_count` | integer or `unknown` | Distinct names the rendered skills index offered during the session. `unknown` when no rendered-index evidence was obtainable. |
| `skills_exposed_unused` | array or `unknown` | Sorted list of exposed skills that were never activated; `unknown` under the same condition. |
| `tool_calls` | object | Map of tool name to call count. |
| `tool_errors` | object | Map of tool name to heuristic failure count. |
| `tool_result_chars` | object | Map of tool name to total chars of result the model read. |
| `tool_result_max_chars` | object | Map of tool name to largest single result. |
| `tool_output_capped` | object | Map of tool name to count of results the tool truncated before the model saw them. |
| `tool_raw_max_chars` | object | Map of tool name to largest pre-cap output size the tool reported. |
| `errors` | array | Up to 10 content-free API error tokens (exception class and message length, e.g. `HTTPError(214)`). |

## Session lifetime semantics

`on_session_end` is a turn boundary on persistent chat surfaces, not the session's final
event. The plugin accumulates state across boundaries and only releases it on
`on_session_finalize` or `on_session_reset`. A non-final card therefore carries
cumulative totals and a `boundaries` count greater than or equal to one.

`session_seconds` is measured from the first event this process observed for the session id.
It is a lower bound: a gateway restart or a plugin loaded mid-session cannot recover
unobserved work.

## Bounds and retention

All bounds are configurable under the `trace:` block; defaults are listed in
[`docs/operations.md`](operations.md).

- **Content-free projection.** There is no field-clipping knob: payload (arguments,
  results, error text, unknown objects) is omitted outright, not truncated. Identifier
  strings are bounded to 128 characters; that bound is a guard against oversized
  values, not a redaction mechanism.
- **Per-day event-file size cap.** Each `events/YYYY-MM-DD.jsonl` file stops accepting
  events once it reaches `max_event_file_mb` megabytes. Events for that day are silently
  dropped rather than rotated or split.
- **Event-day retention.** Only the most recent `keep_event_days` daily event files are kept;
  older files are removed during card-write pruning.
- **Card retention.** Only the most recent `max_cards` card files are kept; older cards are
  removed during pruning.
- **Per-session tool-name cap.** A session tracks at most `max_tools_tracked` distinct tool
  names. A tool not already tracked is ignored once the cap is reached.

## Profile attribution

Profile identity is derived from the Hermes home path, using the framework's
`get_active_profile_name()` helper when available. The `HERMES_PROFILE` environment
variable is never consulted because the gateway does not set it.
