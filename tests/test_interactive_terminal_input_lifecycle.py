"""Deterministic contracts for irreversible terminal input ownership."""

from __future__ import annotations

import codecs
import signal
import unittest
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest import mock

from story_writing_agents.interactive_terminal import InteractiveInputLifecycle, InteractiveTerminal
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history, transition_line_input, transition_main_command_input,
)
from story_writing_agents.interactive_terminal import _input_lifecycle as lifecycle
from story_writing_agents.interactive_terminal import _driver as driver_module
from story_writing_agents.interactive_terminal._driver import (
    RawTerminalDriver,
    _TerminalConfiguration,
)
from story_writing_agents.interactive_terminal._input_lifecycle import (
    LiveCharacterReader,
    LiveDecoderSpec,
    TerminalInputLatch,
    TerminalInputMode,
    inspect_live_decoder_spec,
)
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    InputPromptView,
    Prompt,
    TerminalCapabilities,
)


class _ByteBackedInput:
    """Expose scripted exact byte reads without permitting text reads."""

    def __init__(
        self,
        *chunks: object,
        encoding: object = "utf-8",
        errors: object = "strict",
    ) -> None:
        self.chunks = deque(chunks)
        self.encoding = encoding
        self.errors = errors
        self.buffer = self
        self.calls: list[tuple[str, int]] = []

    def read(self, size: int) -> str:
        self.calls.append(("read", size))
        raise AssertionError("live input must not use the text wrapper")

    def read1(self, size: int) -> object:
        self.calls.append(("read1", size))
        value = self.chunks.popleft() if self.chunks else b""
        if isinstance(value, BaseException):
            raise value
        return value


def _spec(stream: _ByteBackedInput) -> LiveDecoderSpec:
    observed = inspect_live_decoder_spec(stream)
    assert observed is not None
    return observed


def _line_capabilities(width: int = 80) -> TerminalCapabilities:
    return TerminalCapabilities(
        CapabilityProfile.LINE,
        width,
        input_tty=False,
        output_tty=False,
        raw_mode=False,
        cursor_controls=False,
        decorative_sgr=False,
    )


def _live_capabilities(
    width: int = 80,
    *,
    decorative_sgr: bool = True,
) -> TerminalCapabilities:
    return TerminalCapabilities(
        CapabilityProfile.ANSI if decorative_sgr else CapabilityProfile.PLAIN,
        width,
        input_tty=True,
        output_tty=True,
        raw_mode=True,
        cursor_controls=True,
        decorative_sgr=decorative_sgr,
    )


class _TermiosIdentity:
    PENDIN = 16
    ECHO = 8
    ECHONL = 32
    ICANON = 2
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self):
        self.attributes = [0, 0, 0, self.ICANON | self.ECHO | self.ECHONL, 0, 0, [b"\x04", b"\x03"]]
        self.calls = []

    def tcsetattr(self, descriptor, when, attributes):
        self.calls.append((descriptor, when))
        self.attributes = list(attributes)
        self.attributes[6] = list(attributes[6])


def _configuration(
    termios_module: object,
    *,
    input_descriptor: int = 7,
    output_descriptor: int = 8,
    local_flags: int = 2,
    eof_character: str | None = "\x04",
    interrupt_character: str | None = "\x03",
    disabled_control_byte: int = 255,
    width: int = 80,
) -> _TerminalConfiguration:
    original = [0, 0, 0, local_flags, 0, 0, [b"\x04", b"\x03"]]
    editor = [0, 0, 0, 0, 0, 0, [b"\x04", b"\x03"]]
    return _TerminalConfiguration(
        input_descriptor,
        output_descriptor,
        original,
        editor,
        width,
        eof_character,
        interrupt_character,
        disabled_control_byte,
        termios_module,
    )


def _resource_free_driver(
    fallback: mock.Mock,
) -> tuple[RawTerminalDriver, mock.Mock, mock.Mock]:
    input_stream = mock.Mock(name="input_stream")
    output_stream = mock.Mock(name="output_stream")
    driver = RawTerminalDriver(
        fallback,
        input_stream,
        output_stream,
        mock.Mock(name="line_transition"),
        mock.Mock(name="main_transition"),
        mock.Mock(name="history_append"),
        termios_provider=mock.Mock(name="termios_provider"),
        os_provider=mock.Mock(name="os_provider"),
    )
    return driver, input_stream, output_stream


@contextmanager
def _session_driver(*chunks):
    stream = _ByteBackedInput(*chunks)
    termios = _TermiosIdentity()
    fallback, output = mock.Mock(), mock.Mock()
    output.write.side_effect = len
    driver = RawTerminalDriver(fallback, stream, output, transition_line_input,
                               transition_main_command_input, append_main_input_history)
    with (
        mock.patch.object(RawTerminalDriver, "capabilities", return_value=_live_capabilities()),
        mock.patch.object(driver_module, "_terminal_configuration", side_effect=lambda *_a, **_k: _configuration(termios, local_flags=termios.attributes[3])),
    ):
        yield driver, termios, stream, output, fallback


@contextmanager
def _signal_state(disposition):
    selected = (getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name))
    state = {signal.SIGINT: lambda *_args: None, **{signum: disposition for signum in selected}}
    def install(signum, handler):
        previous = state[signum]
        state[signum] = handler
        return previous
    with (
        mock.patch.object(signal, "getsignal", side_effect=state.__getitem__),
        mock.patch.object(signal, "signal", side_effect=install),
    ):
        yield state


class DecoderAdmissionTests(unittest.TestCase):
    """Lock safe decoder admission and stable source identity."""

    def test_safe_aliases_and_builtin_error_handlers_are_canonical(self) -> None:
        cases = (
            ("UTF8", None, "utf-8", "strict"),
            ("us-ascii", "", "ascii", "strict"),
            ("latin-1", "surrogateescape", "iso8859-1", "surrogateescape"),
        )
        for encoding, errors, expected_encoding, expected_errors in cases:
            with self.subTest(encoding=encoding, errors=errors):
                stream = _ByteBackedInput(
                    encoding=encoding,
                    errors=errors,
                )
                spec = _spec(stream)
                self.assertEqual(spec.encoding, expected_encoding)
                self.assertEqual(spec.errors, expected_errors)
                self.assertEqual(stream.calls, [])

    def test_source_identity_matches_repeat_observation_and_rejects_drift(self) -> None:
        stream = _ByteBackedInput()
        first = _spec(stream)
        second = _spec(stream)
        self.assertIsNot(first, second)
        self.assertTrue(first.matches(second))
        self.assertFalse(first.matches(_spec(_ByteBackedInput())))
        changed_errors = _ByteBackedInput(errors="surrogateescape")
        changed_errors.buffer = stream.buffer
        self.assertFalse(first.matches(_spec(changed_errors)))

    def test_missing_byte_source_is_fallback_eligible_without_wrapper_reads(self) -> None:
        wrapper = mock.Mock(spec=[])
        self.assertIsNone(inspect_live_decoder_spec(wrapper))
        self.assertEqual(wrapper.mock_calls, [])

    def test_unknown_unsafe_and_custom_handlers_are_rejected_before_read(self) -> None:
        custom_name = "story_writing_agents_expanding_test_handler"
        codecs.register_error(custom_name, lambda failure: ("expanded", failure.end))
        cases = (
            ("missing-story-codec", "strict", LookupError),
            ("utf-16", "strict", ValueError),
            ("utf-8", "missing-story-handler", LookupError),
            ("utf-8", "replace", ValueError),
            ("utf-8", custom_name, ValueError),
        )
        for encoding, errors, expected in cases:
            with self.subTest(encoding=encoding, errors=errors):
                stream = _ByteBackedInput(encoding=encoding, errors=errors)
                with self.assertRaises(expected):
                    inspect_live_decoder_spec(stream)
                self.assertEqual(stream.calls, [])

    def test_control_bytes_decode_exactly_and_round_trip(self) -> None:
        strict = _spec(_ByteBackedInput(encoding="latin-1"))
        escaped = _spec(_ByteBackedInput(errors="surrogateescape"))
        self.assertEqual(strict.decode_control_byte(0xE9), "é")
        self.assertEqual(
            tuple(strict.decode_control_byte(value) for value in range(256)),
            tuple(chr(value) for value in range(256)),
        )
        self.assertEqual(escaped.decode_control_byte(0xFF), "\udcff")
        for invalid in (-1, 256, True, "3"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    strict.decode_control_byte(invalid)  # type: ignore[arg-type]


class LiveCharacterReaderTests(unittest.TestCase):
    """Verify one-byte decoding, bounded suffixes, EOF, and poison state."""

    def test_fragmented_utf8_reads_exactly_one_byte_and_keeps_suffix_pending(self) -> None:
        raw = "🌊A".encode("utf-8")
        stream = _ByteBackedInput(*(bytes((value,)) for value in raw), b"")
        reader = LiveCharacterReader(_spec(stream))
        self.assertEqual(reader.read_character(), "🌊")
        self.assertEqual(reader.read_character(), "A")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(
            stream.calls,
            [("read1", 1)] * (len(raw) + 1),
        )

    def test_decoder_suffix_is_bounded_and_consumed_without_another_read(self) -> None:
        class SuffixDecoder:
            def decode(self, chunk: bytes, *, final: bool) -> str:
                return "ab" if chunk else ""

        stream = _ByteBackedInput(b"x")
        with mock.patch.object(
            lifecycle.codecs,
            "getincrementaldecoder",
            return_value=lambda *, errors: SuffixDecoder(),
        ):
            reader = LiveCharacterReader(_spec(stream))
        self.assertEqual(reader.read_character(), "a")
        self.assertTrue(reader.has_pending_codepoint)
        self.assertEqual(reader.read_character(), "b")
        self.assertEqual(stream.calls, [("read1", 1)])

        class ExpandingDecoder:
            def decode(self, chunk: bytes, *, final: bool) -> str:
                return "abcde" if chunk else ""

        overflow = _ByteBackedInput(b"x")
        with mock.patch.object(
            lifecycle.codecs,
            "getincrementaldecoder",
            return_value=lambda *, errors: ExpandingDecoder(),
        ):
            overflowing_reader = LiveCharacterReader(_spec(overflow))
        with self.assertRaisesRegex(RuntimeError, "suffix exceeds its bound"):
            overflowing_reader.read_character()

    def test_clean_eof_is_stable_without_future_reads(self) -> None:
        stream = _ByteBackedInput(b"")
        reader = LiveCharacterReader(_spec(stream))
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(stream.calls, [("read1", 1)])

    def test_strict_partial_eof_permanently_rethrows_the_same_failure(self) -> None:
        stream = _ByteBackedInput(b"\xe2", b"")
        reader = LiveCharacterReader(_spec(stream))
        with self.assertRaises(UnicodeDecodeError) as first:
            reader.read_character()
        with self.assertRaises(UnicodeDecodeError) as second:
            reader.read_character()
        self.assertIs(second.exception, first.exception)
        self.assertEqual(stream.calls, [("read1", 1), ("read1", 1)])

    def test_surrogate_final_byte_then_eof_are_stable(self) -> None:
        stream = _ByteBackedInput(b"\xff", b"", errors="surrogateescape")
        reader = LiveCharacterReader(_spec(stream))
        self.assertEqual(reader.read_character(), "\udcff")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(stream.calls, [("read1", 1), ("read1", 1)])

    def test_interrupt_reset_discards_incomplete_bytes_and_future_reads_continue(self) -> None:
        stream = _ByteBackedInput(b"\xe2", KeyboardInterrupt(), b"A", b"")
        reader = LiveCharacterReader(_spec(stream))
        with self.assertRaises(KeyboardInterrupt):
            reader.read_character()
        reader.reset_incomplete()
        self.assertEqual(reader.read_character(), "A")
        self.assertEqual(reader.read_character(), "")
        self.assertEqual(reader.read_character(), "")

    def test_non_interrupt_failure_is_permanent_and_reset_cannot_clear_it(self) -> None:
        failure = OSError("read failed")
        stream = _ByteBackedInput(failure, b"A")
        reader = LiveCharacterReader(_spec(stream))
        with self.assertRaises(OSError) as first:
            reader.read_character()
        reader.reset_incomplete()
        with self.assertRaises(OSError) as second:
            reader.read_character()
        self.assertIs(first.exception, failure)
        self.assertIs(second.exception, failure)
        self.assertEqual(stream.calls, [("read1", 1)])

    def test_synchronous_reader_reentry_permanently_poisoned(self) -> None:
        reader: LiveCharacterReader

        def reenter(_size: int) -> bytes:
            reader.read_character()
            return b"A"

        spec = LiveDecoderSpec(reenter, "utf-8", "strict", None, reenter)
        reader = LiveCharacterReader(spec)
        with self.assertRaisesRegex(RuntimeError, "input is reentrant") as first:
            reader.read_character()
        with self.assertRaises(RuntimeError) as second:
            reader.read_character()
        self.assertIs(second.exception, first.exception)

    def test_malformed_byte_shapes_poison_without_normalization(self) -> None:
        for malformed in (b"two", "x", bytearray(b"x"), bytearray(), None):
            with self.subTest(
                malformed_type=type(malformed).__name__,
                malformed_length=(
                    len(malformed) if hasattr(malformed, "__len__") else None
                ),
            ):
                stream = _ByteBackedInput(malformed)
                reader = LiveCharacterReader(_spec(stream))
                with self.assertRaisesRegex(
                    TypeError,
                    "byte reader must return exactly one byte",
                ) as first:
                    reader.read_character()
                with self.assertRaises(TypeError) as second:
                    reader.read_character()
                self.assertIs(second.exception, first.exception)
                self.assertEqual(stream.calls, [("read1", 1)])

    def test_before_read_checks_each_byte_and_timeout_is_sticky(self) -> None:
        stream = _ByteBackedInput(b"\xe2", b"\x82", b"\xac")
        reader = LiveCharacterReader(_spec(stream))
        failure = TimeoutError("fixed deadline")
        ready = mock.Mock(side_effect=[None, failure])
        with self.assertRaises(TimeoutError) as first:
            reader.read_character(before_read=ready)
        self.assertIs(first.exception, failure)
        self.assertEqual(stream.calls, [("read1", 1)])
        self.assertEqual(ready.call_count, 2)
        reader.reset_incomplete()
        with self.assertRaises(TimeoutError) as again:
            reader.read_character(before_read=ready)
        self.assertIs(again.exception, failure)
        self.assertEqual(stream.calls, [("read1", 1)])
        self.assertEqual(ready.call_count, 2)

        stream = _ByteBackedInput(b"\xe2", b"\x82", b"\xac")
        reader = LiveCharacterReader(_spec(stream))
        ready = mock.Mock(side_effect=[None, lifecycle.LiveInputIdle(), None, None])
        with self.assertRaises(lifecycle.LiveInputIdle):
            reader.read_character(before_read=ready)
        self.assertEqual(stream.calls, [("read1", 1)])
        self.assertEqual(reader.read_character(before_read=ready), "€")
        self.assertEqual(ready.call_count, 4)

    def test_before_read_does_not_wait_for_pending_codepoints_or_stable_eof(self) -> None:
        class SuffixDecoder:
            def decode(self, chunk: bytes, *, final: bool) -> str:
                return "ab" if chunk else ""
        stream = _ByteBackedInput(b"x", b"")
        with mock.patch.object(lifecycle.codecs, "getincrementaldecoder", return_value=lambda *, errors: SuffixDecoder()):
            reader = LiveCharacterReader(_spec(stream))
        ready = mock.Mock()
        self.assertEqual(reader.read_character(before_read=ready), "a")
        self.assertEqual(ready.call_count, 1)
        ready.side_effect = lifecycle.LiveInputIdle()
        self.assertEqual(reader.read_character(before_read=ready), "b")
        ready.side_effect = None
        self.assertEqual(ready.call_count, 1)
        self.assertEqual(reader.read_character(before_read=ready), "")
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(reader.read_character(before_read=ready), "")
        self.assertEqual(ready.call_count, 2)
        self.assertEqual(stream.calls, [("read1", 1), ("read1", 1)])


class InputLatchAndDriverTests(unittest.TestCase):
    """Lock irreversible selection, exclusion, and pre-latch validation."""

    def test_latch_starts_resource_free_and_live_identity_is_permanent(self) -> None:
        latch = TerminalInputLatch()
        self.assertIs(latch.mode, TerminalInputMode.UNDECIDED)
        with self.assertRaisesRegex(RuntimeError, "not live-owned"):
            _ = latch.live_reader

        stream = _ByteBackedInput()
        spec = _spec(stream)
        reader = latch.latch_live(spec)
        self.assertIs(latch.mode, TerminalInputMode.LIVE)
        self.assertIs(latch.live_reader, reader)
        self.assertIs(latch.require_live(_spec(stream)), reader)
        with self.assertRaisesRegex(RuntimeError, "decoder changed"):
            latch.require_live(_spec(_ByteBackedInput()))
        with self.assertRaisesRegex(RuntimeError, "cannot switch to fallback"):
            latch.latch_fallback()

    def test_fallback_latch_is_sticky_and_never_constructs_a_live_reader(self) -> None:
        latch = TerminalInputLatch()
        latch.latch_fallback()
        latch.latch_fallback()
        self.assertIs(latch.mode, TerminalInputMode.FALLBACK)
        with self.assertRaisesRegex(RuntimeError, "already selected"):
            latch.latch_live(_spec(_ByteBackedInput()))
        with self.assertRaisesRegex(RuntimeError, "not live-owned"):
            _ = latch.live_reader

    def test_input_operation_rejects_reentry_and_releases_after_failure(self) -> None:
        latch = TerminalInputLatch()
        with latch.input_operation():
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with latch.input_operation():
                    pass
        with self.assertRaisesRegex(ValueError, "primary"):
            with latch.input_operation():
                raise ValueError("primary")
        with latch.input_operation():
            pass
        for failure in (None, KeyboardInterrupt("interrupt"), EOFError("eof"), ValueError("operation")):
            with _session_driver(b"s", b"e", b"e", b"d", b"\n", b"\x03", b"\x04", b"") as (driver, termios, _stream, _output, fallback), _signal_state(signal.SIG_DFL) as signals:
                original = list(termios.attributes)
                original_signals = dict(signals)
                try:
                    with driver.input_session():
                        guarded = list(termios.attributes)
                        guarded_signals = dict(signals)
                        self.assertTrue(all(callable(handler) for handler in signals.values()))
                        self.assertIs(signals[signal.SIGINT], original_signals[signal.SIGINT])
                        self.assertEqual(guarded[3], termios.ICANON)
                        self.assertEqual(driver.read_main_line("main> ", lambda _text: ()), "seed")
                        self.assertEqual(termios.attributes, guarded)
                        for control in (KeyboardInterrupt, EOFError, EOFError):
                            with self.assertRaises(control):
                                driver.read_line("control> ")
                            self.assertEqual(termios.attributes, guarded)
                        with self.assertRaisesRegex(RuntimeError, "session is already active"):
                            with driver.input_session():
                                pass
                        with driver.native_input_handoff():
                            self.assertEqual(termios.attributes, original)
                            self.assertEqual(signals, guarded_signals)
                            with self.assertRaisesRegex(RuntimeError, "handoff is already active"):
                                with driver.native_input_handoff():
                                    pass
                            with self.assertRaisesRegex(RuntimeError, "suspended"):
                                driver.read_line("forbidden> ")
                        self.assertEqual(termios.attributes, guarded)
                        self.assertEqual(signals, guarded_signals)
                        if failure is not None:
                            raise failure
                except BaseException as raised:
                    self.assertIs(raised, failure)
                else:
                    self.assertIsNone(failure)
                self.assertEqual(termios.attributes, original)
                self.assertEqual(signals, original_signals)
                self.assertFalse(driver._input_session_active)
                fallback.assert_not_called()
                with driver.native_input_handoff():
                    self.assertEqual(termios.attributes, original)

        from story_writing_agents.interactive_terminal._model import MultiAgentScrollState, NestedScrollViewport
        f6, click, prompt = "\x1b[17~", "\x1b[<0;2;248M", "\x1b[<0;2;250M"
        prefix = "draft\x1b[D"
        cases = [(prefix + click + "\x1b[<64;2;249M\r", "draft", 0, 40, 250, True)]
        cases += [(prefix + f6 + click + "\x1b[<65;2;249M\x1b[<64;2;249M\x1b[B" + release + "x\r", "drafxt", 1, 40, 250, True)
                  for release in ("\r", "\x1b", f6, "", prompt)]
        cases += [(f6 + "x\r", "x", 0, width, height, retain)
                  for width, height, retain in ((40, 250, False), (7, 250, True), (40, 1, True))]
        cases += [(prefix + f6 + click + "\x1b[D\r", "draft", 1, -7, 250, True)]
        cases += [(prefix + f6 + "\rx\r", "drafxt", 1, 40, 250, True)]
        cutoff = len((prefix + f6 + click).encode())
        for scripted, expected, captures, width, height, retain in cases:
            scripted = scripted[:-1] + "\x1b[<x;2;3M\x1b[<0;" + "1" * 100 + ";3M\r"
            with _session_driver(*(bytes([byte]) for byte in scripted.encode())) as (driver, termios, stream, output, fallback):
                original = list(termios.attributes)
                view = NestedScrollViewport("a:1", "Agent", 3, 1, ("first",), 1, False,
                                            buffer_lines=("first", "second", "third"))
                if retain:
                    driver._output_history.retain(MultiAgentScrollState((view,), 0))
                output.fileno.return_value = 8
                driver._os_provider = lambda: mock.Mock(get_terminal_size=lambda _: mock.Mock(columns=(7 if len(stream.calls) > cutoff else 40) if width == -7 else width, lines=height))
                with (mock.patch.object(RawTerminalDriver, "_escape_continuation_is_ready", return_value=True),
                      mock.patch.object(RawTerminalDriver, "_query_cursor_row", return_value=height)):
                    self.assertEqual(driver.read_main_line("main> ", lambda _: ()), expected)
                rendered = "".join(call.args[0] for call in output.write.call_args_list)
                self.assertEqual(rendered.count(driver_module._MOUSE_ENABLE), captures)
                if captures:
                    self.assertIn("Focused", rendered)
                    if width != -7 and click in scripted:
                        self.assertIn("second", rendered)
                        self.assertIn("third", rendered)
                        self.assertLess(rendered.rfind(driver_module._MOUSE_DISABLE), rendered.rfind(expected))
                    self.assertGreater(rendered.rfind(driver_module._MOUSE_DISABLE), rendered.rfind(driver_module._MOUSE_ENABLE))
                self.assertEqual(driver._history_state.entries, (expected,))
                self.assertIsNone(driver._output_history.focused_id)
                self.assertEqual(termios.attributes, original)
                fallback.assert_not_called()

        with _session_driver(*(bytes([byte]) for byte in (f6 + click + "\r").encode())) as (driver, termios, _, output, fallback):
            driver._output_history.retain(MultiAgentScrollState((view,), 0))
            output.fileno.return_value = 8
            driver._os_provider = lambda: mock.Mock(get_terminal_size=lambda _: mock.Mock(columns=40, lines=250))
            failures = []
            def write(text):
                if text == driver_module._MOUSE_DISABLE and not failures:
                    failures.append(True)
                    raise OSError("mouse release failed")
                return len(text)
            output.write.side_effect = write
            with (mock.patch.object(RawTerminalDriver, "_escape_continuation_is_ready", return_value=True),
                  mock.patch.object(RawTerminalDriver, "_query_cursor_row", return_value=250),
                  self.assertRaisesRegex(OSError, "mouse release failed")):
                driver.read_main_line("main> ", lambda _: ())
            self.assertEqual(output.write.call_args_list.count(mock.call(driver_module._MOUSE_DISABLE)), 2)
            self.assertEqual(termios.attributes, original)
            fallback.assert_not_called()

        from story_writing_agents.interactive_terminal._model import Message
        for interleaved in ("", "界"):
            scripted = (prefix + f6 + "\x1b[H\x1b[<0;2;2M\x1b[B\rx\r"
                        if not interleaved else "draft" + f6 + "x\r")
            expected = "drafxt" if not interleaved else "draft界x"
            with _session_driver(*(bytes([byte]) for byte in scripted.encode())) as (driver, termios, stream, output, fallback):
                original = list(termios.attributes)
                driver._output_history.retain(MultiAgentScrollState((view,), 0))
                driver._output_history.append_component(Message("Lead", "\n".join(f"TAIL_{i}" for i in range(35))))
                self.assertFalse(driver._output_history.render(_live_capabilities(), 22).hits)
                output.fileno.return_value = 8
                driver._os_provider = lambda: mock.Mock(get_terminal_size=lambda _: mock.Mock(columns=40, lines=24))
                observations = []
                def write(text):
                    if text == "\x1b[6n":
                        reply = (interleaved if not observations else "") + "\x1b[24;1R"
                        observations.append(reply)
                        for byte in reversed(reply.encode()):
                            stream.chunks.appendleft(bytes([byte]))
                    return len(text)
                output.write.side_effect = write
                with (mock.patch.object(RawTerminalDriver, "_escape_continuation_is_ready", return_value=True),
                      mock.patch.object(driver_module._select, "select", return_value=([7], [], []))):
                    self.assertEqual(driver.read_main_line("main> ", lambda _: ()), expected)
                rendered = "".join(call.args[0] for call in output.write.call_args_list)
                self.assertTrue(observations)
                self.assertEqual(rendered.count(driver_module._MOUSE_ENABLE), 1)
                self.assertEqual(driver._history_state.entries, (expected,))
                if not interleaved:
                    self.assertIn("second", rendered)
                    self.assertEqual(driver._output_history.frames[0].view.start_line, 2)
                self.assertLess(rendered.rfind(driver_module._MOUSE_DISABLE), rendered.rfind(expected))
                self.assertEqual(termios.attributes, original)
                fallback.assert_not_called()

        with _session_driver(b"seed\r") as (driver, termios, _, output, fallback):
            size = [40, 24]
            output.fileno.return_value = 8
            driver._os_provider = lambda: mock.Mock(get_terminal_size=lambda _: mock.Mock(columns=size[0], lines=size[1]))
            self.assertEqual(driver.read_main_line("main> ", lambda _: ()), "seed")
            user = Message("User", "ORIGINAL_INPUT")
            with driver.submission("session", "conversation", user):
                origin = driver._output_history.origin
                for width, height, label in ((7, 24, "NARROW"), (40, 1, "SHORT")):
                    size[:] = [width, height]
                    output.reset_mock()
                    driver.write_component(Message("Lead", f"SEMANTIC_{label}"))
                    driver.write_text(f"RAW_{label}\n")
                    rendered = "".join(call.args[0] for call in output.write.call_args_list)
                    visible = "".join(driver_module._EXTERNAL_VIEW_SGR.sub("", rendered).split())
                    self.assertIn(f"SEMANTIC_{label}", visible)
                    self.assertIn(f"RAW_{label}", visible)
                    self.assertNotIn(driver_module._MOUSE_ENABLE, rendered)
                    self.assertTrue(all(entry.origin == origin for entry in driver._output_history.entries))
                size[:] = [40, 24]
                output.reset_mock()
                driver.write_component(Message("Lead", "USABLE_AGAIN"))
                restored = driver_module._EXTERNAL_VIEW_SGR.sub("", "".join(call.args[0] for call in output.write.call_args_list))
                previous = -1
                for token in ("ORIGINAL_INPUT", "SEMANTIC_NARROW", "RAW_NARROW", "SEMANTIC_SHORT", "RAW_SHORT", "USABLE_AGAIN"):
                    self.assertGreater(restored.index(token), previous)
                    previous = restored.index(token)
                self.assertEqual(sum(entry.component == user for entry in driver._output_history.entries), 1)
            self.assertIsNone(driver._output_history.origin)
            self.assertNotIn(driver_module._MOUSE_ENABLE, restored)
            fallback.assert_not_called()

    def test_driver_construction_does_not_inspect_resources(self) -> None:
        fallback = mock.Mock(return_value="unused")
        driver, input_stream, output_stream = _resource_free_driver(fallback)
        self.assertIs(driver._input_latch.mode, TerminalInputMode.UNDECIDED)
        self.assertEqual(input_stream.mock_calls, [])
        self.assertEqual(output_stream.mock_calls, [])
        fallback.assert_not_called()
        driver._termios_provider.assert_not_called()
        driver._os_provider.assert_not_called()
        read, write = mock.Mock(), mock.Mock()
        lifecycle = InteractiveInputLifecycle(InteractiveTerminal(read, write))
        with lifecycle.session(), lifecycle.native_handoff():
            pass
        read.assert_not_called()
        write.assert_not_called()
        with self.assertRaisesRegex(TypeError, "terminal must be an InteractiveTerminal"):
            InteractiveInputLifecycle(object())
        with _session_driver() as (driver, termios, _stream, _output, _fallback):
            original = list(termios.attributes)
            with mock.patch.object(signal, "getsignal", return_value=signal.SIG_DFL), mock.patch.object(termios, "tcsetattr", wraps=termios.tcsetattr) as writes:
                def acquire():
                    with driver.input_session():
                        pass
                with ThreadPoolExecutor(max_workers=1) as executor:
                    with self.assertRaises(RuntimeError):
                        executor.submit(acquire).result(timeout=2)
            self.assertEqual(termios.attributes, original)
            self.assertTrue(all(call.args[2][3] == original[3] for call in writes.call_args_list))

    def test_accepted_input_output_obeys_the_sticky_mode_without_retention(
        self,
    ) -> None:
        payload = "user [completed]> accepted input 🌊\n"

        undecided, undecided_input, undecided_output = _resource_free_driver(
            mock.Mock(return_value="unused")
        )
        with self.assertRaisesRegex(RuntimeError, "requires a selected input mode"):
            undecided.write_accepted_input(payload)
        self.assertEqual(undecided_input.mock_calls, [])
        self.assertEqual(undecided_output.mock_calls, [])

        fallback, fallback_input, fallback_output = _resource_free_driver(
            mock.Mock(return_value="unused")
        )
        fallback._input_latch.latch_fallback()
        fallback.write_accepted_input(payload)
        self.assertEqual(fallback_input.mock_calls, [])
        self.assertEqual(fallback_output.mock_calls, [])

        live, live_input, live_output = _resource_free_driver(
            mock.Mock(return_value="unused")
        )
        live._input_latch.latch_live(_spec(_ByteBackedInput()))
        live_output.write.return_value = len(payload)
        live.write_accepted_input(payload)
        self.assertEqual(live_input.mock_calls, [])
        self.assertEqual(
            live_output.mock_calls,
            [mock.call.write(payload), mock.call.flush()],
        )
        self.assertFalse(
            any("accepted" in name for name in RawTerminalDriver.__slots__)
        )

    def test_first_fallback_is_permanent_and_skips_future_capability_inspection(self) -> None:
        fallback = mock.Mock(side_effect=("first", "second"))
        driver, input_stream, output_stream = _resource_free_driver(fallback)
        with mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=_line_capabilities(),
        ) as capabilities:
            self.assertEqual(driver.read_line("one> "), "first")
            self.assertEqual(driver.read_line("two> "), "second")
            with driver.input_session(), driver.native_input_handoff():
                pass
        self.assertIs(driver._input_latch.mode, TerminalInputMode.FALLBACK)
        capabilities.assert_called_once_with(driver)
        self.assertEqual(input_stream.mock_calls, [])
        self.assertEqual(output_stream.mock_calls, [])
        self.assertEqual(
            fallback.call_args_list,
            [mock.call("one> "), mock.call("two> ")],
        )

    def test_semantic_render_validation_fails_before_mode_selection(self) -> None:
        fallback = mock.Mock(return_value="unused")
        driver, input_stream, output_stream = _resource_free_driver(fallback)
        renderer = mock.Mock(return_value=object())
        with mock.patch.object(
            RawTerminalDriver,
            "capabilities",
            autospec=True,
            return_value=_line_capabilities(72),
        ):
            with self.assertRaisesRegex(
                TypeError,
                "renderer must return a RenderedInputFrame",
            ):
                driver.read_input_line(
                    InputPromptView((), Prompt("Subject")),
                    renderer,
                    lambda _text: (),
                )
        self.assertIs(driver._input_latch.mode, TerminalInputMode.UNDECIDED)
        fallback.assert_not_called()
        self.assertEqual(input_stream.mock_calls, [])
        self.assertEqual(output_stream.mock_calls, [])

    def test_live_capability_allows_resize_only_and_configuration_allows_pendin(
        self,
    ) -> None:
        baseline = _live_capabilities(80)
        self.assertTrue(
            driver_module._same_live_capabilities(
                baseline,
                _live_capabilities(24),
            )
        )
        self.assertFalse(
            driver_module._same_live_capabilities(
                baseline,
                _live_capabilities(80, decorative_sgr=False),
            )
        )

        termios_module = _TermiosIdentity()
        configuration = _configuration(termios_module, local_flags=2)
        resized_and_pending = _configuration(
            termios_module,
            local_flags=2 | termios_module.PENDIN,
            width=24,
        )
        self.assertTrue(
            driver_module._same_terminal_configuration(
                configuration,
                resized_and_pending,
            )
        )
        for changed in (
            _configuration(termios_module, input_descriptor=9),
            _configuration(termios_module, output_descriptor=9),
            _configuration(termios_module, eof_character="\x05"),
            _configuration(termios_module, interrupt_character="\x02"),
            _configuration(termios_module, disabled_control_byte=0),
            _configuration(termios_module, local_flags=4),
            _configuration(_TermiosIdentity()),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(
                    driver_module._same_terminal_configuration(
                        configuration,
                        changed,
                    )
                )

    def test_live_revalidation_rejects_decoder_capability_and_configuration_drift(
        self,
    ) -> None:
        fallback = mock.Mock(return_value="unused")
        driver, _input_stream, _output_stream = _resource_free_driver(fallback)
        first_stream = _ByteBackedInput()
        first_spec = _spec(first_stream)
        baseline = _configuration(_TermiosIdentity())
        with (
            mock.patch.object(
                driver_module,
                "inspect_live_decoder_spec",
                return_value=first_spec,
            ),
            mock.patch.object(
                driver_module,
                "_terminal_configuration",
                return_value=baseline,
            ),
        ):
            capabilities, configuration = driver._prepare_input(
                _live_capabilities()
            )
        self.assertIs(capabilities, driver._live_capabilities)
        self.assertIs(configuration, baseline)
        self.assertIs(driver._input_latch.mode, TerminalInputMode.LIVE)

        with self.assertRaisesRegex(RuntimeError, "capability changed"):
            driver._prepare_input(_live_capabilities(decorative_sgr=False))

        with (
            mock.patch.object(
                driver_module,
                "inspect_live_decoder_spec",
                return_value=_spec(_ByteBackedInput()),
            ),
            mock.patch.object(
                driver_module,
                "_terminal_configuration",
                return_value=baseline,
            ),
            self.assertRaisesRegex(RuntimeError, "decoder changed"),
        ):
            driver._prepare_input(_live_capabilities())

        changed_configuration = _configuration(
            baseline.termios_module,
            input_descriptor=9,
        )
        with (
            mock.patch.object(
                driver_module,
                "inspect_live_decoder_spec",
                return_value=_spec(first_stream),
            ),
            mock.patch.object(
                driver_module,
                "_terminal_configuration",
                return_value=changed_configuration,
            ),
            self.assertRaisesRegex(RuntimeError, "configuration changed"),
        ):
            driver._prepare_input(_live_capabilities())
        fallback.assert_not_called()
        with _session_driver() as (driver, termios, stream, _output, fallback):
            original = list(termios.attributes)
            with driver.input_session():
                guarded = list(termios.attributes)
                for failure in (KeyboardInterrupt("native interrupt"), EOFError("native eof"), ValueError("native failure")):
                    with self.assertRaises(type(failure)) as raised:
                        with driver.native_input_handoff():
                            raise failure
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(termios.attributes, guarded)
                termios.attributes[3] |= termios.ECHO
                with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                    driver.read_line("drift> ")
                self.assertEqual(stream.calls, [])
            self.assertEqual(termios.attributes, original)
            with driver.input_session():
                with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                    with driver.native_input_handoff():
                        termios.attributes[3] |= 64
                with self.assertRaisesRegex(RuntimeError, "suspended"):
                    driver.read_line("uncertain handoff> ")
                self.assertEqual(stream.calls, [])
            self.assertEqual(termios.attributes, original)
            fallback.assert_not_called()
        for disposition in (signal.SIG_IGN, lambda *_args: None):
            with _session_driver() as (driver, _termios, _stream, _output, _fallback), _signal_state(disposition) as signals:
                original_signals = dict(signals)
                with driver.input_session():
                    with driver.native_input_handoff():
                        self.assertEqual(signals, original_signals)
                self.assertEqual(signals, original_signals)
        for replacement in (signal.SIG_DFL, lambda *_args: None):
            with _session_driver() as (driver, termios, _stream, _output, _fallback), _signal_state(signal.SIG_DFL) as signals:
                original, original_signals = list(termios.attributes), dict(signals)
                with self.assertRaisesRegex(RuntimeError, "interactive terminal signal restoration failed"):
                    with driver.input_session(), driver.native_input_handoff():
                        signals[signal.SIGTERM] = replacement
                self.assertEqual(signals, {**original_signals, signal.SIGTERM: replacement})
                self.assertEqual(termios.attributes, original)


if __name__ == "__main__":
    unittest.main()
