"""Immutable semantic terminal components and interaction value contracts."""

from __future__ import annotations

import unicodedata
from dataclasses import KW_ONLY, dataclass, field
from enum import StrEnum
from typing import TypeAlias


class SemanticRole(StrEnum):
    """Meaning that may receive redundant fixed-theme decoration."""

    DEFAULT = "default"
    EMPHASIS = "emphasis"
    MUTED = "muted"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    COMMAND = "command"
    AGENT = "agent"


class ViewState(StrEnum):
    """Explicit view or control state independent of presentation style."""

    IDLE = "idle"
    FOCUSED = "focused"
    OPEN = "open"
    HIGHLIGHTED = "highlighted"
    SELECTED = "selected"
    DISABLED = "disabled"
    LOADING = "loading"
    EMPTY = "empty"
    ERROR = "error"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class CapabilityProfile(StrEnum):
    """Observable terminal rendering and input capability profile."""

    ANSI = "ansi"
    PLAIN = "plain"
    LINE = "line"


class KeyKind(StrEnum):
    """Normalized key events produced only by the raw terminal driver."""

    TEXT = "text"
    ENTER = "enter"
    BACKSPACE = "backspace"
    DELETE = "delete"
    LEFT = "left"
    RIGHT = "right"
    HOME = "home"
    END = "end"
    UP = "up"
    DOWN = "down"
    TAB = "tab"
    SHIFT_TAB = "shift_tab"
    ESCAPE = "escape"
    INTERRUPT = "interrupt"
    EOF = "eof"


class ControlOutcomeKind(StrEnum):
    """Result that may cross from a pure control to its caller."""

    COMMIT = "commit"
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    EOF = "eof"
    REJECTED = "rejected"


def _require_text(name: str, value: object, *, allow_blank: bool = False) -> str:
    """Return one exact string after validating its semantic presence."""

    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not allow_blank and (not value or value.isspace()):
        raise ValueError(f"{name} must not be blank")
    return value


def _require_optional_text(name: str, value: object) -> str | None:
    """Validate optional semantic text without normalizing it."""

    if value is None:
        return None
    return _require_text(name, value)


def _require_state(value: object) -> ViewState:
    """Require one exact view-state enum value."""

    if not isinstance(value, ViewState):
        raise TypeError("state must be a ViewState")
    return value


@dataclass(frozen=True, slots=True)
class TerminalCapabilities:
    """One immutable independently observed input/output capability result."""

    profile: CapabilityProfile
    width: int
    _: KW_ONLY
    input_tty: bool
    output_tty: bool
    raw_mode: bool
    cursor_controls: bool
    decorative_sgr: bool

    def __post_init__(self) -> None:
        """Require a correlated, bounded capability description."""

        if not isinstance(self.profile, CapabilityProfile):
            raise TypeError("profile must be a CapabilityProfile")
        if type(self.width) is not int:
            raise TypeError("width must be an integer")
        if self.width <= 0:
            raise ValueError("width must be positive")
        for name in (
            "input_tty",
            "output_tty",
            "raw_mode",
            "cursor_controls",
            "decorative_sgr",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if self.raw_mode and not (self.input_tty and self.output_tty):
            raise ValueError("raw mode requires input and output TTYs")
        if self.cursor_controls and not self.raw_mode:
            raise ValueError("cursor controls require raw mode")
        if self.decorative_sgr and self.profile is not CapabilityProfile.ANSI:
            raise ValueError("decorative SGR requires the ANSI profile")
        if self.profile is CapabilityProfile.ANSI and not self.decorative_sgr:
            raise ValueError("the ANSI profile requires decorative SGR")
        if self.profile is CapabilityProfile.PLAIN and (
            not self.raw_mode
            or not self.cursor_controls
            or self.decorative_sgr
        ):
            raise ValueError(
                "the capable plain profile requires raw cursor support "
                "without decorative SGR"
            )
        if self.profile is CapabilityProfile.LINE and (
            self.raw_mode or self.cursor_controls or self.decorative_sgr
        ):
            raise ValueError("line fallback cannot own terminal controls")


@dataclass(frozen=True, slots=True)
class KeyEvent:
    """One normalized key event with text only for literal input."""

    kind: KeyKind
    _: KW_ONLY
    text: str | None = None

    def __post_init__(self) -> None:
        """Correlate the key kind and optional literal code point."""

        if not isinstance(self.kind, KeyKind):
            raise TypeError("kind must be a KeyKind")
        if self.kind is KeyKind.TEXT:
            if type(self.text) is not str or len(self.text) != 1:
                raise ValueError("text keys require one code point")
        elif self.text is not None:
            raise ValueError(f"{self.kind.value} keys do not carry text")


@dataclass(frozen=True, slots=True)
class LineInputState:
    """Immutable editable one-line buffer state for pure transitions."""

    buffer: str = ""
    cursor: int = 0

    def __post_init__(self) -> None:
        """Require the cursor to identify one buffer boundary."""

        if type(self.buffer) is not str:
            raise TypeError("buffer must be a string")
        if type(self.cursor) is not int:
            raise TypeError("cursor must be an integer")
        if self.cursor < 0 or self.cursor > len(self.buffer):
            raise ValueError("cursor must identify a buffer boundary")


@dataclass(frozen=True, slots=True)
class ComposerState:
    """Retain exact submitted lines and their derived joined counts."""

    lines: tuple[str, ...] = ()
    content: str = field(init=False)
    line_count: int = field(init=False)
    character_count: int = field(init=False)

    def __post_init__(self) -> None:
        """Validate one-line entries and derive immutable composition facts."""

        if type(self.lines) is not tuple:
            raise TypeError("lines must be a tuple")
        for line in self.lines:
            if type(line) is not str:
                raise TypeError("composer lines must be strings")
            if "\r" in line or "\n" in line:
                raise ValueError("composer lines must not contain CR or LF")
        content = "\n".join(self.lines)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "line_count", len(self.lines))
        object.__setattr__(self, "character_count", len(content))


@dataclass(frozen=True, slots=True)
class MainInputHistoryState:
    """Immutable bounded process-local main-command history."""

    entries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Require at most 100 exact nonblank entries in stored order."""

        if type(self.entries) is not tuple:
            raise TypeError("entries must be a tuple")
        if len(self.entries) > 100:
            raise ValueError("entries must contain at most 100 values")
        for entry in self.entries:
            if type(entry) is not str:
                raise TypeError("history entries must be strings")
            if not entry or entry.isspace():
                raise ValueError("history entries must not be blank")


@dataclass(frozen=True, slots=True)
class MainInputControlState:
    """Own one main-input line plus its inactive or active history cursor."""

    line_state: LineInputState = LineInputState()
    history_index: int | None = None
    saved_draft: LineInputState | None = None
    overlay_open: bool = False

    def __post_init__(self) -> None:
        """Correlate the line, navigation cursor, draft, and overlay flag."""

        if type(self.line_state) is not LineInputState:
            raise TypeError("line_state must be a LineInputState")
        if self.history_index is not None:
            if type(self.history_index) is not int:
                raise TypeError("history_index must be an integer or None")
            if self.history_index < 0:
                raise ValueError("history_index must not be negative")
        if (
            self.saved_draft is not None
            and type(self.saved_draft) is not LineInputState
        ):
            raise TypeError("saved_draft must be a LineInputState or None")
        if (self.history_index is None) != (self.saved_draft is None):
            raise ValueError(
                "history_index and saved_draft must both be active or inactive"
            )
        if type(self.overlay_open) is not bool:
            raise TypeError("overlay_open must be a bool")


@dataclass(frozen=True, slots=True)
class SlashCommandSuggestion:
    """Identify one displayed slash token and its canonical registry token."""

    token: str
    canonical_token: str

    def __post_init__(self) -> None:
        """Validate slash-token identity without normalization or I/O."""

        for name, value in (
            ("token", self.token),
            ("canonical_token", self.canonical_token),
        ):
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value.startswith("/"):
                raise ValueError(f"{name} must begin with a slash")


@dataclass(frozen=True, slots=True)
class ControlOutcome:
    """One commit or terminal event returned by a pure control transition."""

    kind: ControlOutcomeKind
    _: KW_ONLY
    text: str | None = None

    def __post_init__(self) -> None:
        """Require text only for a committed control value."""

        if not isinstance(self.kind, ControlOutcomeKind):
            raise TypeError("kind must be a ControlOutcomeKind")
        if self.kind is ControlOutcomeKind.COMMIT:
            if type(self.text) is not str:
                raise TypeError("commit outcomes require text")
        elif self.text is not None:
            raise ValueError(f"{self.kind.value} outcomes do not carry text")


@dataclass(frozen=True, slots=True)
class Field:
    """Associate one visible semantic label with one exact value."""

    label: str
    value: str
    _: KW_ONLY
    role: SemanticRole = SemanticRole.DEFAULT
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("label", self.label)
        _require_text("value", self.value, allow_blank=True)
        if not isinstance(self.role, SemanticRole):
            raise TypeError("role must be a SemanticRole")
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Prompt:
    """Describe one active input request without performing input."""

    name: str
    _: KW_ONLY
    scope: str | None = None
    instruction: str | None = None
    default: str | None = None
    state: ViewState = ViewState.FOCUSED

    def __post_init__(self) -> None:
        _require_text("name", self.name)
        _require_optional_text("scope", self.scope)
        _require_optional_text("instruction", self.instruction)
        _require_optional_text("default", self.default)
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Notice:
    """Communicate explicit status or guidance without color-only meaning."""

    text: str
    _: KW_ONLY
    label: str | None = None
    role: SemanticRole = SemanticRole.INFO
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("text", self.text)
        _require_optional_text("label", self.label)
        if not isinstance(self.role, SemanticRole):
            raise TypeError("role must be a SemanticRole")
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Message:
    """Preserve one attributed content boundary."""

    sender: str
    content: str
    _: KW_ONLY
    role: SemanticRole = SemanticRole.DEFAULT
    state: ViewState = ViewState.COMPLETED

    def __post_init__(self) -> None:
        _require_text("sender", self.sender)
        _require_text("content", self.content, allow_blank=True)
        if not isinstance(self.role, SemanticRole):
            raise TypeError("role must be a SemanticRole")
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Record:
    """Present one atomic domain identity, metadata tuple, and optional content."""

    identity: str
    _: KW_ONLY
    title: str | None = None
    fields: tuple[Field, ...] = ()
    content: str | None = None
    role: SemanticRole = SemanticRole.DEFAULT
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("identity", self.identity)
        _require_optional_text("title", self.title)
        if type(self.fields) is not tuple or any(
            type(item) is not Field for item in self.fields
        ):
            raise TypeError("fields must be a tuple of Field values")
        if self.content is not None and type(self.content) is not str:
            raise TypeError("content must be a string or None")
        if not isinstance(self.role, SemanticRole):
            raise TypeError("role must be a SemanticRole")
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class ListView:
    """Present ordered records with explicit count, position, and empty meaning."""

    title: str
    items: tuple[Record | Field, ...]
    _: KW_ONLY
    total_count: int | None = None
    position: int | None = None
    empty_notice: Notice | None = None
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("title", self.title)
        if type(self.items) is not tuple or any(
            type(item) not in {Record, Field} for item in self.items
        ):
            raise TypeError("items must be a tuple of Record or Field values")
        total = len(self.items) if self.total_count is None else self.total_count
        if type(total) is not int or total < len(self.items):
            raise ValueError("total_count must cover every visible item")
        if self.position is not None and (
            type(self.position) is not int
            or self.position <= 0
            or total == 0
            or self.position > total
        ):
            raise ValueError("position must identify one total item")
        if self.empty_notice is not None and type(self.empty_notice) is not Notice:
            raise TypeError("empty_notice must be a Notice or None")
        if not self.items and self.empty_notice is None:
            raise ValueError("empty lists require an explicit empty notice")
        _require_state(self.state)


SectionItem: TypeAlias = Field | Notice | Message | Record | ListView


@dataclass(frozen=True, slots=True)
class Section:
    """Divide a bounded view into one named logical region."""

    title: str
    items: tuple[SectionItem, ...]
    _: KW_ONLY
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("title", self.title)
        if type(self.items) is not tuple or any(
            type(item) not in {Field, Notice, Message, Record, ListView}
            for item in self.items
        ):
            raise TypeError("items contain an unsupported section component")
        _require_state(self.state)


PanelItem: TypeAlias = Section | Field | Notice | Message | Record | ListView


@dataclass(frozen=True, slots=True)
class Panel:
    """Group one bounded related view without claiming the screen."""

    title: str
    items: tuple[PanelItem, ...]
    _: KW_ONLY
    state: ViewState = ViewState.IDLE

    def __post_init__(self) -> None:
        _require_text("title", self.title)
        if type(self.items) is not tuple or any(
            type(item) not in {Section, Field, Notice, Message, Record, ListView}
            for item in self.items
        ):
            raise TypeError("items contain an unsupported panel component")
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Choice:
    """One stable selectable semantic option used by palettes and pickers."""

    identity: str
    label: str
    _: KW_ONLY
    description: str | None = None
    enabled: bool = True
    disabled_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text("identity", self.identity)
        _require_text("label", self.label)
        _require_optional_text("description", self.description)
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a bool")
        _require_optional_text("disabled_reason", self.disabled_reason)
        if self.enabled and self.disabled_reason is not None:
            raise ValueError("enabled choices cannot carry a disabled reason")


@dataclass(frozen=True, slots=True)
class SuggestionPalette:
    """Describe noncommitting candidates anchored to an editable buffer."""

    buffer: str
    candidates: tuple[Choice, ...]
    _: KW_ONLY
    highlighted_id: str | None = None
    state: ViewState = ViewState.OPEN

    def __post_init__(self) -> None:
        _require_text("buffer", self.buffer, allow_blank=True)
        _validate_choices(self.candidates, self.highlighted_id)
        _require_state(self.state)


@dataclass(frozen=True, slots=True)
class Picker:
    """Describe one single-select control without committing its highlight."""

    prompt: Prompt
    choices: tuple[Choice, ...]
    _: KW_ONLY
    highlighted_id: str | None = None
    selected_id: str | None = None
    notice: Notice | None = None
    state: ViewState = ViewState.OPEN

    def __post_init__(self) -> None:
        if type(self.prompt) is not Prompt:
            raise TypeError("prompt must be a Prompt")
        identities = _validate_choices(self.choices, self.highlighted_id)
        if self.selected_id is not None:
            if self.selected_id not in identities:
                raise ValueError("selected_id must identify one choice")
            selected = next(
                item for item in self.choices if item.identity == self.selected_id
            )
            if not selected.enabled:
                raise ValueError("selected_id must identify one enabled choice")
        if self.notice is not None and type(self.notice) is not Notice:
            raise TypeError("notice must be a Notice or None")
        _require_state(self.state)


InputContext: TypeAlias = Panel | Section | Field | Notice


@dataclass(frozen=True, slots=True)
class InputPromptView:
    """Order semantic input context before one focused active prompt."""

    context: tuple[InputContext, ...]
    prompt: Prompt

    def __post_init__(self) -> None:
        """Require only approved context components and one focused prompt."""

        if type(self.context) is not tuple or any(
            type(item) not in {Panel, Section, Field, Notice}
            for item in self.context
        ):
            raise TypeError(
                "context must be a tuple of Panel, Section, Field, or Notice values"
            )
        if type(self.prompt) is not Prompt:
            raise TypeError("prompt must be a Prompt")
        if self.prompt.state is not ViewState.FOCUSED:
            raise ValueError("input prompt must be focused")


def _validate_choices(
    choices: object,
    highlighted_id: object,
) -> frozenset[str]:
    """Validate ordered unique choices and an optional enabled highlight."""

    if type(choices) is not tuple or any(type(item) is not Choice for item in choices):
        raise TypeError("choices must be a tuple of Choice values")
    identities = [item.identity for item in choices]
    if len(set(identities)) != len(identities):
        raise ValueError("choice identities must be unique")
    if highlighted_id is not None:
        _require_text("highlighted_id", highlighted_id)
        matches = [item for item in choices if item.identity == highlighted_id]
        if not matches or not matches[0].enabled:
            raise ValueError("highlighted_id must identify one enabled choice")
    return frozenset(identities)


Component: TypeAlias = (
    Prompt
    | Panel
    | Section
    | Field
    | Notice
    | Message
    | Record
    | ListView
    | SuggestionPalette
    | Picker
)


@dataclass(frozen=True, slots=True)
class RenderedFrame:
    """Pure rendered text plus width and visible-line accounting."""

    text: str
    width: int
    line_count: int

    def __post_init__(self) -> None:
        if type(self.text) is not str:
            raise TypeError("text must be a string")
        if type(self.width) is not int or self.width <= 0:
            raise ValueError("width must be positive")
        if type(self.line_count) is not int or self.line_count <= 0:
            raise ValueError("line_count must be positive")
        if self.line_count != self.text.count("\n") + 1:
            raise ValueError("line_count must match rendered text")


@dataclass(frozen=True, slots=True)
class RenderedInputFrame:
    """Carry trusted and plain equivalent input text with visible accounting."""

    text: str
    plain_text: str
    width: int
    line_count: int

    def __post_init__(self) -> None:
        """Require matching newline topology and a final input-space boundary."""

        if type(self.text) is not str or type(self.plain_text) is not str:
            raise TypeError("rendered input text must be strings")
        if type(self.width) is not int or self.width <= 0:
            raise ValueError("width must be positive")
        if type(self.line_count) is not int or self.line_count <= 0:
            raise ValueError("line_count must be positive")
        if self.text.endswith("\n") or self.plain_text.endswith("\n"):
            raise ValueError("rendered input text must not end with LF")
        if not self.text.endswith(" ") or not self.plain_text.endswith(" "):
            raise ValueError("rendered input text must preserve a trailing space")
        if self.text.count("\n") + 1 != self.line_count:
            raise ValueError("styled line_count must match rendered input text")
        if self.plain_text.count("\n") + 1 != self.line_count:
            raise ValueError("plain line_count must match rendered input text")
        if sanitize_terminal_text(self.plain_text) != self.plain_text:
            raise ValueError("plain input text must not contain unsafe controls")


def sanitize_terminal_text(text: str) -> str:
    """Return display text whose control code points cannot affect a terminal."""

    if not isinstance(text, str):
        raise TypeError("terminal text must be a string")
    rendered: list[str] = []
    for character in text:
        if character == "\n":
            rendered.append(character)
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs"} or character in {"\u2028", "\u2029"}:
            rendered.append(_visible_escape(character))
        else:
            rendered.append(character)
    return "".join(rendered)


def display_character_width(character: str) -> int:
    """Return deterministic terminal cells for one sanitized character."""

    if type(character) is not str or len(character) != 1:
        raise TypeError("character must be one string code point")
    if unicodedata.combining(character) or unicodedata.category(character) in {
        "Cf",
        "Mn",
        "Me",
    }:
        return 0
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 2
    return 1


def _visible_escape(character: str) -> str:
    """Format one unsafe code point as a lowercase visible ASCII escape."""

    value = ord(character)
    if value <= 0xFF:
        return f"\\x{value:02x}"
    if value <= 0xFFFF:
        return f"\\u{value:04x}"
    return f"\\U{value:08x}"


__all__ = [
    "SemanticRole",
    "ViewState",
    "CapabilityProfile",
    "KeyKind",
    "ControlOutcomeKind",
    "TerminalCapabilities",
    "KeyEvent",
    "LineInputState",
    "MainInputHistoryState",
    "MainInputControlState",
    "SlashCommandSuggestion",
    "ControlOutcome",
    "Prompt",
    "Panel",
    "Section",
    "Field",
    "Notice",
    "Message",
    "Record",
    "ListView",
    "Choice",
    "SuggestionPalette",
    "Picker",
    "Component",
    "RenderedFrame",
    "sanitize_terminal_text",
    "display_character_width",
]
