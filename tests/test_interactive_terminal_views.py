"""Cohesive contracts for package-internal semantic terminal output views."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

import story_writing_agents
from story_writing_agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    COMMANDS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
)
from story_writing_agents.interactive_commands import AGENT_ROUTES
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    AttachmentKind,
    Field,
    InteractiveSemanticOutput,
    InteractiveTerminal,
    ListView,
    Message,
    MessagePresentation,
    Notice,
    Panel,
    Record,
    Section,
    SemanticRole,
    SpeakerAccent,
    ViewState,
)
from story_writing_agents.interactive_terminal import _core, _model
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    TerminalCapabilities,
)
from story_writing_agents.interactive_terminal._render import (
    format_agent_reply_content,
    render_component,
    render_plain,
    strip_decorative_sgr,
)


_ARTIFACT_REFERENCE_GUIDANCE = (
    "Artifact references require an attached session and focused conversation. "
    "In provider-bound prompts, $ opens whole-file selection and completion; "
    "exact $$ immediately opens ranged selection, where Enter/Tab opens the "
    "highlighted source in the viewer."
)


def _capability(
    profile: CapabilityProfile,
    *,
    width: int,
) -> TerminalCapabilities:
    """Build one correlated render profile without terminal I/O."""

    raw = profile is not CapabilityProfile.LINE
    return TerminalCapabilities(
        profile,
        width,
        input_tty=raw,
        output_tty=raw,
        raw_mode=raw,
        cursor_controls=raw,
        decorative_sgr=profile is CapabilityProfile.ANSI,
    )


def _literal_view() -> Panel:
    """Return one tree exercising labels, roles, states, and unsafe content."""

    return Panel(
        "Status",
        (
            Field("Workspace", "/stories/界"),
            Notice(
                "Unsafe\x1b[31m text",
                label="warning",
                role=SemanticRole.WARNING,
                state=ViewState.ERROR,
            ),
            Message(
                "plot",
                "Line one\nline two",
                role=SemanticRole.AGENT,
                state=ViewState.COMPLETED,
            ),
            Record(
                "debate_1",
                title="Consensus",
                content="Done.",
                role=SemanticRole.SUCCESS,
                state=ViewState.COMPLETED,
            ),
        ),
    )


def _dense_view() -> ListView:
    """Return one count-aware dense row with hostile and wide text."""

    return ListView(
        "Search results",
        (
            Record(
                "search c_0123456789",
                fields=(
                    Field("Status", "active"),
                    Field("Focus", "focused"),
                    Field("Subject", "Harbor 界 e\u0301"),
                    Field(
                        "Snippet",
                        "Unsafe\x1b]8;;owned\x07\u202e text",
                    ),
                ),
                state=ViewState.COMPLETED,
            ),
        ),
        total_count=3,
        position=1,
        state=ViewState.COMPLETED,
    )


def _physical_width(text: str) -> int:
    """Measure one already-sanitized row using the terminal cell contract."""

    return sum(_model.display_character_width(character) for character in text)


def _chat_block_message(
    content: str,
    *,
    sender: str = "plot",
    metadata: str | None = "Plot & Structure Architect",
    role: SemanticRole = SemanticRole.AGENT,
    state: ViewState = ViewState.COMPLETED,
    accent: SpeakerAccent = SpeakerAccent.YELLOW,
) -> Message:
    """Build one explicit speaker-rail fixture without changing INLINE defaults."""

    return Message(
        sender,
        content,
        role=role,
        state=state,
        presentation=MessagePresentation.CHAT_BLOCK,
        metadata=metadata,
        accent=accent,
    )


def _object_wrap_patch(target, attribute, value):
    return mock.patch.object(target, attribute, wraps=value)

class InteractiveSemanticOutputTests(unittest.TestCase):
    """Lock the internal adapter boundary and deterministic view fixtures."""

    def test_internal_facade_objects_are_unlisted_canonical_identities(self) -> None:
        expected = {
            "InteractiveSemanticOutput": _core.InteractiveSemanticOutput,
            "Field": _model.Field,
            "ListView": _model.ListView,
            "Message": _model.Message,
            "MessagePresentation": _model.MessagePresentation,
            "Notice": _model.Notice,
            "Panel": _model.Panel,
            "Record": _model.Record,
            "Section": _model.Section,
            "SemanticRole": _model.SemanticRole,
            "SpeakerAccent": _model.SpeakerAccent,
            "ViewState": _model.ViewState,
        }
        for name, defining_object in expected.items():
            with self.subTest(name=name):
                self.assertIs(getattr(terminal_facade, name), defining_object)
                self.assertNotIn(name, terminal_facade.__all__)
                self.assertFalse(hasattr(story_writing_agents, name))

        self.assertEqual(
            str(inspect.signature(InteractiveSemanticOutput)),
            "(terminal: 'InteractiveTerminal') -> 'None'",
        )
        self.assertEqual(
            str(inspect.signature(InteractiveSemanticOutput.write_component)),
            "(self, component: 'Component') -> 'None'",
        )
        self.assertEqual(
            str(inspect.signature(InteractiveSemanticOutput.write_accepted_input)),
            "(self, component: 'Component') -> 'None'",
        )
        self.assertEqual(
            InteractiveSemanticOutput.__slots__,
            ("_write", "_write_accepted"),
        )

        self.assertIs(
            terminal_facade.format_agent_reply_content,
            format_agent_reply_content,
        )
        self.assertNotIn("format_agent_reply_content", terminal_facade.__all__)
        self.assertFalse(hasattr(story_writing_agents, "format_agent_reply_content"))

    def test_agent_reply_formatter_pretty_prints_only_strict_json_containers(
        self,
    ) -> None:
        object_source = '{"z": "界", "a": [1, true]}'
        array_source = '[{"b":2,"a":1}, "é"]'
        self.assertEqual(
            format_agent_reply_content(object_source),
            '{\n  "z": "界",\n  "a": [\n    1,\n    true\n  ]\n}',
        )
        self.assertEqual(
            format_agent_reply_content(array_source),
            '[\n  {\n    "b": 2,\n    "a": 1\n  },\n  "é"\n]',
        )

        for source in (
            "42",
            "true",
            "null",
            "NaN",
            "Infinity",
            "-Infinity",
            '{"a": 1, "a": 2}',
            "{not json}",
            'prefix {"a": 1}',
        ):
            with self.subTest(source=source):
                self.assertIs(format_agent_reply_content(source), source)

    def test_agent_reply_newlines_are_narrowly_normalized_for_agent_chat_blocks(
        self,
    ) -> None:
        actual = "first\n\nsecond"
        singly_escaped = r"first\n\nsecond\r\nthird\x0afourth\x0Afifth"
        doubly_escaped = r"first\\nsecond\\r\\nthird\\x0afourth\\x0Afifth"

        self.assertEqual(format_agent_reply_content(actual), actual)
        self.assertEqual(
            format_agent_reply_content(singly_escaped),
            "first\n\nsecond\nthird\nfourth\nfifth",
        )
        self.assertEqual(
            format_agent_reply_content(doubly_escaped),
            doubly_escaped,
        )
        self.assertEqual(
            format_agent_reply_content(r"invalid {json}\nnext"),
            "invalid {json}\nnext",
        )
        self.assertEqual(format_agent_reply_content(r"42"), "42")

        agent = render_plain(
            _chat_block_message(singly_escaped, sender="lead", metadata=None),
            width=80,
        ).text
        default = render_plain(
            _chat_block_message(
                singly_escaped,
                sender="user",
                metadata=None,
                role=SemanticRole.DEFAULT,
            ),
            width=80,
        ).text
        self.assertTrue(all(agent.index(word) < agent.index(next_word) for word, next_word in zip(("first", "second", "third", "fourth"), ("second", "third", "fourth", "fifth"))))
        self.assertIn(singly_escaped, default)

    def test_agent_reply_rendering_uses_bold_sgr_and_plain_no_color(self) -> None:
        source = '{"message":"界"}'
        component = Message(
            "plot",
            format_agent_reply_content(source),
            role=SemanticRole.AGENT,
            state=ViewState.COMPLETED,
        )
        ansi = render_component(
            component,
            _capability(CapabilityProfile.ANSI, width=80),
        )
        plain = render_component(
            component,
            _capability(CapabilityProfile.PLAIN, width=80),
        )
        self.assertTrue(ansi.text.startswith("\x1b[1mplot> {"))
        self.assertNotIn("\x1b", plain.text)
        self.assertIn('  "message": "界"', plain.text)

    def test_chat_block_plain_snapshots_cover_supported_widths_and_geometry(self) -> None:
        message = _chat_block_message("Keep each returning vessel visible while harbor lantern guides final approach safely.",
            sender="plot", metadata="Outcome: answer\nTokens: input 0 | output 2")
        for width in (1, 20, 39, 40, 140):
            with self.subTest(width=width):
                frame = render_plain(message, width=width)
                lines = frame.text.split("\n")
                self.assertEqual(frame.width, width)
                self.assertEqual(frame.line_count, len(lines))
                self.assertTrue(all(_physical_width(line) <= min(width, 120) for line in lines))
                self.assertEqual(lines[-1], "")
                if width >= 40:
                    self.assertTrue(lines[0].startswith("╭─ plot "))
                    self.assertEqual(_physical_width(lines[0]), min(width, 120))
                    self.assertTrue(all(line.startswith("│ ") and line.endswith(" │")
                        for line in lines[1:] if line and line[0] not in "╭├╰"))
                    self.assertGreater(frame.text.index("├─ Metadata "), frame.text.index("│ Keep"))
                    self.assertTrue(lines[-2].startswith("╰") and lines[-2].endswith("╯"))
                else:
                    self.assertNotIn("╭", frame.text)
                    self.assertEqual(lines[-2], "─" * width)
        self.assertEqual(_physical_width(render_plain(message, width=120).text.split("\n")[0]), 120)
        for width in (1, 2, 3, 4):
            tiny = render_plain(_chat_block_message("abc", sender="u", metadata=None), width=width)
            self.assertTrue(all(_physical_width(line) <= width for line in tiny.text.split("\n")))
            self.assertEqual("│" in tiny.text, width >= 3)
            self.assertTrue(all(tiny.text.count(letter) == 1 for letter in "abc"))
            if width == 3:
                self.assertIn("│ab\n│c", tiny.text)


    def test_chat_block_preserves_words_tokens_and_exact_whitespace(self) -> None:
        cases = ((10, "alpha beta abcdefghijk"), (40, "```text\n  indented words here  \n\n  final\n```"),
            (12, "first\n\nsecond"))
        for width, content in cases:
            with self.subTest(width=width, content=repr(content)):
                frame = render_plain(_chat_block_message(content, sender="u", metadata=None,
                    role=SemanticRole.DEFAULT, state=ViewState.IDLE), width=width)
                self.assertTrue(all(_physical_width(line) <= width for line in frame.text.split("\n")))
                if width == 10:
                    self.assertIn("alpha", frame.text); self.assertIn("beta", frame.text)
                    self.assertIn("│ abcdefgh\n│ ijk", frame.text)
                elif width == 40:
                    self.assertIn("│   indented words here", frame.text)
                    self.assertIn("│   final", frame.text)
                    self.assertNotIn("```", frame.text)
                else:
                    self.assertIn("│ first\n│ \n│ second", frame.text)


    def test_chat_block_unicode_json_and_hostile_controls_are_safe(self) -> None:
        source = "# Heading\n**bold** *italic* `code`\n\n- item\n  - nested\n\n> quote\n\n[label](https://example.com/a)\n\nentity &#27;[31m and &#x202e;\n界界界 e\u0301 🌊 x"
        message = _chat_block_message(source, metadata="Tokens: input 0 | output 2")
        ansi = render_component(message, _capability(CapabilityProfile.ANSI, width=40)).text
        plain = render_plain(message, width=40).text
        self.assertEqual(strip_decorative_sgr(ansi), plain)
        self.assertNotIn("\x1b", plain); self.assertNotIn("\u202e", plain)
        self.assertNotIn("\x07", ansi); self.assertNotIn("\x1b]", ansi)
        self.assertTrue(all(_physical_width(line) <= 40 for line in plain.split("\n")))
        for visible in ("Heading", "bold", "italic", "code", "item", "nested", "> quote", "label", "https://example.com/a", "界界界", "e\u0301", "\ufffd[31m", "\\u202e"):
            self.assertIn(visible, plain)
        self.assertNotIn("**bold**", plain); self.assertNotIn("[label](", plain)
        self.assertIn("\x1b[1m", ansi); self.assertIn("\x1b[3m", ansi)
        hostile = _chat_block_message("a\x1b]8;;owned\x07\u202e\n[link](https://example.com/&#27;)", metadata="m\u202e")
        sanitized = render_plain(hostile, width=80).text
        self.assertNotIn("\x1b", sanitized); self.assertNotIn("\x07", sanitized)
        self.assertNotIn("\u202e", sanitized); self.assertIn("\\x1b]8;;owned\\x07\\u202e", sanitized)
        literal = _chat_block_message(format_agent_reply_content('{"z":"界","a":[1,true]}'), metadata=None)
        rendered = render_plain(literal, width=40).text
        self.assertIn('│   "z": "界",', rendered); self.assertIn('│     true', rendered)
        fenced = render_plain(_chat_block_message("```python\n  **literal** <tag>\n```", metadata=None), width=40).text
        self.assertIn("│   **literal** <tag>", fenced)
        for unsupported in ("![image](https://example.com/a)", "| a | b |\n| --- | --- |", "<b>literal</b>"):
            rendered = render_plain(_chat_block_message(unsupported, metadata=None), width=80).text
            self.assertIn(unsupported.split("\n")[0], rendered)
        with mock.patch("story_writing_agents.interactive_terminal._markdown.MarkdownIt", side_effect=RuntimeError("parser failed")):
            fallback = render_plain(_chat_block_message("**literal fallback**", metadata=None), width=40).text
        self.assertIn("**literal fallback**", fallback)
        label = "界e\u0301" * 20 + "\x1b[31m\u202e"
        labeled = render_plain(_chat_block_message("body", sender=label, metadata="Outcome: safe"), width=40).text
        self.assertEqual(labeled.count("界"), 20); self.assertEqual(labeled.count("e\u0301"), 20)
        self.assertEqual((labeled.count("╭"), labeled.count("╰")), (1, 1))
        self.assertNotIn("\x1b", labeled); self.assertNotIn("\u202e", labeled)
        self.assertTrue(all(_physical_width(row) == 40 for row in labeled.splitlines() if row))
        for width in (3, 10):
            narrow = render_plain(_chat_block_message("界界界 e\u0301 🌊 x", metadata=None), width=width).text
            self.assertTrue(all(_physical_width(row) <= width for row in narrow.splitlines()))
            self.assertEqual(narrow.count("界"), 3); self.assertIn("e\u0301", narrow)


    def test_completed_state_is_silent_without_rewriting_literal_content(self) -> None:
        rendered = render_plain(_chat_block_message("body [completed]", sender="provider [completed]",
            metadata="literal [completed]", role=SemanticRole.DEFAULT, state=ViewState.COMPLETED,
            accent=SpeakerAccent.CYAN), width=80).text
        self.assertEqual(rendered.count("[completed]"), 3)
        self.assertTrue(rendered.startswith("╭─ provider [completed] "))
        self.assertLess(rendered.index("body [completed]"), rendered.index("├─ Metadata "))


    def test_chat_block_accents_only_headers_and_profiles_share_topology(self) -> None:
        expected_sgr = {SpeakerAccent.MAGENTA: "\x1b[1;35m", SpeakerAccent.CYAN: "\x1b[1;36m",
            SpeakerAccent.YELLOW: "\x1b[1;33m", SpeakerAccent.GREEN: "\x1b[1;32m", SpeakerAccent.BLUE: "\x1b[1;34m"}
        for accent, sgr in expected_sgr.items():
            with self.subTest(accent=accent):
                message = _chat_block_message("first\nsecond", sender="you", metadata="Tokens: input 0 | output 2",
                    role=SemanticRole.DEFAULT, state=ViewState.IDLE, accent=accent)
                ansi = render_component(message, _capability(CapabilityProfile.ANSI, width=80))
                plain = render_component(message, _capability(CapabilityProfile.PLAIN, width=80))
                line = render_component(message, _capability(CapabilityProfile.LINE, width=80))
                self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
                self.assertEqual(plain, line)
                self.assertTrue(ansi.text.startswith(sgr + "╭─ you "))
                self.assertIn("\x1b[0m", ansi.text)
                body = next(row for row in ansi.text.split("\n") if "first" in row)
                self.assertTrue(body.startswith(sgr + "│\x1b[0m first"))
                self.assertTrue(body.endswith(sgr + "│\x1b[0m"))
                self.assertEqual(body.count("\x1b"), 4)
                self.assertIn("\x1b[34m", next(row for row in ansi.text.split("\n") if "Metadata" in row))
                self.assertIn("\x1b[2m", next(row for row in ansi.text.split("\n") if "Tokens:" in row))
                self.assertIn("Metadata", plain.text)
                self.assertLess(plain.text.index("first"), plain.text.index("Metadata"))
        wrapped = render_component(_chat_block_message("body", sender="plot",
            metadata="Plot & Structure Architect", accent=SpeakerAccent.YELLOW), _capability(CapabilityProfile.ANSI, width=20))
        self.assertTrue(wrapped.text.startswith("\x1b[1;33m─ plot ─\x1b[0m"))
        self.assertIn("─ Metadata ─", strip_decorative_sgr(wrapped.text))
        self.assertTrue(all(_physical_width(row) <= 20 for row in strip_decorative_sgr(wrapped.text).split("\n")))


    def test_list_view_renders_inline_and_chat_block_messages_in_order(self) -> None:
        view = ListView("Messages", (Field("Before", "field"), Message("inline", "old", state=ViewState.IDLE),
            _chat_block_message("one two", sender="you", metadata=None, role=SemanticRole.DEFAULT,
                state=ViewState.IDLE, accent=SpeakerAccent.MAGENTA), Record("after", content="record")),
            total_count=4, state=ViewState.COMPLETED)
        frame = render_plain(view, width=40)
        self.assertEqual(tuple(type(item) for item in view.items), (Field, Message, Message, Record))
        self.assertTrue(frame.text.startswith("Messages (4)\n"))
        self.assertTrue(frame.text.index("Before:") < frame.text.index("inline>") < frame.text.index("─ you ─")
            < frame.text.index("one two") < frame.text.index("after"))
        self.assertTrue(all(_physical_width(line) <= 40 for line in frame.text.split("\n")))
        self.assertEqual(frame.line_count, len(frame.text.split("\n")))


    def test_adjacent_chat_blocks_own_one_separator_across_composition(self) -> None:
        first = _chat_block_message("body", sender="first", metadata=None,
            role=SemanticRole.DEFAULT, state=ViewState.IDLE, accent=SpeakerAccent.MAGENTA)
        second = _chat_block_message("reply", sender="second", metadata=None,
            state=ViewState.IDLE, accent=SpeakerAccent.YELLOW)
        section = Section("Chat", (first, second))
        text = render_plain(section, width=80).text
        self.assertEqual(text.count("╭─ first "), 1); self.assertEqual(text.count("╭─ second "), 1)
        self.assertIn("\n\n  ╭─ second ", text)
        self.assertNotIn("\n\n\n  ╭─ second ", text)
        records: list[str] = []
        output = InteractiveSemanticOutput(InteractiveTerminal(lambda _prompt: "unused", records.append))
        output.write_component(first); output.write_component(second)
        self.assertEqual(len(records), 2)
        joined = "".join(records)
        self.assertIn("\n\n╭─ second ", joined)
        self.assertNotIn("\n\n\n╭─ second ", joined)
        self.assertIn("│ body", joined); self.assertIn("│ reply", joined)


    def test_adapter_construction_is_resource_free_and_each_call_writes_once(
        self,
    ) -> None:
        reads: list[str] = []
        records: list[str] = []

        def read_line(prompt: str) -> str:
            reads.append(prompt)
            raise AssertionError("semantic output must not read")

        terminal = InteractiveTerminal(read_line, records.append, width=80)
        with _object_wrap_patch(_core, "render_component", render_component) as render:
            output = InteractiveSemanticOutput(terminal)
            self.assertEqual(reads, [])
            self.assertEqual(records, [])
            render.assert_not_called()
            choice = _model.Choice("one", "One")
            components = (
                _model.Prompt("input"),
                _literal_view(),
                _model.Section("Section", (Field("Key", "Value"),)),
                Field("Key", "Value"),
                Notice("Notice"),
                Message("lead", "Message"),
                Record("record", content="Content"),
                _model.ListView("Rows", (Field("Key", "Value"),)),
                _model.SuggestionPalette("/", (choice,)),
                _model.Picker(_model.Prompt("choose"), (choice,)),
            )
            for component in components:
                with self.subTest(component=type(component).__name__):
                    prior_records = len(records)
                    prior_renders = render.call_count
                    output.write_component(component)
                    self.assertEqual(len(records), prior_records + 1)
                    self.assertEqual(render.call_count, prior_renders + 1)
                    self.assertEqual(render.call_args.args[0], component)
                    self.assertTrue(records[-1].endswith("\n"))

        self.assertEqual(render.call_count, len(components))
        self.assertEqual(len(records), len(components))

    def test_callback_accepted_input_validates_but_remains_silent(self) -> None:
        reads: list[str] = []
        records: list[str] = []

        def read_line(prompt: str) -> str:
            reads.append(prompt)
            raise AssertionError("accepted input output must not read")

        terminal = InteractiveTerminal(read_line, records.append, width=80)
        output = InteractiveSemanticOutput(terminal)
        component = Message("user", "Accepted input remains callback-visible.")

        output.write_accepted_input(component)
        self.assertEqual(reads, [])
        self.assertEqual(records, [])

        with self.assertRaisesRegex(TypeError, "closed semantic vocabulary"):
            output.write_accepted_input(object())  # type: ignore[arg-type]
        self.assertEqual(reads, [])
        self.assertEqual(records, [])

    def test_adapter_rejects_non_terminal_and_propagates_render_and_writer_failures(
        self,
    ) -> None:
        with self.assertRaisesRegex(TypeError, "InteractiveTerminal"):
            InteractiveSemanticOutput(object())  # type: ignore[arg-type]

        records: list[str] = []
        output = InteractiveSemanticOutput(
            InteractiveTerminal(lambda prompt: "", records.append)
        )
        with self.assertRaisesRegex(TypeError, "closed semantic vocabulary"):
            output.write_component(object())  # type: ignore[arg-type]
        self.assertEqual(records, [])

        calls = 0

        def failing_writer(_text: str) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("writer failed")

        output = InteractiveSemanticOutput(
            InteractiveTerminal(lambda prompt: "", failing_writer)
        )
        with self.assertRaisesRegex(RuntimeError, "writer failed"):
            output.write_component(_literal_view())
        self.assertEqual(calls, 1)

    def test_literal_tree_has_exact_plain_and_narrow_rendering(self) -> None:
        self.assertEqual(
            render_plain(_literal_view(), width=80).text,
            "--- Status ---------------------------------------------------------------------\n"
            "  Workspace: /stories/界\n"
            "  [warning] Unsafe\\x1b[31m text\n"
            "  plot> Line one\n"
            "        line two\n"
            "  [debate_1] Consensus [success]\n"
            "    Done.\n"
            "--------------------------------------------------------------------------------",
        )
        narrow = render_plain(_literal_view(), width=32)
        self.assertEqual(
            narrow.text,
            "--- Status ---------------------\n"
            " Workspace:\n"
            "  /stories/界\n"
            " [warning] Unsafe\\x1b[31m text\n"
            " plot> Line one\n"
            "       line two\n"
            " [debate_1] Consensus [success]\n"
            "  Done.\n"
            "--------------------------------",
        )
        self.assertEqual(narrow.width, 32)
        self.assertEqual(narrow.line_count, 9)
        self.assertNotIn("\x1b", narrow.text)

    def test_literal_tree_has_exact_ansi_and_no_color_plain_equivalence(self) -> None:
        ansi = render_component(
            _literal_view(),
            _capability(CapabilityProfile.ANSI, width=80),
        )
        self.assertEqual(
            ansi.text,
            "\x1b[1m--- Status ---------------------------------------------------------------------\x1b[0m\n"
            "  Workspace: /stories/界\n"
            "\x1b[33m  [warning] Unsafe\\x1b[31m text\x1b[0m\n"
            "\x1b[1m  plot> Line one\x1b[0m\n"
            "\x1b[1m        line two\x1b[0m\n"
            "\x1b[32m  [debate_1] Consensus [success]\x1b[0m\n"
            "\x1b[32m    Done.\x1b[0m\n"
            "\x1b[1m--------------------------------------------------------------------------------\x1b[0m",
        )
        plain = render_component(
            _literal_view(),
            _capability(CapabilityProfile.PLAIN, width=80),
        )
        line = render_component(
            _literal_view(),
            _capability(CapabilityProfile.LINE, width=80),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertEqual(plain, line)
        self.assertNotIn("\x1b", plain.text)
        self.assertIn("Unsafe\\x1b[31m", plain.text)

    def test_dense_view_has_exact_plain_narrow_ansi_and_line_equivalence(
        self,
    ) -> None:
        view = _dense_view()
        wide = render_plain(view, width=80)
        self.assertEqual(
            wide.text,
            "Search results (1/3)\n"
            "  [search c_0123456789]\n"
            "    Status: active\n"
            "    Focus: focused\n"
            "    Subject: Harbor 界 e\u0301\n"
            "    Snippet: Unsafe\\x1b]8;;owned\\x07\\u202e text",
        )
        self.assertEqual((wide.width, wide.line_count), (80, 6))

        narrow = render_plain(view, width=32)
        self.assertEqual(
            narrow.text,
            "Search results (1/3)\n"
            " [search c_0123456789]\n"
            "  Status:\n"
            "   active\n"
            "  Focus:\n"
            "   focused\n"
            "  Subject:\n"
            "   Harbor 界 e\u0301\n"
            "  Snippet:\n"
            "   Unsafe\\x1b]8;;owned\\x07\\u202e\n"
            "    text",
        )
        self.assertEqual((narrow.width, narrow.line_count), (32, 11))

        ansi = render_component(
            view,
            _capability(CapabilityProfile.ANSI, width=80),
        )
        self.assertEqual(
            ansi.text,
            "\x1b[1mSearch results (1/3)\x1b[0m\n"
            "  [search c_0123456789]\n"
            "    Status: active\n"
            "    Focus: focused\n"
            "    Subject: Harbor 界 e\u0301\n"
            "    Snippet: Unsafe\\x1b]8;;owned\\x07\\u202e text",
        )
        plain = render_component(
            view,
            _capability(CapabilityProfile.PLAIN, width=80),
        )
        line = render_component(
            view,
            _capability(CapabilityProfile.LINE, width=80),
        )
        self.assertEqual(strip_decorative_sgr(ansi.text), plain.text)
        self.assertEqual(plain, line)
        self.assertNotIn("\x1b]", ansi.text)
        self.assertNotIn("\x07", ansi.text)
        self.assertNotIn("\u202e", ansi.text)
        self.assertIn("\\x1b]8;;owned\\x07\\u202e", ansi.text)
        self.assertEqual(view.items[0].state, ViewState.COMPLETED)
        self.assertTrue(
            all(field.state is ViewState.IDLE for field in view.items[0].fields)
        )

    def test_specialized_terminal_writers_build_exact_semantic_trees(self) -> None:
        terminal = InteractiveTerminal(lambda prompt: "", lambda text: None)
        cases = (
            (
                lambda: terminal.write_lobby_orientation("/stories/example"),
                Panel(
                    "Story chat",
                    (
                        Field("Workspace", "/stories/example"),
                        Field("Session", "lobby"),
                        Notice(
                            "Provider calls are unavailable in the lobby. Use "
                            "/session create NAME, /session resume [NAME], or "
                            "/session list.",
                            role=SemanticRole.WARNING,
                        ),
                        Notice(_ARTIFACT_REFERENCE_GUIDANCE),
                        Notice("Type /help for commands; /quit exits."),
                    ),
                ),
            ),
            (
                lambda: terminal.write_session_orientation(
                    "/stories/example",
                    "SESSION",
                    AttachmentKind.OPENED,
                    PLOT_ARCHITECT,
                    2,
                ),
                Panel(
                    "Story chat",
                    (
                        Field("Workspace", "/stories/example"),
                        Field("Session", "SESSION (opened)"),
                        Field("Target", "Plot & Structure Architect"),
                        Field("History", "2 visible room events"),
                        Notice(
                            "Room messages are saved locally and shared with "
                            "addressed agents."
                        ),
                        Notice(_ARTIFACT_REFERENCE_GUIDANCE),
                        Notice(
                            "Type /help for commands; /quit releases access "
                            "without pausing."
                        ),
                    ),
                ),
            ),
            (
                lambda: terminal.write_conversation_scope(
                    session_id="SESSION",
                    conversation_handle="c_0123456789",
                    conversation_subject="Opening scene",
                    agent_id=LEAD_ORCHESTRATOR,
                ),
                Panel(
                    "Conversation scope",
                    (
                        Field("Session", "SESSION"),
                        Field("Conversation", "c_0123456789"),
                        Field("Subject", "Opening scene"),
                        Field("Agent", "lead"),
                    ),
                ),
            ),
            (
                lambda: terminal.write_run("DEBATE-1", "complete"),
                Record("run DEBATE-1", content="complete"),
            ),
            (
                lambda: terminal.write_agent(PLOT_ARCHITECT, "reply"),
                Message(
                    "plot",
                    "reply",
                    role=SemanticRole.AGENT,
                    state=ViewState.COMPLETED,
                    presentation=MessagePresentation.CHAT_BLOCK,
                    accent=SpeakerAccent.YELLOW,
                ),
            ),
            (
                lambda: terminal.write_error(
                    terminal_facade.InteractiveError(
                        terminal_facade.InteractiveErrorCode.INTERNAL_ERROR
                    )
                ),
                Panel(
                    "Interactive error",
                    (
                        Notice(
                            "An internal error occurred.",
                            label="error internal_error",
                            role=SemanticRole.ERROR,
                            state=ViewState.ERROR,
                        ),
                        Notice(
                            "Retry once; if it repeats, report the "
                            "internal_error code.",
                            label="hint",
                        ),
                    ),
                    state=ViewState.ERROR,
                ),
            ),
        )
        with _object_wrap_patch(_core, "render_component", render_component) as render:
            for call, expected in cases:
                with self.subTest(expected=expected):
                    before = render.call_count
                    call()
                    self.assertEqual(render.call_count, before + 1)
                    self.assertEqual(render.call_args.args[0], expected)

    def test_write_agent_maps_every_identity_once_and_formats_json_first(
        self,
    ) -> None:
        records: list[str] = []
        terminal = InteractiveTerminal(lambda prompt: "", records.append)
        agent_cases = (
            (LEAD_ORCHESTRATOR, "lead", SpeakerAccent.CYAN),
            (PLOT_ARCHITECT, "plot", SpeakerAccent.YELLOW),
            (CHARACTER_CUSTODIAN, "character", SpeakerAccent.GREEN),
            (EXECUTION_SPECIALIST, "execution", SpeakerAccent.BLUE),
        )
        expected_content = '{\n  "answer": "界"\n}'
        with _object_wrap_patch(_core, "render_component", render_component) as render:
            for agent_id, alias, accent in agent_cases:
                with self.subTest(agent_id=agent_id):
                    prior_renders = render.call_count
                    prior_records = len(records)
                    terminal.write_agent(agent_id, '{"answer":"界"}')
                    self.assertEqual(render.call_count, prior_renders + 1)
                    self.assertEqual(len(records), prior_records + 1)
                    self.assertEqual(
                        render.call_args.args[0],
                        Message(
                            alias,
                            '{"answer":"界"}',
                            role=SemanticRole.AGENT,
                            state=ViewState.COMPLETED,
                            presentation=MessagePresentation.CHAT_BLOCK,
                            accent=accent,
                        ),
                    )
                    self.assertTrue(records[-1].startswith(f"╭─ {alias} "))
                    body = "\n".join(row[2:-2].rstrip() for row in records[-1].splitlines() if row.startswith("│ "))
                self.assertEqual(body, expected_content)

        self.assertEqual(render.call_count, 4)
        self.assertEqual(len(records), 4)

    def test_dense_terminal_writers_build_exact_semantic_trees(self) -> None:
        terminal = InteractiveTerminal(lambda prompt: "", lambda text: None)
        roster = tuple(
            Record(
                "agent",
                title=AGENT_SPECS[route.agent_id].display_name,
                fields=(
                    Field("ID", route.agent_id),
                    Field("Aliases", ", ".join(route.aliases)),
                    Field("Advisory domain", route.advisory_domain),
                ),
                role=SemanticRole.AGENT,
                state=ViewState.COMPLETED,
            )
            for route in AGENT_ROUTES
        )
        help_sections = (
            Section(
                "Writing artifact references",
                (Notice(_ARTIFACT_REFERENCE_GUIDANCE),),
                state=ViewState.COMPLETED,
            ),
            *tuple(
                Section(
                    group,
                    (
                        ListView(
                            "Commands",
                            tuple(
                                Record(
                                    "command",
                                    title=spec.syntax,
                                    fields=(
                                        Field(
                                            "Aliases",
                                            ", ".join(spec.aliases)
                                            if spec.aliases
                                            else "none",
                                        ),
                                        Field(
                                            "Calls provider",
                                            "yes" if spec.may_call_provider else "no",
                                        ),
                                        Field(
                                            "May write artifacts",
                                            "yes" if spec.may_write_artifacts else "no",
                                        ),
                                    ),
                                    role=SemanticRole.COMMAND,
                                    state=ViewState.COMPLETED,
                                )
                                for spec in COMMANDS
                                if spec.group == group
                            ),
                            total_count=sum(spec.group == group for spec in COMMANDS),
                            state=ViewState.COMPLETED,
                        ),
                    ),
                    state=ViewState.COMPLETED,
                )
                for group in dict.fromkeys(spec.group for spec in COMMANDS)
            ),
        )
        agent_help = next(spec for spec in COMMANDS if spec.syntax == "/agents")
        cases = (
            (
                lambda: terminal.write_conversation_list_entry(
                    "c_0123456789",
                    "Opening scene",
                    archived=False,
                    focused=True,
                ),
                Record(
                    "conversation c_0123456789",
                    fields=(
                        Field("Status", "active"),
                        Field("Focus", "focused"),
                        Field("Subject", "Opening scene"),
                    ),
                    state=ViewState.COMPLETED,
                ),
            ),
            (
                lambda: terminal.write_conversation_search_hit(
                    "c_0123456789",
                    "Opening scene",
                    "event",
                    "harbor clue",
                    archived=True,
                    citation_turn_id="turn_2",
                    citation_sequence=3,
                    citation_kind="reply",
                ),
                Message("search c_0123456789", "harbor clue", presentation=MessagePresentation.CHAT_BLOCK, metadata="Status: archived\nMatch: event\nSubject: Opening scene\nCitation: conversation c_0123456789, turn turn_2, sequence 3, kind reply\nOutcome: search excerpt\nInput tokens: unavailable | Output tokens: unavailable", role=SemanticRole.MUTED, state=ViewState.COMPLETED),
            ),
            (
                lambda: terminal.write_conversation_transcript_event(
                    "c_0123456789",
                    sequence=3,
                    turn_id="turn_2",
                    kind="reply",
                    sender="plot",
                    recipient="user",
                    content="line one\nline two\n",
                ),
                Record(
                    "transcript c_0123456789",
                    fields=(
                        Field("Sequence", "3"),
                        Field("Turn", "turn_2"),
                        Field("Kind", "reply"),
                        Field("Sender", "plot"),
                        Field("Recipient", "user"),
                        Field("Content", ""),
                    ),
                    content="line one\nline two\n",
                    state=ViewState.COMPLETED,
                ),
            ),
            (
                terminal.write_agent_roster,
                ListView(
                    "Agents",
                    roster,
                    total_count=4,
                    state=ViewState.COMPLETED,
                ),
            ),
            (
                terminal.write_help,
                Panel("Help", help_sections, state=ViewState.COMPLETED),
            ),
            (
                lambda: terminal.write_help("/agents"),
                Panel(
                    "Help",
                    (
                        Record(
                            "command",
                            fields=(
                                Field("Group", agent_help.group),
                                Field("Syntax", agent_help.syntax),
                                Field(
                                    "Aliases",
                                    ", ".join(agent_help.aliases) or "none",
                                ),
                                Field("Calls provider", "no"),
                                Field("May write artifacts", "no"),
                                Field("Example", agent_help.example),
                            ),
                            role=SemanticRole.COMMAND,
                            state=ViewState.COMPLETED,
                        ),
                    ),
                    state=ViewState.COMPLETED,
                ),
            ),
        )
        with _object_wrap_patch(_core, "render_component", render_component) as render:
            for call, expected in cases:
                with self.subTest(expected=expected):
                    before = render.call_count
                    call()
                    self.assertEqual(render.call_count, before + 1)
                    self.assertEqual(render.call_args.args[0], expected)

    def test_each_dense_terminal_writer_propagates_one_callback_failure(self) -> None:
        calls: list[str] = []

        def failing_writer(text: str) -> None:
            calls.append(text)
            raise RuntimeError("writer failed")

        terminal = InteractiveTerminal(lambda prompt: "", failing_writer)
        dense_calls = (
            lambda: terminal.write_conversation_list_entry(
                "c_0123456789",
                "Subject",
                archived=False,
                focused=True,
            ),
            lambda: terminal.write_conversation_search_hit(
                "c_0123456789",
                "Subject",
                "subject",
                "snippet",
                archived=False,
            ),
            lambda: terminal.write_conversation_transcript_event(
                "c_0123456789",
                sequence=1,
                turn_id="turn_1",
                kind="message",
                sender="user",
                recipient="plot",
                content="body",
            ),
            terminal.write_agent_roster,
            terminal.write_help,
        )
        for call in dense_calls:
            with self.subTest(call=call):
                before = len(calls)
                with self.assertRaisesRegex(RuntimeError, "writer failed"):
                    call()
                self.assertEqual(len(calls), before + 1)
                self.assertTrue(calls[-1].endswith("\n"))


if __name__ == "__main__":
    unittest.main()
