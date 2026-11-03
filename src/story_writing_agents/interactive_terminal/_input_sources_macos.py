"""Select enabled macOS keyboard sources through native TIS/CoreFoundation."""

from __future__ import annotations

import ctypes

from ._input_sources import InputSourceState


_UTF8 = 0x08000100


class MacOSInputSourceAdapter:
    """Own native bindings, never retained native input-source objects."""

    def __init__(self) -> None:
        self._tis = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        self._cf = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        pointer = ctypes.c_void_p
        index = ctypes.c_long
        self._bind(self._tis, "TISCopyCurrentKeyboardInputSource", [], pointer)
        self._bind(self._tis, "TISCopyInputSourceForLanguage", [pointer], pointer)
        self._bind(self._tis, "TISCreateInputSourceList", [pointer, ctypes.c_bool], pointer)
        self._bind(self._tis, "TISGetInputSourceProperty", [pointer, pointer], pointer)
        self._bind(self._tis, "TISSelectInputSource", [pointer], ctypes.c_int32)
        self._bind(self._cf, "CFRelease", [pointer], None)
        self._bind(self._cf, "CFArrayGetCount", [pointer], index)
        self._bind(self._cf, "CFArrayGetValueAtIndex", [pointer, index], pointer)
        self._bind(self._cf, "CFBooleanGetValue", [pointer], ctypes.c_bool)
        self._bind(self._cf, "CFStringGetLength", [pointer], index)
        self._bind(
            self._cf, "CFStringGetMaximumSizeForEncoding",
            [index, ctypes.c_uint32], index,
        )
        self._bind(
            self._cf, "CFStringGetCString",
            [pointer, pointer, index, ctypes.c_uint32], ctypes.c_bool,
        )
        self._bind(
            self._cf, "CFStringCreateWithCString",
            [pointer, ctypes.c_char_p, ctypes.c_uint32], pointer,
        )
        self._keys = {
            name: pointer.in_dll(self._tis, "kTISProperty" + name).value
            for name in (
                "InputSourceID", "InputSourceLanguages", "InputSourceIsEnabled",
                "InputSourceIsSelectCapable", "InputSourceIsASCIICapable",
            )
        }

    @staticmethod
    def _bind(library, name, arguments, result) -> None:
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result

    def _property(self, source, name):
        return self._tis.TISGetInputSourceProperty(source, self._keys[name])

    def _boolean(self, source, name) -> bool:
        value = self._property(source, name)
        return bool(value and self._cf.CFBooleanGetValue(value))

    def _string(self, value) -> str | None:
        if not value:
            return None
        length = self._cf.CFStringGetLength(value)
        if not 0 <= length <= 4096:
            return None
        size = self._cf.CFStringGetMaximumSizeForEncoding(length, _UTF8) + 1
        if not 1 <= size <= 65536:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not self._cf.CFStringGetCString(value, buffer, size, _UTF8):
            return None
        return buffer.value.decode("utf-8")

    def _state(self, source) -> InputSourceState | None:
        if not source or not (
            self._boolean(source, "InputSourceIsEnabled")
            and self._boolean(source, "InputSourceIsSelectCapable")
        ):
            return None
        identifier = self._string(self._property(source, "InputSourceID"))
        if not identifier:
            return None
        languages = self._property(source, "InputSourceLanguages")
        english = False
        if languages:
            count = self._cf.CFArrayGetCount(languages)
            if 0 <= count <= 128:
                for index in range(count):
                    language = self._string(
                        self._cf.CFArrayGetValueAtIndex(languages, index)
                    )
                    if language and language.split("-", 1)[0].lower() == "en":
                        english = self._boolean(source, "InputSourceIsASCIICapable")
                        break
        return InputSourceState((identifier,), english)

    def current_source(self) -> InputSourceState | None:
        source = self._tis.TISCopyCurrentKeyboardInputSource()
        if not source:
            return None
        try:
            return self._state(source)
        finally:
            self._cf.CFRelease(source)

    def default_source(self) -> InputSourceState | None:
        # TIS exposes current/MRU sources, not an independent saved OS default.
        return None

    def english_source(self) -> InputSourceState | None:
        current = self.current_source()
        if current is not None and current.english:
            return current
        language = self._cf.CFStringCreateWithCString(None, b"en", _UTF8)
        if not language:
            return None
        source = None
        try:
            source = self._tis.TISCopyInputSourceForLanguage(language)
            state = self._state(source)
            return state if state is not None and state.english else None
        finally:
            if source:
                self._cf.CFRelease(source)
            self._cf.CFRelease(language)

    def select_source(self, source: InputSourceState) -> None:
        if len(source.identity) != 1:
            raise ValueError("invalid macOS input-source identity")
        sources = self._tis.TISCreateInputSourceList(None, False)
        if not sources:
            raise OSError("enabled macOS input sources are unavailable")
        try:
            count = self._cf.CFArrayGetCount(sources)
            if not 0 <= count <= 4096:
                raise OSError("invalid macOS input-source list")
            for index in range(count):
                candidate = self._cf.CFArrayGetValueAtIndex(sources, index)
                state = self._state(candidate)
                if state is not None and state.identity == source.identity:
                    if self._tis.TISSelectInputSource(candidate) != 0:
                        raise OSError("macOS input-source selection failed")
                    return
            raise OSError("saved macOS input source is no longer enabled")
        finally:
            self._cf.CFRelease(sources)
