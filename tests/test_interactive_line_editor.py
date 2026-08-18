"""Contract tests for live, registry-backed interactive line editing."""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import pickle
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import interactive_commands as commands_module
from story_writing_agents import interactive_line_editor as editor_module
from story_writing_agents.interactive_commands import CommandSpec
from story_writing_agents.interactive_line_editor import (
    InteractiveMainLineEditor,
    SlashCommandSuggestion,
    bind_suggestion_reader,
    composition_slash_command_suggestions,
    slash_command_suggestions,
)


EXPECTED_SUGGESTIONS = (
    "/say",
    "/to",
    "/agent",
    "/agents",
    "/language",
    "/persona",
    "/reset",
    "/run",
    "/draft-file",
    "/session",
    "/conversation",
    "/search",
    "/transcript",
    "/inspect",
    "/history",
    "/status",
    "/help",
    "/?",
    "/quit",
    "/exit",
)


class ScriptedInput:
    """Expose scripted code points and observable terminal capabilities."""

    def __init__(
        self,
        *reads: object,
        is_terminal: bool = True,
        descriptor: object = 7,
    ) -> None:
        self.reads = list(reads)
        self.is_terminal = is_terminal
        self.descriptor = descriptor
        self.read_calls = 0

    def isatty(self) -> bool:
        """Return the configured terminal flag."""

        return self.is_terminal

    def fileno(self) -> object:
        """Return the configured descriptor shape."""

        return self.descriptor

    def read(self, size: int) -> object:
        """Return one scripted read value or physical EOF."""

        if size != 1:
            raise AssertionError(f"unexpected read size: {size}")
        self.read_calls += 1
        if not self.reads:
            return ""
        value = self.reads.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class RecordingOutput:
    """Record redraw writes, flushes, and terminal capability checks."""

    def __init__(
        self,
        *,
        is_terminal: bool = True,
        descriptor: object = 8,
    ) -> None:
        self.is_terminal = is_terminal
        self.descriptor = descriptor
        self.records: list[str] = []
        self.flush_calls = 0
        self.write_result: int | None = None
        self.write_failure: BaseException | None = None
        self.flush_failure: BaseException | None = None

    def isatty(self) -> bool:
        """Return the configured terminal flag."""

        return self.is_terminal

    def fileno(self) -> object:
        """Return the configured descriptor shape."""

        return self.descriptor

    def write(self, text: str) -> int | None:
        """Record one payload or raise the configured writer failure."""

        self.records.append(text)
        if self.write_failure is not None:
            raise self.write_failure
        if self.write_result is not None:
            return self.write_result
        return len(text)

    def flush(self) -> None:
        """Record one flush or raise its configured failure."""

        self.flush_calls += 1
        if self.flush_failure is not None:
            raise self.flush_failure


class FakeTermios:
    """Model the exact termios setup and restoration calls used by the editor."""

    ICANON = 0x02
    ECHO = 0x08
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self) -> None:
        self.original = [
            0,
            0,
            0,
            self.ICANON | self.ECHO | 0x40,
            0,
            0,
            [b"\x04", b"\x03", b"x", b"x", 9, 9],
        ]
        self.get_failure: BaseException | None = None
        self.activation_failure: BaseException | None = None
        self.restoration_failure: BaseException | None = None
        self.get_calls: list[int] = []
        self.set_calls: list[tuple[int, int, list[object]]] = []

    def tcgetattr(self, descriptor: int) -> list[object]:
        """Return a fresh copy of the configured original attributes."""

        self.get_calls.append(descriptor)
        if self.get_failure is not None:
            raise self.get_failure
        attributes = list(self.original)
        attributes[6] = list(self.original[6])  # type: ignore[arg-type]
        return attributes

    def tcsetattr(
        self,
        descriptor: int,
        when: int,
        attributes: list[object],
    ) -> None:
        """Record one mode change and raise the selected phase failure."""

        copied = list(attributes)
        copied[6] = list(attributes[6])  # type: ignore[arg-type]
        self.set_calls.append((descriptor, when, copied))
        if when == self.TCSANOW and self.activation_failure is not None:
            raise self.activation_failure
        if when == self.TCSADRAIN and self.restoration_failure is not None:
            raise self.restoration_failure


@contextlib.contextmanager
def capable_terminal(
    termios_double: FakeTermios,
    *,
    width: int = 80,
):
    """Install one deterministic capable POSIX terminal for a test call."""

    with (
        mock.patch.object(editor_module, "_termios", termios_double),
        mock.patch.object(editor_module.os, "name", "posix"),
        mock.patch.dict(os.environ, {"TERM": "xterm-256color"}),
        mock.patch.object(
            editor_module.os,
            "get_terminal_size",
            return_value=os.terminal_size((width, 24)),
        ),
    ):
        yield


def make_editor(
    characters: str,
    *,
    output: RecordingOutput | None = None,
) -> tuple[
    InteractiveMainLineEditor,
    ScriptedInput,
    RecordingOutput,
    mock.Mock,
]:
    """Build one editor with code-point input and an observable fallback."""

    input_stream = ScriptedInput(*characters)
    output_stream = output or RecordingOutput()
    fallback = mock.Mock(return_value="fallback")
    editor = InteractiveMainLineEditor(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
    )
    return editor, input_stream, output_stream, fallback


class SuggestionContractTests(unittest.TestCase):
    def test_public_surface_model_and_docstrings_are_exact(self) -> None:
        self.assertEqual(
            tuple(editor_module.__all__),
            (
                "SlashCommandSuggestion",
                "slash_command_suggestions",
                "InteractiveMainLineEditor",
            ),
        )
        self.assertEqual(
            tuple(inspect.signature(SlashCommandSuggestion).parameters),
            ("token", "canonical_token"),
        )
        self.assertEqual(
            tuple(inspect.signature(slash_command_suggestions).parameters),
            ("buffer",),
        )
        self.assertEqual(
            tuple(
                inspect.signature(
                    composition_slash_command_suggestions
                ).parameters
            ),
            ("buffer",),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractiveMainLineEditor).parameters),
            ("fallback_read_line", "input_stream", "output_stream"),
        )
        self.assertEqual(
            tuple(inspect.signature(InteractiveMainLineEditor.read_line).parameters),
            ("self", "prompt"),
        )
        self.assertEqual(
            tuple(inspect.signature(bind_suggestion_reader).parameters),
            ("editor", "provider"),
        )
        for internal_name in (
            "composition_slash_command_suggestions",
            "bind_suggestion_reader",
        ):
            self.assertNotIn(internal_name, editor_module.__all__)
        suggestion = SlashCommandSuggestion("/agent", "/to")
        self.assertIs(type(suggestion), SlashCommandSuggestion)
        self.assertIs(type(pickle.loads(pickle.dumps(suggestion))), type(suggestion))
        self.assertEqual(pickle.loads(pickle.dumps(suggestion)), suggestion)
        self.assertFalse(hasattr(suggestion, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            suggestion.token = "/changed"  # type: ignore[misc]
        for changed_callable in (
            SlashCommandSuggestion,
            slash_command_suggestions,
            composition_slash_command_suggestions,
            bind_suggestion_reader,
            InteractiveMainLineEditor,
            InteractiveMainLineEditor.read_line,
        ):
            with self.subTest(docstring=changed_callable.__qualname__):
                self.assertTrue(inspect.getdoc(changed_callable))
        for canonical_callable in (
            SlashCommandSuggestion,
            slash_command_suggestions,
            InteractiveMainLineEditor,
        ):
            self.assertEqual(
                canonical_callable.__module__,
                "story_writing_agents.interactive_line_editor",
            )
        self.assertNotIn("interactive_line_editor", story_writing_agents.__all__)
        for name in editor_module.__all__:
            self.assertNotIn(name, story_writing_agents.__all__)
            self.assertFalse(hasattr(story_writing_agents, name))

    def test_suggestion_model_rejects_invalid_identity_values(self) -> None:
        for arguments, error_type in (
            ((1, "/to"), TypeError),
            (("/agent", 1), TypeError),
            (("agent", "/to"), ValueError),
            (("/agent", "to"), ValueError),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(error_type):
                    SlashCommandSuggestion(*arguments)  # type: ignore[arg-type]

    def test_empty_prefix_projects_the_exact_main_registry_order(self) -> None:
        suggestions = slash_command_suggestions("/")
        self.assertEqual(
            tuple(suggestion.token for suggestion in suggestions),
            EXPECTED_SUGGESTIONS,
        )
        expected_identities = {
            "/agent": "/to",
            "/?": "/help",
            "/exit": "/quit",
        }
        for suggestion in suggestions:
            with self.subTest(token=suggestion.token):
                self.assertEqual(
                    suggestion.canonical_token,
                    expected_identities.get(suggestion.token, suggestion.token),
                )
        self.assertTrue(
            {"/compose", "/send", "/cancel"}.isdisjoint(EXPECTED_SUGGESTIONS)
        )

    def test_matching_uses_the_exact_case_sensitive_whole_buffer_prefix(self) -> None:
        cases = {
            "/a": ("/agent", "/agents"),
            "/agent": ("/agent", "/agents"),
            "/agents": ("/agents",),
            "/he": ("/help",),
            "/?": ("/?",),
            "/exit": ("/exit",),
            "": (),
            "run": (),
            " /run": (),
            "/RUN": (),
            "/run ": (),
            "/run\t": (),
            "/not-a-command": (),
        }
        for buffer, expected in cases.items():
            with self.subTest(buffer=buffer):
                self.assertEqual(
                    tuple(
                        suggestion.token
                        for suggestion in slash_command_suggestions(buffer)
                    ),
                    expected,
                )
        with self.assertRaises(TypeError):
            slash_command_suggestions(None)  # type: ignore[arg-type]

    def test_projection_reads_the_registry_and_preserves_alias_identity(self) -> None:
        registry = (
            CommandSpec("Test", "/alpha VALUE", ("/a",), False, False, "/a"),
            CommandSpec(
                "Test",
                "/compose",
                ("/composition",),
                False,
                False,
                "/compose",
            ),
            CommandSpec("Test", "/beta", (), False, False, "/beta"),
        )
        with mock.patch.object(editor_module, "COMMANDS", registry):
            self.assertEqual(
                slash_command_suggestions("/"),
                (
                    SlashCommandSuggestion("/alpha", "/alpha"),
                    SlashCommandSuggestion("/a", "/alpha"),
                    SlashCommandSuggestion("/beta", "/beta"),
                ),
            )
        self.assertIs(editor_module.COMMANDS, commands_module.COMMANDS)

    def test_composition_projection_is_exact_ordered_and_context_only(self) -> None:
        cases = (
            ("/", ("/send", "/cancel")),
            ("/s", ("/send",)),
            ("/se", ("/send",)),
            ("/send", ("/send",)),
            ("/c", ("/cancel",)),
            ("/ca", ("/cancel",)),
            ("/cancel", ("/cancel",)),
            ("", ()),
            ("plain 한글", ()),
            (" /send", ()),
            ("/send ", ()),
            ("/send\t", ()),
            ("/SEND", ()),
            ("/send-more", ()),
            ("/cancelled", ()),
            ("/say", ()),
            ("/say /send", ()),
            ("/say /cancel", ()),
        )
        for buffer, expected in cases:
            with self.subTest(buffer=buffer):
                suggestions = composition_slash_command_suggestions(buffer)
                self.assertEqual(
                    tuple(suggestion.token for suggestion in suggestions),
                    expected,
                )
                self.assertEqual(
                    tuple(
                        suggestion.canonical_token
                        for suggestion in suggestions
                    ),
                    expected,
                )
        with self.assertRaises(TypeError):
            composition_slash_command_suggestions(None)  # type: ignore[arg-type]

        self.assertEqual(
            tuple(suggestion.token for suggestion in slash_command_suggestions("/")),
            EXPECTED_SUGGESTIONS,
        )
        self.assertTrue(
            {"/send", "/cancel"}.isdisjoint(
                suggestion.token for suggestion in slash_command_suggestions("/")
            )
        )

    def test_internal_binding_is_call_local_and_public_read_stays_main_only(
        self,
    ) -> None:
        editor, input_stream, output, fallback = make_editor("")
        first_provider = mock.Mock(return_value=())
        second_provider = mock.Mock(return_value=())
        with mock.patch.object(
            InteractiveMainLineEditor,
            "_read_line",
            autospec=True,
            side_effect=lambda _self, prompt, _provider: prompt,
        ) as internal_read:
            first_reader = bind_suggestion_reader(editor, first_provider)
            second_reader = bind_suggestion_reader(editor, second_provider)
            self.assertEqual(first_reader("compose[first]> "), "compose[first]> ")
            self.assertEqual(editor.read_line("story[lobby]> "), "story[lobby]> ")
            self.assertEqual(second_reader("compose[second]> "), "compose[second]> ")

        self.assertEqual(
            internal_read.call_args_list,
            [
                mock.call(editor, "compose[first]> ", first_provider),
                mock.call(editor, "story[lobby]> ", slash_command_suggestions),
                mock.call(editor, "compose[second]> ", second_provider),
            ],
        )
        self.assertEqual(
            InteractiveMainLineEditor.__slots__,
            ("_driver",),
        )
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        fallback.assert_not_called()
        with self.assertRaises(TypeError):
            bind_suggestion_reader(object(), first_provider)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            bind_suggestion_reader(editor, None)  # type: ignore[arg-type]


class LiveEditingTests(unittest.TestCase):
    def test_insert_cursor_delete_and_unknown_sequences_return_exact_text(self) -> None:
        characters = "".join(
            (
                "abcd",
                "\x1b[A",  # ignored up
                "\x1b[D",
                "\x1b[D",
                "\x7f",  # remove b
                "\x1b[3~",  # remove c
                "\x1b[H",
                "\x1b[C",
                "X",
                "\x1b[F",
                "\x1b[D",
                "Y",
                "\x1b[999~",  # ignored unknown CSI
                "\r",
            )
        )
        editor, input_stream, _output, fallback = make_editor(characters)
        termios_double = FakeTermios()

        with capable_terminal(termios_double):
            self.assertEqual(editor.read_line("story[lobby]> "), "aXYd")

        fallback.assert_not_called()
        self.assertEqual(input_stream.read_calls, len(characters))
        self.assertEqual(
            tuple(call[1] for call in termios_double.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        editor_attributes = termios_double.set_calls[0][2]
        self.assertFalse(
            editor_attributes[3] & (FakeTermios.ICANON | FakeTermios.ECHO)
        )
        self.assertEqual(
            editor_attributes[6][FakeTermios.VMIN],  # type: ignore[index]
            1,
        )
        self.assertEqual(
            editor_attributes[6][FakeTermios.VTIME],  # type: ignore[index]
            0,
        )
        self.assertEqual(termios_double.set_calls[1][2], termios_double.original)

    def test_home_end_variants_and_both_backspace_bytes_are_supported(self) -> None:
        cases = (
            ("ab\x1bOHX\x1bOFY\n", "XabY"),
            ("ab\x1b[1~X\x1b[4~Y\n", "XabY"),
            ("ab\x1b[7~X\x1b[8~Y\n", "XabY"),
            ("ab\x08\x7f\n", ""),
        )
        for characters, expected in cases:
            with self.subTest(characters=repr(characters)):
                editor, _input, _output, fallback = make_editor(characters)
                with capable_terminal(FakeTermios()):
                    self.assertEqual(editor.read_line("p> "), expected)
                fallback.assert_not_called()

    def test_unicode_buffer_is_returned_exactly_without_normalization(self) -> None:
        value = "한글 e\u0301 🌊\u2028"
        editor, _input, output, fallback = make_editor(value + "\n")

        with capable_terminal(FakeTermios(), width=40):
            self.assertEqual(editor.read_line("이야기> "), value)

        fallback.assert_not_called()
        rendered = "".join(output.records)
        self.assertIn("한글 e\u0301 🌊", rendered)
        self.assertIn("\\u2028", rendered)
        self.assertNotIn("\u2028", rendered)

    def test_widths_wrap_at_one_reserved_column_with_fixed_redraw(self) -> None:
        prompt = "story> "
        value = "x" * 90
        for width in (20, 40, 80):
            with self.subTest(width=width):
                editor, _input, output, fallback = make_editor(value + "\n")
                with capable_terminal(FakeTermios(), width=width):
                    self.assertEqual(editor.read_line(prompt), value)
                render_width = width - 1
                text = prompt + value
                lines = [
                    text[index : index + render_width]
                    for index in range(0, len(text), render_width)
                ]
                expected = "".join(
                    (
                        "\r",
                        "\x1b[A" * (len(lines) - 1),
                        "\r\x1b[J",
                        "\n".join(lines),
                        "\n",
                    )
                )
                self.assertEqual(output.records[-1], expected)
                fallback.assert_not_called()

    def test_dynamic_text_is_sanitized_and_only_fixed_controls_are_emitted(
        self,
    ) -> None:
        value = "A\x00B\u2060C"
        editor, _input, output, _fallback = make_editor(value + "\n")

        with capable_terminal(FakeTermios(), width=40):
            self.assertEqual(editor.read_line("\x1b[31m\n> "), value)

        rendered = "".join(output.records)
        self.assertIn("\\x1b[31m\\x0a> ", rendered)
        self.assertIn("A\\x00B\\u2060C", rendered)
        self.assertNotIn("\x1b[31m", rendered)
        self.assertNotIn("\x00", rendered)
        self.assertNotIn("\u2060", rendered)
        residue = rendered
        for fixed_control in ("\x1b[J", "\x1b[A", "\x1b[C", "\r"):
            residue = residue.replace(fixed_control, "")
        self.assertNotIn("\x1b", residue)

        registry = (
            CommandSpec(
                "Test",
                "/unsafe\x1b[31m",
                (),
                False,
                False,
                "/unsafe",
            ),
        )
        editor, _input, output, _fallback = make_editor("/\n")
        with (
            mock.patch.object(editor_module, "COMMANDS", registry),
            capable_terminal(FakeTermios(), width=40),
        ):
            self.assertEqual(editor.read_line("p> "), "/")
        rendered = "".join(output.records)
        self.assertIn("/unsafe\\x1b[31m", rendered)
        self.assertNotIn("/unsafe\x1b[31m", rendered)

    def test_enter_interrupt_and_eof_events_restore_the_terminal(self) -> None:
        cases = (
            ("\n", "", None),
            ("text\x04", "text", None),
            ("\x03", None, KeyboardInterrupt),
            ("\x04", None, EOFError),
            ("", None, EOFError),
        )
        for characters, expected, error_type in cases:
            with self.subTest(characters=repr(characters)):
                editor, _input, _output, fallback = make_editor(characters)
                termios_double = FakeTermios()
                with capable_terminal(termios_double):
                    if error_type is None:
                        self.assertEqual(editor.read_line("p> "), expected)
                    else:
                        with self.assertRaises(error_type):
                            editor.read_line("p> ")
                fallback.assert_not_called()
                self.assertEqual(
                    tuple(call[1] for call in termios_double.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_contextual_panel_clears_on_whitespace_no_match_and_submit(self) -> None:
        cases = (
            ("/ \n", "/ ", ("", "/", "/ ")),
            ("/x\n", "/x", ("", "/", "/x")),
            (
                "/send\n",
                "/send",
                ("", "/", "/s", "/se", "/sen", "/send"),
            ),
        )
        for characters, expected, expected_buffers in cases:
            with self.subTest(characters=repr(characters)):
                editor, _input, output, fallback = make_editor(characters)
                provider = mock.Mock(
                    wraps=composition_slash_command_suggestions
                )
                reader = bind_suggestion_reader(editor, provider)
                with capable_terminal(FakeTermios(), width=20):
                    self.assertEqual(reader("compose[run:1]> "), expected)

                self.assertEqual(
                    tuple(call.args[0] for call in provider.call_args_list),
                    expected_buffers,
                )
                preview = next(
                    record for record in output.records if "[suggestions]" in record
                )
                self.assertIn("/send", preview)
                self.assertIn("/cancel", preview)
                self.assertNotIn("/status", preview)
                self.assertNotIn("[suggestions]", output.records[-1])
                self.assertIn("\x1b[J", output.records[-1])
                fallback.assert_not_called()

    def test_contextual_interrupt_eof_and_physical_eof_clear_and_restore(
        self,
    ) -> None:
        cases = (
            ("\x03", KeyboardInterrupt),
            ("\x04", EOFError),
            ("", EOFError),
        )
        for characters, error_type in cases:
            with self.subTest(characters=repr(characters)):
                editor, _input, output, fallback = make_editor(characters)
                provider = mock.Mock(
                    wraps=composition_slash_command_suggestions
                )
                termios_double = FakeTermios()
                with capable_terminal(termios_double):
                    with self.assertRaises(error_type):
                        bind_suggestion_reader(editor, provider)(
                            "compose[run:1]> "
                        )
                self.assertEqual(
                    output.records[-1],
                    "\r\r\x1b[J\n",
                )
                self.assertEqual(
                    tuple(call[1] for call in termios_double.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )
                provider.assert_called_once_with("")
                fallback.assert_not_called()

    def test_contextual_editing_narrow_wrapping_and_sanitization_are_exact(
        self,
    ) -> None:
        characters = "/sX\x1b[D\x7fsend\x1b[3~\n"
        editor, _input, output, fallback = make_editor(characters)
        with capable_terminal(FakeTermios(), width=20):
            self.assertEqual(
                bind_suggestion_reader(
                    editor,
                    composition_slash_command_suggestions,
                )("compose[run:1]> "),
                "/send",
            )
        rendered = "".join(output.records)
        self.assertIn("[suggestions]", rendered)
        self.assertIn("/send", rendered)
        self.assertNotIn("/status", rendered)
        self.assertIn("compose[run:1]> /se\nnd", rendered)
        fallback.assert_not_called()

        unsafe_provider = mock.Mock(
            return_value=(
                SlashCommandSuggestion("/unsafe\x1b[31m界界界", "/unsafe"),
            )
        )
        editor, _input, output, fallback = make_editor("/\n")
        with capable_terminal(FakeTermios(), width=20):
            self.assertEqual(
                bind_suggestion_reader(editor, unsafe_provider)("compose> "),
                "/",
            )
        rendered = "".join(output.records)
        self.assertIn("/unsafe\\x1b[31m", rendered)
        self.assertNotIn("/unsafe\x1b[31m", rendered)
        self.assertIn("界界\n界", rendered)
        fallback.assert_not_called()


class CapabilityAndFailureTests(unittest.TestCase):
    def test_dumb_missing_and_nonterminal_capabilities_use_plain_fallback(self) -> None:
        cases = (
            ("dumb", True, True),
            ("DUMB", True, True),
            ("", True, True),
            ("xterm", False, True),
            ("xterm", True, False),
        )
        for terminal_name, input_tty, output_tty in cases:
            with self.subTest(
                terminal_name=terminal_name,
                input_tty=input_tty,
                output_tty=output_tty,
            ):
                input_stream = ScriptedInput("u", is_terminal=input_tty)
                output_stream = RecordingOutput(is_terminal=output_tty)
                fallback = mock.Mock(return_value="plain")
                editor = InteractiveMainLineEditor(
                    fallback,
                    input_stream,  # type: ignore[arg-type]
                    output_stream,  # type: ignore[arg-type]
                )
                termios_double = FakeTermios()
                with (
                    mock.patch.object(editor_module, "_termios", termios_double),
                    mock.patch.object(editor_module.os, "name", "posix"),
                    mock.patch.dict(os.environ, {"TERM": terminal_name}),
                ):
                    self.assertEqual(editor.read_line("plain> "), "plain")
                fallback.assert_called_once_with("plain> ")
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output_stream.records, [])
                self.assertEqual(termios_double.get_calls, [])
                self.assertEqual(termios_double.set_calls, [])

    def test_capability_inspection_failures_fallback_before_input_or_mutation(
        self,
    ) -> None:
        for input_descriptor, output_descriptor in (
            (True, 8),
            (-1, 8),
            ("7", 8),
            (7, False),
            (7, -1),
        ):
            with self.subTest(
                input_descriptor=input_descriptor,
                output_descriptor=output_descriptor,
            ):
                input_stream = ScriptedInput("x", descriptor=input_descriptor)
                output_stream = RecordingOutput(descriptor=output_descriptor)
                fallback = mock.Mock(return_value="plain")
                editor = InteractiveMainLineEditor(
                    fallback,
                    input_stream,  # type: ignore[arg-type]
                    output_stream,  # type: ignore[arg-type]
                )
                with capable_terminal(FakeTermios()):
                    self.assertEqual(editor.read_line("p> "), "plain")
                fallback.assert_called_once_with("p> ")
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output_stream.records, [])

        editor, input_stream, output, fallback = make_editor("x")
        termios_double = FakeTermios()
        termios_double.get_failure = OSError("attributes unavailable")
        with capable_terminal(termios_double):
            self.assertEqual(editor.read_line("p> "), "fallback")
        fallback.assert_called_once_with("p> ")
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(termios_double.set_calls, [])

    def test_unsupported_platform_and_size_failures_fallback_without_ansi(self) -> None:
        unsupported_platforms = ((None, "posix"), (FakeTermios(), "nt"))
        for unsupported_termios, platform_name in unsupported_platforms:
            with self.subTest(
                unsupported_termios=unsupported_termios is None,
                platform_name=platform_name,
            ):
                editor, input_stream, output, fallback = make_editor("unused")
                with (
                    mock.patch.object(editor_module, "_termios", unsupported_termios),
                    mock.patch.object(editor_module.os, "name", platform_name),
                    mock.patch.dict(os.environ, {"TERM": "xterm"}),
                ):
                    self.assertEqual(editor.read_line("p> "), "fallback")
                fallback.assert_called_once_with("p> ")
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output.records, [])

        editor, input_stream, output, fallback = make_editor("unused")
        with (
            mock.patch.object(editor_module, "_termios", FakeTermios()),
            mock.patch.object(editor_module.os, "name", "posix"),
            mock.patch.dict(os.environ, {"TERM": "xterm"}),
            mock.patch.object(
                editor_module.os,
                "get_terminal_size",
                side_effect=OSError("size unavailable"),
            ),
        ):
            self.assertEqual(editor.read_line("p> "), "fallback")
        fallback.assert_called_once_with("p> ")
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])

    def test_recoverable_activation_failure_restores_then_uses_fallback(self) -> None:
        editor, input_stream, output, fallback = make_editor("unused")
        termios_double = FakeTermios()
        termios_double.activation_failure = OSError("activation failed")

        with capable_terminal(termios_double):
            self.assertEqual(editor.read_line("p> "), "fallback")

        fallback.assert_called_once_with("p> ")
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(
            tuple(call[1] for call in termios_double.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_active_input_and_output_failures_propagate_after_restoration(self) -> None:
        cases = (
            (
                ScriptedInput(RuntimeError("read failed")),
                RecordingOutput(),
                RuntimeError,
            ),
            (ScriptedInput("x"), RecordingOutput(), OSError),
        )
        cases[1][1].write_result = 0
        for input_stream, output_stream, error_type in cases:
            with self.subTest(error_type=error_type.__name__):
                fallback = mock.Mock(return_value="unused")
                editor = InteractiveMainLineEditor(
                    fallback,
                    input_stream,  # type: ignore[arg-type]
                    output_stream,  # type: ignore[arg-type]
                )
                termios_double = FakeTermios()
                with capable_terminal(termios_double):
                    with self.assertRaises(error_type):
                        editor.read_line("p> ")
                fallback.assert_not_called()
                self.assertEqual(
                    tuple(call[1] for call in termios_double.set_calls),
                    (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
                )

    def test_restoration_failure_overrides_normal_and_active_results(self) -> None:
        for characters, active_failure in (("ok\n", False), ("\x03", True)):
            with self.subTest(active_failure=active_failure):
                editor, _input, _output, fallback = make_editor(characters)
                termios_double = FakeTermios()
                restoration_failure = OSError("secret restoration detail")
                termios_double.restoration_failure = restoration_failure
                with capable_terminal(termios_double):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "^interactive terminal restoration failed$",
                    ) as caught:
                        editor.read_line("p> ")
                self.assertIs(caught.exception.__cause__, restoration_failure)
                if active_failure:
                    self.assertEqual(len(caught.exception.__notes__), 1)
                else:
                    self.assertFalse(hasattr(caught.exception, "__notes__"))
                fallback.assert_not_called()

        editor, _input, _output, fallback = make_editor("unused")
        termios_double = FakeTermios()
        termios_double.activation_failure = OSError("activation detail")
        restoration_failure = OSError("restoration detail")
        termios_double.restoration_failure = restoration_failure
        with capable_terminal(termios_double):
            with self.assertRaisesRegex(
                RuntimeError,
                "^interactive terminal restoration failed$",
            ) as caught:
                editor.read_line("p> ")
        self.assertIs(caught.exception.__cause__, restoration_failure)
        self.assertEqual(len(caught.exception.__notes__), 1)
        fallback.assert_not_called()

    def test_non_exception_activation_failure_restores_and_propagates(self) -> None:
        editor, input_stream, output, fallback = make_editor("unused")
        termios_double = FakeTermios()
        termios_double.activation_failure = KeyboardInterrupt()

        with capable_terminal(termios_double):
            with self.assertRaises(KeyboardInterrupt):
                editor.read_line("p> ")

        fallback.assert_not_called()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])
        self.assertEqual(
            tuple(call[1] for call in termios_double.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )

    def test_constructor_and_reader_validate_without_acquiring_resources(self) -> None:
        input_stream = ScriptedInput("x")
        output_stream = RecordingOutput()
        with self.assertRaises(TypeError):
            InteractiveMainLineEditor(  # type: ignore[arg-type]
                None,
                input_stream,
                output_stream,
            )
        editor = InteractiveMainLineEditor(
            lambda prompt: "unused",
            input_stream,  # type: ignore[arg-type]
            output_stream,  # type: ignore[arg-type]
        )
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output_stream.records, [])
        with self.assertRaises(TypeError):
            editor.read_line(None)  # type: ignore[arg-type]
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output_stream.records, [])

    def test_contextual_fallback_and_failure_paths_keep_one_resource_owner(
        self,
    ) -> None:
        fallback_cases = (
            ("dumb", True, True, "posix"),
            ("xterm", False, True, "posix"),
            ("xterm", True, False, "posix"),
            ("xterm", True, True, "nt"),
        )
        for terminal_name, input_tty, output_tty, platform_name in fallback_cases:
            with self.subTest(
                terminal_name=terminal_name,
                input_tty=input_tty,
                output_tty=output_tty,
                platform_name=platform_name,
            ):
                input_stream = ScriptedInput("unused", is_terminal=input_tty)
                output = RecordingOutput(is_terminal=output_tty)
                fallback = mock.Mock(return_value="plain")
                editor = InteractiveMainLineEditor(
                    fallback,
                    input_stream,  # type: ignore[arg-type]
                    output,  # type: ignore[arg-type]
                )
                provider = mock.Mock(
                    wraps=composition_slash_command_suggestions
                )
                with (
                    mock.patch.object(editor_module, "_termios", FakeTermios()),
                    mock.patch.object(editor_module.os, "name", platform_name),
                    mock.patch.dict(os.environ, {"TERM": terminal_name}),
                ):
                    self.assertEqual(
                        bind_suggestion_reader(editor, provider)("compose> "),
                        "plain",
                    )
                fallback.assert_called_once_with("compose> ")
                provider.assert_not_called()
                self.assertEqual(input_stream.read_calls, 0)
                self.assertEqual(output.records, [])

        output = RecordingOutput()
        editor, _input, _unused_output, fallback = make_editor("/x", output=output)
        termios_double = FakeTermios()

        def fail_after_panel(text: str) -> int:
            output.records.append(text)
            if len(output.records) == 3:
                raise OSError("redraw failed")
            return len(text)

        output.write = fail_after_panel  # type: ignore[method-assign]
        with capable_terminal(termios_double):
            with self.assertRaises(OSError):
                bind_suggestion_reader(
                    editor,
                    composition_slash_command_suggestions,
                )("compose> ")
        self.assertEqual(
            tuple(call[1] for call in termios_double.set_calls),
            (FakeTermios.TCSANOW, FakeTermios.TCSADRAIN),
        )
        self.assertIn("[suggestions]", output.records[1])
        self.assertEqual(output.records[-1], "\r\r\x1b[J\n")
        fallback.assert_not_called()

        editor, input_stream, output, fallback = make_editor("unused")
        termios_double = FakeTermios()
        termios_double.activation_failure = OSError("activation failed")
        provider = mock.Mock(wraps=composition_slash_command_suggestions)
        with capable_terminal(termios_double):
            self.assertEqual(
                bind_suggestion_reader(editor, provider)("compose> "),
                "fallback",
            )
        provider.assert_not_called()
        self.assertEqual(input_stream.read_calls, 0)
        self.assertEqual(output.records, [])

        editor, _input, _output, fallback = make_editor("/send\n")
        termios_double = FakeTermios()
        termios_double.restoration_failure = OSError("restore failed")
        with capable_terminal(termios_double):
            with self.assertRaisesRegex(
                RuntimeError,
                "^interactive terminal restoration failed$",
            ):
                bind_suggestion_reader(
                    editor,
                    composition_slash_command_suggestions,
                )("compose> ")
        self.assertIn("\x1b[J", _output.records[-1])
        self.assertNotIn("[suggestions]", _output.records[-1])
        fallback.assert_not_called()


class EditorBoundaryTests(unittest.TestCase):
    def test_dependency_boundary_clean_import_and_non_facade_surface(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        source_path = (
            repository_root
            / "src"
            / "story_writing_agents"
            / "interactive_line_editor.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        dependencies = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        }
        self.assertEqual(
            dependencies,
            {"interactive_commands", "interactive_terminal"},
        )

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(repository_root / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        script = (
            "import story_writing_agents as facade; "
            "from story_writing_agents import interactive_line_editor as module; "
            "expected = ('SlashCommandSuggestion', "
            "'slash_command_suggestions', 'InteractiveMainLineEditor'); "
            "assert tuple(module.__all__) == expected; "
            "assert all(name not in facade.__all__ for name in expected); "
            "assert all(not hasattr(facade, name) for name in expected); "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_editor_does_not_parse_call_provider_or_touch_artifacts(self) -> None:
        cwd_before = Path.cwd()
        environment_before = os.environ.copy()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            editor, _input, _output, fallback = make_editor("/status\n")
            contextual_editor, _context_input, _context_output, contextual_fallback = (
                make_editor("/\x7fplain\n")
            )
            with (
                mock.patch.object(
                    commands_module,
                    "parse_interactive_line",
                ) as parse_line,
                mock.patch.object(subprocess, "Popen") as popen,
                mock.patch.object(subprocess, "run") as run,
                mock.patch.object(socket, "create_connection") as connect,
                mock.patch.object(Path, "open") as path_open,
                mock.patch.object(Path, "write_text") as write_text,
                mock.patch.object(Path, "write_bytes") as write_bytes,
                mock.patch.object(Path, "mkdir") as mkdir,
                capable_terminal(FakeTermios()),
            ):
                self.assertEqual(editor.read_line("p> "), "/status")
                self.assertEqual(
                    bind_suggestion_reader(
                        contextual_editor,
                        composition_slash_command_suggestions,
                    )("compose[run:1]> "),
                    "plain",
                )
                self.assertEqual(
                    tuple(
                        suggestion.token
                        for suggestion in slash_command_suggestions("/st")
                    ),
                    ("/status",),
                )
                parse_line.assert_not_called()
                popen.assert_not_called()
                run.assert_not_called()
                connect.assert_not_called()
                path_open.assert_not_called()
                write_text.assert_not_called()
                write_bytes.assert_not_called()
                mkdir.assert_not_called()
            self.assertEqual(tuple(root.iterdir()), ())
        fallback.assert_not_called()
        contextual_fallback.assert_not_called()
        self.assertEqual(Path.cwd(), cwd_before)
        self.assertEqual(os.environ, environment_before)


if __name__ == "__main__":
    unittest.main()
