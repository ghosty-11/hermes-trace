# Installation

## Prerequisites

Use Python 3.11 or newer on a POSIX host. Pin and review the exact
[Hermes Agent](https://github.com/NousResearch/hermes-agent) revision before installing the
plugin.

## Install the plugin

From a pinned checkout:

```sh
python3 -m unittest discover -s tests -v
python3 -m json.tool schemas/trace-event.schema.json >/dev/null
python3 -m json.tool schemas/trace-card.schema.json >/dev/null
```

Copy the plugin files into the Hermes standalone-plugin directory:

```sh
install -d -m 0700 "$HERMES_HOME/plugins/hermes-trace"
install -m 0600 plugin/__init__.py "$HERMES_HOME/plugins/hermes-trace/__init__.py"
install -m 0600 plugin/plugin.yaml "$HERMES_HOME/plugins/hermes-trace/plugin.yaml"
```

## Enable per profile

Enable the plugin for each profile that should be traced. `plugins.enabled` is a list, so
a scalar config setter cannot write it; use the interactive plugin toggle or edit the
profile config directly:

```yaml
plugins:
  enabled:
    - hermes-trace
```

Enabling the plugin at one profile does not cover others. Repeat the step for every
`<profile>` that needs tracing.

## Restart and config propagation

Plugin code requires a gateway restart to load. Config values under the `trace:` block
reach new sessions without a restart.

## Verification

Start a session on the traced profile, generate one tool call and one API request, then
inspect `$HERMES_HOME/trace/cards/<session_id>.yaml` and
`$HERMES_HOME/trace/events/YYYY-MM-DD.jsonl`. Confirm the card names the correct profile
and the event stream carries the same `profile` value.

## Upgrade and removal

Disable `hermes-trace` on every profile, stop the gateway, back up the trace directory,
install the new pinned plugin files, and restart. Re-enable only after the deterministic
suite and one live profile smoke pass. For removal, disable the plugin on every profile
first; retain trace files until the deployment's retention policy authorizes deletion.
