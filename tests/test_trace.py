"""Behavioural tests for hermes-trace.

These drive the plugin through the hook surface Hermes actually calls — via
``register()`` and a recording context — rather than through internal names, so
the assertions describe observable outcomes and survive refactoring.

Point them at a different implementation with ``HERMES_TRACE_PLUGIN=/path/to/__init__.py``
to confirm a check can fail: every assertion here was first watched failing
against the implementation this package replaces.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

PLUGIN_PATH = Path(os.environ.get(
    "HERMES_TRACE_PLUGIN",
    Path(__file__).resolve().parent.parent / "plugin" / "__init__.py"))


def load_plugin():
    """Import the plugin fresh, so module-level caches never cross tests."""
    for name in [n for n in sys.modules if n.startswith("_trace_under_test")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location("_trace_under_test", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_trace_under_test"] = module
    spec.loader.exec_module(module)
    return module


class _Ctx:
    """Records what the plugin asks to observe, like the real plugin context."""

    def __init__(self) -> None:
        self.hooks: dict[str, list] = {}

    def register_hook(self, name, handler):
        self.hooks.setdefault(str(name), []).append(handler)


class TraceTestCase(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self._env = dict(os.environ)
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        # A profile home, exactly as Hermes lays one out.
        self.home = self.tmp / ".hermes" / "profiles" / "research"
        self.home.mkdir(parents=True)
        os.environ["HERMES_HOME"] = str(self.home)
        os.environ.pop("HERMES_PROFILE", None)
        self.plugin = load_plugin()
        self.ctx = _Ctx()
        self.plugin.register(self.ctx)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    # ---- helpers ----------------------------------------------------------
    def fire(self, hook: str, **kw) -> None:
        """Invoke a hook if the implementation registered it; else do nothing.

        A missing hook is not an error here: it is the absence of a behaviour,
        and the assertion that depends on it is what reports the gap.
        """
        for handler in self.ctx.hooks.get(hook, []):
            handler(**kw)

    def cards(self) -> list[Path]:
        directory = self.home / "trace" / "cards"
        return sorted(directory.glob("*.yaml")) if directory.is_dir() else []

    def card(self, sid: str = "s1") -> dict[str, str]:
        matches = [p for p in self.cards() if p.stem == sid]
        self.assertTrue(matches, f"no TraceCard written for session {sid!r}")
        parsed: dict[str, str] = {}
        for line in matches[0].read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or ":" not in line:
                continue
            key, _, value = line.partition(":")
            parsed[key.strip()] = value.split("  #")[0].split("   #")[0].strip()
        return parsed

    def events(self) -> list[dict]:
        directory = self.home / "trace" / "events"
        out = []
        for path in sorted(directory.glob("*.jsonl")) if directory.is_dir() else []:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        return out

    def start(self, sid: str = "s1", platform: str = "chat") -> None:
        self.fire("on_session_start", session_id=sid, platform=platform)

    # ---- profile attribution ---------------------------------------------
    def test_profile_comes_from_the_home_path(self):
        """A card must name the profile that produced it.

        The gateway does not export a profile variable; profile identity lives
        in the home path. Reading it from the environment labelled every card
        'unknown' in production.
        """
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(self.card()["profile"], "research")

    def test_profile_env_variable_does_not_decide_the_profile(self):
        """`HERMES_PROFILE` is a recognised name the gateway never sets.

        Trusting it is how a tracer reports the wrong seat while looking right.
        """
        os.environ["HERMES_PROFILE"] = "not-this-one"
        self.plugin = load_plugin()
        self.ctx = _Ctx()
        self.plugin.register(self.ctx)
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(self.card()["profile"], "research")

    def test_default_home_is_named_default(self):
        os.environ["HERMES_HOME"] = str(self.tmp / ".hermes")
        (self.tmp / ".hermes").mkdir(exist_ok=True)
        self.plugin = load_plugin()
        self.ctx = _Ctx()
        self.plugin.register(self.ctx)
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)
        directory = self.tmp / ".hermes" / "trace" / "cards"
        text = (directory / "s1.yaml").read_text(encoding="utf-8")
        self.assertIn("profile: default", text)

    # ---- session lifetime -------------------------------------------------
    def test_session_end_is_a_boundary_not_a_reset(self):
        """`on_session_end` fires per turn on a persistent surface.

        Treating it as terminal discards the accumulator, so a session that ran
        all day reports only its last fragment.
        """
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("pre_tool_call", session_id="s1", tool_name="write_file")
        self.fire("on_session_end", session_id="s1", completed=True)

        card = self.card()
        self.assertEqual(json.loads(card["tool_calls"]),
                         {"read_file": 2, "write_file": 1})
        self.assertEqual(card["boundaries"], "2")

    def test_totals_accumulate_across_boundaries(self):
        """Accounting semantics, taken from the provider's own vocabulary.

        The provider reports three different quantities and an earlier version of
        this plugin mapped two of them onto one counter: summing `prompt_tokens`
        adds the whole context once per call, so a stable window reported as
        millions of tokens and manufactured a context explosion that never
        happened. `input` is NEW tokens only; the whole prompt is a high-water
        mark, which is the question worth asking of a context window.
        """
        self.start()
        for _ in range(2):
            self.fire("post_api_request", session_id="s1", model="m1",
                      api_duration=1.5,
                      usage={"prompt_tokens": 1000, "input_tokens": 100,
                             "completion_tokens": 10, "cache_read_tokens": 900})
            self.fire("on_session_end", session_id="s1", completed=True)

        card = self.card()
        self.assertEqual(card["api_calls"], "2")
        self.assertEqual(card["api_seconds"], "3.0")
        self.assertEqual(json.loads(card["tokens"]),
                         {"input": 200, "output": 20, "cached": 1800})
        # The reported whole prompt, not the sum and not a reconstruction.
        self.assertEqual(card["context_peak"], "1000")
        self.assertEqual(json.loads(card["models"]), {"m1": 2})

    def test_api_seconds_reads_the_framework_field(self):
        """The hook passes `api_duration`. Guessing another name silently reports zero."""
        self.start()
        self.fire("post_api_request", session_id="s1", model="m1", api_duration=2.5,
                  usage={"prompt_tokens": 10, "input_tokens": 10})
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(self.card()["api_seconds"], "2.5")

    def test_input_falls_back_to_whole_prompt_without_a_split(self):
        """A provider reporting no `input_tokens` still has its cost counted."""
        self.start()
        self.fire("post_api_request", session_id="s1", model="m1", api_duration=1.0,
                  usage={"prompt_tokens": 700, "completion_tokens": 3})
        self.fire("on_session_end", session_id="s1", completed=True)
        card = self.card()
        self.assertEqual(json.loads(card["tokens"])["input"], 700)
        self.assertEqual(card["context_peak"], "700")

    def test_a_cached_session_does_not_inflate_input(self):
        """The regression this accounting exists to prevent, stated as a number.

        Ten calls over a stable 20k window with 19k cached cost 10k new tokens,
        not 200k. Summing the whole prompt is what produced the false explosion.
        """
        self.start()
        for _ in range(10):
            self.fire("post_api_request", session_id="s1", model="m1", api_duration=0.1,
                      usage={"prompt_tokens": 20_000, "input_tokens": 1_000,
                             "completion_tokens": 0, "cache_read_tokens": 19_000})
        self.fire("on_session_end", session_id="s1", completed=True)
        card = self.card()
        self.assertEqual(json.loads(card["tokens"])["input"], 10_000)
        self.assertEqual(card["context_peak"], "20000")

    def test_emitted_events_agree_with_the_published_schema(self):
        """A schema nothing validates against is documentation, not a contract.

        Stdlib only: `required` keys must be present and no key may fall outside
        `properties`, checked against events the plugin actually wrote. This is the
        check that catches a field added to the emitter and not to the schema.
        """
        schema = json.loads(
            (PLUGIN_PATH.parent.parent / "schemas" / "trace-event.schema.json")
            .read_text(encoding="utf-8"))
        defs = schema.get("$defs") or {}

        self.plugin._exposed_skills = lambda: ["alpha"]
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file", args={"p": 1})
        self.fire("post_tool_call", session_id="s1", tool_name="read_file", result="ok")
        self.fire("post_api_request", session_id="s1", model="m1", api_duration=1.0,
                  usage={"prompt_tokens": 10, "input_tokens": 4, "completion_tokens": 1,
                         "cache_read_tokens": 6})
        self.fire("api_request_error", session_id="s1", error="boom", model="m1")
        self.fire("on_skill_lifecycle", session_id="s1", skill_name="alpha")
        self.fire("subagent_start", session_id="s1", agent_name="worker")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.fire("on_session_reset", session_id="s1")
        self.fire("on_session_finalize", session_id="s1")

        recorded = self.events()
        self.assertTrue(recorded)
        seen = set()
        for event in recorded:
            kind = event["kind"]
            definition = defs.get(kind)
            self.assertIsNotNone(definition, f"schema has no definition for kind {kind!r}")
            allowed = set(definition.get("properties") or {})
            required = set(definition.get("required") or {})
            extra = set(event) - allowed
            self.assertFalse(
                extra, f"{kind} emits {sorted(extra)}, absent from the schema")
            missing = required - set(event)
            self.assertFalse(
                missing, f"{kind} schema requires {sorted(missing)}, never emitted")
            seen.add(kind)

        # Every kind the schema defines must be reachable through the hook surface.
        self.assertEqual(seen, set(defs) - {"base"})

    def test_exposed_skills_survive_a_boundary(self):
        """The headline field must not be emptied by a turn boundary."""
        self.plugin._exposed_skills = lambda: ["alpha", "beta", "gamma"]
        self.start()
        self.fire("on_skill_lifecycle", session_id="s1", skill_name="beta")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.fire("on_session_end", session_id="s1", completed=True)

        card = self.card()
        self.assertEqual(card["skills_exposed_count"], "3")
        self.assertEqual(json.loads(card["skills_exposed_unused"]),
                         ["alpha", "gamma"])
        self.assertEqual(json.loads(card["skills_activated"]), {"beta": 1})

    def test_open_session_has_a_card_marked_not_final(self):
        """A session that never ends must still be observable."""
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(self.card()["final"], "false")

    def test_finalize_marks_the_card_final_and_releases_the_session(self):
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("on_session_finalize", session_id="s1")
        self.assertEqual(self.card()["final"], "true")

        # A boundary after finalize starts a new accumulation, not a resurrection.
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(json.loads(self.card()["tool_calls"]), {})

    def test_session_seconds_spans_the_whole_session(self):
        real_time = self.plugin.time.time
        clock = {"now": real_time()}
        self.plugin.time.time = lambda: clock["now"]
        try:
            self.start()
            clock["now"] += 300.0
            self.fire("on_session_end", session_id="s1", completed=True)
            self.assertEqual(self.card()["session_seconds"], "300.0")
        finally:
            self.plugin.time.time = real_time

    def test_two_sessions_do_not_share_totals(self):
        self.start("s1")
        self.start("s2")
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.fire("on_session_end", session_id="s2", completed=True)
        self.assertEqual(json.loads(self.card("s1")["tool_calls"]), {"read_file": 1})
        self.assertEqual(json.loads(self.card("s2")["tool_calls"]), {})

    # ---- it observes, it never gates -------------------------------------
    def test_no_handler_raises_on_hostile_input(self):
        """A tracer that breaks a turn is worse than one that misses an event."""
        junk = [
            {},
            {"session_id": None},
            {"session_id": 7, "tool_name": None, "result": object()},
            {"session_id": "s1", "usage": "not-a-mapping", "duration": "soon"},
            {"session_id": "s1", "skill_name": None, "error": object()},
        ]
        for hook, handlers in self.ctx.hooks.items():
            for handler in handlers:
                for kwargs in junk:
                    with self.subTest(hook=hook, kwargs=sorted(kwargs)):
                        self.assertIsNone(handler(**kwargs))

    def test_unwritable_trace_directory_is_survivable(self):
        os.environ["HERMES_HOME"] = "/proc/nonexistent-hermes-home"
        self.plugin = load_plugin()
        self.ctx = _Ctx()
        self.plugin.register(self.ctx)
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)  # must not raise

    def test_captured_values_are_bounded(self):
        self.start()
        self.fire("post_tool_call", session_id="s1", tool_name="read_file",
                  result="x" * 100_000)
        payloads = [e for e in self.events() if e.get("kind") == "tool_after"]
        self.assertTrue(payloads)
        self.assertLessEqual(len(payloads[0]["result"]),
                             self.plugin.DEFAULTS["max_field_chars"] + 1)

    # ---- configuration ----------------------------------------------------
    def test_trace_directory_is_configurable(self):
        target = self.tmp / "elsewhere"
        self.assertEqual(self.plugin._trace_dir({"dir": str(target)}), target)

    def test_event_stream_can_be_disabled_without_losing_cards(self):
        self.plugin._config = lambda: dict(self.plugin.DEFAULTS, events=False)
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(self.events(), [])
        self.assertEqual(json.loads(self.card()["tool_calls"]), {"read_file": 1})

    def test_defaults_apply_when_no_config_is_reachable(self):
        config = self.plugin._config()
        for key, value in self.plugin.DEFAULTS.items():
            self.assertEqual(config[key], value)

    # ---- events -----------------------------------------------------------
    def test_events_carry_the_profile_for_offline_reading(self):
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        profiles = {e.get("profile") for e in self.events()}
        self.assertEqual(profiles, {"research"})

    def test_every_event_names_its_kind_and_session(self):
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="read_file")
        self.fire("post_tool_call", session_id="s1", tool_name="read_file", result="ok")
        self.fire("on_session_end", session_id="s1", completed=True)
        recorded = self.events()
        self.assertTrue(recorded)
        for event in recorded:
            self.assertTrue(event.get("kind"))
            self.assertEqual(event.get("session_id"), "s1")


if __name__ == "__main__":
    unittest.main()
