# Operations

## Configuration

All settings live under the `trace:` block in the Hermes profile config.

| Key | Default | Meaning |
|---|---|---|
| `dir` | `""` | Trace directory. Empty means `$HERMES_HOME/trace`. Absolute or relative to `$HERMES_HOME`. |
| `events` | `true` | Emit the append-only event stream. `false` keeps cards and drops events. |
| `max_event_file_mb` | `32` | Per-day event-file size cap in megabytes. |
| `keep_event_days` | `14` | Number of daily event files to retain. |
| `max_cards` | `500` | Number of card files to retain. |
| `max_field_chars` | `400` | String length bound before clipping. |
| `max_tools_tracked` | `400` | Per-session cap on distinct tool names. |

Trade-offs:

- Disabling `events` removes the full audit trail but leaves session summaries. Use it when
disk or privacy matters more than replay.
- Raising `max_event_file_mb` extends replay depth but risks a single busy day filling the
disk. The cap is per-day, not total.
- Raising `keep_event_days` extends history; lowering it reduces storage but loses older
context.
- Raising `max_cards` keeps more sessions; lowering it drops older cards during pruning.
- Raising `max_field_chars` captures larger arguments and results; lowering it trims the
event payload at the cost of detail.
- Raising `max_tools_tracked` preserves rare tools; once the cap is reached, newly seen
tools are not counted unless already tracked.

## Reading a card

A card is plain YAML:

```sh
cat "$HERMES_HOME/trace/cards/<session_id>.yaml"
```

Field order is stable. Comments annotate semantics. `final: false` means the session is
still open; `final: true` means `session_finalize` or `session_reset` released the
accumulator.

## Reading events

Events are JSON Lines, one object per line:

```sh
jq -c 'select(.session_id == "<session_id>")' \
  "$HERMES_HOME/trace/events/YYYY-MM-DD.jsonl"
```

Replay in file order; each file is append-only.

## Common questions

### What did this session cost?

Sum `tokens.input`, `tokens.output`, and `tokens.cached` from the card. `api_seconds` gives
wall time. `api_calls` and `models` show request shape. `context_peak` shows how full the
largest prompt window became. These are observed totals, not provider billing records.

### Which skills were exposed and never activated?

Read `skills_exposed_unused`. Compare it with `skills_activated` and
`skills_exposed_count`.

### Is tool output expensive on this seat?

`tool_result_chars` is what the model read per tool; `tool_result_max_chars` is the largest
single result. Those two alone understate the traffic, because a tool truncates before this
plugin sees the result: `tool_output_capped` counts the results a tool cut, and
`tool_raw_max_chars` is the largest pre-cap size the tool reported for itself. A large gap
between `tool_raw_max_chars` and `tool_result_max_chars` is output being dropped, not output
being paid for.

A tool that reports no pre-cap size contributes to the first two fields only; absence there
means "not reported", never "not truncated".

## Retention and disk sizing

Worst-case steady-state event storage is roughly `max_event_file_mb × keep_event_days`.
Card storage is bounded by `max_cards` small YAML files. The trace directory may grow
briefly beyond these limits because pruning runs only on card writes.

Size `max_event_file_mb` and `keep_event_days` so that the trace directory fits inside the
profile's allocated storage with headroom. Busy profiles should use smaller per-day caps or
fewer retained days.

## Misuse warning: `skills_exposed_unused` is not a prune list

A skill appearing in `skills_exposed_unused` is not evidence it should be removed. On a
young deployment most skills are unused because nothing has needed them yet, not because
they are dead weight. Treat a long unused list as the expected shape of a new system.

Two conditions must hold before this data justifies removing anything:

1. **Enough elapsed time and enough variety of work** that the skill would plausibly have
   been reached for. A month of real sessions, not a week of setup.
2. **A reason it was passed over.** Unused because never relevant is fine. Unused because a
   competing skill won the same trigger, or because the description does not describe what
   it does, is a finding — and those look identical in this file. The card tells you *what*
   was not chosen; it can never tell you *why*.

The published ClawTrace evidence argues for the same caution: curated skills raised the mean
pass rate while 16 of 84 tasks regressed, with "keep what worked" patches driving the
regressions and prune patches acting as guardrails. Aggregate numbers conceal offsetting
wins and losses. Decide per skill, with a reason, never on a count.
