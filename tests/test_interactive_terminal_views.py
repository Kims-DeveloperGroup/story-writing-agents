"""Cohesive contracts for package-internal semantic terminal output views."""

from __future__ import annotations

import inspect
import unittest
from unittest import mock

import story_writing_agents
from story_writing_agents import LEAD_ORCHESTRATOR, PLOT_ARCHITECT
from story_writing_agents import interactive_terminal as terminal_facade
from story_writing_agents.interactive_terminal import (
    AttachmentKind,
    Field,
    InteractiveSemanticOutput,
    InteractiveTerminal,
    Message,
    Notice,
    Panel,
    Record,
    SemanticRole,
    ViewState,
)
from story_writing_agents.interactive_terminal import _core, _model
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    TerminalCapabilities,
)
from story_writing_agents.interactive_terminal._render import (
    render_component,
    render_plain,
    strip_decorative_sgr,
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


class InteractiveSemanticOutputTests(unittest.TestCase):
    """Lock the internal adapter boundary and deterministic view fixtures."""

    def test_internal_facade_objects_are_unlisted_canonical_identities(self) -> None:
        expected = {
            "InteractiveSemanticOutput": _core.InteractiveSemanticOutput,
            "Field": _model.Field,
            "Message": _model.Message,
            "Notice": _model.Notice,
            "Panel": _model.Panel,
            "Record": _model.Record,
            "Section": _model.Section,
            "SemanticRole": _model.SemanticRole,
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
        self.assertEqual(InteractiveSemanticOutput.__slots__, ("_write",))

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
            "Status\n"
            "  Workspace: /stories/界\n"
            "  [warning] Unsafe\\x1b[31m text\n"
            "  plot [completed]> Line one\n"
            "                    line two\n"
            "  [debate_1] Consensus [completed] [success]\n"
            "    Done.",
        )
        narrow = render_plain(_literal_view(), width=32)
        self.assertEqual(
            narrow.text,
            "Status\n"
            " Workspace:\n"
            "  /stories/界\n"
            " [warning] Unsafe\\x1b[31m text\n"
            " plot [completed]> Line one\n"
            "                   line two\n"
            " [debate_1] Consensus [completed\n"
            " ] [success]\n"
            "  Done.",
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
            "\x1b[1mStatus\x1b[0m\n"
            "  Workspace: /stories/界\n"
            "\x1b[33m  [warning] Unsafe\\x1b[31m text\x1b[0m\n"
            "\x1b[34m  plot [completed]> Line one\x1b[0m\n"
            "\x1b[34m                    line two\x1b[0m\n"
            "\x1b[32m  [debate_1] Consensus [completed] [success]\x1b[0m\n"
            "\x1b[32m    Done.\x1b[0m",
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
                            "/session create NAME, /session open [NAME], or "
                            "/session list.",
                            role=SemanticRole.WARNING,
                        ),
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


if __name__ == "__main__":
    unittest.main()
