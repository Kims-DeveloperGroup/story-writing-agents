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
                "search\tprovider\tcodex",
                "cancel",
                "next_match",
                "close",
            ],
        )
        self.assertEqual(writes, ["plain frame\n"])
        self.assertEqual(prompts, ["viewer> "] * 4)

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

    def test_goto_stays_canonical_while_search_commits_through_raw_prompts(
        self,
    ) -> None:
        driver, _input, _output, fallback, termios = _raw_driver(
            "g/provider\nCodex response\nq",
            answers=("19",),
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
            [mock.call("Go to source line: ")],
        )
        self.assertEqual(termios.events.count("activate"), 6)
        self.assertEqual(termios.events.count("restore"), 4)
        self.assertEqual(driver._history_state, driver_module.MainInputHistoryState())

    def test_search_field_escape_redraws_exact_frame_without_emitting_event(
        self,
    ) -> None:
        driver, _input, output, fallback, termios = _raw_driver("/provid\x1bq")
        frame = (
            "Session session_1 | Conversation debate_1 | Agent character_custodian\n"
            "SIMPLE | selected exchange_2 | Search content='needle' | 2/5\n"
            "Footer: n/N matches; / search; q close"
        )
        events: list[str | None] = []

        def operation(host: ExternalViewerHost) -> None:
            host.present(frame)
            events.append(host.read_event())

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=False,
        ):
            driver.open_external_view(operation)

        self.assertEqual(events, ["close"])
        self.assertEqual(
            output.records.count(f"{driver_module._VIEWER_SCREEN_REDRAW}{frame}"),
            2,
        )
        final_redraw = "".join(output.records).rfind(frame)
        self.assertNotEqual(final_redraw, -1)
        self.assertNotIn("provid", "".join(output.records)[final_redraw:])
        fallback.assert_not_called()
        self.assertEqual(termios.events.count("activate"), 3)
        self.assertEqual(termios.events.count("restore"), 2)
        self.assertEqual(driver._history_state, driver_module.MainInputHistoryState())

    def test_search_query_escape_preserves_state_source_and_next_match(self) -> None:
        driver, _input, output, fallback, _termios = _raw_driver(
            "/content\npartial query\x1bnq"
        )
        source = b'{"content":"needle","sequence":2}\n'
        source_before = bytes(source)
        active = (
            "Session session_1 | Conversation conversation_1 | Agent plot_architect\n"
            "VERBOSE | selected exchange_2 | Search content='needle' | 2/5\n"
            "Footer: result 2/5"
        )
        advanced = active.replace("exchange_2", "exchange_3").replace("2/5", "3/5")
        events: list[str | None] = []

        def operation(host: ExternalViewerHost) -> None:
            host.present(active)
            event = host.read_event()
            events.append(event)
            if event == "next_match":
                host.present(advanced)
            events.append(host.read_event())

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=False,
        ):
            driver.open_external_view(operation)

        self.assertEqual(events, ["next_match", "close"])
        self.assertEqual(source, source_before)
        self.assertEqual(
            output.records.count(f"{driver_module._VIEWER_SCREEN_REDRAW}{active}"),
            2,
        )
        self.assertEqual(
            output.records.count(f"{driver_module._VIEWER_SCREEN_REDRAW}{advanced}"),
            1,
        )
        fallback.assert_not_called()

    def test_retained_frame_is_transactional_discarded_and_host_local(self) -> None:
        driver, _input, output, _fallback, _termios = _raw_driver(
            "/draft\x1bq/draft\x1bq"
        )
        stable = "stable successfully presented frame"
        rejected = "rejected candidate frame"
        presentation_failure = OSError("candidate presentation failed")
        first_host: list[ExternalViewerHost] = []

        def first(host: ExternalViewerHost) -> None:
            first_host.append(host)
            host.present(stable)
            with self.assertRaises(ValueError):
                host.present("unsafe\rframe")
            output.failure = presentation_failure
            with self.assertRaises(OSError) as raised:
                host.present(rejected)
            self.assertIs(raised.exception, presentation_failure)
            output.failure = None
            self.assertEqual(host.read_event(), "close")

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=False,
        ):
            driver.open_external_view(first)
        self.assertEqual(
            output.records.count(f"{driver_module._VIEWER_SCREEN_REDRAW}{stable}"),
            2,
        )
        self.assertNotIn(rejected, "".join(output.records))
        retained_host = first_host[0]
        self.assertIsNone(retained_host.read_event())

        mark = len(output.records)
        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=False,
        ):
            driver.open_external_view(
                lambda host: self.assertEqual(host.read_event(), "close")
            )
        self.assertNotIn(stable, "".join(output.records[mark:]))

    def test_prompt_escape_sequences_are_bounded_consumed_and_do_not_leak(
        self,
    ) -> None:
        self.assertEqual(driver_module._MAX_ESCAPE_SEQUENCE_LENGTH, 64)
        supported = {
            "CSI": "\x1b[D",
            "SS3": "\x1bOD",
        }
        for name, sequence in supported.items():
            with self.subTest(kind=name):
                driver, _input, _output, fallback, _termios = _raw_driver(
                    f"/ab{sequence}X\nneedle\nq"
                )
                supported_events: list[str | None] = []

                def operation(host: ExternalViewerHost) -> None:
                    host.present("frame")
                    supported_events.append(host.read_event())
                    supported_events.append(host.read_event())

                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    return_value=True,
                ):
                    driver.open_external_view(operation)
                self.assertEqual(
                    supported_events,
                    ["search\taXb\tneedle", "close"],
                )
                fallback.assert_not_called()

        exact = "\x1b[" + "1" * 61 + "B"
        over_cap = "\x1b[" + "1" * 62 + "B"
        self.assertEqual(len(exact), driver_module._MAX_ESCAPE_SEQUENCE_LENGTH)
        self.assertEqual(len(over_cap), driver_module._MAX_ESCAPE_SEQUENCE_LENGTH + 1)
        for name, sequence in (
            ("unsupported_csi", "\x1b[99~"),
            ("unsupported_ss3", "\x1bOP"),
            ("exact_boundary", exact),
            ("over_cap", over_cap),
        ):
            with self.subTest(kind=name):
                driver, _input, output, fallback, _termios = _raw_driver(
                    f"/draft{sequence}q"
                )
                cancelled_events: list[str | None] = []

                def operation(host: ExternalViewerHost) -> None:
                    host.present("retained frame")
                    cancelled_events.append(host.read_event())

                with mock.patch.object(
                    driver_module,
                    "_escape_input_is_ready",
                    return_value=True,
                ):
                    driver.open_external_view(operation)
                self.assertEqual(cancelled_events, ["close"])
                self.assertEqual(
                    output.records.count(
                        f"{driver_module._VIEWER_SCREEN_REDRAW}retained frame"
                    ),
                    2,
                )
                fallback.assert_not_called()

        driver, _input, output, fallback, _termios = _raw_driver("/draft\x1b[")

        def truncated_operation(host: ExternalViewerHost) -> None:
            host.present("retained frame")
            self.assertIsNone(host.read_event())

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=True,
        ):
            driver.open_external_view(truncated_operation)
        self.assertEqual(
            output.records.count(
                f"{driver_module._VIEWER_SCREEN_REDRAW}retained frame"
            ),
            1,
        )
        fallback.assert_not_called()

    def test_main_escape_remains_cancel_and_does_not_use_prompt_redraw(self) -> None:
        driver, _input, output, _fallback, _termios = _raw_driver("\x1bq")
        events: list[str | None] = []

        def operation(host: ExternalViewerHost) -> None:
            host.present("active search frame")
            events.append(host.read_event())
            events.append(host.read_event())

        with mock.patch.object(
            driver_module,
            "_escape_input_is_ready",
            return_value=False,
        ):
            driver.open_external_view(operation)
        self.assertEqual(events, ["cancel", "close"])
        self.assertEqual(
            output.records.count(
                f"{driver_module._VIEWER_SCREEN_REDRAW}active search frame"
            ),
            1,
        )

    def test_search_prompt_interrupt_and_eof_paths_never_submit_drafts(self) -> None:
        cases: tuple[tuple[str, str, object | None], ...] = (
            ("field configured VINTR", "/draft\x03", None),
            ("query configured VINTR", "/field\ndraft\x03", None),
            ("field configured VEOF", "/\x04", None),
            ("query configured VEOF", "/field\n\x04", None),
            ("field physical EOF", "/draft", None),
            ("query physical EOF", "/field\ndraft", None),
            ("field physical interrupt", "", KeyboardInterrupt()),
            ("query physical interrupt", "", KeyboardInterrupt()),
        )
        for name, characters, physical in cases:
            with self.subTest(kind=name):
                driver, _input, output, fallback, termios = _raw_driver(characters)
                events: list[str | None] = []

                def operation(host: ExternalViewerHost) -> None:
                    host.present("retained frame")
                    events.append(host.read_event())

                read_patch = None
                if physical is not None:
                    prefix = ["/"] if name.startswith("field") else ["/", *"field\n"]
                    read_patch = mock.patch.object(
                        RawTerminalDriver,
                        "_read_character",
                        side_effect=(*prefix, physical),
                    )
                context = (
                    read_patch
                    if read_patch is not None
                    else mock.patch.object(
                        driver_module,
                        "_escape_input_is_ready",
                        return_value=False,
                    )
                )
                with context:
                    driver.open_external_view(operation)
                self.assertEqual(events, [None])
                self.assertEqual(
                    output.records.count(
                        f"{driver_module._VIEWER_SCREEN_REDRAW}retained frame"
                    ),
                    1,
                )
                fallback.assert_not_called()
                self.assertEqual(termios.events[-1], "restore")

    def test_prompt_failure_precedence_preserves_active_or_restoration_error(
        self,
    ) -> None:
        driver, _input, output, _fallback, termios = _raw_driver("/")
        active = OSError("prompt render failed")
        failed_hosts: list[ExternalViewerHost] = []

        def fail_prompt(host: ExternalViewerHost) -> None:
            failed_hosts.append(host)
            host.present("retained frame")
            output.failure = active
            host.read_event()

        with self.assertRaises(OSError) as raised:
            driver.open_external_view(fail_prompt)
        self.assertIs(raised.exception, active)
        self.assertEqual(termios.events[-1], "restore")
        output.failure = None
        self.assertIsNone(failed_hosts[0].read_event())

        driver, _input, output, _fallback, termios = _raw_driver("/")
        active = OSError("prompt render failed")
        restoration = OSError("terminal restore failed")

        def fail_prompt_and_restore(host: ExternalViewerHost) -> None:
            host.present("retained frame")
            output.failure = active
            termios.restoration_failure = restoration
            host.read_event()

        with self.assertRaisesRegex(
            RuntimeError, "interactive terminal restoration failed"
        ) as restoration_raised:
            driver.open_external_view(fail_prompt_and_restore)
        self.assertIs(restoration_raised.exception.__cause__, restoration)
        self.assertTrue(
            any(
                "another interactive input failure" in note
                for note in restoration_raised.exception.__notes__
            )
        )

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
