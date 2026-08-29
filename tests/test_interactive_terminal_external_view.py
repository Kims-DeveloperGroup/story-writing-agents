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
        cases = {
            "q": "close",
            " quit ": "close",
            "j": "down",
            "down": "down",
            "k": "up",
            "up": "up",
            "pgdn": "page_down",
            "pgup": "page_up",
            "n": "next_match",
            "N": "previous_match",
            "m": "toggle_mode",
            "h": "help",
            "?": "help",
            "esc": "cancel",
            "c": "clear_search",
            "clear": "clear_search",
            "g 17": "goto\t17",
            "/ provider CODEX": "search\tprovider\tCODEX",
            "": "unknown",
            "g": "unknown",
            "/ provider": "unknown",
            "g 1\t2": "unknown",
            "/ provider x\tbad": "unknown",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(external_view_module._line_view_event(value), expected)
        self.assertEqual(
            external_view_module._line_view_event("x" * 8_193),
            "unknown",
        )
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
        reads = iter(("/ provider codex", "q", "q"))
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
            host.close_view()
            self.assertIsNone(host.read_event())

        viewer.open(operation)
        self.assertEqual(
            observed,
            [(72, 24), False, "search\tprovider\tcodex", "close"],
        )
        self.assertEqual(writes, ["plain frame\n"])
        self.assertEqual(prompts, ["viewer> ", "viewer> "])

        viewer.open(lambda host: observed.append(host.read_event()))
        self.assertEqual(observed[-1], "close")

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
        driver, _input, output, fallback, termios = _raw_driver(
            "\x1b[A\x1b[B\x1b[5~\x1b[6~jkb nNmh?cq",
            width=91,
            height=27,
        )
        events: list[object] = []

        def operation(host: ExternalViewerHost) -> None:
            events.extend((host.terminal_size(), host.color_enabled()))
            host.present("\x1b[36mframe\x1b[0m")
            for _index in range(15):
                events.append(host.read_event())
            host.close_view()

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=True,
        ):
            driver.open_external_view(operation)
        self.assertEqual(events[:2], [(91, 27), True])
        self.assertEqual(
            events[2:],
            [
                "up",
                "down",
                "page_up",
                "page_down",
                "down",
                "up",
                "page_up",
                "page_down",
                "next_match",
                "previous_match",
                "toggle_mode",
                "help",
                "help",
                "clear_search",
                "close",
            ],
        )
        fallback.assert_not_called()
        self.assertEqual(termios.events.count("activate"), 1)
        self.assertEqual(termios.events.count("restore"), 1)
        rendered = "".join(output.records)
        self.assertEqual(rendered.count(driver_module._VIEWER_SCREEN_ENTER), 1)
        self.assertEqual(rendered.count(driver_module._VIEWER_SCREEN_EXIT), 1)
        self.assertIn("\x1b[36mframe\x1b[0m", rendered)

    def test_goto_search_prompts_restore_then_reactivate_and_bound_events(self) -> None:
        driver, _input, _output, fallback, termios = _raw_driver(
            "g/q",
            answers=("19", "provider", "Codex response"),
        )
        events: list[str | None] = []

        def operation(host: ExternalViewerHost) -> None:
            events.append(host.read_event())
            events.append(host.read_event())
            events.append(host.read_event())

        driver.open_external_view(operation)
        self.assertEqual(
            events,
            ["goto\t19", "search\tprovider\tCodex response", "close"],
        )
        self.assertEqual(
            fallback.call_args_list,
            [
                mock.call("Go to source line: "),
                mock.call("Search field: "),
                mock.call("Search query: "),
            ],
        )
        self.assertEqual(termios.events.count("activate"), 4)
        self.assertEqual(termios.events.count("restore"), 4)

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
        self.assertEqual(observed, [(80, 24), False, "cancel", None])
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
            self.assertEqual(host.read_event(), "close")

        driver.open_external_view(operation)
        self.assertIsInstance(reentry[0], RuntimeError)
        self.assertIn("already active", str(reentry[0]))
        driver.open_external_view(
            lambda host: self.assertEqual(host.read_event(), "close")
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
