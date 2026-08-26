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
            '{\n'
            '  "z": "界",\n'
            '  "a": [\n'
            '    1,\n'
            '    true\n'
            '  ]\n'
            '}',
        )
        self.assertEqual(
            format_agent_reply_content(array_source),
            '[\n'
            '  {\n'
            '    "b": 2,\n'
            '    "a": 1\n'
            '  },\n'
            '  "é"\n'
            ']',
        )

        for source in (
            "42",
            "true",
            "null",
            "NaN",
            "Infinity",
            "-Infinity",
            '{"a": 1, "a": 2}',
            '{not json}',
            'prefix {"a": 1}',
        ):
            with self.subTest(source=source):
                self.assertIs(format_agent_reply_content(source), source)

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
        self.assertTrue(ansi.text.startswith("\x1b[1mplot [completed]> {"))
        self.assertNotIn("\x1b", plain.text)
        self.assertIn('  "message": "界"', plain.text)

    def test_chat_block_plain_snapshots_cover_supported_widths_and_geometry(
        self,
    ) -> None:
        message = _chat_block_message(
            "Keep each returning vessel visible while the harbor lantern "
            "guides the final approach safely."
        )
        snapshots = {
            20: (
                "plot · Plot & \n"
                "Structure Architect \n"
                "[completed]\n"
                "\n"
                "│ Keep each \n"
                "│ returning vessel \n"
                "│ visible while the \n"
                "│ harbor lantern \n"
                "│ guides the final \n"
                "│ approach safely."
            ),
            24: (
                "plot · Plot & Structure \n"
                "Architect [completed]\n"
                "\n"
                "│ Keep each returning \n"
                "│ vessel visible while \n"
                "│ the harbor lantern \n"
                "│ guides the final \n"
                "│ approach safely."
            ),
            40: (
                "plot · Plot & Structure Architect \n"
                "[completed]\n"
                "\n"
                "│ Keep each returning vessel visible \n"
                "│ while the harbor lantern guides the \n"
                "│ final approach safely."
            ),
            80: (
                "plot · Plot & Structure Architect [completed]\n"
                "\n"
                "│ Keep each returning vessel visible while the harbor "
                "lantern guides the final \n"
                "│ approach safely."
            ),
            120: (
                "plot · Plot & Structure Architect [completed]\n"
                "\n"
                "│ Keep each returning vessel visible while the harbor "
                "lantern guides the final approach safely."
            ),
        }
        for width, expected in snapshots.items():
            with self.subTest(width=width):
                frame = render_plain(message, width=width)
                self.assertEqual(frame.text, expected)
                self.assertEqual(frame.width, width)
                self.assertEqual(frame.line_count, expected.count("\n") + 1)
                for line in frame.text.split("\n"):
                    self.assertLessEqual(_physical_width(line), width)

        geometry = _chat_block_message(
            "ab",
            sender="u",
            metadata=None,
            role=SemanticRole.DEFAULT,
            state=ViewState.IDLE,
            accent=SpeakerAccent.MAGENTA,
        )
        self.assertEqual(render_plain(geometry, width=1).text, "u\n\na\nb")
        self.assertEqual(render_plain(geometry, width=2).text, "u\n\nab")
        self.assertEqual(render_plain(geometry, width=3).text, "u\n\n│ a\n│ b")
        self.assertEqual(render_plain(geometry, width=4).text, "u\n\n│ ab")

        row_local_wide = _chat_block_message(
            "a界b",
            sender="u",
            metadata=None,
            role=SemanticRole.DEFAULT,
            state=ViewState.IDLE,
            accent=SpeakerAccent.MAGENTA,
        )
        wide_frame = render_plain(row_local_wide, width=3)
        self.assertEqual(wide_frame.text, "u\n\n│ a\n界\n│ b")
        self.assertTrue(
            all(_physical_width(line) <= 3 for line in wide_frame.text.split("\n"))
        )

    def test_chat_block_preserves_words_tokens_and_exact_whitespace(self) -> None:
        cases = (
            (
                10,
                "alpha beta abcdefghijk",
                "u\n\n│ alpha \n│ beta \n│ abcdefgh\n│ ijk",
            ),
            (
                12,
                "  indented  words  here  ",
                "u\n\n│   indented\n│   words  \n│ here  ",
            ),
            (
                10,
                "first\n\n  second\n",
                "u\n\n│ first\n│\n│   second\n│",
            ),
        )
        for width, content, expected in cases:
            with self.subTest(width=width, content=repr(content)):
                message = _chat_block_message(
                    content,
                    sender="u",
                    metadata=None,
                    role=SemanticRole.DEFAULT,
                    state=ViewState.IDLE,
                    accent=SpeakerAccent.MAGENTA,
                )
                frame = render_plain(message, width=width)
                self.assertEqual(frame.text, expected)
                self.assertEqual(frame.line_count, expected.count("\n") + 1)
                self.assertTrue(
                    all(
                        _physical_width(line) <= width
                        for line in frame.text.split("\n")
                    )
                )

    def test_chat_block_unicode_json_and_hostile_controls_are_safe(self) -> None:
        unicode_message = _chat_block_message(
            "界界界 e\u0301 🌊 x",
            sender="u",
            metadata=None,
            role=SemanticRole.DEFAULT,
            state=ViewState.IDLE,
            accent=SpeakerAccent.MAGENTA,
        )
        unicode_frame = render_plain(unicode_message, width=10)
        self.assertEqual(unicode_frame.text, "u\n\n│ 界界界 \n│ e\u0301 🌊 x")
        self.assertNotIn("\n│ \u0301", unicode_frame.text)
        self.assertTrue(
            all(
                _physical_width(line) <= 10
                for line in unicode_frame.text.split("\n")
            )
        )

        json_message = _chat_block_message(
            format_agent_reply_content('{"z":"界","a":[1,true]}'),
            sender="u",
            metadata=None,
            role=SemanticRole.DEFAULT,
            state=ViewState.IDLE,
            accent=SpeakerAccent.MAGENTA,
        )
        self.assertEqual(
            render_plain(json_message, width=20).text,
            "u\n"
            "\n"
            "│ {\n"
            '│   "z": "界",\n'
            '│   "a": [\n'
            "│     1,\n"
            "│     true\n"
            "│   ]\n"
            "│ }",
        )

        hostile = _chat_block_message(
            "safe\x07 \x1b]8;;owned\x07 \u202e",
            sender="u\x1b",
            metadata="m\u202e",
            role=SemanticRole.DEFAULT,
            state=ViewState.IDLE,
            accent=SpeakerAccent.MAGENTA,
        )
        hostile_plain = render_plain(hostile, width=80).text
        self.assertEqual(
            hostile_plain,
            "u\\x1b · m\\u202e\n"
            "\n"
            "│ safe\\x07 \\x1b]8;;owned\\x07 \\u202e",
        )
        self.assertNotIn("\x1b", hostile_plain)
        self.assertNotIn("\x07", hostile_plain)
        self.assertNotIn("\u202e", hostile_plain)

    def test_chat_block_accents_only_headers_and_profiles_share_topology(
        self,
    ) -> None:
        expected_sgr = {
            SpeakerAccent.MAGENTA: "\x1b[1;35m",
            SpeakerAccent.CYAN: "\x1b[1;36m",
            SpeakerAccent.YELLOW: "\x1b[1;33m",
            SpeakerAccent.GREEN: "\x1b[1;32m",
            SpeakerAccent.BLUE: "\x1b[1;34m",
        }
        plain_text = "you · metadata [completed]\n\n│ first\n│ second"
        for accent, sgr in expected_sgr.items():
            with self.subTest(accent=accent):
                message = _chat_block_message(
                    "first\nsecond",
                    sender="you",
                    metadata="metadata",
                    accent=accent,
                )
                ansi = render_component(
                    message,
                    _capability(CapabilityProfile.ANSI, width=80),
                )
                plain = render_component(
                    message,
                    _capability(CapabilityProfile.PLAIN, width=80),
                )
                line = render_component(
                    message,
                    _capability(CapabilityProfile.LINE, width=80),
                )
                self.assertEqual(
                    ansi.text,
                    f"{sgr}you · metadata [completed]\x1b[0m\n"
                    "\n"
                    "│ first\n"
                    "│ second",
                )
                self.assertEqual(strip_decorative_sgr(ansi.text), plain_text)
                self.assertEqual(plain.text, plain_text)
                self.assertEqual(plain, line)
                self.assertNotIn("\x1b", ansi.text.split("\n\n", 1)[1])

        wrapped = render_component(
            _chat_block_message(
                "body",
                metadata="Plot & Structure Architect",
                accent=SpeakerAccent.YELLOW,
            ),
            _capability(CapabilityProfile.ANSI, width=20),
        )
        header, body = wrapped.text.split("\n\n", 1)
        for line in header.split("\n"):
            self.assertTrue(line.startswith("\x1b[1;33m"))
            self.assertTrue(line.endswith("\x1b[0m"))
        self.assertEqual(body, "│ body")
        self.assertNotIn("\x1b", body)

    def test_list_view_renders_inline_and_chat_block_messages_in_order(
        self,
    ) -> None:
        view = ListView(
            "Messages",
            (
                Field("Before", "field"),
                Message("inline", "old", state=ViewState.IDLE),
                _chat_block_message(
                    "one two",
                    sender="you",
                    metadata=None,
                    role=SemanticRole.DEFAULT,
                    state=ViewState.IDLE,
                    accent=SpeakerAccent.MAGENTA,
                ),
                Record("after", content="record"),
            ),
            total_count=4,
            state=ViewState.COMPLETED,
        )
        frame = render_plain(view, width=40)
        self.assertEqual(
            frame.text,
            "Messages (4) [completed]\n"
            "  Before:\n"
            "   field\n"
            "  inline> old\n"
            "  you\n"
            "\n"
            "  │ one two\n"
            "  [after]\n"
            "   record",
        )
        self.assertEqual(frame.line_count, 9)
        self.assertEqual(
            tuple(type(item) for item in view.items),
            (Field, Message, Message, Record),
        )

    def test_adapter_construction_is_resource_free_and_each_call_writes_once(
        self,
    ) -> None:
        reads: list[str] = []
        records: list[str] = []

        def read_line(prompt: str) -> str:
            reads.append(prompt)
            raise AssertionError("semantic output must not read")

        terminal = InteractiveTerminal(read_line, records.append, width=80)
        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
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
            "  plot [completed]> Line one\n"
            "                    line two\n"
            "  [debate_1] Consensus [completed] [success]\n"
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
            " plot [completed]> Line one\n"
            "                   line two\n"
            " [debate_1] Consensus [completed\n"
            " ] [success]\n"
            "  Done.\n"
            "--------------------------------",
        )
        self.assertEqual(narrow.width, 32)
        self.assertEqual(narrow.line_count, 10)
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
            "\x1b[1m  plot [completed]> Line one\x1b[0m\n"
            "\x1b[1m                    line two\x1b[0m\n"
            "\x1b[32m  [debate_1] Consensus [completed] [success]\x1b[0m\n"
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
            "Search results (1/3) [completed]\n"
            "  [search c_0123456789] [completed]\n"
            "    Status: active\n"
            "    Focus: focused\n"
            "    Subject: Harbor 界 e\u0301\n"
            "    Snippet: Unsafe\\x1b]8;;owned\\x07\\u202e text",
        )
        self.assertEqual((wide.width, wide.line_count), (80, 6))

        narrow = render_plain(view, width=32)
        self.assertEqual(
            narrow.text,
            "Search results (1/3) [completed]\n"
            " [search c_0123456789] [complete\n"
            " d]\n"
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
        self.assertEqual((narrow.width, narrow.line_count), (32, 12))

        ansi = render_component(
            view,
            _capability(CapabilityProfile.ANSI, width=80),
        )
        self.assertEqual(
            ansi.text,
            "\x1b[1mSearch results (1/3) [completed]\x1b[0m\n"
            "  [search c_0123456789] [completed]\n"
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
        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
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
        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
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
                            expected_content,
                            role=SemanticRole.AGENT,
                            state=ViewState.COMPLETED,
                            presentation=MessagePresentation.CHAT_BLOCK,
                            accent=accent,
                        ),
                    )
                    self.assertEqual(
                        records[-1],
                        f"{alias} [completed]\n"
                        "\n"
                        "│ {\n"
                        '│   "answer": "界"\n'
                        "│ }\n",
                    )

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
                        total_count=sum(
                            spec.group == group for spec in COMMANDS
                        ),
                        state=ViewState.COMPLETED,
                    ),
                ),
                state=ViewState.COMPLETED,
            )
            for group in dict.fromkeys(spec.group for spec in COMMANDS)
            ),
        )
        agent_help = next(spec for spec in COMMANDS if spec.syntax == "/to NAME")
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
                Record(
                    "search c_0123456789",
                    fields=(
                        Field("Status", "archived"),
                        Field("Match", "event"),
                        Field("Subject", "Opening scene"),
                        Field(
                            "Citation",
                            "conversation c_0123456789, turn turn_2, "
                            "sequence 3, kind reply",
                        ),
                        Field("Snippet", "harbor clue"),
                    ),
                    role=SemanticRole.MUTED,
                    state=ViewState.COMPLETED,
                ),
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
                lambda: terminal.write_help("/agent"),
                Panel(
                    "Help",
                    (
                        Record(
                            "command",
                            fields=(
                                Field("Group", agent_help.group),
                                Field("Syntax", agent_help.syntax),
                                Field("Aliases", ", ".join(agent_help.aliases)),
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
        with mock.patch.object(
            _core,
            "render_component",
            wraps=render_component,
        ) as render:
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
