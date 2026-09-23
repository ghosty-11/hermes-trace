"""Content-free capture contracts for hermes-trace (S05 / V12).

E20 verified the deployed tracer captured tool arguments and results into
events through a 400-character clipping window, and a synthetic private
fixture string survived that clipping. Clipping is a size bound, not a
redaction: these tests pin the allowlist projection that must run BEFORE
any formatting. Tool name, status, sizes and counters pass; arguments,
results, arbitrary error text and unknown objects are omitted by default,
and no ``str()``/``default=str`` later in the pipeline may reintroduce
them.

They also pin the v2 skills-exposure counter. The v1 counter read
installed-minus-disabled configuration, so a seat whose rendered index was
empty still reported every configured name. v2 takes the names from the
profile's actually-rendered skills index; when that index cannot be
obtained the evidence is UNKNOWN, never a configured count.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_trace import TraceHarnessMixin  # noqa: E402  (shared fixture, no tests)

# A long secret with recognizable windows at both ends and the middle, so a
# clip at any offset is caught. Longer than every historical bound in the
# plugin (200, 400), because clipping is exactly what must stop being the
# defence.
SECRET = "sk-test-" + "7c1f9e2a55d04b68" * 40
FRAGMENTS = [SECRET[:24], SECRET[140:164], SECRET[-24:], "sk-test-"]


class _Leaky:
    """An object whose str()/repr() are pure payload: if any code path
    stringifies it into an event, the fixture appears verbatim."""

    def __str__(self) -> str:  # pragma: no cover - fixture
        return SECRET

    def __repr__(self) -> str:  # pragma: no cover - fixture
        return SECRET


class ContentFreeCaptureTest(TraceHarnessMixin, unittest.TestCase):
    maxDiff = None

    def raw_events_text(self) -> str:
        directory = self.home / "trace" / "events"
        if not directory.is_dir():
            return ""
        return "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(directory.glob("*.jsonl"))
        )

    def assert_no_secret(self, where: str) -> None:
        blob = self.raw_events_text()
        for fragment in [SECRET] + FRAGMENTS:
            self.assertNotIn(fragment, blob, where)

    # ---- arguments -------------------------------------------------------
    def test_tool_arguments_leave_no_fragments_in_events(self) -> None:
        """Arguments are content by definition; only their shape survives."""
        args = {
            "path": "/etc/shadow",
            "note": SECRET,
            "nested": {"token": SECRET, "deep": [SECRET, 2]},
        }
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="write_file",
                  args=args, tool_call_id="c1", turn_id="t1")

        before = [e for e in self.events() if e.get("kind") == "tool_before"]
        self.assertTrue(before, "tool_before must still be recorded")
        self.assertNotIn("args", before[0],
                         "no form of the arguments may appear, clipped or not")
        self.assertEqual(before[0]["args_count"], 3)
        self.assertEqual(before[0]["args_chars"], len(json.dumps(args)))
        self.assert_no_secret("tool arguments leaked into the event stream")

    def test_unknown_args_object_defaults_to_omission(self) -> None:
        """A non-dict args value is omitted, never stringified for size."""
        self.start()
        self.fire("pre_tool_call", session_id="s1", tool_name="delegate",
                  args=_Leaky(), tool_call_id="c1")
        before = [e for e in self.events() if e.get("kind") == "tool_before"]
        self.assertTrue(before)
        self.assertIsNone(before[0]["args_count"])
        self.assertIsNone(before[0]["args_chars"])
        self.assert_no_secret("an unknown args object was stringified")

    # ---- results ---------------------------------------------------------
    def test_tool_result_text_is_never_captured(self) -> None:
        self.start()
        self.fire("post_tool_call", session_id="s1", tool_name="terminal",
                  result="TOKEN " + SECRET)
        after = [e for e in self.events() if e.get("kind") == "tool_after"]
        self.assertTrue(after, "tool_after must still be recorded")
        self.assertNotIn("result", after[0])
        self.assertEqual(after[0]["result_chars"], len("TOKEN " + SECRET))
        self.assert_no_secret("tool result text leaked into the event stream")

    def test_result_object_is_measured_not_stringified(self) -> None:
        """A non-string result contributes its size and nothing else."""
        self.start()
        self.fire("post_tool_call", session_id="s1", tool_name="vision_analyze",
                  result=_Leaky())
        after = [e for e in self.events() if e.get("kind") == "tool_after"]
        self.assertTrue(after)
        self.assertNotIn("result", after[0])
        self.assertEqual(after[0]["result_chars"], len(SECRET))
        self.assert_no_secret("a result object was stringified into the event")

    # ---- errors ----------------------------------------------------------
    def test_error_text_is_reduced_to_kind_and_length(self) -> None:
        """Exception messages are arbitrary text (provider bodies quote
        request content). The class name and the length are the safe facts."""
        self.start()
        self.fire("api_request_error", session_id="s1",
                  error=ValueError(SECRET), model="m1")
        errors = [e for e in self.events() if e.get("kind") == "api_error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0]["error_kind"], "ValueError")
        self.assertEqual(errors[0]["error_chars"], len(SECRET))
        self.assert_no_secret("error message text leaked into the event stream")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.assertEqual(json.loads(self.card()["errors"]),
                         [f"ValueError({len(SECRET)})"])

    def test_string_error_is_reduced_to_kind_and_length(self) -> None:
        self.start()
        self.fire("api_request_error", session_id="s1", error=SECRET, model=None)
        errors = [e for e in self.events() if e.get("kind") == "api_error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0]["error_kind"], "str")
        self.assertEqual(errors[0]["error_chars"], len(SECRET))
        self.assert_no_secret("a string error was captured verbatim")

    # ---- other identifier-adjacent fields --------------------------------
    def test_object_valued_hook_fields_are_omitted_not_stringified(self) -> None:
        """The final JSON write used default=str, which would happily write
        any object's str() into the file. Every field must be type-gated."""
        self.start()
        self.fire("subagent_start", session_id="s1", agent_name=_Leaky())
        starts = [e for e in self.events() if e.get("kind") == "subagent_start"]
        self.assertTrue(starts)
        self.assertIsNone(starts[0]["agent"])
        self.assert_no_secret("an object-valued field was stringified")

    def test_post_api_metrics_stay_content_free(self) -> None:
        """The provider/cache counters this exists for survive the gate."""
        self.start()
        self.fire("post_api_request", session_id="s1", model="m1",
                  api_duration=1.25,
                  usage={"prompt_tokens": 20_000, "input_tokens": 1_000,
                         "completion_tokens": 4, "cache_read_tokens": 19_000})
        after = [e for e in self.events() if e.get("kind") == "api_after"]
        self.assertTrue(after)
        self.assertEqual(after[0]["input"], 1_000)
        self.assertEqual(after[0]["cached"], 19_000)
        self.assertEqual(after[0]["prompt"], 20_000)


# A rendered index in the framework's own shape (`_render_skills_index`):
# category headers, `- name: description` entries, an entry without a
# description, and one demoted names-only category.
RENDERED_PROMPT = """## Skills
Before replying, scan the skills below.

<available_skills>
  general:
    - alpha: Does general things
    - beta
  social-media [names only]: gamma, delta
  org:org-1:
    - epsilon: by someone
</available_skills>

Only proceed without loading a skill if genuinely none are relevant."""


class RenderedExposureTest(TraceHarnessMixin, unittest.TestCase):
    maxDiff = None

    def test_exposed_names_come_from_the_rendered_index(self) -> None:
        """v2: count what the model was actually offered, not configuration."""
        self.plugin._rendered_skills_prompt = lambda: RENDERED_PROMPT
        self.start()
        self.fire("on_skill_lifecycle", session_id="s1", skill_name="alpha")
        self.fire("on_session_end", session_id="s1", completed=True)

        starts = [e for e in self.events() if e.get("kind") == "session_start"]
        self.assertEqual(starts[0]["skills_exposed_count"], 5)
        self.assertEqual(starts[0]["skills_exposed_def"], 2)

        card = self.card()
        self.assertEqual(card["skills_exposed_def"], "2")
        self.assertEqual(card["skills_exposed_count"], "5")
        self.assertEqual(json.loads(card["skills_exposed_unused"]),
                         ["beta", "delta", "epsilon", "gamma"])

    def test_zero_rendered_skills_reports_zero(self) -> None:
        """An empty rendered index is a fact about the surface, and v1
        would have answered it with the configured name list."""
        self.plugin._rendered_skills_prompt = lambda: ""
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)

        starts = [e for e in self.events() if e.get("kind") == "session_start"]
        self.assertEqual(starts[0]["skills_exposed_count"], 0)
        self.assertEqual(starts[0]["skills_exposed_def"], 2)
        card = self.card()
        self.assertEqual(card["skills_exposed_count"], "0")
        self.assertEqual(json.loads(card["skills_exposed_unused"]), [])

    def test_unavailable_rendered_index_reports_unknown(self) -> None:
        """No evidence means unknown — never installed-minus-disabled."""

        def unavailable() -> str:
            raise RuntimeError("renderer unavailable")

        self.plugin._rendered_skills_prompt = unavailable
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)

        starts = [e for e in self.events() if e.get("kind") == "session_start"]
        self.assertIsNone(starts[0]["skills_exposed_count"])
        self.assertEqual(starts[0]["skills_exposed_def"], 2)
        card = self.card()
        self.assertEqual(card["skills_exposed_count"], "unknown")
        self.assertEqual(card["skills_exposed_unused"], "unknown")

    def test_exposure_evidence_outlives_one_boundary(self) -> None:
        """A later successful render keeps its names alongside earlier ones."""
        self.plugin._EXPOSED_TTL_S = 0  # every start re-renders
        renders = iter([RENDERED_PROMPT, "", RENDERED_PROMPT])
        self.plugin._rendered_skills_prompt = lambda: next(renders)
        self.start()
        self.fire("on_session_end", session_id="s1", completed=True)
        self.start("s1")
        self.fire("on_session_end", session_id="s1", completed=True)
        self.start("s1")
        self.fire("on_session_end", session_id="s1", completed=True)
        # Union across starts; the empty render is a fact of its own moment
        # and never erases what was offered before.
        self.assertEqual(self.card()["skills_exposed_count"], "5")

    def test_parser_reads_the_frameworks_index_shapes(self) -> None:
        parse = self.plugin._names_from_rendered_index
        self.assertEqual(
            sorted(parse(RENDERED_PROMPT)),
            ["alpha", "beta", "delta", "epsilon", "gamma"])
        self.assertEqual(
            parse("<available_skills>\n  org:org-1 [names only]: zeta, eta\n"
                  "</available_skills>"),
            ["eta", "zeta"],
        )
        self.assertEqual(parse(""), [])
        # Non-empty prompt without the marker block: a format this parser
        # does not understand is unknown, not zero and not a guess.
        self.assertIsNone(parse("## Skills\n(no block)"))
        self.assertIsNone(parse("something else entirely"))


if __name__ == "__main__":
    unittest.main()
