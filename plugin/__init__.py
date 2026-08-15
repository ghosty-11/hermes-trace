"""hermes-trace — per-session evidence of what an agent actually did.

WHY THIS EXISTS
---------------
Hermes keeps a live per-profile ``.usage.json`` with ``use_count`` and
``last_used_at`` for each skill. That is a running total, and a running total
cannot answer the question a skill review actually asks:

  * which skills were AVAILABLE and not chosen — a total can never say
    "three others were passed over";
  * channel-pinned or preloaded skills, which never reach ``bump_use`` at all;
  * per-session attribution, cost, or duration.

So this plugin records participation, not just totals. The vocabulary is taken
from the Skill-BOM pattern (arXiv:2606.20631): a skill in a run is *exposed*,
*activated*, or neither — and the interesting one is the gap between them.

SHAPE
-----
Two artefacts, both plain files under the configured trace directory
(``$HERMES_HOME/trace`` by default):

  events/YYYY-MM-DD.jsonl   append-only, one JSON object per event
  cards/<session>.yaml      a compact TraceCard for one session

The TraceCard shape is borrowed from ClawTrace (arXiv:2604.23853), which makes
the case better than we could: standard observability stacks "expose this
information as dashboard analytics for human operators rather than as a compact
summary a distillation pipeline can ingest". A small file is diffable,
committable, greppable, and readable by a scheduled script. A dashboard is the
thing nobody opens.

SESSION LIFETIME — THE PART THAT IS NOT OBVIOUS
-----------------------------------------------
``on_session_end`` is **not** a session's final event. On a persistent chat
surface it fires at every turn boundary, many times for one session id, and the
same id can also see several ``on_session_start`` events. A tracer that treats
it as terminal and discards its accumulator therefore reports only the last
fragment: one API call, zero exposed skills, a duration of zero — for a session
that ran all day.

So state is accumulated across ``on_session_end`` and only released on
``on_session_finalize`` / ``on_session_reset``, which are the terminal hooks.
The card is rewritten at every boundary, cumulatively, so a card exists for a
session that never ends — and ``boundaries`` records how many turn boundaries
the totals span. ``session_seconds`` is measured from the first event this
process saw for the id, and is a floor, not the session's true age: a restart
or a plugin loaded mid-session cannot recover what it did not observe.

HOW TO READ IT — AND HOW NOT TO
-------------------------------
``skills_exposed_unused`` is the field this exists for, and it is the easiest
one to misuse. **A skill appearing there is not evidence it should be pruned.**

On a young deployment most skills are unused because nothing has needed them
yet, not because they are dead weight. Treat a long unused list as the expected
shape of a new system.

Two conditions before this data justifies removing anything:

  1. **Enough elapsed time and enough variety of work** that the skill would
     plausibly have been reached for. A month of real sessions, not a week of
     setup.
  2. **A reason it was passed over.** Unused because never relevant is fine.
     Unused because a competing skill won the same trigger, or because the
     description does not describe what it does, is a finding — and those look
     identical in this file. The card tells you *what* was not chosen; it can
     never tell you *why*.

The published evidence argues for the same caution from the other side:
ClawTrace measured curated skills raising mean pass rate 16.2 points while 16
of 84 tasks REGRESSED, with their "keep what worked" patches driving the
regressions and their prune patches acting as guardrails. Aggregate numbers
conceal offsetting wins and losses in both directions. Decide per skill, with a
reason, never on a count.

RULES IT FOLLOWS
----------------
* **Never raise.** Every hook body is wrapped. A tracer that breaks a turn is
  infinitely worse than one that misses an event — this observes, it never gates.
* **Never block.** No network, no locks held across work, bounded writes.
* **Bounded.** Event files are size-capped and old ones pruned, because an
  observability tool that fills the disk has caused an outage, not prevented one.
* **Silent.** It emits nothing to any channel. Reading it is a separate job's
  business.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Defaults. A tracer is not allowed to become the reason the host fell over.
# Every one is overridable under the `trace:` config block — see docs/operations.md.
DEFAULTS: dict[str, Any] = {
    "dir": "",                       # empty -> <hermes_home>/trace
    "events": True,                  # False keeps cards and drops the event stream
    "max_event_file_mb": 32,
    "keep_event_days": 14,
    "max_cards": 500,
    "max_field_chars": 400,
    "max_tools_tracked": 400,
}

_EXPOSED_TTL_S = 600
_EXPOSED_CACHE: dict[str, Any] = {"at": 0.0, "names": [], "home": ""}


# ---- environment resolution ------------------------------------------------
# Everything here answers "which profile is this session?" and "where do its
# files go?". Both must be resolved PER CALL: one multiplexed gateway process
# serves several profiles, so a value cached at import or at register() time
# describes whichever profile happened to load the plugin first.

def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def _profile_from_home(home: Path) -> str:
    """Derive the profile name from a Hermes home path.

    The fallback for when the framework helper is unavailable, and the reason
    this plugin does not read ``HERMES_PROFILE``: that variable is a recognised
    name which the gateway does not set, so a tracer keyed on it labels every
    session ``unknown``. Profile identity lives in the home PATH — a profile is
    ``<default home>/profiles/<name>`` — which is the same fact the trace
    directory is already resolved from.
    """
    try:
        parts = Path(home).resolve().parts
        if len(parts) >= 2 and parts[-2] == "profiles" and parts[-1]:
            return parts[-1]
    except Exception:
        return "default"
    return "default"


def _profile() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name
        name = str(get_active_profile_name() or "").strip()
        # "custom" means the framework could not place HERMES_HOME in the
        # profiles tree. Our own derivation cannot do better, so keep its word.
        if name:
            return name
    except Exception:
        pass
    return _profile_from_home(_home())


def _config() -> dict[str, Any]:
    """Resolved `trace:` settings for the profile serving THIS call."""
    cfg = dict(DEFAULTS)
    try:
        from hermes_cli.config import load_config
        block = (load_config() or {}).get("trace") or {}
        if isinstance(block, dict):
            for key in DEFAULTS:
                if key in block and block[key] is not None:
                    cfg[key] = block[key]
    except Exception:
        logger.debug("hermes-trace: config unreadable, using defaults", exc_info=True)
    return cfg


def _trace_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg if cfg is not None else _config()
    configured = str(cfg.get("dir") or "").strip()
    return Path(configured) if configured else _home() / "trace"


def _clip(value: Any, limit: int) -> Any:
    """Bound a captured value. Strings truncate; everything else summarises."""
    try:
        if isinstance(value, str):
            return value if len(value) <= limit else value[:limit] + "…"
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return {str(k)[:64]: _clip(v, limit) for k, v in list(value.items())[:32]}
        if isinstance(value, (list, tuple)):
            return [_clip(v, limit) for v in list(value)[:32]]
        text = str(value)
        return text if len(text) <= limit else text[:limit] + "…"
    except Exception:
        return "<unrepresentable>"


class _Tracer:
    """Accumulates per-session state and writes the two artefacts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    # ---- storage -----------------------------------------------------------
    def _write_event(self, event: dict, cfg: dict[str, Any]) -> None:
        try:
            if not cfg.get("events", True):
                return
            directory = _trace_dir(cfg) / "events"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{time.strftime('%Y-%m-%d')}.jsonl"
            cap = int(cfg.get("max_event_file_mb", 32)) * 1024 * 1024
            try:
                if path.exists() and path.stat().st_size > cap:
                    return  # cap reached for today; drop rather than grow
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.debug("hermes-trace: event write failed", exc_info=True)

    def _prune(self, cfg: dict[str, Any]) -> None:
        """Keep the footprint bounded. Cheap, and only on a card write."""
        try:
            events = _trace_dir(cfg) / "events"
            if events.is_dir():
                keep_days = max(1, int(cfg.get("keep_event_days", 14)))
                for old in sorted(events.glob("*.jsonl"))[:-keep_days]:
                    old.unlink(missing_ok=True)
            cards = _trace_dir(cfg) / "cards"
            if cards.is_dir():
                keep_cards = max(1, int(cfg.get("max_cards", 500)))
                existing = sorted(cards.glob("*.yaml"), key=lambda p: p.stat().st_mtime)
                for old in existing[:-keep_cards]:
                    old.unlink(missing_ok=True)
        except Exception:
            logger.debug("hermes-trace: prune failed", exc_info=True)

    # ---- session bookkeeping ----------------------------------------------
    def _session(self, sid: str) -> dict:
        """The accumulator for `sid`, created on first sight.

        Re-seeing an id must never reset it: on a persistent surface the same
        session produces many turn boundaries, and a reset there is what turns
        a day of work into a card reading "one API call, zero skills".
        """
        session = self._sessions.get(sid)
        if session is None:
            session = {
                "session_id": sid,
                "first_seen_at": time.time(),
                "platform": "",
                "profile": _profile(),
                "boundaries": 0,        # observed turn/session-end boundaries
                "skills_exposed": [],   # available to choose from
                "skills_activated": {},  # name -> times activated
                "tools": {},            # name -> count
                "tool_errors": {},      # name -> count
                "api_calls": 0,
                "api_seconds": 0.0,
                "tokens": {"input": 0, "output": 0, "cached": 0},
                "context_peak": 0,
                "models": {},
                "errors": [],
                "subagents": 0,
            }
            self._sessions[sid] = session
        return session

    def event(self, kind: str, sid: str, **fields: Any) -> None:
        """Record one event. Never raises, never blocks on anything real."""
        try:
            cfg = _config()
            limit = int(cfg.get("max_field_chars", 400))
            payload = {
                "ts": round(time.time(), 3),
                "kind": kind,
                "session_id": str(sid or ""),
                "profile": _profile(),
                **{k: _clip(v, limit) for k, v in fields.items()},
            }
            self._write_event(payload, cfg)
        except Exception:
            logger.debug("hermes-trace: event failed", exc_info=True)

    # ---- the card ---------------------------------------------------------
    def write_card(self, sid: str, *, final: bool) -> None:
        """Write the TraceCard for `sid`.

        `final` releases the accumulator; a boundary write keeps it, because
        `on_session_end` is a turn boundary and more work usually follows.
        """
        try:
            sid = str(sid or "")
            with self._lock:
                session = self._sessions.get(sid)
                if session is None:
                    return
                if final:
                    self._sessions.pop(sid, None)
                snapshot = json.loads(json.dumps(session, default=str))

            cfg = _config()
            elapsed = max(0.0, time.time() - float(snapshot["first_seen_at"]))
            exposed = {name for name in snapshot["skills_exposed"] if name}
            unused = sorted(exposed - set(snapshot["skills_activated"]))

            lines = [
                "# hermes-trace TraceCard — what this session actually did.",
                "# skills_exposed_unused is the interesting field: available, never activated.",
                f"session_id: {snapshot['session_id']}",
                f"profile: {snapshot['profile'] or 'unknown'}",
                f"platform: {snapshot['platform'] or 'unknown'}",
                f"final: {'true' if final else 'false'}"
                "  # false = session still open, totals are cumulative so far",
                f"boundaries: {snapshot['boundaries']}"
                "  # turn boundaries these totals span",
                f"session_seconds: {round(elapsed, 1)}"
                "  # since first event OBSERVED; a floor, not the session's age",
                f"api_calls: {snapshot['api_calls']}",
                f"api_seconds: {round(float(snapshot['api_seconds']), 1)}",
                f"tokens: {json.dumps(snapshot['tokens'])}"
                "   # input = NEW tokens only; cached = reused prompt",
                f"context_peak: {snapshot.get('context_peak', 0)}"
                "  # largest single prompt — how full the window ever got",
                f"models: {json.dumps(snapshot['models'])}",
                f"subagents: {snapshot['subagents']}",
                f"skills_activated: {json.dumps(snapshot['skills_activated'])}",
                f"skills_exposed_count: {len(exposed)}",
                f"skills_exposed_unused: {json.dumps(unused)}",
                f"tool_calls: {json.dumps(snapshot['tools'])}",
                f"tool_errors: {json.dumps(snapshot['tool_errors'])}",
                f"errors: {json.dumps(snapshot['errors'][:10])}",
            ]

            directory = _trace_dir(cfg) / "cards"
            directory.mkdir(parents=True, exist_ok=True)
            safe = "".join(c for c in snapshot["session_id"] if c.isalnum() or c in "-_")[:80]
            tmp = directory / f".{safe or 'unknown'}.yaml.tmp"
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(directory / f"{safe or 'unknown'}.yaml")
            self._prune(cfg)
        except Exception:
            logger.debug("hermes-trace: card write failed", exc_info=True)


_T = _Tracer()


# ---- hook handlers ---------------------------------------------------------
# Each is deliberately tiny and total: capture, record, return None. Returning
# anything from an observer hook risks influencing control flow, which this
# plugin must never do.

def _exposed_skills() -> list[str]:
    """Skills this profile could have chosen from, right now.

    Cached briefly and keyed by home, so a multiplexed process cannot serve one
    profile's skill list to another.
    """
    home = str(_home())
    now = time.time()
    if _EXPOSED_CACHE["home"] == home and now - float(_EXPOSED_CACHE["at"]) < _EXPOSED_TTL_S:
        return list(_EXPOSED_CACHE["names"])
    try:
        from hermes_cli.skills_config import _list_all_skills, get_disabled_skills
        from hermes_cli.config import load_config
        installed = {s.get("name") for s in _list_all_skills() if s.get("name")}
        names = sorted(installed - set(get_disabled_skills(load_config())))
        _EXPOSED_CACHE.update({"at": now, "names": names, "home": home})
        return list(names)
    except Exception:
        return []


def _sid(kw: dict) -> str:
    return str(kw.get("session_id") or "")


def _on_session_start(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        exposed = _exposed_skills()
        with _T._lock:
            session = _T._session(sid)
            session["platform"] = str(kw.get("platform") or "") or session["platform"]
            if exposed:
                # Union, never replace: a re-started id keeps what it could
                # already have chosen from.
                session["skills_exposed"] = sorted(
                    set(session["skills_exposed"]) | set(exposed))
        _T.event("session_start", sid, platform=kw.get("platform"),
                 boundary_reason=kw.get("boundary_reason"),
                 skills_exposed_count=len(exposed))
    except Exception:
        pass


def _on_session_end(**kw: Any) -> None:
    """A TURN boundary, not the end of the session. Refresh, never release."""
    try:
        sid = _sid(kw)
        with _T._lock:
            _T._session(sid)["boundaries"] += 1
        _T.event("session_end", sid, completed=kw.get("completed"))
        _T.write_card(sid, final=False)
    except Exception:
        pass


def _on_session_finalize(**kw: Any) -> None:
    """Terminal. Write the last card and release the accumulator."""
    try:
        sid = _sid(kw)
        _T.event("session_finalize", sid)
        _T.write_card(sid, final=True)
    except Exception:
        pass


def _on_session_reset(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        _T.event("session_reset", sid)
        _T.write_card(sid, final=True)
    except Exception:
        pass


def _on_pre_tool_call(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        name = str(kw.get("tool_name") or "")
        cap = int(_config().get("max_tools_tracked", 400))
        with _T._lock:
            session = _T._session(sid)
            if len(session["tools"]) < cap or name in session["tools"]:
                session["tools"][name] = session["tools"].get(name, 0) + 1
        _T.event("tool_before", sid, tool=name, args=kw.get("args"),
                 tool_call_id=kw.get("tool_call_id"), turn_id=kw.get("turn_id"))
    except Exception:
        pass


def _on_post_tool_call(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        name = str(kw.get("tool_name") or "")
        result = kw.get("result")
        # A tool "error" here is a heuristic on the result text, not a status
        # code — Hermes returns tool failures as content. Recorded as a signal
        # to look at, never as an assertion that something broke.
        text = result if isinstance(result, str) else str(result)
        looks_bad = text[:200].lower().lstrip().startswith(("error", "failed", "traceback"))
        if looks_bad:
            with _T._lock:
                session = _T._session(sid)
                session["tool_errors"][name] = session["tool_errors"].get(name, 0) + 1
        _T.event("tool_after", sid, tool=name, looks_failed=looks_bad,
                 result=result, tool_call_id=kw.get("tool_call_id"))
    except Exception:
        pass


def _usage_int(usage: Any, *names: str) -> int | None:
    """First integer among `names`, or None when the provider reports none."""
    for name in names:
        try:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, bool) or value is None:
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _on_post_api_request(**kw: Any) -> None:
    """Account for one API call.

    THE ACCOUNTING IS THE POINT, and the three quantities are not interchangeable:

      input_tokens       NEW (uncached) tokens for this call
      prompt_tokens      the WHOLE prompt, cached part included
      cache_read_tokens  the cached part

    Summing `prompt_tokens` across a session adds the entire context once per
    call, so a stable 20k window reads as hundreds of thousands of tokens. That
    is not a rounding error: it manufactures a context explosion that never
    happened and sends an investigation the wrong way.

    So `input` counts new tokens only — what the session actually cost — and the
    whole prompt is kept as a HIGH-WATER MARK, which is the question worth asking
    of a context window: not how many times we re-sent it, but how full it ever got.
    """
    try:
        sid = _sid(kw)
        usage = kw.get("usage") or {}
        model = str(kw.get("model") or "")
        seconds = float(kw.get("api_duration") or 0.0)

        whole_prompt = _usage_int(usage, "prompt_tokens")
        # A provider without the cached/new split reports only the whole prompt.
        new_input = _usage_int(usage, "input_tokens")
        if new_input is None:
            new_input = whole_prompt
        output = _usage_int(usage, "output_tokens", "completion_tokens")
        cached = _usage_int(usage, "cache_read_tokens", "cached_tokens",
                            "cache_read_input_tokens")

        with _T._lock:
            session = _T._session(sid)
            session["api_calls"] += 1
            session["api_seconds"] += max(0.0, seconds)
            if new_input is not None:
                session["tokens"]["input"] += new_input
            if output is not None:
                session["tokens"]["output"] += output
            if cached is not None:
                session["tokens"]["cached"] += cached
            if whole_prompt is not None:
                session["context_peak"] = max(
                    int(session["context_peak"]), whole_prompt)
            if model:
                session["models"][model] = session["models"].get(model, 0) + 1
        _T.event("api_after", sid, model=model, provider=kw.get("provider"),
                 seconds=round(seconds, 3), input=new_input, output=output,
                 cached=cached, prompt=whole_prompt,
                 finish_reason=kw.get("finish_reason"),
                 tool_calls=kw.get("assistant_tool_call_count"))
    except Exception:
        pass


def _on_api_error(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        detail = str(kw.get("error") or kw.get("message") or "")[:200]
        with _T._lock:
            session = _T._session(sid)
            if len(session["errors"]) < 50:
                session["errors"].append(detail)
        _T.event("api_error", sid, error=detail, model=kw.get("model"))
    except Exception:
        pass


def _on_skill_lifecycle(**kw: Any) -> None:
    """The event that closes the original gap: a named skill, actually used."""
    try:
        sid = _sid(kw)
        name = str(kw.get("skill_name") or kw.get("skill") or "")
        if name:
            with _T._lock:
                session = _T._session(sid)
                session["skills_activated"][name] = \
                    session["skills_activated"].get(name, 0) + 1
        _T.event("skill", sid, skill=name, phase=kw.get("phase"),
                 source=kw.get("source"))
    except Exception:
        pass


def _on_subagent_start(**kw: Any) -> None:
    try:
        sid = _sid(kw)
        with _T._lock:
            _T._session(sid)["subagents"] += 1
        _T.event("subagent_start", sid, agent=kw.get("agent_name") or kw.get("agent"))
    except Exception:
        pass


HANDLERS: dict[str, Any] = {
    "on_session_start": _on_session_start,
    "on_session_end": _on_session_end,
    "on_session_finalize": _on_session_finalize,
    "on_session_reset": _on_session_reset,
    "pre_tool_call": _on_pre_tool_call,
    "post_tool_call": _on_post_tool_call,
    "post_api_request": _on_post_api_request,
    "api_request_error": _on_api_error,
    "on_skill_lifecycle": _on_skill_lifecycle,
    "subagent_start": _on_subagent_start,
}


def register(ctx) -> None:
    """Attach observers. Any hook this Hermes build lacks is skipped quietly.

    Registration is defensive on purpose: the hook set is upstream's and can
    change between releases. A tracer that refuses to load because one hook was
    renamed costs more than a tracer missing one field.
    """
    attached = []
    for name, handler in HANDLERS.items():
        try:
            ctx.register_hook(name, handler)
            attached.append(name)
        except Exception:
            logger.debug("hermes-trace: could not attach %s", name, exc_info=True)
    logger.info(
        "hermes-trace: %d/%d hooks attached (%s)",
        len(attached), len(HANDLERS), ", ".join(attached))
