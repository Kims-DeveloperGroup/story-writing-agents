"""Own irreversible terminal input selection and persistent byte decoding."""

from __future__ import annotations

import codecs
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum


_SAFE_ENCODINGS = frozenset({"ascii", "utf-8", "iso8859-1"})
_MAX_PENDING_CODEPOINTS = 4
_STRICT_HANDLER = codecs.lookup_error("strict")
_SURROGATEESCAPE_HANDLER = codecs.lookup_error("surrogateescape")


class TerminalInputMode(StrEnum):
    """The irreversible source selected by one terminal driver."""

    UNDECIDED = "undecided"
    LIVE = "live"
    FALLBACK = "fallback"


def _reader_identity(reader: Callable[[int], object]) -> tuple[object | None, object]:
    """Return a stable identity for Python or built-in bound read methods."""

    owner = getattr(reader, "__self__", None)
    function = getattr(reader, "__func__", None)
    if owner is not None and function is not None:
        return owner, function
    if owner is not None:
        name = getattr(reader, "__name__", None)
        return owner, (type(reader), name)
    return None, reader


@dataclass(frozen=True, slots=True, eq=False)
class LiveDecoderSpec:
    """One canonical byte source, codec, and built-in error policy."""

    read_one_byte: Callable[[int], object]
    encoding: str
    errors: str
    _reader_owner: object | None
    _reader_token: object

    def __post_init__(self) -> None:
        if not callable(self.read_one_byte):
            raise TypeError("read_one_byte must be callable")
        if self.encoding not in _SAFE_ENCODINGS:
            raise ValueError("encoding is not safe for live decoding")
        if self.errors not in {"strict", "surrogateescape"}:
            raise ValueError("errors is not safe for live decoding")

    def matches(self, other: LiveDecoderSpec) -> bool:
        """Return whether another observation identifies the same live source."""

        return (
            type(other) is LiveDecoderSpec
            and self.encoding == other.encoding
            and self.errors == other.errors
            and self._reader_owner is other._reader_owner
            and (
                self._reader_token is other._reader_token
                or (
                    self._reader_owner is not None
                    and self._reader_token == other._reader_token
                )
            )
        )

    def decode_control_byte(self, value: int) -> str:
        """Decode and round-trip one configured terminal control byte."""

        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("terminal control must be one byte")
        raw = bytes((value,))
        decoder = codecs.getincrementaldecoder(self.encoding)(errors=self.errors)
        character = decoder.decode(raw, final=True)
        if type(character) is not str or len(character) != 1:
            raise ValueError("terminal control is not one decoded code point")
        encoder = codecs.getincrementalencoder(self.encoding)(errors=self.errors)
        if encoder.encode(character, final=True) != raw:
            raise ValueError("terminal control does not round-trip through its codec")
        return character


def inspect_live_decoder_spec(input_stream: object) -> LiveDecoderSpec | None:
    """Inspect one safe byte-backed decoder without reading the stream."""

    byte_stream = getattr(input_stream, "buffer", None)
    read_one_byte = getattr(byte_stream, "read1", None)
    if not callable(read_one_byte):
        return None

    encoding = getattr(input_stream, "encoding", None)
    if not isinstance(encoding, str) or not encoding:
        raise ValueError("byte-backed input requires a declared encoding")
    canonical_encoding = codecs.lookup(encoding).name
    if canonical_encoding not in _SAFE_ENCODINGS:
        raise ValueError("byte-backed input codec is not safe for live decoding")

    errors = getattr(input_stream, "errors", None)
    if errors is None or errors == "":
        errors = "strict"
    if not isinstance(errors, str):
        raise ValueError("byte-backed input requires a declared error handler")
    handler = codecs.lookup_error(errors)
    if handler is _STRICT_HANDLER:
        canonical_errors = "strict"
    elif handler is _SURROGATEESCAPE_HANDLER:
        canonical_errors = "surrogateescape"
    else:
        raise ValueError("byte-backed input error handler is not safe")

    owner, token = _reader_identity(read_one_byte)
    return LiveDecoderSpec(
        read_one_byte=read_one_byte,
        encoding=canonical_encoding,
        errors=canonical_errors,
        _reader_owner=owner,
        _reader_token=token,
    )


class LiveCharacterReader:
    """Persistently decode exactly one byte at a time into one code point."""

    __slots__ = (
        "_decoder",
        "_eof",
        "_failure",
        "_pending",
        "_reading",
        "spec",
    )

    def __init__(self, spec: LiveDecoderSpec) -> None:
        if type(spec) is not LiveDecoderSpec:
            raise TypeError("spec must be a LiveDecoderSpec")
        self.spec = spec
        self._decoder = self._new_decoder()
        self._pending: deque[str] = deque()
        self._eof = False
        self._failure: BaseException | None = None
        self._reading = False

    @property
    def has_pending_codepoint(self) -> bool:
        """Return whether a decoded suffix can be consumed without waiting."""

        return bool(self._pending)

    def read_character(self) -> str:
        """Return one decoded code point or a stable empty string at clean EOF."""

        if self._failure is not None:
            raise self._failure
        if self._reading:
            failure = RuntimeError("live character input is reentrant")
            self._failure = failure
            raise failure
        if self._pending:
            return self._pending.popleft()
        if self._eof:
            return ""

        self._reading = True
        try:
            while not self._pending and not self._eof:
                chunk = self.spec.read_one_byte(1)
                if type(chunk) is not bytes:
                    raise TypeError(
                        "live input byte reader must return exactly one byte"
                    )
                if chunk == b"":
                    decoded = self._decoder.decode(b"", final=True)
                    self._append_decoded(decoded)
                    self._eof = True
                    break
                if len(chunk) != 1:
                    raise TypeError(
                        "live input byte reader must return exactly one byte"
                    )
                decoded = self._decoder.decode(chunk, final=False)
                self._append_decoded(decoded)
        except KeyboardInterrupt:
            raise
        except BaseException as failure:
            self._failure = failure
            raise
        finally:
            self._reading = False

        if self._pending:
            return self._pending.popleft()
        return ""

    def reset_incomplete(self) -> None:
        """Drop only incomplete interaction-local decoding after an interrupt."""

        if self._failure is not None:
            return
        self._pending.clear()
        if not self._eof:
            self._decoder = self._new_decoder()

    def _new_decoder(self) -> object:
        return codecs.getincrementaldecoder(self.spec.encoding)(
            errors=self.spec.errors
        )

    def _append_decoded(self, decoded: object) -> None:
        if type(decoded) is not str:
            raise TypeError("live input decoder must return text")
        if len(self._pending) + len(decoded) > _MAX_PENDING_CODEPOINTS:
            raise RuntimeError("live input decoder suffix exceeds its bound")
        self._pending.extend(decoded)


class TerminalInputLatch:
    """Own one driver's irreversible mode, live reader, and input exclusion."""

    __slots__ = ("_active", "_mode", "_reader", "_spec")

    def __init__(self) -> None:
        self._mode = TerminalInputMode.UNDECIDED
        self._spec: LiveDecoderSpec | None = None
        self._reader: LiveCharacterReader | None = None
        self._active = False

    @property
    def mode(self) -> TerminalInputMode:
        return self._mode

    @property
    def live_reader(self) -> LiveCharacterReader:
        """Return the reader only after LIVE ownership is established."""

        if self._mode is not TerminalInputMode.LIVE or self._reader is None:
            raise RuntimeError("terminal input is not live-owned")
        return self._reader

    @contextmanager
    def input_operation(self) -> Iterator[None]:
        """Reject synchronous reentry until one complete input call returns."""

        if self._active:
            raise RuntimeError("terminal input is already active")
        self._active = True
        try:
            yield
        finally:
            self._active = False

    def latch_live(self, spec: LiveDecoderSpec) -> LiveCharacterReader:
        """Select LIVE exactly once after the driver completed safe preflight."""

        if type(spec) is not LiveDecoderSpec:
            raise TypeError("spec must be a LiveDecoderSpec")
        if self._mode is not TerminalInputMode.UNDECIDED:
            raise RuntimeError("terminal input mode is already selected")
        reader = LiveCharacterReader(spec)
        self._spec = spec
        self._reader = reader
        self._mode = TerminalInputMode.LIVE
        return reader

    def require_live(self, spec: LiveDecoderSpec) -> LiveCharacterReader:
        """Return the persistent reader or reject source/decoder drift."""

        if self._mode is not TerminalInputMode.LIVE:
            raise RuntimeError("terminal input is not live-owned")
        if self._spec is None or not self._spec.matches(spec):
            raise RuntimeError("live terminal input decoder changed")
        assert self._reader is not None
        return self._reader

    def latch_fallback(self) -> None:
        """Select permanent ordinary-callback input immediately before use."""

        if self._mode is TerminalInputMode.LIVE:
            raise RuntimeError("live terminal input cannot switch to fallback")
        self._mode = TerminalInputMode.FALLBACK

    def reset_after_interrupt(self) -> None:
        """Reset incomplete live decoding without changing the selected mode."""

        if self._reader is not None:
            self._reader.reset_incomplete()
