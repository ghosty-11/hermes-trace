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

Deploy through this module's `deploy/deploy` manifest owner, not a manual
`install` into HERMES_HOME. First review a `plan` for the exact target and
source revision. A production `apply` and gateway restart require separate
operator approval; an isolated source test is not proof of the loaded plugin.

## Enable per profile

Enable the plugin only for the profiles that should be traced. `plugins.enabled`
is a list, so use the Hermes CLI's `config edit` with a reviewed, scripted
editor for a list change; do not hand-edit a profile's live `config.yaml`.
Retain all existing enabled names and verify each profile's resolved plugin
list afterward. Enablement on root does not enable a multiplexed profile.

## Restart and config propagation

Plugin code requires a separately approved gateway restart to load. A changed
source file or config read alone does not prove the active plugin or its schema.

## Verification

Start a session on the traced profile, generate one tool call and one API request, then
inspect `$HERMES_HOME/trace/cards/<session_id>.yaml` and
`$HERMES_HOME/trace/events/YYYY-MM-DD.jsonl`. Confirm the card names the correct profile
and the event stream carries the same `profile` value.

## Upgrade and removal

For an upgrade, preserve the prior source revision and deployed receipt,
review the module's deployment plan, then request a controlled apply and
gateway restart. Keep profile enablement and current trace data unless the
operator separately authorizes a change. For removal, disable the plugin
through the Hermes CLI on each owning profile first; retain trace files until
the deployment's retention policy authorizes deletion.
