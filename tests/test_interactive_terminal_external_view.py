"""Terminal ownership contracts for hosted external read-only viewers."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from story_writing_agents.interactive_terminal import (
    InteractiveExternalViewer,
    InteractiveTerminal,
)
from story_writing_agents.interactive_terminal import _driver as driver_module
from story_writing_agents.interactive_terminal import (
    _external_view as external_view_module,
)
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history,
    transition_line_input,
    transition_main_command_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver
from story_writing_agents.interactive_terminal._external_view import (
    ExternalViewerHost,
)


class _Input:
    def __init__(self, characters: str, *, tty: bool = True) -> None:
        self.characters = list(characters.encode("utf-8"))
        self.tty = tty
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return 7

    def read(self, size: int) -> str:
        raise AssertionError(f"text read must not run: {size}")

    def read1(self, size: int) -> bytes:
        if size != 1:
            raise AssertionError(size)
        return bytes((self.characters.pop(0),)) if self.characters else b""


class _Output:
    def __init__(self, *, tty: bool = True) -> None:
        self.tty = tty
        self.records: list[str] = []
        self.failure: BaseException | None = None

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return 8

    def write(self, text: str) -> int:
        if self.failure is not None:
            raise self.failure
        self.records.append(text)
        return len(text)

    def flush(self) -> None:
        return None


class _Termios:
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
            self.ICANON | self.ECHO,
            0,
            0,
            [b"\x04", b"\x03", 0, 0, 9, 9],
        ]
        self.events: list[str] = []
        self.restoration_failure: BaseException | None = None

    def tcgetattr(self, _descriptor: int) -> list[object]:
        self.events.append("get")
        value = list(self.original)
        value[6] = list(self.original[6])  # type: ignore[arg-type]
        return value

    def tcsetattr(
        self,
        _descriptor: int,
        when: int,
        _attributes: list[object],
    ) -> None:
        event = "activate" if when == self.TCSANOW else "restore"
        self.events.append(event)
        if event == "restore" and self.restoration_failure is not None:
            raise self.restoration_failure


def _raw_driver(
    characters: str,
    *,
    tty: bool = True,
    answers: tuple[object, ...] = ("q",),
    width: int = 91,
    height: int = 27,
) -> tuple[RawTerminalDriver, _Input, _Output, mock.Mock, _Termios]:
    input_stream = _Input(characters, tty=tty)
    output_stream = _Output(tty=tty)
    fallback = mock.Mock(side_effect=answers)
    termios = _Termios()
    os_double = SimpleNamespace(
        name="posix",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=lambda _descriptor: os.terminal_size((width, height)),
        fpathconf=lambda _descriptor, _name: 0,
    )
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
        termios_provider=lambda: termios,
        os_provider=lambda: os_double,
    )
    return driver, input_stream, output_stream, fallback, termios


class _Host:
    def __init__(self, *, close_failure: BaseException | None = None) -> None:
        self.closed = 0
        self.close_failure = close_failure

    def terminal_size(self) -> tuple[int, int]:
        return 80, 24

    def color_enabled(self) -> bool:
        return False

    def present(self, _frame: str) -> None:
        return None

    def read_event(self) -> str | None:
        return "close"

    def close_view(self) -> None:
        self.closed += 1
        if self.close_failure is not None:
            raise self.close_failure


class ExternalViewContractTests(unittest.TestCase):
    def test_protocol_surface_and_plain_line_event_vocabulary_are_exact(self) -> None:
        self.assertEqual(
            external_view_module.__all__,
            ["ExternalViewOperation", "ExternalViewerHost"],
        )
        values = ("q", " quit ", "j", "down", "k", "up", "pgdn", "pgup", "n", "N", "m", "h", "?", "esc", "c", "clear", "g 17", "/ provider CODEX", "", "g", "/ provider", "g 1\t2", "/ provider x\tbad", "개요", "x" * 8192)
        for value in values:
            with self.subTest(value=value[:40]):
                self.assertEqual(external_view_module._line_view_event(value), "line\t" + value)
        self.assertEqual(external_view_module._line_view_event("x" * 8193), "key\tunknown")
        with self.assertRaises(ValueError):
            external_view_module._line_view_event("q\n")

    def test_frame_validation_allows_only_printable_newline_and_authorized_sgr(
        self,
    ) -> None:
        plain = "header\nbody 한🙂"
        colored = "\x1b[31mheader\x1b[0m"
        self.assertEqual(
            external_view_module._require_external_view_frame(
                plain,
                color_enabled=False,
            ),
            plain,
        )
        self.assertEqual(
            external_view_module._require_external_view_frame(
                colored,
                color_enabled=True,
            ),
            colored,
        )
        for unsafe, color in (
            (colored, False),
            ("\x1b[2J", True),
            ("carriage\rreturn", False),
            ("nul\x00", False),
            ("bidi\u202e", False),
            ("separator\u2028", False),
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                external_view_module._require_external_view_frame(
                    unsafe,
                    color_enabled=color,
                )

    def test_plain_terminal_borrows_callbacks_is_non_tty_and_discards_state(
        self,
    ) -> None:
        reads = iter(("/ provider codex", "esc", "n", "q", "q"))
        prompts: list[str] = []
        writes: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            return next(reads)

        terminal = InteractiveTerminal(read_line, writes.append, width=72)
        viewer = InteractiveExternalViewer(terminal)
        observed: list[object] = []

        def operation(host: ExternalViewerHost) -> None:
            observed.extend((host.terminal_size(), host.color_enabled()))
            host.present("plain frame")
            observed.append(host.read_event())
            observed.append(host.read_event())
            observed.append(host.read_event())
            observed.append(host.read_event())
            host.close_view()
            self.assertIsNone(host.read_event())

        viewer.open(operation)
        self.assertEqual(
            observed,
            [
                (72, 24),
                False,
                "line\t/ provider codex",
                "line\tesc",
                "line\tn",
                "line\tq",
            ],
        )
        self.assertEqual(writes, ["plain frame\n"])
        self.assertEqual(prompts, [""] * 4)

        viewer.open(lambda host: observed.append(host.read_event()))
        self.assertEqual(observed[-1], "line\tq")

    def test_operation_and_cleanup_failure_precedence_preserves_identity(self) -> None:
        driver, *_rest = _raw_driver("q")
        active = RuntimeError("active")
        cleanup = OSError("cleanup")
        host = _Host(close_failure=cleanup)

        def fail(_host: ExternalViewerHost) -> None:
            raise active

        with self.assertRaises(RuntimeError) as raised:
            driver._run_external_view_operation(fail, host)
        self.assertIs(raised.exception, active)
        self.assertEqual(host.closed, 1)
        cleanup_host = _Host(close_failure=cleanup)
        with self.assertRaises(OSError) as cleanup_raised:
            driver._run_external_view_operation(lambda _host: None, cleanup_host)
        self.assertIs(cleanup_raised.exception, cleanup)


class RawExternalViewLifecycleTests(unittest.TestCase):
    def test_live_host_owns_geometry_color_events_screen_and_restoration(self) -> None:
        driver, _input, output, fallback, termios = _raw_driver("\x1b[A\x1b[B\x1b[5~\x1b[6~jkb nNmh?cq", width=91, height=27)
        events = []
        def operation(host):
            events.extend((host.terminal_size(), host.color_enabled()))
            host.present("\x1b[36mframe\x1b[0m")
            events.extend(host.read_event() for _ in range(15))
            host.close_view()
        with mock.patch.object(driver_module._select, "select", return_value=((7,), (), ())):
            driver.open_external_view(operation)
        self.assertEqual(events[:2], [(91, 27), True])
        self.assertEqual(events[2:], ["key\tup", "key\tdown", "key\tpage_up", "key\tpage_down"] + ["text\t" + c for c in "jkb nNmh?cq"])
        fallback.assert_not_called()
        self.assertEqual(termios.events.count("activate"), 1)
        self.assertEqual(termios.events.count("restore"), 1)
        rendered = "".join(output.records)
        self.assertEqual(rendered.count(driver_module._VIEWER_SCREEN_ENTER), 1)
        self.assertEqual(rendered.count(driver_module._VIEWER_SCREEN_EXIT), 1)
        self.assertIn("\x1b[36mframe\x1b[0m", rendered)

    def test_physical_input_has_no_viewer_bindings_prompts_or_history(self) -> None:
        text = "/gqjkb nNmh?c개요😀e\u0301"
        controls = "\r\n\x08\x7f\x01\x05\x15\x0b\x17\x03\x04\t"
        names = ("enter", "enter", "backspace", "backspace", "ctrl_a", "ctrl_e", "ctrl_u", "ctrl_k", "ctrl_w", "interrupt", "eof", "unknown")
        driver, _input, output, fallback, termios = _raw_driver(text + controls)
        events = []
        driver.open_external_view(lambda host: events.extend(host.read_event() for _ in range(len(text + controls))))
        self.assertEqual(events, ["text\t" + c for c in text] + ["key\t" + n for n in names])
        self.assertEqual(output.records, [driver_module._VIEWER_SCREEN_ENTER, driver_module._VIEWER_SCREEN_REDRAW, driver_module._VIEWER_SCREEN_EXIT])
        self.assertEqual(termios.events.count("activate"), 1)
        self.assertEqual(termios.events.count("restore"), 1)
        fallback.assert_not_called()
        self.assertEqual(driver._history_state, driver_module.MainInputHistoryState())

    def test_escape_sequences_are_physical_bounded_and_drained(self) -> None:
        for suffix, expected in (("[C", "right"), ("OD", "left"), ("[H", "home"), ("OF", "end"), ("[3~", "delete"), ("[Z", "unknown_escape"), ("[" + "0" * 40 + "Z", "unknown_escape")):
            with self.subTest(suffix=suffix):
                driver, _input, output, fallback, _termios = _raw_driver("\x1b" + suffix + "q")
                events = []
                with mock.patch.object(driver_module._select, "select", return_value=((7,), (), ())):
                    driver.open_external_view(lambda host: events.extend((host.read_event(), host.read_event())))
                self.assertEqual(events, ["key\t" + expected, "text\tq"])
                self.assertNotIn("Search", "".join(output.records))
                fallback.assert_not_called()
        driver, _input, _output, _fallback, _termios = _raw_driver("\x1bq")
        events = []
        with mock.patch.object(driver_module._select, "select", return_value=((), (), ())):
            driver.open_external_view(lambda host: events.extend((host.read_event(), host.read_event())))
        self.assertEqual(events, ["key\tescape", "text\tq"])

    def test_escape_partial_utf8_deadline_stops_byte_reads_and_restores_terminal(self) -> None:
        driver, input_stream, output, fallback, termios = _raw_driver("")
        input_stream.characters = [0x1B, 0xE2, 0x82, 0xAC]
        original_read = input_stream.read1
        with (
            mock.patch.object(input_stream, "read1", wraps=original_read) as reads,
            mock.patch.object(driver_module.time, "monotonic", return_value=100.0),
            mock.patch.object(driver_module._select, "select", side_effect=[((7,), (), ()), ((7,), (), ()), ((), (), ())]),
            self.assertRaisesRegex(TimeoutError, "^external terminal character deadline expired$") as raised,
        ):
            driver.open_external_view(lambda host: host.read_event())
        self.assertEqual(reads.call_args_list, [mock.call(1), mock.call(1)])
        self.assertEqual(input_stream.characters, [0x82, 0xAC])
        reader = driver._input_latch.live_reader
        reader.reset_incomplete()
        with self.assertRaises(TimeoutError) as again:
            reader.read_character()
        self.assertIs(again.exception, raised.exception)
        self.assertEqual(termios.events.count("activate"), 1)
        self.assertEqual(termios.events.count("restore"), 1)
        self.assertEqual("".join(output.records).count(driver_module._VIEWER_SCREEN_EXIT), 1)
        fallback.assert_not_called()

    def test_escape_drain_bound_fails_closed_and_restores_terminal(self) -> None:
        driver, input_stream, output, fallback, termios = _raw_driver("\x1b[" + "0" * 4096 + "q")
        with (
            mock.patch.object(driver_module.time, "monotonic", return_value=100.0),
            mock.patch.object(driver_module._select, "select", return_value=((7,), (), ())),
            self.assertRaisesRegex(ValueError, "drain bound"),
        ):
            driver.open_external_view(lambda host: host.read_event())
        self.assertEqual(input_stream.characters, [ord("q")])
        self.assertEqual(termios.events.count("restore"), 1)
        self.assertEqual("".join(output.records).count(driver_module._VIEWER_SCREEN_EXIT), 1)
        fallback.assert_not_called()

    def test_real_library_raw_and_line_fallback_have_equal_search_behavior(self) -> None:
        import io
        import re
        from jsonl_viewer import ViewerSpec, view_jsonl
        from jsonl_viewer._standalone import _TerminalHost

        source = ('{"content":"개요 one 개요 one"}\n{"content":"other"}\n').encode()
        spec = ViewerSpec("s", "c", "a", input_protocol="keys")
        command = "/ 개요 one\nn\nesc\nq\n"
        standalone_output = io.StringIO()
        with _TerminalHost(io.StringIO(command), standalone_output, no_color=True) as host:
            view_jsonl(source, spec, host)
        lines = iter(command.splitlines())
        story_output = []
        viewer = InteractiveExternalViewer(InteractiveTerminal(lambda prompt: next(lines), story_output.append, width=80))
        viewer.open(lambda host: view_jsonl(source, spec, host))
        self.assertIn("2/2 occurrences • Search all text='개요 one'", "".join(story_output))
        self.assertIn("2/2 occurrences • Search all text='개요 one'", standalone_output.getvalue())
        driver, _input, output, fallback, termios = _raw_driver("/wrong\x15개요 one\nnq")
        driver.open_external_view(lambda host: view_jsonl(source, spec, host))
        self.assertIn("2/2 occurrences • Search all text='개요 one'", "".join(output.records))
        self.assertIn("개요 one ⟦개요 one⟧", re.sub(r"\x1b\[[0-9;]*m", "", "".join(output.records)))
        self.assertIn("개요 one ⟦개요 one⟧", "".join(story_output))
        self.assertNotIn("U0015", "".join(output.records))
        self.assertEqual(termios.events.count("activate"), 1)
        self.assertEqual(termios.events.count("restore"), 1)
        fallback.assert_not_called()
        self.assertEqual(source, ('{"content":"개요 one 개요 one"}\n{"content":"other"}\n').encode())

    def test_fallback_never_acquires_raw_or_ansi_and_maps_interrupt_eof(self) -> None:
        driver, _input, output, fallback, termios = _raw_driver(
            "",
            tty=False,
            answers=(KeyboardInterrupt(), EOFError()),
            width=43,
        )
        observed: list[object] = []

        def operation(host: ExternalViewerHost) -> None:
            observed.extend((host.terminal_size(), host.color_enabled()))
            host.present("fallback frame")
            observed.append(host.read_event())
            observed.append(host.read_event())

        driver.open_external_view(operation)
        self.assertEqual(observed, [(80, 24), False, "key\tinterrupt", None])
        self.assertEqual("".join(output.records), "fallback frame\n")
        self.assertEqual(termios.events, [])
        self.assertEqual(fallback.call_count, 2)

    def test_reentry_and_progress_overlap_are_rejected_then_latch_releases(
        self,
    ) -> None:
        driver, _input, _output, _fallback, _termios = _raw_driver("qq")
        reentry: list[BaseException] = []

        def operation(host: ExternalViewerHost) -> None:
            try:
                driver.open_external_view(lambda _nested: None)
            except BaseException as failure:
                reentry.append(failure)
            self.assertEqual(host.read_event(), "text\tq")

        driver.open_external_view(operation)
        self.assertIsInstance(reentry[0], RuntimeError)
        self.assertIn("already active", str(reentry[0]))
        driver.open_external_view(
            lambda host: self.assertEqual(host.read_event(), "text\tq")
        )

        driver._progress_active = True
        called = False

        def prohibited(_host: ExternalViewerHost) -> None:
            nonlocal called
            called = True

        with self.assertRaisesRegex(RuntimeError, "overlap terminal progress"):
            driver.open_external_view(prohibited)
        self.assertFalse(called)
        driver._progress_active = False

    def test_active_failure_survives_screen_and_restoration_cleanup(self) -> None:
        driver, _input, output, _fallback, termios = _raw_driver("q")
        active = RuntimeError("PRIVATE active viewer failure")
        cleanup = OSError("PRIVATE restoration failure")
        termios.restoration_failure = cleanup

        def operation(_host: ExternalViewerHost) -> None:
            output.failure = OSError("screen cleanup")
            raise active

        with self.assertRaises(RuntimeError) as raised:
            driver.open_external_view(operation)
        self.assertEqual(
            str(raised.exception), "interactive terminal restoration failed"
        )
        self.assertIs(raised.exception.__cause__, cleanup)
        self.assertTrue(
            any(
                "another interactive input failure" in note
                for note in raised.exception.__notes__
            )
        )


if __name__ == "__main__":
    unittest.main()
