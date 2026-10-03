"""Deterministic contracts for irreversible terminal input ownership."""

from __future__ import annotations

import codecs
import unittest
from collections import deque
from unittest import mock

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

    def test_driver_construction_does_not_inspect_resources(self) -> None:
        fallback = mock.Mock(return_value="unused")
        driver, input_stream, output_stream = _resource_free_driver(fallback)
        self.assertIs(driver._input_latch.mode, TerminalInputMode.UNDECIDED)
        self.assertEqual(input_stream.mock_calls, [])
        self.assertEqual(output_stream.mock_calls, [])
        fallback.assert_not_called()
        driver._termios_provider.assert_not_called()
        driver._os_provider.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
