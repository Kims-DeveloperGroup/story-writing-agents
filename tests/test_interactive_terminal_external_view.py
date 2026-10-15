"""Terminal ownership contracts for hosted external read-only viewers."""

from __future__ import annotations

import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

from story_writing_agents.interactive_terminal import (
    InteractiveExternalViewer,
    InteractiveTerminal, InteractiveTranscriptViewer,
)
from story_writing_agents.interactive_terminal import _driver as driver_module
from story_writing_agents.interactive_terminal import (
    _external_view as external_view_module,
)
from story_writing_agents.interactive_terminal._external_view import (
    ExternalViewUnavailableError, ExternalTerminalIO,
    ExternalViewerHost,
)
from tests._external_view_support import _raw_driver, _Host, PortableTerminalScreen


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
        self.assertEqual(observed, [
            (72, 24), False, "line\t/ provider codex", "line\tesc",
            "line\tn", "line\tq",
        ])
        self.assertEqual(writes, ["plain frame\n"])
        self.assertEqual(prompts, [""] * 4)

        viewer.open(lambda host: observed.append(host.read_event()))
        self.assertEqual(observed[-1], "line\tq")

    def test_plain_terminal_refuses_transient_view_without_presenting_content(self) -> None:
        writes: list[str] = []
        terminal = InteractiveTerminal(lambda _prompt: "q", writes.append)
        operation = mock.Mock()

        with self.assertRaises(ExternalViewUnavailableError):
            InteractiveExternalViewer(terminal).open(operation, transient_only=True)

        operation.assert_not_called()
        with self.assertRaises(ExternalViewUnavailableError):
            InteractiveExternalViewer(terminal).open_owned(operation)
        operation.assert_not_called()
        self.assertEqual(writes, [])

    def test_viewer_and_callback_opener_reject_non_boolean_transient_flag(self) -> None:
        writes: list[str] = []
        read_line = mock.Mock(return_value="q")
        terminal = InteractiveTerminal(read_line, writes.append)
        operation = mock.Mock()

        for value in (1, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "transient_only must be a bool"):
                    InteractiveExternalViewer(terminal).open(  # type: ignore[arg-type]
                        operation, transient_only=value
                    )
                with self.assertRaisesRegex(TypeError, "transient_only must be a bool"):
                    terminal._external_view_open(  # type: ignore[arg-type]
                        operation, transient_only=value
                    )

        operation.assert_not_called()
        read_line.assert_not_called()
        self.assertEqual(writes, [])

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
    def setUp(self):
        patcher = mock.patch.object(driver_module._select, "select", return_value=((7,), (), ()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_live_host_owns_geometry_color_events_screen_and_restoration(self) -> None:
        driver, _input, output, fallback, termios = _raw_driver("\x1b[A\x1b[B\x1b[5~\x1b[6~jkb nNmh?cq", width=91, height=27)
        events = []
        def operation(host):
            events.extend((host.terminal_size(), host.color_enabled()))
            host.present("\x1b[36mframe\x1b[0m")
            events.extend(host.read_event() for _ in range(15))
            host.close_view()
        with mock.patch.object(driver_module._select, "select", return_value=((7,), (), ())):
            driver.open_external_view(operation, transient_only=True)
        self.assertEqual(events[:2], [(91, 27), True])
        self.assertEqual(events[2:], ["key\tup", "key\tdown", "key\tpage_up", "key\tpage_down"] + ["text\t" + c for c in "jkb nNmh?cq"])
        fallback.assert_not_called()
        self.assertEqual((termios.events.count("activate"), termios.events.count("restore")), (1, 1))
        rendered = "".join(output.records)
        self.assertEqual(rendered.count(driver_module._EXTERNAL_VIEW_SCREEN_ENTER), 1)
        self.assertEqual(rendered.count(driver_module._VIEWER_SCREEN_EXIT), 1)
        self.assertIn("\x1b[36mframe\x1b[0m", rendered)

        driver, input_stream, _output, _fallback, _termios = _raw_driver("€J")
        events = []
        with mock.patch.object(driver_module.time, "monotonic", side_effect=[100.0, 100.1, 100.4, 101.0, 101.0, 101.1, 102.0, 102.0]), \
             mock.patch.object(driver_module._select, "select", side_effect=[((7,), (), ()), ((), (), ()), ((7,), (), ()), ((7,), (), ()), ((7,), (), ())]) as poll:
            driver.open_external_view(lambda host: events.extend(host.read_event() for _ in range(3)))
        self.assertEqual(events, ["idle", "text\t€", "text\tJ"])
        self.assertEqual(input_stream.characters, [])
        self.assertAlmostEqual(poll.call_args_list[0].args[3], 0.4)
        self.assertAlmostEqual(poll.call_args_list[1].args[3], 0.1)

        from story_writing_agents.interactive_terminal._model import Notice, Record
        from tests.collaboration_contract_fixture import CONVERSATION_ID, contract_batches, measured_batch, store_batch
        history = ()
        batches = contract_batches(CONVERSATION_ID)
        for name in ("proposal", "confirmation", "running", "calls", "result", "completion"):
            history = store_batch(history, measured_batch(batches[name]))
        # Portable terminals may archive ED2 even in an alternate buffer. The
        # model also wraps eagerly at the right margin, exposing bottom-row leaks.
        leaky = PortableTerminalScreen(); before_leak = leaky.snapshot()
        leaky.write("\x1b[?1049hPRIVATE\x1b[2J\x1b[?1049l")
        self.assertNotEqual(leaky.snapshot(), before_leak)
        leaky = PortableTerminalScreen()
        leaky.write("\x1b[?1049h\x1b[27;1H" + "x" * 91 + "\x1b[?1049l")
        self.assertNotEqual(leaky.snapshot(), before_leak)
        for characters, resize, failure in (("q", False, None), ("\x1b", False, None),
                ("\x03", False, None), ("", False, None), ("qq", False, None),
                ("jk\x1b[B\x1b[A\x1b[6~\x1b[5~\x1b[F\x1b[Hq", True, None),
                ("\x1b[6~" * 80 + "q", False, None),
                ("j", False, "input"), ("q", False, "render")):
            driver, input_stream, output, fallback, termios = _raw_driver(characters)
            screen = PortableTerminalScreen(); baseline = screen.clone()
            original_write, original_read = output.write, input_stream.read1
            def write(value):
                screen.write(value)
                return original_write(value)
            frames = []; original_present = driver_module._RawExternalViewerHost.present
            def present(host, frame):
                frames.append((host.terminal_size(), frame))
                return original_present(host, frame)
            reads = 0
            def read(size):
                nonlocal reads
                reads += 1
                if failure == "input":
                    raise RuntimeError("viewer input failed")
                if resize and reads == 1:
                    screen.resize(47, 10); baseline.resize(47, 10)
                    driver._os_provider().get_terminal_size = lambda _fd: os.terminal_size((47, 10))
                return original_read(size)
            writes = []
            terminal = InteractiveTerminal(lambda _prompt: self.fail("fallback input"), writes.append)
            terminal._external_view_open = driver.open_external_view
            viewer = InteractiveTranscriptViewer(terminal)
            def open_view():
                viewer.open(history, request=Record("context", title="Transcript request", content="개요 e\u0301🙂"),
                    result=Notice("Transcript returned all records."), continuation=Notice("Continuation hint"))
            from story_writing_agents.interactive_terminal import _transcript_view
            render = _transcript_view.render_component
            with mock.patch.object(output, "write", side_effect=write), mock.patch.object(input_stream, "read1", side_effect=read), mock.patch.object(driver_module._RawExternalViewerHost, "present", autospec=True, side_effect=present):
                if failure:
                    with mock.patch.object(_transcript_view, "render_component", side_effect=RuntimeError("viewer render failed") if failure == "render" else render):
                        with self.assertRaisesRegex(RuntimeError, "viewer " + failure + " failed"):
                            open_view()
                else:
                    open_view()
                    if characters == "qq":
                        self.assertEqual(screen.snapshot(), baseline.snapshot())
                        open_view()
            self.assertEqual(screen.snapshot(), baseline.snapshot(), (characters, resize, failure))
            self.assertFalse(screen.alternate); self.assertEqual(screen.pending, "")
            self.assertEqual(writes, []); fallback.assert_not_called()
            self.assertEqual(termios.events.count("activate"), termios.events.count("restore"))
            self.assertTrue(all(len(frame.split("\n")) <= size[1] for size, frame in frames))
            if resize:
                self.assertEqual(frames[-1][0], (47, 10)); self.assertIn("Transcript 1-", frames[-1][1])
            transcript = "".join(output.records)
            self.assertEqual(transcript.count("\x1b[?1049h"), transcript.count("\x1b[?1049l"))
            if characters.startswith("\x1b[6~"):
                self.assertTrue(all(event.event_id in transcript for event in history))
                self.assertIn("Input tokens: 0 | Output tokens: 2", re.sub(r"\x1b\[[0-9;]*m", "", transcript))
                self.assertIn("Continuation hint", transcript)
        plain_writes = []
        with self.assertRaises(ExternalViewUnavailableError):
            InteractiveTranscriptViewer(InteractiveTerminal(lambda _p: "q", plain_writes.append)).open(
                history, request=Record("context"), result=Notice("PRIVATE transcript"))
        self.assertEqual(plain_writes, [])

    def test_physical_input_has_no_viewer_bindings_prompts_or_history(self) -> None:
        text = "/gqjkb nNmh?cJK개요😀e\u0301"
        controls = "\r\n\x08\x7f\x01\x05\x15\x0b\x17\x03\x04\t"
        names = ("enter", "enter", "backspace", "backspace", "ctrl_a", "ctrl_e", "ctrl_u", "ctrl_k", "ctrl_w", "interrupt", "eof", "unknown")
        driver, _input, output, fallback, termios = _raw_driver(text + controls)
        events = []
        driver.open_external_view(lambda host: events.extend(host.read_event() for _ in range(len(text + controls))))
        self.assertEqual(events, ["text\t" + c for c in text] + ["key\t" + n for n in names])
        self.assertEqual((output.records[0], output.records[-1]), ("\x1b[?1049h", "\x1b[?1049l"))
        self.assertNotIn("\x1b[2J", "".join(output.records))
        self.assertEqual((termios.events.count("activate"), termios.events.count("restore")), (1, 1))
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
        with mock.patch.object(driver_module._select, "select", side_effect=[((7,), (), ()), ((), (), ()), ((7,), (), ())]):
            driver.open_external_view(lambda host: events.extend((host.read_event(), host.read_event())))
        self.assertEqual(events, ["key\tescape", "text\tq"])

    def test_escape_partial_utf8_deadline_stops_byte_reads_and_restores_terminal(self) -> None:
        driver, input_stream, output, fallback, termios = _raw_driver("€J")
        captured = []; timeout = TimeoutError("owned deadline"); custom = ValueError("owned readiness")
        def operation(transport):
            captured.append(transport); driver._typeahead_buffer.append("Q")
            ready = mock.Mock(side_effect=[None, timeout])
            self.assertEqual(transport.read_character(ready), "Q"); ready.assert_not_called()
            with self.assertRaises(TimeoutError) as raised: transport.read_character(ready)
            self.assertIs(raised.exception, timeout)
            self.assertEqual(input_stream.characters, [0x82, 0xAC, ord("J")])
            self.assertEqual(transport.read_character(), "€")
            with self.assertRaises(ValueError) as raised: transport.read_character(mock.Mock(side_effect=custom))
            self.assertIs(raised.exception, custom)
            self.assertEqual(transport.read_character(), "J")
        with mock.patch.object(input_stream, "read1", wraps=input_stream.read1) as reads:
            driver.open_owned_external_view(operation)
        self.assertEqual(reads.call_args_list, [mock.call(1)] * 4)
        self.assertEqual(input_stream.characters, []); self.assertEqual(output.records, [])
        self.assertEqual(termios.events.count("activate"), 0); fallback.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "transport.*closed"): captured[0].read_character()

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
        import io, re, jsonl_viewer
        from jsonl_viewer import ViewerSpec, ViewerTerminal, view_jsonl
        from jsonl_viewer import _terminal
        source = ('{"content":"개요 one 개요 one"}\n{"content":"other"}\n').encode()
        spec = ViewerSpec("s", "c", "a", input_protocol="keys"); command = "/ 개요 one\nn\nesc\nq\n"
        standalone_output = io.StringIO()
        view_jsonl(source, spec, terminal=ViewerTerminal(io.StringIO(command), standalone_output, no_color=True))
        lines = iter(command.splitlines()); story_output = []
        viewer = InteractiveExternalViewer(InteractiveTerminal(lambda prompt: next(lines), story_output.append, width=80))
        viewer.open(lambda host: view_jsonl(source, spec, host))
        driver, _input, output, fallback, termios = _raw_driver("/wrong\x15개요 one\nnq")
        with mock.patch.object(_terminal, "termios", termios), mock.patch.object(_terminal, "tty", SimpleNamespace(setcbreak=lambda descriptor, when: None)), mock.patch.dict(os.environ, {"TERM": "xterm-256color", "COLUMNS": "200"}):
            driver.open_owned_external_view(lambda transport: view_jsonl(source, spec, terminal=ViewerTerminal(transport.input_stream, transport.output_stream, transport.read_character, True), transient_only=True))
        for rendered in ("".join(story_output), standalone_output.getvalue(), "".join(output.records)):
            self.assertIn("2/2 occurrences • Search all text='개요 one'", rendered)
            self.assertIn("개요 one ⟦개요 one⟧", re.sub(r"\x1b\[[0-9;]*m", "", rendered))
            self.assertNotIn("U0015", rendered)
        self.assertEqual("".join(output.records).count("\x1b[?1049h"), 1)
        self.assertEqual("".join(output.records).count("\x1b[?1000h"), 1)
        fallback.assert_not_called(); self.assertEqual(source, ('{"content":"개요 one 개요 one"}\n{"content":"other"}\n').encode())

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

    def test_transient_view_refuses_line_fallback_before_writing_content(self) -> None:
        driver, _input, output, fallback, termios = _raw_driver(
            "", tty=False
        )
        operation = mock.Mock()

        with self.assertRaises(ExternalViewUnavailableError):
            driver.open_external_view(operation, transient_only=True)

        operation.assert_not_called()
        self.assertEqual(output.records, [])
        self.assertEqual(termios.events, [])
        fallback.assert_not_called()

    def test_driver_rejects_non_boolean_transient_flag_before_terminal_access(self) -> None:
        driver, _input, output, fallback, termios = _raw_driver("q")
        operation = mock.Mock()

        for value in (1, None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TypeError, "transient_only must be a bool"):
                    driver.open_external_view(  # type: ignore[arg-type]
                        operation, transient_only=value
                    )

        operation.assert_not_called()
        self.assertEqual(output.records, [])
        self.assertEqual(termios.events, [])
        fallback.assert_not_called()

    def test_reentry_and_progress_overlap_are_rejected_then_latch_releases(
        self,
    ) -> None:
        driver, _input, _output, _fallback, _termios = _raw_driver("qq")
        reentry: list[BaseException] = []

        def operation(host: ExternalTerminalIO) -> None:
            try:
                driver.open_owned_external_view(lambda _nested: None)
            except BaseException as failure:
                reentry.append(failure)
            self.assertEqual(host.read_character(), "q")

        driver.open_owned_external_view(operation)
        self.assertIsInstance(reentry[0], RuntimeError)
        self.assertIn("already active", str(reentry[0]))
        driver.open_owned_external_view(
            lambda host: self.assertEqual(host.read_character(), "q")
        )

        driver._progress_active = True
        called = False

        def prohibited(_host: ExternalTerminalIO) -> None:
            nonlocal called
            called = True

        with self.assertRaisesRegex(RuntimeError, "overlap terminal progress"):
            driver.open_owned_external_view(prohibited)
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
