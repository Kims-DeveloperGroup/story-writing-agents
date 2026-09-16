"""Own raw terminal capability, key, stream, and restoration mechanics."""

from __future__ import annotations

import asyncio
import os
import select as _select
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TextIO, TypeAlias, TypeVar

from ._input_lifecycle import (
    LiveDecoderSpec,
    TerminalInputLatch,
    TerminalInputMode,
    inspect_live_decoder_spec,
)
from ._live_composer import (
    BoundaryProvider,
    CancelAndJoin,
    LiveComposerDeliveryKind,
    LiveComposerInput,
    LiveComposerInputKind,
    LiveComposerResult,
    LiveOperationBoundary,
    deliver_live_composer_input,
)
from ._external_view import (
    _BorrowedLineViewerHost,
    _require_external_view_frame,
    ExternalViewerHost,
    ExternalViewOperation,
)
from ._model import (
    CapabilityProfile,
    CommandCompletionProjection,
    ControlOutcome,
    ControlOutcomeKind,
    InputPromptView,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainCommandControlState,
    MainInputControlState,
    MainInputHistoryState,
    PickerControlState,
    PickerViewport,
    RenderedInputFrame,
    RenderedFrame,
    SlashCommandSuggestion,
    SuggestionPalette,
    TerminalCapabilities,
    display_character_width,
    sanitize_terminal_text,
)
from ._reference_activation import (
    ReferenceActivationKind,
    ReferenceActivationRequest,
    ReferenceInputFailure,
    ReferenceInputFailureKind,
    ReferenceInputProjection,
    replace_reference_completion,
    replace_reference_range,
    selected_reference_request,
)
from ._viewer_model import (
    TextViewerExitKind,
    TextViewerResult,
    TextViewerState,
    TextViewerViewport,
    dispose_text_viewer,
)

try:
    import termios as _termios
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    _termios = None


_SUGGESTION_HEADER = "[suggestions]"
_CARRIAGE_RETURN = "\r"
_CLEAR_TO_END = "\x1b[J"
_CURSOR_UP = "\x1b[A"
_CURSOR_RIGHT = "\x1b[C"
_SGR_RESET = "\x1b[0m"
_DEFAULT_TERMINAL_WIDTH = 80
_MAX_ESCAPE_SEQUENCE_LENGTH = 64
_ESCAPE_LOOKAHEAD_SECONDS = 0.05
_DEFAULT_TERMIOS = object()
_PROGRESS_FRAME_COUNT = 14
_LIVE_COMPOSER_POLL_SECONDS = 0.025
_PROGRESS_INTERVAL_SECONDS = 0.08
_PROGRESS_SGR_FRAMES = (
    "\x1b[1;36m",
    "\x1b[1;34m",
    "\x1b[1;35m",
    "\x1b[1;31m",
    "\x1b[1;33m",
    "\x1b[1;32m",
    "\x1b[1;36m",
    "\x1b[1;34m",
    "\x1b[1;36m",
    "\x1b[1;32m",
    "\x1b[1;33m",
    "\x1b[1;31m",
    "\x1b[1;35m",
    "\x1b[1;34m",
)
_STARTUP_WELCOME_FRAME_COUNT = 4
_STARTUP_WELCOME_INTERVAL_SECONDS = 0.4
_STARTUP_NOTICE_DURATION_SECONDS = 0.8
_REDUCED_MOTION_ENVIRONMENT_KEY = "STORY_REDUCED_MOTION"
_DEFAULT_VIEWER_HEIGHT = 24
_MAX_VIEWER_HEIGHT = 200
_VIEWER_SCREEN_ENTER = "\x1b[?1049h\x1b[2J\x1b[H"
_VIEWER_SCREEN_REDRAW = "\x1b[H\x1b[2J"
_VIEWER_SCREEN_EXIT = "\x1b[?1049l"
_T = TypeVar("_T")


class AdvisoryProgressError(RuntimeError):
    """Mark a terminal-owned indicator failure for fatal controller cleanup."""


class _DiscardingEscapeSequence:
    """Identify bounded consumption of one overlong escape sequence."""

    __slots__ = ()


_DISCARDING_ESCAPE_SEQUENCE = _DiscardingEscapeSequence()
_EscapeSequenceState: TypeAlias = str | _DiscardingEscapeSequence | None


@dataclass(slots=True)
class _LiveEditorState:
    """Retain only call-local escape decoding and redraw position."""

    escape_sequence: _EscapeSequenceState = None
    rendered_cursor_row: int = 0


@dataclass(slots=True)
class _StartupFrameState:
    """Track one displayed or attempted startup frame for exact cleanup."""

    physical_line_count: int = 0
    cursor_below_frame: bool = False


@dataclass(frozen=True, slots=True)
class _TerminalConfiguration:
    """Capture one validated POSIX terminal mode and its exact restoration."""

    input_descriptor: int
    output_descriptor: int
    original_attributes: list[object]
    editor_attributes: list[object]
    width: int
    eof_character: str | None
    interrupt_character: str | None
    disabled_control_byte: int
    termios_module: object


class _RawExternalViewerHost:
    """Implement one borrowed viewer host inside the raw driver's lifecycle."""

    __slots__ = (
        "_capabilities",
        "_closed",
        "_configuration",
        "_driver",
    )

    def __init__(
        self,
        driver: RawTerminalDriver,
        capabilities: TerminalCapabilities,
        configuration: _TerminalConfiguration,
    ) -> None:
        self._driver = driver
        self._capabilities = capabilities
        self._configuration = configuration
        self._closed = False

    def terminal_size(self) -> tuple[int, int]:
        if self._closed:
            raise RuntimeError("external viewer host is closed")
        return self._driver._text_viewer_geometry(self._capabilities.width)

    def color_enabled(self) -> bool:
        return self._capabilities.decorative_sgr

    def present(self, frame: str) -> None:
        if self._closed:
            raise RuntimeError("external viewer host is closed")
        validated = _require_external_view_frame(
            frame,
            color_enabled=self._capabilities.decorative_sgr,
        )
        self._driver._write_live_text(f"{_VIEWER_SCREEN_REDRAW}{validated}")

    def read_event(self) -> str | None:
        if self._closed:
            return None
        return self._driver._read_live_external_view_event(
            self._configuration,
        )

    def close_view(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._driver._write_live_text(_VIEWER_SCREEN_REDRAW)


class _PromptCancelled(Exception):
    """Carry one raw Escape through the shared line cleanup boundary."""


_ControlState: TypeAlias = (
    LineInputState | MainInputControlState | MainCommandControlState
)
_ControlTransition: TypeAlias = Callable[
    [_ControlState, KeyEvent],
    tuple[
        _ControlState,
        ControlOutcome | ReferenceActivationRequest | ReferenceInputFailure | None,
    ],
]
_LineTransition: TypeAlias = Callable[
    [LineInputState, KeyEvent],
    tuple[LineInputState, ControlOutcome | None],
]
_PromptTransition: TypeAlias = _LineTransition
_CompletionProvider: TypeAlias = Callable[
    [str, int], CommandCompletionProjection | None
]
_MainCommandTransition: TypeAlias = Callable[
    [
        MainCommandControlState,
        KeyEvent,
        MainInputHistoryState,
        _CompletionProvider,
    ],
    tuple[MainCommandControlState, ControlOutcome | None],
]
_PaletteProjector: TypeAlias = Callable[
    [MainCommandControlState], SuggestionPalette | None
]
_PaletteRenderer: TypeAlias = Callable[
    [SuggestionPalette, TerminalCapabilities], RenderedFrame
]
_HistoryAppend: TypeAlias = Callable[
    [MainInputHistoryState, str], MainInputHistoryState
]
_InputRenderer: TypeAlias = Callable[
    [InputPromptView, TerminalCapabilities], RenderedInputFrame
]
_InputFrameProvider: TypeAlias = Callable[
    [str, int, TerminalCapabilities], RenderedInputFrame
]
_StartupFrameRenderer: TypeAlias = Callable[
    [int, TerminalCapabilities, int], RenderedFrame
]
_PickerTransition: TypeAlias = Callable[
    [PickerControlState, KeyEvent],
    tuple[PickerControlState, ControlOutcome | None],
]
_PickerFallbackTransition: TypeAlias = Callable[
    [PickerControlState, str],
    tuple[PickerControlState, ControlOutcome],
]
_PickerViewportProjector: TypeAlias = Callable[[PickerControlState], PickerViewport]
_PickerViewportRenderer: TypeAlias = Callable[
    [PickerViewport, TerminalCapabilities], RenderedFrame
]
_PickerFallbackRenderer: TypeAlias = Callable[
    [PickerControlState, TerminalCapabilities], RenderedInputFrame
]
_ViewerTransition: TypeAlias = Callable[
    [TextViewerState, KeyEvent],
    tuple[TextViewerState, TextViewerResult | None],
]
_ViewerFallbackTransition: TypeAlias = Callable[
    [TextViewerState, str],
    tuple[TextViewerState, TextViewerResult | None],
]
_ViewerViewportProjector: TypeAlias = Callable[
    [TextViewerState, int], TextViewerViewport
]
_ViewerViewportRenderer: TypeAlias = Callable[
    [TextViewerViewport, TerminalCapabilities], RenderedFrame
]
_ViewerFallbackRenderer: TypeAlias = Callable[
    [TextViewerState, TerminalCapabilities], RenderedInputFrame
]


@dataclass(frozen=True, slots=True)
class ReferenceInputBindings:
    """Bind one call-local reference UI without owning its resources."""

    projection_provider: Callable[[str, int], ReferenceInputProjection | None]
    completion_candidate_matches: Callable[[str, str], bool]
    ranged_picker_selection: Callable[
        [MainCommandControlState, CommandCompletionProjection],
        tuple[bool, str | None],
    ]
    read_text: Callable[[str], str]
    write_failure: Callable[[ReferenceInputFailureKind], None]
    initialize_picker: Callable[[tuple[str, ...], str], PickerControlState]
    picker_transition: _PickerTransition
    picker_fallback_transition: _PickerFallbackTransition
    picker_viewport_projector: _PickerViewportProjector
    picker_viewport_renderer: _PickerViewportRenderer
    picker_fallback_renderer: _PickerFallbackRenderer
    initialize_viewer: Callable[[str, str], TextViewerState]
    viewer_transition: _ViewerTransition
    viewer_fallback_transition: _ViewerFallbackTransition
    viewer_viewport_projector: _ViewerViewportProjector
    viewer_viewport_renderer: _ViewerViewportRenderer
    viewer_fallback_renderer: _ViewerFallbackRenderer

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if not callable(getattr(self, name)):
                raise TypeError(f"{name} must be callable")


def _fallback_reference_projection(
    line: str,
    bindings: ReferenceInputBindings,
) -> ReferenceInputProjection | None:
    """Project the rightmost actionable raw-dollar marker in one line."""

    search_end = len(line)
    while True:
        marker = line.rfind("$", 0, search_end)
        if marker < 0:
            return None
        projection = bindings.projection_provider(line, marker + 1)
        if projection is not None:
            return projection
        search_end = marker


def _live_reference_key_outcome(
    state: MainCommandControlState,
    event: KeyEvent,
    bindings: ReferenceInputBindings | None,
) -> ReferenceActivationRequest | ReferenceInputFailure | None:
    """Project Enter/Tab into a correlated viewer request without modal pick."""

    if bindings is None or event.kind not in {KeyKind.ENTER, KeyKind.TAB}:
        return None
    line = state.main_input.line_state
    projection = bindings.projection_provider(line.buffer, line.cursor)
    if projection is None:
        return None
    if projection.failure is not None:
        if projection.defers_empty_ranged_failure:
            assert projection.completion is not None
            picker_open, _selected_path = bindings.ranged_picker_selection(
                state,
                projection.completion,
            )
            if not picker_open:
                return None
        return ReferenceInputFailure(projection.failure)
    request = projection.request
    if request is None:
        return None
    if request.kind is ReferenceActivationKind.VIEW:
        return request
    completion = projection.completion
    if completion is None:
        return ReferenceInputFailure(ReferenceInputFailureKind.INVALID)
    picker_open, selected_path = bindings.ranged_picker_selection(
        state,
        completion,
    )
    if not picker_open:
        return None
    if selected_path is None:
        return ReferenceInputFailure(ReferenceInputFailureKind.STALE)
    try:
        return selected_reference_request(request, selected_path)
    except ReferenceInputFailure as failure:
        return failure


class RawTerminalDriver:
    """Own one stream pair's live editing and raw terminal resource lifecycle."""

    __slots__ = (
        "_fallback_read_line",
        "_fallback_capabilities",
        "_history_append",
        "_history_state",
        "_input_stream",
        "_input_latch",
        "_line_transition",
        "_live_capabilities",
        "_live_configuration",
        "_main_transition",
        "_os_provider",
        "_output_stream",
        "_progress_active",
        "_progress_frame",
        "_progress_renderer",
        "_progress_rows",
        "_termios_provider",
    )

    def __init__(
        self,
        fallback_read_line: Callable[[str], str],
        input_stream: TextIO,
        output_stream: TextIO,
        line_transition: _LineTransition,
        main_transition: _MainCommandTransition,
        history_append: _HistoryAppend,
        *,
        termios_provider: Callable[[], object | None] | None = None,
        os_provider: Callable[[], object] | None = None,
    ) -> None:
        """Retain borrowed fallback and terminal stream collaborators.

        Construction performs no stream access or terminal mutation.
        The first input call resolves and permanently selects either the live
        byte source or the ordinary fallback callback.
        """

        if not callable(fallback_read_line):
            raise TypeError("fallback_read_line must be callable")
        if not callable(line_transition):
            raise TypeError("line_transition must be callable")
        if not callable(main_transition):
            raise TypeError("main_transition must be callable")
        if not callable(history_append):
            raise TypeError("history_append must be callable")
        if termios_provider is not None and not callable(termios_provider):
            raise TypeError("termios_provider must be callable or None")
        if os_provider is not None and not callable(os_provider):
            raise TypeError("os_provider must be callable or None")
        self._fallback_read_line = fallback_read_line
        self._fallback_capabilities: TerminalCapabilities | None = None
        self._input_stream = input_stream
        self._input_latch = TerminalInputLatch()
        self._output_stream = output_stream
        self._line_transition = line_transition
        self._live_capabilities: TerminalCapabilities | None = None
        self._live_configuration: _TerminalConfiguration | None = None
        self._main_transition = main_transition
        self._history_append = history_append
        self._history_state = MainInputHistoryState()
        self._progress_active = False
        self._progress_frame = 0
        self._progress_renderer: Callable[[int], str] | None = None
        self._progress_rows = 0
        self._termios_provider = (
            (lambda: _termios) if termios_provider is None else termios_provider
        )
        self._os_provider = (lambda: os) if os_provider is None else os_provider

    def read_line(self, prompt: str) -> str:
        """Read one history-free line through the driver's selected input."""

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_fallback_line(prompt, capabilities)
            return self._read_control_line(
                prompt,
                _empty_suggestion_preview,
                LineInputState(),
                self._line_transition,
                configuration=configuration,
                transient=True,
            )

    def write_text(self, text: str) -> None:
        """Write and flush one payload through the sole stream owner."""

        if type(text) is not str:
            raise TypeError("terminal output must be a string")
        if self._progress_active:
            self._clear_progress_frame()
            try:
                self._write_raw_text(text)
            finally:
                self._draw_progress_frame()
            return
        self._write_raw_text(text)

    def write_accepted_input(self, text: str) -> None:
        """Project rendered accepted input according to the selected input mode."""

        if type(text) is not str:
            raise TypeError("accepted input output must be a string")
        mode = self._input_latch.mode
        if mode is TerminalInputMode.UNDECIDED:
            raise RuntimeError(
                "accepted input projection requires a selected input mode"
            )
        if mode is TerminalInputMode.FALLBACK:
            return
        self.write_text(text)

    def _write_raw_text(self, text: str) -> None:
        """Write one payload without progress interception."""

        written = self._output_stream.write(text)
        if written is not None and written != len(text):
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()

    def _clear_progress_frame(self) -> None:
        """Clear every physical row owned by the current indicator."""

        rows = max(1, self._progress_rows)
        commands: list[str] = []
        for ordinal in range(rows):
            commands.append("\r\x1b[K")
            if ordinal + 1 < rows:
                commands.append("\x1b[1A")
        self._write_raw_text("".join(commands))
        self._progress_rows = 0

    def _draw_progress_frame(
        self,
        frame_renderer: Callable[[int], str] | None = None,
    ) -> None:
        """Draw the current safe multiline indicator frame."""

        frame_renderer = (
            self._progress_renderer if frame_renderer is None else frame_renderer
        )
        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        frame = frame_renderer(self._progress_frame)
        if type(frame) is not str:
            raise TypeError("frame_renderer must return a string")
        plain_frame = frame
        for prefix in _PROGRESS_SGR_FRAMES:
            if frame.startswith(prefix) and frame.endswith(_SGR_RESET):
                plain_frame = frame[len(prefix) : -len(_SGR_RESET)]
                break
        lines = plain_frame.split("\n")
        if (
            not lines
            or any(
                sanitize_terminal_text(line) != line
                or any(character in line for character in ("\r", "\x1b"))
                for line in lines
            )
            or (plain_frame is frame and "\x1b" in frame)
        ):
            raise ValueError("progress frame must contain safe text rows")
        display_frame = frame if plain_frame != frame else "\n".join(lines)
        self._write_raw_text("\r" + display_frame)
        self._progress_rows = len(lines)

    async def run_advisory_progress(
        self,
        operation: Awaitable[_T],
        frame_renderer: Callable[[int], str],
    ) -> _T:
        """Join one advisory operation with a capable-terminal indicator."""

        if not hasattr(operation, "__await__"):
            raise TypeError("operation must be awaitable")
        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        capabilities = self.capabilities()
        if not capabilities.raw_mode:
            return await operation

        def fitted_frame_renderer(frame: int) -> str:
            """Reserve the rightmost cell on every safe logical row."""

            rendered = frame_renderer(frame)
            if type(rendered) is not str:
                raise TypeError("frame_renderer must return a string")
            if sanitize_terminal_text(rendered) != rendered or any(
                character in rendered for character in ("\r", "\x1b")
            ):
                raise ValueError("progress frame must be safe text")
            available_width = max(0, capabilities.width - 1)
            available_height = 8
            rows: list[str] = []
            for logical in rendered.split("\n"):
                if not logical:
                    rows.append("")
                    continue
                fitted: list[str] = []
                fitted_width = 0
                for character in logical:
                    character_width = display_character_width(character)
                    if fitted and fitted_width + character_width > available_width:
                        rows.append("".join(fitted))
                        fitted = []
                        fitted_width = 0
                    if not fitted and character_width > available_width:
                        continue
                    fitted.append(character)
                    fitted_width += character_width
                if fitted:
                    rows.append("".join(fitted))
            if len(rows) > available_height:
                rows = [rows[0]] + rows[-(available_height - 1) :]
            fitted_text = "\n".join(rows)
            if len(rows) == 1 and fitted_text and capabilities.decorative_sgr:
                return f"{_PROGRESS_SGR_FRAMES[frame]}{fitted_text}{_SGR_RESET}"
            return fitted_text

        self._progress_active = True
        self._progress_frame = 0
        self._progress_renderer = fitted_frame_renderer
        try:
            self._draw_progress_frame(fitted_frame_renderer)
        except BaseException as exc:
            self._progress_active = False
            self._progress_renderer = None
            try:
                self._clear_progress_frame()
            except BaseException:
                pass
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise AdvisoryProgressError(
                "advisory progress could not be displayed"
            ) from exc

        operation_task = asyncio.ensure_future(operation)
        progress_task = asyncio.create_task(
            self._run_progress_frames(fitted_frame_renderer)
        )
        operation_failure: BaseException | None = None
        progress_failure: BaseException | None = None
        result: _T | None = None
        operation_succeeded = False
        outer_failure = False
        try:
            while True:
                done, _pending = await asyncio.wait(
                    (operation_task, progress_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if operation_task in done:
                    try:
                        result = operation_task.result()
                        operation_succeeded = True
                    except BaseException as exc:
                        operation_failure = exc
                    if progress_task in done:
                        try:
                            progress_task.result()
                        except asyncio.CancelledError:
                            pass
                        except BaseException as exc:
                            progress_failure = exc
                    break
                if progress_task in done:
                    try:
                        progress_task.result()
                    except asyncio.CancelledError:
                        pass
                    except BaseException as exc:
                        progress_failure = exc
                    if progress_failure is None:
                        progress_failure = RuntimeError(
                            "advisory progress worker stopped unexpectedly"
                        )
                    break
        except BaseException as exc:
            operation_failure = exc
            outer_failure = True
        finally:
            if (operation_failure is not None or progress_failure is not None) and (
                not operation_task.done()
            ):
                operation_task.cancel()
            if not operation_task.done():
                try:
                    await operation_task
                except BaseException as exc:
                    if not outer_failure and progress_failure is None:
                        operation_failure = operation_failure or exc
            elif operation_failure is None and progress_failure is None:
                try:
                    operation_task.result()
                except BaseException as exc:
                    operation_failure = exc

            if not progress_task.done():
                progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                progress_failure = progress_failure or exc

            clear_failure: BaseException | None = None
            self._progress_active = False
            self._progress_renderer = None
            try:
                self._clear_progress_frame()
            except BaseException as exc:
                clear_failure = exc
            if (
                operation_failure is None
                and progress_failure is None
                and clear_failure is not None
            ):
                progress_failure = clear_failure

        if operation_failure is not None:
            raise operation_failure
        if progress_failure is not None:
            raise AdvisoryProgressError(
                "advisory progress worker failed"
            ) from progress_failure
        assert operation_succeeded
        return result

    async def run_live_composer(
        self,
        operation: Awaitable[_T],
        prompt: str,
        frame_renderer: Callable[
            [LineInputState, int, TerminalCapabilities], RenderedInputFrame
        ],
        boundary_provider: BoundaryProvider,
        cancel_and_join: CancelAndJoin,
        *,
        initial_state: LineInputState = LineInputState(),
    ) -> LiveComposerResult:
        """Keep one literal editor live while an injected operation runs.

        Runtime callbacks own the durable boundary and cancellation decision.
        This driver owns only terminal input, fixed progress redraw, the single
        output stream, and exact restoration.
        """

        if not hasattr(operation, "__await__"):
            raise TypeError("operation must be awaitable")
        if type(prompt) is not str:
            raise TypeError("prompt must be a string")
        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(boundary_provider):
            raise TypeError("boundary_provider must be callable")
        if not callable(cancel_and_join):
            raise TypeError("cancel_and_join must be callable")
        if type(initial_state) is not LineInputState:
            raise TypeError("initial_state must be a LineInputState")
        if self._progress_active:
            raise RuntimeError("live composer cannot overlap terminal progress")

        operation_task: asyncio.Future[_T] | None = None
        with self._input_latch.input_operation():
            try:
                observed = self._observe_input_capabilities()
                capabilities, configuration = self._prepare_input(observed)
                operation_task = asyncio.ensure_future(operation)
                if configuration is None:
                    accepted = await self._read_fallback_live_composer(
                        operation_task,
                        prompt,
                        initial_state,
                    )
                else:
                    accepted = await self._read_raw_live_composer(
                        operation_task,
                        prompt,
                        frame_renderer,
                        capabilities,
                        configuration,
                        initial_state,
                    )
            except BaseException:
                if operation_task is None:
                    close = getattr(operation, "close", None)
                    if callable(close):
                        close()
                else:
                    await self._cleanup_failed_live_composer(
                        operation_task,
                        boundary_provider,
                        cancel_and_join,
                    )
                raise

        assert operation_task is not None
        if type(accepted) is LiveComposerResult:
            return accepted
        try:
            boundary = boundary_provider()
        except Exception:
            await self._join_without_cancellation(operation_task)
            return LiveComposerResult(
                LiveComposerDeliveryKind.RECOVERY_REQUIRED,
                accepted.state,
            )
        if not isinstance(boundary, LiveOperationBoundary):
            await self._join_without_cancellation(operation_task)
            return LiveComposerResult(
                LiveComposerDeliveryKind.RECOVERY_REQUIRED,
                accepted.state,
            )
        try:
            resolved = await deliver_live_composer_input(
                accepted,
                operation_task,
                lambda: boundary,
                cancel_and_join,
            )
        except Exception:
            operation_task.cancel()
            try:
                await operation_task
            except BaseException:
                pass
            raise
        if boundary is LiveOperationBoundary.POSTCOMMIT:
            try:
                await operation_task
            except asyncio.CancelledError:
                if not operation_task.cancelled():
                    raise
                return LiveComposerResult(
                    LiveComposerDeliveryKind.RECOVERY_REQUIRED,
                    accepted.state,
                )
            except Exception:
                return LiveComposerResult(
                    LiveComposerDeliveryKind.RECOVERY_REQUIRED,
                    accepted.state,
                )
            return resolved
        if operation_task.done():
            # Retrieve any terminal failure so it cannot become an unobserved
            # task exception after the runtime proved the join.
            if not operation_task.cancelled():
                operation_task.exception()
            return resolved
        operation_task.cancel()
        try:
            await operation_task
        except asyncio.CancelledError:
            pass
        return LiveComposerResult(
            LiveComposerDeliveryKind.RECOVERY_REQUIRED,
            accepted.state,
        )

    async def _join_without_cancellation(
        self,
        operation_task: asyncio.Future[object],
    ) -> None:
        """Retrieve one owned task without guessing its durable boundary."""

        try:
            await operation_task
        except Exception:
            pass

    async def _cleanup_failed_live_composer(
        self,
        operation_task: asyncio.Future[object],
        boundary_provider: BoundaryProvider,
        cancel_and_join: CancelAndJoin,
    ) -> None:
        """Best-effort join one operation after terminal-side failure."""

        if operation_task.done():
            if not operation_task.cancelled():
                operation_task.exception()
            return
        try:
            boundary = boundary_provider()
        except Exception:
            await self._join_without_cancellation(operation_task)
            return
        if boundary is LiveOperationBoundary.POSTCOMMIT:
            await self._join_without_cancellation(operation_task)
            return
        if boundary is not LiveOperationBoundary.PRECOMMIT:
            await self._join_without_cancellation(operation_task)
            return
        try:
            await cancel_and_join(operation_task)
        except BaseException:
            pass
        if not operation_task.done():
            operation_task.cancel()
            try:
                await operation_task
            except BaseException:
                pass

    async def _read_fallback_live_composer(
        self,
        operation_task: asyncio.Future[_T],
        prompt: str,
        state: LineInputState,
    ) -> LiveComposerInput | LiveComposerResult:
        """Finish confirmed work without blocking on incapable terminal input."""

        del prompt
        return LiveComposerResult(
            LiveComposerDeliveryKind.OPERATION_COMPLETED,
            state,
            operation_result=await operation_task,
        )

    async def _read_raw_live_composer(
        self,
        operation_task: asyncio.Future[_T],
        prompt: str,
        frame_renderer: Callable[
            [LineInputState, int, TerminalCapabilities], RenderedInputFrame
        ],
        capabilities: TerminalCapabilities,
        configuration: _TerminalConfiguration,
        initial_state: LineInputState,
    ) -> LiveComposerInput | LiveComposerResult:
        """Poll one raw editor and operation without creating an input worker."""

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            raise

        display = _LiveEditorState()
        state = initial_state
        frame = 0
        active_failure: BaseException | None = None
        result: LiveComposerInput | LiveComposerResult | None = None
        try:
            display.rendered_cursor_row = self._redraw_live_composer(
                prompt, state, frame, display, frame_renderer, capabilities
            )
            while result is None:
                if operation_task.done():
                    result = LiveComposerResult(
                        LiveComposerDeliveryKind.OPERATION_COMPLETED,
                        state,
                        operation_result=operation_task.result(),
                    )
                    break
                readable, _writable, _exceptional = _select.select(
                    (configuration.input_descriptor,), (), (), 0
                )
                if readable or self._input_latch.live_reader.has_pending_codepoint:
                    character = self._read_character()
                    event = self._live_composer_key_event(
                        character, configuration, display
                    )
                    if event is not None:
                        updated, outcome = self._line_transition(state, event)
                        if type(updated) is not LineInputState:
                            raise TypeError(
                                "line transition must return a LineInputState"
                            )
                        if outcome is not None and type(outcome) is not ControlOutcome:
                            raise TypeError(
                                "line transition must return a ControlOutcome or None"
                            )
                        state = updated
                        if outcome is not None:
                            if outcome.kind is ControlOutcomeKind.COMMIT:
                                result = LiveComposerInput(
                                    LiveComposerInputKind.SUBMIT, state
                                )
                            elif outcome.kind is ControlOutcomeKind.INTERRUPT:
                                result = LiveComposerInput(
                                    LiveComposerInputKind.INTERRUPT, state
                                )
                            elif outcome.kind is ControlOutcomeKind.EOF:
                                result = LiveComposerInput(
                                    LiveComposerInputKind.EOF, state
                                )
                            else:
                                raise ValueError(
                                    "live composer received an unsupported outcome"
                                )
                        if result is None:
                            display.rendered_cursor_row = self._redraw_live_composer(
                                prompt,
                                state,
                                frame,
                                display,
                                frame_renderer,
                                capabilities,
                            )
                    continue
                await asyncio.sleep(_LIVE_COMPOSER_POLL_SECONDS)
                frame = (frame + 1) % _PROGRESS_FRAME_COUNT
                display.rendered_cursor_row = self._redraw_live_composer(
                    prompt, state, frame, display, frame_renderer, capabilities
                )
            self._clear_live_display(display, line_break=False)
        except BaseException as failure:
            active_failure = failure
            try:
                self._clear_live_display(display, line_break=True)
            except BaseException:
                pass

        _restore_terminal_or_raise(configuration, active_failure)
        if active_failure is not None:
            raise active_failure
        assert result is not None
        return result

    def _redraw_live_composer(
        self,
        prompt: str,
        state: LineInputState,
        frame: int,
        display: _LiveEditorState,
        renderer: Callable[
            [LineInputState, int, TerminalCapabilities], RenderedInputFrame
        ],
        capabilities: TerminalCapabilities,
    ) -> int:
        rendered = renderer(state, frame, capabilities)
        if type(rendered) is not RenderedInputFrame:
            raise TypeError("frame_renderer must return a RenderedInputFrame")
        if rendered.width != capabilities.width:
            raise ValueError("live composer frame width must match capabilities")
        return self._redraw_live_display(
            prompt,
            capabilities.width,
            display,
            state,
            _empty_suggestion_preview,
            rendered,
            capabilities,
            None,
            None,
            None,
        )

    def _live_composer_key_event(
        self,
        character: str,
        configuration: _TerminalConfiguration,
        display: _LiveEditorState,
    ) -> KeyEvent | None:
        configured = _configured_control_event(character, configuration)
        if configured is not None:
            display.escape_sequence = None
            return configured
        if display.escape_sequence is not None:
            event, reprocess = _consume_escape_character(display, character)
            if event is not None:
                return event
            if not reprocess:
                return None
        if character == "\x1b":
            display.escape_sequence = character
            return None
        return _key_event(character, configuration)

    async def _run_progress_frames(
        self,
        frame_renderer: Callable[[int], str],
    ) -> None:
        """Animate fixed frames until the joined operation finishes."""

        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL_SECONDS)
            self._progress_frame = (self._progress_frame + 1) % _PROGRESS_FRAME_COUNT
            if self._progress_rows > 0:
                self._clear_progress_frame()
            self._draw_progress_frame(frame_renderer)

    def capabilities(self) -> TerminalCapabilities:
        """Resolve the current destination profile without acquiring raw mode."""

        try:
            termios_module = self._termios_provider()
        except Exception:
            termios_module = None
        try:
            os_module = self._os_provider()
        except Exception:
            os_module = object()
        return detect_terminal_capabilities(
            self._input_stream,
            self._output_stream,
            termios_module=termios_module,
            os_module=os_module,
        )

    def run_startup_welcome(
        self,
        frame_renderer: _StartupFrameRenderer,
        sleeper: Callable[[float], object],
    ) -> None:
        """Render one bounded startup sequence without selecting input mode."""

        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        with self._input_latch.input_operation():
            base_capabilities = self._observe_input_capabilities()
            reduced_motion = self._startup_reduced_motion_preference()
            configuration = (
                self._startup_terminal_configuration(base_capabilities)
                if self._startup_animation_is_safe(
                    base_capabilities,
                    reduced_motion,
                )
                else None
            )
            if configuration is None or not self._run_guarded_startup_presentation(
                configuration,
                lambda: self._run_startup_welcome_frames(
                    frame_renderer,
                    sleeper,
                    base_capabilities,
                ),
            ):
                physical_width = base_capabilities.width
                static_capabilities = _static_startup_capabilities(
                    base_capabilities,
                    decorative_sgr=(
                        reduced_motion is True
                        and base_capabilities.profile is CapabilityProfile.ANSI
                        and base_capabilities.decorative_sgr
                    ),
                )
                final_frame = _startup_frame(
                    frame_renderer,
                    _STARTUP_WELCOME_FRAME_COUNT - 1,
                    static_capabilities,
                    physical_width,
                )
                self._write_raw_text(f"{final_frame.text}\n")
        return

    def _run_startup_welcome_frames(
        self,
        frame_renderer: _StartupFrameRenderer,
        sleeper: Callable[[float], object],
        base_capabilities: TerminalCapabilities,
    ) -> None:
        """Render and clean only the guarded capable welcome frames."""

        live_state = _StartupFrameState()
        active_failure: BaseException | None = None
        try:
            for index in range(_STARTUP_WELCOME_FRAME_COUNT):
                render_capabilities, physical_width = (
                    self._startup_welcome_render_context(base_capabilities)
                )
                frame = _startup_frame(
                    frame_renderer,
                    index,
                    render_capabilities,
                    physical_width,
                )
                self._replace_startup_frame(frame, live_state)
                if index < _STARTUP_WELCOME_FRAME_COUNT - 1:
                    sleeper(_STARTUP_WELCOME_INTERVAL_SECONDS)
            self._finish_startup_frame(live_state)
            live_state = _StartupFrameState()
        except BaseException as failure:
            active_failure = failure
        if live_state.physical_line_count:
            try:
                self._clear_startup_state(live_state)
            except BaseException as cleanup_failure:
                if active_failure is None:
                    active_failure = cleanup_failure
        if active_failure is not None:
            raise active_failure

    def run_startup_notice(
        self,
        frame_renderer: Callable[[TerminalCapabilities], RenderedFrame],
        sleeper: Callable[[float], object],
    ) -> None:
        """Show then clear one auth notice only when transient display is safe."""

        self._run_startup_notice(
            frame_renderer,
            sleeper,
            result_boundary=False,
        )

    def _run_startup_notice(
        self,
        frame_renderer: Callable[[TerminalCapabilities], RenderedFrame],
        sleeper: Callable[[float], object],
        *,
        result_boundary: bool,
    ) -> None:
        """Choose one guarded or static purpose-bound notice lifecycle."""

        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        if type(result_boundary) is not bool:
            raise TypeError("result_boundary must be a bool")
        with self._input_latch.input_operation():
            base_capabilities = self._observe_input_capabilities()
            reduced_motion = self._startup_reduced_motion_preference()
            configuration = (
                self._startup_terminal_configuration(base_capabilities)
                if self._startup_animation_is_safe(
                    base_capabilities,
                    reduced_motion,
                )
                else None
            )
            if configuration is None or not self._run_guarded_startup_presentation(
                configuration,
                lambda: self._run_startup_notice_frame(
                    frame_renderer,
                    sleeper,
                    base_capabilities,
                    result_boundary=result_boundary,
                ),
            ):
                static_capabilities = _static_startup_capabilities(base_capabilities)
                frame = frame_renderer(static_capabilities)
                _require_startup_frame(frame, static_capabilities)
                if result_boundary:
                    self._write_raw_text("\n")
                self._write_raw_text(f"{frame.text}\n")

    def _run_startup_notice_frame(
        self,
        frame_renderer: Callable[[TerminalCapabilities], RenderedFrame],
        sleeper: Callable[[float], object],
        base_capabilities: TerminalCapabilities,
        *,
        result_boundary: bool,
    ) -> None:
        """Render and clean only one guarded capable startup notice."""

        if result_boundary:
            self._write_raw_text(f"{_CARRIAGE_RETURN}\n")
        render_capabilities = self._startup_render_capabilities(base_capabilities)
        frame = frame_renderer(render_capabilities)
        _require_startup_frame(frame, render_capabilities)

        live_state = _StartupFrameState()
        active_failure: BaseException | None = None
        try:
            self._replace_startup_frame(frame, live_state)
            sleeper(_STARTUP_NOTICE_DURATION_SECONDS)
        except BaseException as failure:
            active_failure = failure
        if live_state.physical_line_count:
            try:
                self._clear_startup_state(live_state)
            except BaseException as cleanup_failure:
                if active_failure is None:
                    active_failure = cleanup_failure
        if active_failure is not None:
            raise active_failure

    def _startup_terminal_configuration(
        self,
        capabilities: TerminalCapabilities,
    ) -> _TerminalConfiguration | None:
        """Preflight one guard without selecting the permanent input mode."""

        mode = self._input_latch.mode
        if mode is TerminalInputMode.FALLBACK:
            return None
        try:
            termios_module = self._termios_provider()
            if not _startup_input_discard_is_supported(termios_module):
                raise ValueError("startup input discard is unavailable")
            decoder_spec = inspect_live_decoder_spec(self._input_stream)
            if decoder_spec is None:
                raise ValueError("startup input requires a byte reader")
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
                termios_module=termios_module,
                os_module=self._os_provider(),
                width=capabilities.width,
                decoder_spec=decoder_spec,
            )
            if configuration is None:
                raise ValueError("startup terminal configuration is unavailable")
            if configuration.width != capabilities.width:
                raise ValueError("startup terminal geometry changed during preflight")
            if mode is TerminalInputMode.LIVE:
                baseline_capabilities = self._live_capabilities
                if baseline_capabilities is None or not _same_live_capabilities(
                    baseline_capabilities,
                    capabilities,
                ):
                    raise RuntimeError("live terminal capability changed")
                self._input_latch.require_live(decoder_spec)
                baseline_configuration = self._live_configuration
                if baseline_configuration is None or not _same_terminal_configuration(
                    baseline_configuration,
                    configuration,
                ):
                    raise RuntimeError("live terminal configuration changed")
            return configuration
        except Exception as failure:
            if mode is TerminalInputMode.LIVE:
                raise RuntimeError(
                    "live terminal startup presentation preflight failed"
                ) from failure
            return None

    def _run_guarded_startup_presentation(
        self,
        configuration: _TerminalConfiguration,
        operation: Callable[[], None],
    ) -> bool:
        """Run one bounded presentation with no echo or retained type-ahead."""

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            if (
                isinstance(setup_failure, Exception)
                and self._input_latch.mode is not TerminalInputMode.LIVE
            ):
                return False
            raise

        active_failure: BaseException | None = None
        try:
            operation()
        except BaseException as failure:
            active_failure = failure
        _restore_terminal_or_raise(
            configuration,
            active_failure,
            discard_input=True,
        )
        if active_failure is not None:
            raise active_failure
        return True

    def run_startup_result_notice(
        self,
        frame_renderer: Callable[[TerminalCapabilities], RenderedFrame],
        sleeper: Callable[[float], object],
    ) -> None:
        """Establish a fresh provider-output boundary before one result."""

        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._run_startup_notice(
            frame_renderer,
            sleeper,
            result_boundary=True,
        )

    def _startup_animation_is_safe(
        self,
        capabilities: TerminalCapabilities,
        reduced_motion: bool | None,
    ) -> bool:
        """Require known ANSI cursor support and no reduced-motion request."""

        if (
            capabilities.profile is not CapabilityProfile.ANSI
            or not capabilities.raw_mode
            or not capabilities.cursor_controls
            or not capabilities.decorative_sgr
        ):
            return False
        return reduced_motion is False

    def _startup_reduced_motion_preference(self) -> bool | None:
        """Observe the presence-based motion opt-out or return uncertainty."""

        try:
            os_module = self._os_provider()
            environment = getattr(os_module, "environ", {})
            return _REDUCED_MOTION_ENVIRONMENT_KEY in environment
        except Exception:
            return None

    def _replace_startup_frame(
        self,
        frame: RenderedFrame,
        state: _StartupFrameState,
    ) -> None:
        """Replace one purpose-bound startup frame without claiming the screen."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        if state.physical_line_count:
            self._clear_startup_state(state)
        self._write_startup_frame(frame, state)

    def _write_startup_frame(
        self,
        frame: RenderedFrame,
        state: _StartupFrameState,
    ) -> None:
        """Write one frame and retain only collaborator-confirmed progress."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        # A raised write exposes no trustworthy progress. Leave cleanup
        # unarmed so an unknown partial write cannot erase preceding output.
        written = self._output_stream.write(frame.text)
        if written is None:
            state.physical_line_count = frame.line_count
        elif type(written) is int and 0 <= written <= len(frame.text):
            if written:
                state.physical_line_count = frame.text[:written].count("\n") + 1
            if written != len(frame.text):
                raise OSError("interactive terminal write was incomplete")
        else:
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()

    def _finish_startup_frame(self, state: _StartupFrameState) -> None:
        """Advance below the final welcome with confirmed cursor progress."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        try:
            written = self._output_stream.write("\n")
        except BaseException:
            state.physical_line_count = 0
            state.cursor_below_frame = False
            raise
        if written is None or type(written) is int and written == 1:
            state.cursor_below_frame = True
        else:
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()

    def _startup_render_capabilities(
        self,
        base_capabilities: TerminalCapabilities,
    ) -> TerminalCapabilities:
        """Reobserve width and reserve one column from live startup output."""

        observed_width = self.capabilities().width
        return _capabilities_with_width(
            base_capabilities,
            max(1, observed_width - 1),
        )

    def _startup_welcome_render_context(
        self,
        base_capabilities: TerminalCapabilities,
    ) -> tuple[TerminalCapabilities, int]:
        """Observe physical width separately from the safe welcome width."""

        physical_width = self.capabilities().width
        safe_width = _startup_safe_projection_width(
            base_capabilities,
            physical_width,
        )
        return (
            _capabilities_with_width(base_capabilities, safe_width),
            physical_width,
        )

    def _clear_startup_state(self, state: _StartupFrameState) -> None:
        """Clear an attempted frame using its stored write-time geometry."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        if not state.physical_line_count:
            return
        self._clear_startup_frame(
            state.physical_line_count,
            cursor_below_frame=state.cursor_below_frame,
        )
        state.physical_line_count = 0
        state.cursor_below_frame = False

    def _clear_startup_frame(
        self,
        line_count: int,
        *,
        cursor_below_frame: bool = False,
    ) -> None:
        """Clear one complete call-local startup frame without leaving a row."""

        if type(line_count) is not int or line_count <= 0:
            raise ValueError("line_count must be positive")
        if type(cursor_below_frame) is not bool:
            raise TypeError("cursor_below_frame must be a bool")
        self._write_raw_text(
            "".join(
                (
                    _SGR_RESET,
                    _CARRIAGE_RETURN,
                    _CURSOR_UP * (line_count if cursor_below_frame else line_count - 1),
                    _CARRIAGE_RETURN,
                    _CLEAR_TO_END,
                )
            )
        )

    def _observe_input_capabilities(self) -> TerminalCapabilities:
        """Observe capabilities without selecting or consuming an input source."""

        if self._input_latch.mode is TerminalInputMode.FALLBACK:
            assert self._fallback_capabilities is not None
            return self._fallback_capabilities
        return self.capabilities()

    def _prepare_input(
        self,
        observed_capabilities: TerminalCapabilities,
    ) -> tuple[TerminalCapabilities, _TerminalConfiguration | None]:
        """Preflight or revalidate without consuming input or mutating output."""

        if type(observed_capabilities) is not TerminalCapabilities:
            raise TypeError("observed_capabilities must be a TerminalCapabilities")
        mode = self._input_latch.mode
        if mode is TerminalInputMode.FALLBACK:
            assert self._fallback_capabilities is not None
            return self._fallback_capabilities, None

        capabilities = observed_capabilities
        if not capabilities.raw_mode:
            if mode is TerminalInputMode.LIVE:
                raise RuntimeError("live terminal capability changed")
            return _line_only_capabilities(capabilities), None
        if mode is TerminalInputMode.LIVE:
            baseline_capabilities = self._live_capabilities
            if baseline_capabilities is None or not _same_live_capabilities(
                baseline_capabilities,
                capabilities,
            ):
                raise RuntimeError("live terminal capability changed")

        try:
            decoder_spec = inspect_live_decoder_spec(self._input_stream)
            if decoder_spec is None:
                raise ValueError("live terminal input requires a byte reader")
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
                termios_module=self._termios_provider(),
                os_module=self._os_provider(),
                width=None,
                decoder_spec=decoder_spec,
            )
            if configuration is None:
                raise ValueError("live terminal configuration is unavailable")
            if configuration.width != capabilities.width:
                raise ValueError("live terminal geometry changed during preflight")
        except Exception as failure:
            if mode is TerminalInputMode.LIVE:
                raise RuntimeError("live terminal input preflight failed") from failure
            return _line_only_capabilities(capabilities), None

        if mode is TerminalInputMode.LIVE:
            self._input_latch.require_live(decoder_spec)
            baseline = self._live_configuration
            if baseline is None or not _same_terminal_configuration(
                baseline,
                configuration,
            ):
                raise RuntimeError("live terminal configuration changed")
        else:
            self._input_latch.latch_live(decoder_spec)
            self._live_capabilities = capabilities
            self._live_configuration = configuration
        return capabilities, configuration

    def _read_fallback_line(
        self,
        prompt: str,
        capabilities: TerminalCapabilities,
    ) -> str:
        """Latch fallback immediately before invoking its ordinary callback."""

        if self._input_latch.mode is TerminalInputMode.UNDECIDED:
            self._fallback_capabilities = capabilities
        self._input_latch.latch_fallback()
        return self._fallback_read_line(prompt)

    def read_live_line(
        self,
        prompt: str,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
    ) -> str:
        """Read one history-free Unicode buffer with a preview provider."""

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")
        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_fallback_line(prompt, capabilities)
            return self._read_control_line(
                prompt,
                suggestion_provider,
                LineInputState(),
                self._line_transition,
                configuration=configuration,
            )

    def read_input_line(
        self,
        view: InputPromptView,
        renderer: _InputRenderer,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
        *,
        transient: bool = False,
    ) -> str:
        """Read one history-free line through an injected semantic renderer."""

        if type(view) is not InputPromptView:
            raise TypeError("view must be an InputPromptView")
        if not callable(renderer):
            raise TypeError("renderer must be callable")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            rendered_input = renderer(view, observed_capabilities)
            if type(rendered_input) is not RenderedInputFrame:
                raise TypeError("renderer must return a RenderedInputFrame")
            if rendered_input.width != observed_capabilities.width:
                raise ValueError("rendered input width must match current capabilities")
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_fallback_line(
                    rendered_input.plain_text,
                    capabilities,
                )
            return self._read_control_line(
                rendered_input.plain_text,
                suggestion_provider,
                LineInputState(),
                self._line_transition,
                configuration=configuration,
                rendered_input=rendered_input,
                transient=transient,
            )

    def read_prompt(
        self,
        view: InputPromptView,
        renderer: _InputRenderer,
        transition: _PromptTransition,
        *,
        transient: bool = False,
    ) -> ControlOutcome:
        """Read one cancellable semantic line without history or completion."""

        if type(view) is not InputPromptView:
            raise TypeError("view must be an InputPromptView")
        if not callable(renderer):
            raise TypeError("renderer must be callable")
        if not callable(transition):
            raise TypeError("transition must be callable")
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            rendered_input = renderer(view, observed_capabilities)
            if type(rendered_input) is not RenderedInputFrame:
                raise TypeError("renderer must return a RenderedInputFrame")
            if rendered_input.width != observed_capabilities.width:
                raise ValueError("rendered input width must match current capabilities")
            capabilities, configuration = self._prepare_input(observed_capabilities)
            initial_state = (
                LineInputState()
                if view.initial_value is None
                else LineInputState(view.initial_value, len(view.initial_value))
            )
            try:
                line = (
                    self._read_control_line(
                        rendered_input.plain_text,
                        _empty_suggestion_preview,
                        initial_state,
                        transition,
                        configuration=configuration,
                        rendered_input=rendered_input,
                        transient=transient,
                    )
                    if configuration is not None
                    else self._read_fallback_line(
                        rendered_input.plain_text,
                        capabilities,
                    )
                )
            except _PromptCancelled:
                return ControlOutcome(ControlOutcomeKind.CANCEL)
            except KeyboardInterrupt:
                return ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                return ControlOutcome(ControlOutcomeKind.EOF)
        if not isinstance(line, str):
            raise TypeError("read_line must return a string")
        line = str.__str__(line)
        if "\r" in line or "\n" in line:
            raise ValueError("read_line must return one line without CR or LF")
        if line == "\x1b":
            return ControlOutcome(ControlOutcomeKind.CANCEL)
        if line == "" and view.initial_value is not None:
            line = view.initial_value
        return ControlOutcome(ControlOutcomeKind.COMMIT, text=line)

    def read_main_line(
        self,
        prompt: str,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
    ) -> str:
        """Read one main line and retain its successful exact nonblank commit.

        Each read starts with inactive navigation over the driver's one
        process-local history store. The current preview list is not an open
        selectable overlay, so Up and Down remain history keys in this slice.
        History changes only after fallback success or live display cleanup and
        terminal restoration have completed successfully.
        """

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[_ControlState, ControlOutcome | None]:
            if type(state) is not MainCommandControlState:
                raise TypeError("main transition requires MainCommandControlState")
            return self._main_transition(
                state,
                event,
                self._history_state,
                _empty_command_completion,
            )

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            line = (
                self._read_control_line(
                    prompt,
                    suggestion_provider,
                    MainCommandControlState(),
                    transition,
                    configuration=configuration,
                    transient=True,
                )
                if configuration is not None
                else self._read_fallback_line(prompt, capabilities)
            )
            self._append_completed_main_history(line)
            return line

    def read_main_command_line(
        self,
        prompt: str,
        completion_provider: _CompletionProvider,
        palette_projector: _PaletteProjector,
        palette_renderer: _PaletteRenderer,
    ) -> str:
        """Read one selectable semantic main command through stable seams."""

        return self._read_main_command_line(
            prompt,
            completion_provider,
            palette_projector,
            palette_renderer,
            None,
            None,
        )

    def read_reference_main_command_line(
        self,
        prompt: str,
        completion_provider: _CompletionProvider,
        palette_projector: _PaletteProjector,
        palette_renderer: _PaletteRenderer,
        reference_bindings: ReferenceInputBindings,
    ) -> str:
        """Read one main command with call-local reference activation."""

        if type(reference_bindings) is not ReferenceInputBindings:
            raise TypeError("reference_bindings must be a ReferenceInputBindings")
        return self._read_main_command_line(
            prompt,
            completion_provider,
            palette_projector,
            palette_renderer,
            reference_bindings,
            None,
        )

    def read_contextual_main_command_line(
        self,
        prompt: str,
        completion_provider: _CompletionProvider,
        palette_projector: _PaletteProjector,
        palette_renderer: _PaletteRenderer,
        input_frame_provider: _InputFrameProvider,
        reference_bindings: ReferenceInputBindings | None,
    ) -> str:
        """Read a main command beneath a buffer-aware semantic header."""

        if not callable(input_frame_provider):
            raise TypeError("input_frame_provider must be callable")
        if (
            reference_bindings is not None
            and type(reference_bindings) is not ReferenceInputBindings
        ):
            raise TypeError(
                "reference_bindings must be a ReferenceInputBindings or None"
            )
        if input_frame_provider is not None and not callable(input_frame_provider):
            raise TypeError("input_frame_provider must be callable or None")
        return self._read_main_command_line(
            prompt,
            completion_provider,
            palette_projector,
            palette_renderer,
            reference_bindings,
            input_frame_provider,
        )

    def _read_main_command_line(
        self,
        prompt: str,
        completion_provider: _CompletionProvider,
        palette_projector: _PaletteProjector,
        palette_renderer: _PaletteRenderer,
        reference_bindings: ReferenceInputBindings | None,
        input_frame_provider: _InputFrameProvider | None,
    ) -> str:
        """Implement main input with optional package-internal activation."""

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        for name, collaborator in (
            ("completion_provider", completion_provider),
            ("palette_projector", palette_projector),
            ("palette_renderer", palette_renderer),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")
        if (
            reference_bindings is not None
            and type(reference_bindings) is not ReferenceInputBindings
        ):
            raise TypeError(
                "reference_bindings must be a ReferenceInputBindings or None"
            )

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[
            _ControlState,
            ControlOutcome | ReferenceActivationRequest | ReferenceInputFailure | None,
        ]:
            if type(state) is not MainCommandControlState:
                raise TypeError(
                    "main command transition requires MainCommandControlState"
                )
            reference_outcome = _live_reference_key_outcome(
                state,
                event,
                reference_bindings,
            )
            if reference_outcome is not None:
                return state, reference_outcome
            return self._main_transition(
                state,
                event,
                self._history_state,
                completion_provider,
            )

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            line = (
                self._read_control_line(
                    prompt,
                    _empty_suggestion_preview,
                    MainCommandControlState(),
                    transition,
                    configuration=configuration,
                    live_capabilities=capabilities,
                    palette_projector=palette_projector,
                    palette_renderer=palette_renderer,
                    reference_bindings=reference_bindings,
                    input_frame_provider=input_frame_provider,
                    transient=True,
                )
                if configuration is not None
                else (
                    self._read_reference_fallback(
                        _fallback_contextual_prompt(
                            prompt,
                            input_frame_provider,
                            capabilities,
                        ),
                        capabilities,
                        reference_bindings,
                    )
                    if reference_bindings is not None
                    else self._read_fallback_line(
                        _fallback_contextual_prompt(
                            prompt,
                            input_frame_provider,
                            capabilities,
                        ),
                        capabilities,
                    )
                )
            )
            self._append_completed_main_history(line)
            return line

    def read_reference_input_line(
        self,
        view: InputPromptView,
        renderer: _InputRenderer,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
        completion_provider: _CompletionProvider,
        palette_projector: _PaletteProjector,
        palette_renderer: _PaletteRenderer,
        reference_bindings: ReferenceInputBindings,
    ) -> str:
        """Read one history-free composer line with reference activation."""

        if type(view) is not InputPromptView:
            raise TypeError("view must be an InputPromptView")
        if type(reference_bindings) is not ReferenceInputBindings:
            raise TypeError("reference_bindings must be a ReferenceInputBindings")
        for name, collaborator in (
            ("renderer", renderer),
            ("suggestion_provider", suggestion_provider),
            ("completion_provider", completion_provider),
            ("palette_projector", palette_projector),
            ("palette_renderer", palette_renderer),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")
        empty_history = MainInputHistoryState()

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[
            _ControlState,
            ControlOutcome | ReferenceActivationRequest | ReferenceInputFailure | None,
        ]:
            if type(state) is not MainCommandControlState:
                raise TypeError(
                    "reference input transition requires MainCommandControlState"
                )
            reference_outcome = _live_reference_key_outcome(
                state,
                event,
                reference_bindings,
            )
            if reference_outcome is not None:
                return state, reference_outcome
            return self._main_transition(
                state,
                event,
                empty_history,
                completion_provider,
            )

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            rendered_input = renderer(view, observed_capabilities)
            if type(rendered_input) is not RenderedInputFrame:
                raise TypeError("renderer must return a RenderedInputFrame")
            if rendered_input.width != observed_capabilities.width:
                raise ValueError("rendered input width must match current capabilities")
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_reference_fallback(
                    rendered_input.plain_text,
                    capabilities,
                    reference_bindings,
                )
            return self._read_control_line(
                rendered_input.plain_text,
                suggestion_provider,
                MainCommandControlState(),
                transition,
                configuration=configuration,
                rendered_input=rendered_input,
                live_capabilities=capabilities,
                palette_projector=palette_projector,
                palette_renderer=palette_renderer,
                reference_bindings=reference_bindings,
                transient=True,
            )

    def _read_reference_fallback(
        self,
        prompt: str,
        capabilities: TerminalCapabilities,
        bindings: ReferenceInputBindings,
    ) -> str:
        """Run accessible line-mode activation with explicit redispatch."""

        line = self._read_fallback_line(prompt, capabilities)
        if not isinstance(line, str):
            raise TypeError("read_line must return a string")
        line = str.__str__(line)
        if "\r" in line or "\n" in line:
            raise ValueError("read_line must return one line without CR or LF")

        while True:
            state = MainCommandControlState(
                MainInputControlState(LineInputState(line, len(line)))
            )
            projection = _fallback_reference_projection(line, bindings)
            if projection is None:
                return line
            amended = False
            if projection.failure is not None:
                bindings.write_failure(projection.failure)
            elif projection.request is not None:
                updated = self._run_fallback_reference_activation(
                    state,
                    projection.request,
                    capabilities,
                    bindings,
                )
                if updated != state:
                    state = updated
                    line = state.main_input.line_state.buffer
                    amended = True
            elif projection.completion is not None:
                completion = projection.completion
                prefix = line[completion.start : completion.end]
                replacements = tuple(
                    candidate.replacement
                    for candidate in completion.candidates
                    if bindings.completion_candidate_matches(
                        candidate.replacement,
                        prefix,
                    )
                )
                if not replacements:
                    bindings.write_failure(ReferenceInputFailureKind.INVALID)
                else:
                    picker = bindings.initialize_picker(
                        replacements,
                        "Select a whole-file reference",
                    )
                    outcome = self._read_picker_fallback(
                        picker,
                        capabilities,
                        bindings.picker_fallback_transition,
                        bindings.picker_fallback_renderer,
                        transient=True,
                    )
                    if outcome.kind is ControlOutcomeKind.COMMIT:
                        assert outcome.text is not None
                        try:
                            state = replace_reference_completion(
                                state,
                                projection,
                                outcome.text,
                            )
                        except ReferenceInputFailure as failure:
                            bindings.write_failure(failure.kind)
                        else:
                            line = state.main_input.line_state.buffer
                            amended = True

            safe = sanitize_terminal_text(line)
            if len(safe) > 240:
                safe = f"{safe[:237]}..."
            follow_up = (
                "Press Enter to submit it, or type a replacement draft: "
                if amended
                else (
                    "Press Enter to retry the marker without submitting, or type "
                    "a replacement draft: "
                )
            )
            answer = self._read_fallback_line(
                f"Draft retained: {safe}\n{follow_up}",
                capabilities,
            )
            if not isinstance(answer, str):
                raise TypeError("read_line must return a string")
            answer = str.__str__(answer)
            if "\r" in answer or "\n" in answer:
                raise ValueError("read_line must return one line without CR or LF")
            if answer:
                line = answer
                continue
            if amended:
                return line

    def _run_fallback_reference_activation(
        self,
        origin: MainCommandControlState,
        request: ReferenceActivationRequest,
        capabilities: TerminalCapabilities,
        bindings: ReferenceInputBindings,
    ) -> MainCommandControlState:
        """Run picker/viewer children through ordinary line callbacks only."""

        active_request = request
        if request.kind is ReferenceActivationKind.PICK:
            picker = bindings.initialize_picker(
                request.paths,
                "Select a writing artifact to view",
            )
            picker_outcome = self._read_picker_fallback(
                picker,
                capabilities,
                bindings.picker_fallback_transition,
                bindings.picker_fallback_renderer,
                transient=True,
            )
            if picker_outcome.kind is not ControlOutcomeKind.COMMIT:
                return origin
            assert picker_outcome.text is not None
            try:
                active_request = selected_reference_request(
                    request,
                    picker_outcome.text,
                )
            except ReferenceInputFailure as failure:
                bindings.write_failure(failure.kind)
                return origin

        assert active_request.path is not None
        try:
            text = bindings.read_text(active_request.path)
        except ReferenceInputFailure as failure:
            bindings.write_failure(failure.kind)
            return origin
        if type(text) is not str:
            raise TypeError("reference text reader must return a string")
        try:
            viewer = bindings.initialize_viewer(text, active_request.path)
        except ValueError:
            bindings.write_failure(ReferenceInputFailureKind.UNREADABLE)
            return origin
        try:
            result = self._read_text_viewer_fallback(
                viewer,
                capabilities,
                bindings.viewer_fallback_transition,
                bindings.viewer_fallback_renderer,
            )
        finally:
            dispose_text_viewer(viewer)
        if result.kind is not TextViewerExitKind.SELECTED:
            return origin
        assert result.line_range is not None
        try:
            return replace_reference_range(
                origin,
                active_request,
                *result.line_range,
            )
        except ReferenceInputFailure as failure:
            bindings.write_failure(failure.kind)
            return origin

    def read_picker(
        self,
        state: PickerControlState,
        transition: _PickerTransition,
        fallback_transition: _PickerFallbackTransition,
        viewport_projector: _PickerViewportProjector,
        viewport_renderer: _PickerViewportRenderer,
        fallback_renderer: _PickerFallbackRenderer,
        *,
        transient: bool = False,
    ) -> ControlOutcome:
        """Read one generic picker through a dedicated history-free lifecycle."""

        if type(state) is not PickerControlState:
            raise TypeError("state must be a PickerControlState")
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        for name, collaborator in (
            ("transition", transition),
            ("fallback_transition", fallback_transition),
            ("viewport_projector", viewport_projector),
            ("viewport_renderer", viewport_renderer),
            ("fallback_renderer", fallback_renderer),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_picker_fallback(
                    state,
                    capabilities,
                    fallback_transition,
                    fallback_renderer,
                    transient=transient,
                )
            return self._read_live_picker_lifecycle(
                state,
                configuration,
                capabilities,
                transition,
                viewport_projector,
                viewport_renderer,
                transient=transient,
            )

    def _read_live_picker_lifecycle(
        self,
        state: PickerControlState,
        configuration: _TerminalConfiguration,
        capabilities: TerminalCapabilities,
        transition: _PickerTransition,
        viewport_projector: _PickerViewportProjector,
        viewport_renderer: _PickerViewportRenderer,
        *,
        transient: bool,
    ) -> ControlOutcome:
        """Own one selected-LIVE picker activation and exact restoration."""

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            raise

        display_state = _LiveEditorState()
        outcome: ControlOutcome | None = None
        active_failure: BaseException | None = None
        try:
            state, outcome = self._read_live_picker(
                state,
                configuration,
                display_state,
                capabilities,
                transition,
                viewport_projector,
                viewport_renderer,
            )
            if (
                outcome.kind
                in {
                    ControlOutcomeKind.COMMIT,
                    ControlOutcomeKind.CANCEL,
                }
                and not transient
            ):
                display_state.rendered_cursor_row = self._redraw_live_picker(
                    state,
                    display_state,
                    capabilities,
                    viewport_projector,
                    viewport_renderer,
                )
                self._write_live_text("\n")
                display_state.rendered_cursor_row = 0
            else:
                self._clear_live_display(display_state, line_break=False)
        except BaseException as failure:
            active_failure = failure
            try:
                self._clear_live_display(display_state, line_break=True)
            except BaseException:
                # Preserve the active picker failure when the output channel is
                # the collaborator that already failed.
                pass

        _restore_terminal_or_raise(configuration, active_failure)
        if active_failure is not None:
            raise active_failure
        assert outcome is not None
        return outcome

    def read_text_viewer(
        self,
        state: TextViewerState,
        transition: _ViewerTransition,
        fallback_transition: _ViewerFallbackTransition,
        viewport_projector: _ViewerViewportProjector,
        viewport_renderer: _ViewerViewportRenderer,
        fallback_renderer: _ViewerFallbackRenderer,
    ) -> TextViewerResult:
        """Run one storage-free viewer through the sole terminal lifecycle."""

        if type(state) is not TextViewerState:
            raise TypeError("state must be a TextViewerState")
        for name, collaborator in (
            ("transition", transition),
            ("fallback_transition", fallback_transition),
            ("viewport_projector", viewport_projector),
            ("viewport_renderer", viewport_renderer),
            ("fallback_renderer", fallback_renderer),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")

        with self._input_latch.input_operation():
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                return self._read_text_viewer_fallback(
                    state,
                    capabilities,
                    fallback_transition,
                    fallback_renderer,
                )
            return self._read_live_text_viewer_lifecycle(
                state,
                configuration,
                capabilities,
                transition,
                viewport_projector,
                viewport_renderer,
            )

    def open_external_view(self, operation: ExternalViewOperation) -> None:
        """Run one injected viewer while retaining sole terminal ownership."""

        if not callable(operation):
            raise TypeError("operation must be callable")
        if self._progress_active:
            raise RuntimeError("external viewer cannot overlap terminal progress")
        with self._input_latch.input_operation():
            if self._progress_active:
                raise RuntimeError("external viewer cannot overlap terminal progress")
            observed_capabilities = self._observe_input_capabilities()
            capabilities, configuration = self._prepare_input(observed_capabilities)
            if configuration is None:
                host = _BorrowedLineViewerHost(
                    lambda prompt: self._read_fallback_line(prompt, capabilities),
                    self._write_raw_text,
                    capabilities.width,
                )
                self._run_external_view_operation(operation, host)
                return
            self._run_live_external_view_lifecycle(
                operation,
                configuration,
                capabilities,
            )

    def _run_external_view_operation(
        self,
        operation: ExternalViewOperation,
        host: ExternalViewerHost,
    ) -> None:
        """Attempt the operation and idempotent host close exactly once each."""

        active_failure: BaseException | None = None
        cleanup_failure: BaseException | None = None
        try:
            operation(host)
        except BaseException as failure:
            active_failure = failure
        try:
            host.close_view()
        except BaseException as failure:
            cleanup_failure = failure
        if active_failure is not None:
            raise active_failure
        if cleanup_failure is not None:
            raise cleanup_failure

    def _run_live_external_view_lifecycle(
        self,
        operation: ExternalViewOperation,
        configuration: _TerminalConfiguration,
        capabilities: TerminalCapabilities,
    ) -> None:
        """Own raw mode and alternate screen around one external operation."""

        active_failure: BaseException | None = None
        screen_failure: BaseException | None = None
        lifecycle_failure: BaseException | None = None
        screen_cleanup_required = False
        try:
            try:
                _activate_terminal(configuration)
            except BaseException as failure:
                lifecycle_failure = failure
            else:
                try:
                    screen_cleanup_required = True
                    try:
                        self._write_live_text(_VIEWER_SCREEN_ENTER)
                        host = _RawExternalViewerHost(
                            self,
                            capabilities,
                            configuration,
                        )
                        self._run_external_view_operation(operation, host)
                    except BaseException as failure:
                        active_failure = failure
                finally:
                    if screen_cleanup_required:
                        try:
                            self._write_live_text(_VIEWER_SCREEN_EXIT)
                        except BaseException as failure:
                            screen_failure = failure
        except BaseException as failure:
            lifecycle_failure = failure
        finally:
            restoration_context = screen_failure or lifecycle_failure or active_failure
            _restore_terminal_or_raise(configuration, restoration_context)
        if active_failure is not None:
            raise active_failure
        if screen_failure is not None:
            raise screen_failure
        if lifecycle_failure is not None:
            raise lifecycle_failure

    def _read_live_external_view_event(
        self,
        configuration: _TerminalConfiguration,
    ) -> str | None:
        """Deliver one physical input without interpreting viewer behavior."""

        try:
            character = self._read_character()
        except KeyboardInterrupt:
            return "key\tinterrupt"
        except EOFError:
            return None
        if (
            configuration.interrupt_character is not None
            and character == configuration.interrupt_character
        ):
            return "key\tinterrupt"
        if (
            configuration.eof_character is not None
            and character == configuration.eof_character
        ):
            return "key\teof"
        if character == "\x1b":
            return self._read_external_view_escape(configuration)
        controls = {
            "\r": "enter",
            "\n": "enter",
            "\x08": "backspace",
            "\x7f": "backspace",
            "\x01": "ctrl_a",
            "\x05": "ctrl_e",
            "\x15": "ctrl_u",
            "\x0b": "ctrl_k",
            "\x17": "ctrl_w",
        }
        if character in controls:
            return "key\t" + controls[character]
        if ord(character) < 0x20 or character == "\x7f":
            return "key\tunknown"
        return "text\t" + character

    def _read_external_view_escape(
        self,
        configuration: _TerminalConfiguration,
    ) -> str:
        """Decode bounded physical CSI/SS3 input with a total lookahead deadline."""

        deadline = time.monotonic() + _ESCAPE_LOOKAHEAD_SECONDS

        def before_read() -> None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("external terminal character deadline expired")
            ready, _, _ = _select.select(
                (configuration.input_descriptor,), (), (), remaining
            )
            if not ready:
                raise TimeoutError("external terminal character deadline expired")

        sequence = "\x1b"
        received = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "key\tescape" if not received else "key\tunknown_escape"
            if not self._input_latch.live_reader.has_pending_codepoint:
                try:
                    ready, _, _ = _select.select(
                        (configuration.input_descriptor,), (), (), remaining
                    )
                except KeyboardInterrupt:
                    self._input_latch.reset_after_interrupt()
                    return "key\tinterrupt"
                if not ready:
                    return "key\tescape" if not received else "key\tunknown_escape"
            try:
                character = self._read_character(before_read=before_read)
            except EOFError:
                return "key\teof"
            except KeyboardInterrupt:
                return "key\tinterrupt"
            if character == configuration.interrupt_character:
                return "key\tinterrupt"
            if character == configuration.eof_character:
                return "key\teof"
            received += 1
            if received > 4_096:
                raise ValueError("external terminal escape exceeds its drain bound")
            if len(sequence) < _MAX_ESCAPE_SEQUENCE_LENGTH:
                sequence += character
            if received == 1 and character in {"[", "O"}:
                continue
            if sequence[1] == "[":
                if "@" <= character <= "~":
                    break
                if not " " <= character <= "?":
                    return "key\tunknown_escape"
                continue
            if sequence[1] == "O" and "@" <= character <= "~":
                break
            return "key\tunknown_escape"
        if received + 1 > _MAX_ESCAPE_SEQUENCE_LENGTH:
            return "key\tunknown_escape"
        if sequence == "\x1b[5~":
            return "key\tpage_up"
        if sequence == "\x1b[6~":
            return "key\tpage_down"
        event = _escape_event(sequence)
        names = {
            KeyKind.UP: "up",
            KeyKind.DOWN: "down",
            KeyKind.LEFT: "left",
            KeyKind.RIGHT: "right",
            KeyKind.HOME: "home",
            KeyKind.END: "end",
            KeyKind.DELETE: "delete",
        }
        name = (
            names.get(event.kind, "unknown_escape")
            if event is not None
            else "unknown_escape"
        )
        return "key\t" + name

    def _read_live_text_viewer_lifecycle(
        self,
        state: TextViewerState,
        configuration: _TerminalConfiguration,
        capabilities: TerminalCapabilities,
        transition: _ViewerTransition,
        viewport_projector: _ViewerViewportProjector,
        viewport_renderer: _ViewerViewportRenderer,
    ) -> TextViewerResult:
        """Own one selected-LIVE viewer screen and terminal lifecycle."""

        outcome: TextViewerResult | None = None
        active_failure: BaseException | None = None
        screen_failure: BaseException | None = None
        lifecycle_failure: BaseException | None = None
        screen_cleanup_required = False
        try:
            try:
                _activate_terminal(configuration)
            except BaseException as failure:
                lifecycle_failure = failure
            else:
                try:
                    # Pair even a partial alternate-screen entry with one exit
                    # attempt before the enclosing terminal restoration.
                    screen_cleanup_required = True
                    try:
                        self._write_live_text(_VIEWER_SCREEN_ENTER)
                        _state, outcome = self._read_live_text_viewer(
                            state,
                            configuration,
                            capabilities,
                            transition,
                            viewport_projector,
                            viewport_renderer,
                        )
                    except BaseException as failure:
                        active_failure = failure
                finally:
                    if screen_cleanup_required:
                        try:
                            self._write_live_text(_VIEWER_SCREEN_EXIT)
                        except BaseException as failure:
                            screen_failure = failure
        except BaseException as failure:
            # This boundary covers interruption gaps between activation,
            # alternate-screen cleanup, and the enclosing restoration.
            lifecycle_failure = failure
        finally:
            restoration_context = screen_failure or lifecycle_failure or active_failure
            _restore_terminal_or_raise(configuration, restoration_context)
        if screen_failure is not None:
            raise screen_failure
        if lifecycle_failure is not None:
            raise lifecycle_failure
        if active_failure is not None:
            raise active_failure
        assert outcome is not None
        return outcome

    def _read_text_viewer_fallback(
        self,
        state: TextViewerState,
        capabilities: TerminalCapabilities,
        transition: _ViewerFallbackTransition,
        renderer: _ViewerFallbackRenderer,
    ) -> TextViewerResult:
        """Read strict one-line range attempts without raw or ANSI controls."""

        while True:
            frame = renderer(state, capabilities)
            if type(frame) is not RenderedInputFrame:
                raise TypeError(
                    "viewer fallback renderer must return a RenderedInputFrame"
                )
            if frame.width != capabilities.width:
                raise ValueError(
                    "rendered viewer fallback width must match capabilities"
                )
            try:
                answer = self._read_fallback_line(
                    frame.plain_text,
                    capabilities,
                )
            except KeyboardInterrupt:
                return TextViewerResult(
                    TextViewerExitKind.INTERRUPTED,
                )
            except EOFError:
                return TextViewerResult(TextViewerExitKind.EOF)
            if not isinstance(answer, str):
                raise TypeError("read_line must return a string")
            answer = str.__str__(answer)
            if "\r" in answer or "\n" in answer:
                raise ValueError("read_line must return one line without CR or LF")
            updated, outcome = transition(state, answer)
            if type(updated) is not TextViewerState:
                raise TypeError(
                    "viewer fallback transition must return a TextViewerState"
                )
            if outcome is not None and type(outcome) is not TextViewerResult:
                raise TypeError(
                    "viewer fallback transition must return a TextViewerResult or None"
                )
            state = updated
            if outcome is not None:
                return outcome

    def _read_picker_fallback(
        self,
        state: PickerControlState,
        capabilities: TerminalCapabilities,
        transition: _PickerFallbackTransition,
        renderer: _PickerFallbackRenderer,
        *,
        transient: bool = False,
    ) -> ControlOutcome:
        """Read complete numbered attempts without raw mode or history."""

        if type(transient) is not bool:
            raise TypeError("transient must be a bool")

        while True:
            frame = renderer(state, capabilities)
            if type(frame) is not RenderedInputFrame:
                raise TypeError("fallback_renderer must return a RenderedInputFrame")
            if frame.width != capabilities.width:
                raise ValueError(
                    "rendered picker fallback width must match capabilities"
                )
            try:
                answer = self._read_fallback_line(
                    frame.plain_text,
                    capabilities,
                )
            except KeyboardInterrupt:
                return ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                return ControlOutcome(ControlOutcomeKind.EOF)
            if not isinstance(answer, str):
                raise TypeError("read_line must return a string")
            answer = str.__str__(answer)
            if "\r" in answer or "\n" in answer:
                raise ValueError("read_line must return one line without CR or LF")
            updated, outcome = transition(state, answer)
            if type(updated) is not PickerControlState:
                raise TypeError("fallback_transition must return a PickerControlState")
            if type(outcome) is not ControlOutcome:
                raise TypeError("fallback_transition must return a ControlOutcome")
            state = updated
            if outcome.kind is ControlOutcomeKind.REJECTED:
                continue
            if (
                outcome.kind
                in {
                    ControlOutcomeKind.COMMIT,
                    ControlOutcomeKind.CANCEL,
                }
                and not transient
            ):
                final_frame = renderer(state, capabilities)
                if type(final_frame) is not RenderedInputFrame:
                    raise TypeError(
                        "fallback_renderer must return a RenderedInputFrame"
                    )
                if final_frame.width != capabilities.width:
                    raise ValueError(
                        "rendered picker fallback width must match capabilities"
                    )
                self.write_text(f"{final_frame.text}\n")
            return outcome

    def _append_completed_main_history(self, line: object) -> None:
        """Append one successfully returned main line after resource cleanup."""

        # Preserve the established downstream line-validation boundary. Invalid
        # fallback shapes or framing are returned for that boundary to reject,
        # and therefore never become successfully completed history entries.
        history_line = str.__str__(line) if isinstance(line, str) else None
        if (
            history_line is not None
            and "\r" not in history_line
            and "\n" not in history_line
        ):
            updated_history = self._history_append(
                self._history_state,
                history_line,
            )
            if type(updated_history) is not MainInputHistoryState:
                raise TypeError("history_append must return a MainInputHistoryState")
            self._history_state = updated_history

    def _read_control_line(
        self,
        prompt: str,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
        initial_state: _ControlState,
        transition: _ControlTransition,
        *,
        configuration: _TerminalConfiguration,
        rendered_input: RenderedInputFrame | None = None,
        live_capabilities: TerminalCapabilities | None = None,
        palette_projector: _PaletteProjector | None = None,
        palette_renderer: _PaletteRenderer | None = None,
        reference_bindings: ReferenceInputBindings | None = None,
        input_frame_provider: _InputFrameProvider | None = None,
        transient: bool = False,
    ) -> str:
        """Read one exact buffer through one pure logical-state transition.

        The already selected LIVE terminal is placed in call-local
        noncanonical/no-echo mode and restored after submission or every
        failure. Active failures propagate after cleanup; uncertain restoration
        raises a fatal fixed ``RuntimeError`` instead.
        """

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not callable(suggestion_provider):
            raise TypeError("suggestion_provider must be callable")
        if type(initial_state) not in {
            LineInputState,
            MainInputControlState,
            MainCommandControlState,
        }:
            raise TypeError("initial_state must be an input control state")
        if not callable(transition):
            raise TypeError("transition must be callable")
        if type(configuration) is not _TerminalConfiguration:
            raise TypeError("configuration must be a _TerminalConfiguration")
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        if (
            rendered_input is not None
            and type(rendered_input) is not RenderedInputFrame
        ):
            raise TypeError("rendered_input must be a RenderedInputFrame or None")
        if rendered_input is not None and rendered_input.width != configuration.width:
            raise ValueError("rendered input and known width must match")
        if input_frame_provider is not None and not callable(input_frame_provider):
            raise TypeError("input_frame_provider must be callable or None")
        if rendered_input is not None and input_frame_provider is not None:
            raise ValueError(
                "rendered_input and input_frame_provider are mutually exclusive"
            )
        if (
            live_capabilities is not None
            and type(live_capabilities) is not TerminalCapabilities
        ):
            raise TypeError("live_capabilities must be a TerminalCapabilities or None")
        if live_capabilities is not None and (
            live_capabilities.width != configuration.width
        ):
            raise ValueError("live capabilities and known width must match")
        palette_collaborators = (
            palette_projector,
            palette_renderer,
            live_capabilities,
        )
        if any(value is not None for value in palette_collaborators) and not all(
            value is not None for value in palette_collaborators
        ):
            raise ValueError(
                "palette projection, rendering, and capabilities must be supplied together"
            )
        if palette_projector is not None and not callable(palette_projector):
            raise TypeError("palette_projector must be callable or None")
        if palette_renderer is not None and not callable(palette_renderer):
            raise TypeError("palette_renderer must be callable or None")
        if (
            reference_bindings is not None
            and type(reference_bindings) is not ReferenceInputBindings
        ):
            raise TypeError(
                "reference_bindings must be a ReferenceInputBindings or None"
            )
        if reference_bindings is not None and live_capabilities is None:
            raise ValueError(
                "reference input requires live capabilities for capable input"
            )

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            raise

        display_state = _LiveEditorState()
        line: str | None = None
        line_state: LineInputState | None = None
        active_failure: BaseException | None = None
        try:
            line, line_state = self._read_live_buffer(
                prompt,
                configuration,
                display_state,
                suggestion_provider,
                initial_state,
                transition,
                rendered_input,
                live_capabilities,
                palette_projector,
                palette_renderer,
                reference_bindings,
                input_frame_provider,
            )
            if transient:
                self._clear_live_display(display_state, line_break=False)
            else:
                self._finish_live_display(
                    prompt,
                    configuration.width,
                    display_state,
                    line_state,
                    rendered_input,
                    live_capabilities,
                    input_frame_provider,
                )
        except BaseException as failure:
            active_failure = failure
            try:
                self._clear_live_display(display_state, line_break=True)
            except BaseException:
                # The active failure remains authoritative when display cleanup
                # cannot use the same failed output collaborator.
                pass

        _restore_terminal_or_raise(configuration, active_failure)
        if active_failure is not None:
            raise active_failure
        assert line is not None
        assert line_state is not None
        return line

    def _read_live_buffer(
        self,
        prompt: str,
        configuration: _TerminalConfiguration,
        display_state: _LiveEditorState,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
        initial_state: _ControlState,
        transition: _ControlTransition,
        rendered_input: RenderedInputFrame | None,
        live_capabilities: TerminalCapabilities | None,
        palette_projector: _PaletteProjector | None,
        palette_renderer: _PaletteRenderer | None,
        reference_bindings: ReferenceInputBindings | None,
        input_frame_provider: _InputFrameProvider | None,
    ) -> tuple[str, LineInputState]:
        """Edit one code-point buffer until submit, interrupt, EOF, or failure.

        The immutable logical state is the sole buffer, cursor, navigation, and
        focus authority. ``display_state`` owns only physical escape decoding
        and redraw position. Stream failures propagate to the owning read
        boundary for display and attribute cleanup; no parser, story provider,
        persistence, or history append occurs here.
        """

        control_state = initial_state
        display_state.rendered_cursor_row = self._redraw_live_display(
            prompt,
            configuration.width,
            display_state,
            control_state,
            suggestion_provider,
            rendered_input,
            live_capabilities,
            palette_projector,
            palette_renderer,
            input_frame_provider,
        )
        while True:
            try:
                character = self._read_character()
            except EOFError:
                display_state.escape_sequence = None
                raise

            configured_event = _configured_control_event(
                character,
                configuration,
            )
            if configured_event is not None:
                control_state, outcome, changed = _apply_control_event(
                    control_state,
                    configured_event,
                    transition,
                )
                control_state, outcome, reference_changed = (
                    self._resolve_live_reference_outcome(
                        control_state,
                        outcome,
                        configuration,
                        display_state,
                        live_capabilities,
                        reference_bindings,
                    )
                )
                changed = changed or reference_changed
                completed = _completed_text_or_raise(outcome)
                if completed is not None:
                    return completed, _control_line_state(control_state)
                if changed:
                    display_state.rendered_cursor_row = self._redraw_live_display(
                        prompt,
                        configuration.width,
                        display_state,
                        control_state,
                        suggestion_provider,
                        rendered_input,
                        live_capabilities,
                        palette_projector,
                        palette_renderer,
                        input_frame_provider,
                    )
                continue

            if display_state.escape_sequence is not None:
                event, reprocess = _consume_escape_character(
                    display_state,
                    character,
                )
                if event is not None:
                    control_state, outcome, changed = _apply_control_event(
                        control_state,
                        event,
                        transition,
                    )
                    control_state, outcome, reference_changed = (
                        self._resolve_live_reference_outcome(
                            control_state,
                            outcome,
                            configuration,
                            display_state,
                            live_capabilities,
                            reference_bindings,
                        )
                    )
                    changed = changed or reference_changed
                    completed = _completed_text_or_raise(outcome)
                    if completed is not None:
                        return completed, _control_line_state(control_state)
                    if changed:
                        display_state.rendered_cursor_row = self._redraw_live_display(
                            prompt,
                            configuration.width,
                            display_state,
                            control_state,
                            suggestion_provider,
                            rendered_input,
                            live_capabilities,
                            palette_projector,
                            palette_renderer,
                            input_frame_provider,
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                if not self._escape_continuation_is_ready(configuration):
                    control_state, outcome, changed = _apply_control_event(
                        control_state,
                        KeyEvent(KeyKind.ESCAPE),
                        transition,
                    )
                    control_state, outcome, reference_changed = (
                        self._resolve_live_reference_outcome(
                            control_state,
                            outcome,
                            configuration,
                            display_state,
                            live_capabilities,
                            reference_bindings,
                        )
                    )
                    changed = changed or reference_changed
                    completed = _completed_text_or_raise(outcome)
                    if completed is not None:
                        return completed, _control_line_state(control_state)
                    if changed:
                        display_state.rendered_cursor_row = self._redraw_live_display(
                            prompt,
                            configuration.width,
                            display_state,
                            control_state,
                            suggestion_provider,
                            rendered_input,
                            live_capabilities,
                            palette_projector,
                            palette_renderer,
                            input_frame_provider,
                        )
                    continue
                display_state.escape_sequence = character
                continue
            event = _key_event(character, configuration)
            control_state, outcome, changed = _apply_control_event(
                control_state,
                event,
                transition,
            )
            control_state, outcome, reference_changed = (
                self._resolve_live_reference_outcome(
                    control_state,
                    outcome,
                    configuration,
                    display_state,
                    live_capabilities,
                    reference_bindings,
                )
            )
            changed = changed or reference_changed
            completed = _completed_text_or_raise(outcome)
            if completed is not None:
                return completed, _control_line_state(control_state)
            if changed:
                display_state.rendered_cursor_row = self._redraw_live_display(
                    prompt,
                    configuration.width,
                    display_state,
                    control_state,
                    suggestion_provider,
                    rendered_input,
                    live_capabilities,
                    palette_projector,
                    palette_renderer,
                    input_frame_provider,
                )

    def _resolve_live_reference_outcome(
        self,
        state: _ControlState,
        outcome: ControlOutcome
        | ReferenceActivationRequest
        | ReferenceInputFailure
        | None,
        configuration: _TerminalConfiguration,
        display_state: _LiveEditorState,
        capabilities: TerminalCapabilities | None,
        bindings: ReferenceInputBindings | None,
    ) -> tuple[_ControlState, ControlOutcome | None, bool]:
        """Run one nested reference interaction inside the active input latch."""

        if outcome is None or type(outcome) is ControlOutcome:
            return state, outcome, False
        if bindings is None or capabilities is None:
            raise RuntimeError("reference activation requires live bindings")
        if type(state) is not MainCommandControlState:
            raise TypeError("reference activation requires main command state")
        self._clear_live_display(display_state, line_break=False)
        if type(outcome) is ReferenceInputFailure:
            bindings.write_failure(outcome.kind)
            return state, None, True
        updated = self._run_live_reference_activation(
            state,
            outcome,
            configuration,
            display_state,
            capabilities,
            bindings,
        )
        return updated, None, True

    def _run_live_reference_activation(
        self,
        origin: MainCommandControlState,
        request: ReferenceActivationRequest,
        configuration: _TerminalConfiguration,
        display_state: _LiveEditorState,
        capabilities: TerminalCapabilities,
        bindings: ReferenceInputBindings,
    ) -> MainCommandControlState:
        """Run picker/viewer children without reacquiring raw mode or the latch."""

        active_request = request
        if request.kind is ReferenceActivationKind.PICK:
            bindings.write_failure(ReferenceInputFailureKind.INVALID)
            return origin

        assert active_request.path is not None
        try:
            text = bindings.read_text(active_request.path)
        except ReferenceInputFailure as failure:
            bindings.write_failure(failure.kind)
            return origin
        if type(text) is not str:
            raise TypeError("reference text reader must return a string")
        try:
            viewer = bindings.initialize_viewer(text, active_request.path)
        except ValueError:
            bindings.write_failure(ReferenceInputFailureKind.UNREADABLE)
            return origin

        viewer_result: TextViewerResult | None = None
        screen_failure: BaseException | None = None
        try:
            self._write_live_text(_VIEWER_SCREEN_ENTER)
            _viewer_state, viewer_result = self._read_live_text_viewer(
                viewer,
                configuration,
                capabilities,
                bindings.viewer_transition,
                bindings.viewer_viewport_projector,
                bindings.viewer_viewport_renderer,
            )
        finally:
            try:
                self._write_live_text(_VIEWER_SCREEN_EXIT)
            except BaseException as failure:
                screen_failure = failure
            dispose_text_viewer(viewer)
        if screen_failure is not None:
            raise screen_failure
        assert viewer_result is not None
        if viewer_result.kind is not TextViewerExitKind.SELECTED:
            return origin
        assert viewer_result.line_range is not None
        try:
            return replace_reference_range(
                origin,
                active_request,
                *viewer_result.line_range,
            )
        except ReferenceInputFailure as failure:
            bindings.write_failure(failure.kind)
            return origin

    def _read_live_picker(
        self,
        state: PickerControlState,
        configuration: _TerminalConfiguration,
        display_state: _LiveEditorState,
        capabilities: TerminalCapabilities,
        transition: _PickerTransition,
        viewport_projector: _PickerViewportProjector,
        viewport_renderer: _PickerViewportRenderer,
    ) -> tuple[PickerControlState, ControlOutcome]:
        """Drive one picker without fabricating an editable-line control."""

        display_state.rendered_cursor_row = self._redraw_live_picker(
            state,
            display_state,
            capabilities,
            viewport_projector,
            viewport_renderer,
        )
        while True:
            try:
                character = self._read_character()
            except KeyboardInterrupt:
                return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                display_state.escape_sequence = None
                return state, ControlOutcome(ControlOutcomeKind.EOF)

            configured_event = _configured_control_event(
                character,
                configuration,
            )
            if configured_event is not None:
                state, outcome, changed = _apply_picker_event(
                    state,
                    configured_event,
                    transition,
                )
                if outcome is not None and (
                    outcome.kind is not ControlOutcomeKind.REJECTED
                ):
                    return state, outcome
                if changed:
                    display_state.rendered_cursor_row = self._redraw_live_picker(
                        state,
                        display_state,
                        capabilities,
                        viewport_projector,
                        viewport_renderer,
                    )
                continue

            if display_state.escape_sequence is not None:
                event, reprocess = _consume_escape_character(
                    display_state,
                    character,
                )
                if event is not None:
                    state, outcome, changed = _apply_picker_event(
                        state,
                        event,
                        transition,
                    )
                    if outcome is not None and (
                        outcome.kind is not ControlOutcomeKind.REJECTED
                    ):
                        return state, outcome
                    if changed:
                        display_state.rendered_cursor_row = self._redraw_live_picker(
                            state,
                            display_state,
                            capabilities,
                            viewport_projector,
                            viewport_renderer,
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                try:
                    continuation_ready = self._escape_continuation_is_ready(
                        configuration
                    )
                except KeyboardInterrupt:
                    return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
                if not continuation_ready:
                    state, outcome, changed = _apply_picker_event(
                        state,
                        KeyEvent(KeyKind.ESCAPE),
                        transition,
                    )
                    if outcome is not None and (
                        outcome.kind is not ControlOutcomeKind.REJECTED
                    ):
                        return state, outcome
                    if changed:
                        display_state.rendered_cursor_row = self._redraw_live_picker(
                            state,
                            display_state,
                            capabilities,
                            viewport_projector,
                            viewport_renderer,
                        )
                    continue
                display_state.escape_sequence = character
                continue

            event = _key_event(character, configuration)
            state, outcome, changed = _apply_picker_event(
                state,
                event,
                transition,
            )
            if outcome is not None and (
                outcome.kind is not ControlOutcomeKind.REJECTED
            ):
                return state, outcome
            if changed:
                display_state.rendered_cursor_row = self._redraw_live_picker(
                    state,
                    display_state,
                    capabilities,
                    viewport_projector,
                    viewport_renderer,
                )

    def _read_live_text_viewer(
        self,
        state: TextViewerState,
        configuration: _TerminalConfiguration,
        capabilities: TerminalCapabilities,
        transition: _ViewerTransition,
        viewport_projector: _ViewerViewportProjector,
        viewport_renderer: _ViewerViewportRenderer,
    ) -> tuple[TextViewerState, TextViewerResult]:
        """Drive one viewer whose immutable state never enters another control."""

        self._redraw_live_text_viewer(
            state,
            capabilities,
            viewport_projector,
            viewport_renderer,
        )
        escape_sequence: _EscapeSequenceState = None
        while True:
            try:
                character = self._read_character()
            except KeyboardInterrupt:
                return state, TextViewerResult(TextViewerExitKind.INTERRUPTED)
            except EOFError:
                escape_sequence = None
                return state, TextViewerResult(TextViewerExitKind.EOF)

            configured_event = _configured_control_event(
                character,
                configuration,
            )
            if configured_event is not None:
                state, outcome = _apply_viewer_event(
                    state,
                    configured_event,
                    transition,
                )
                if outcome is not None:
                    return state, outcome
                self._redraw_live_text_viewer(
                    state,
                    capabilities,
                    viewport_projector,
                    viewport_renderer,
                )
                continue

            if escape_sequence is not None:
                escape_state = _LiveEditorState(escape_sequence=escape_sequence)
                event, reprocess = _consume_escape_character(
                    escape_state,
                    character,
                )
                escape_sequence = escape_state.escape_sequence
                if event is not None:
                    state, outcome = _apply_viewer_event(state, event, transition)
                    if outcome is not None:
                        return state, outcome
                    self._redraw_live_text_viewer(
                        state,
                        capabilities,
                        viewport_projector,
                        viewport_renderer,
                    )
                elif escape_sequence is None and not reprocess:
                    # A complete unsupported escape key is a deterministic
                    # no-op, but still forms one viewer interaction for resize
                    # observation and repaint.
                    self._redraw_live_text_viewer(
                        state,
                        capabilities,
                        viewport_projector,
                        viewport_renderer,
                    )
                if not reprocess:
                    continue

            if character == "\x1b":
                try:
                    escape_input_is_ready = self._escape_continuation_is_ready(
                        configuration
                    )
                except KeyboardInterrupt:
                    return state, TextViewerResult(
                        TextViewerExitKind.INTERRUPTED,
                    )
                if not escape_input_is_ready:
                    state, outcome = _apply_viewer_event(
                        state,
                        KeyEvent(KeyKind.ESCAPE),
                        transition,
                    )
                    assert outcome is not None
                    return state, outcome
                escape_sequence = character
                continue

            event = _key_event(character, configuration)
            state, outcome = _apply_viewer_event(state, event, transition)
            if outcome is not None:
                return state, outcome
            # Re-observe both dimensions after every non-exit key, including
            # clamped navigation and unrelated no-ops, so resize appears on the
            # next viewer interaction without installing a signal handler.
            self._redraw_live_text_viewer(
                state,
                capabilities,
                viewport_projector,
                viewport_renderer,
            )

    def _redraw_live_text_viewer(
        self,
        state: TextViewerState,
        capabilities: TerminalCapabilities,
        viewport_projector: _ViewerViewportProjector,
        viewport_renderer: _ViewerViewportRenderer,
    ) -> None:
        """Replace the alternate-screen frame using freshly observed geometry."""

        width, height = self._text_viewer_geometry(capabilities.width)
        render_width = max(1, width - 1)
        render_capabilities = _capabilities_with_width(
            capabilities,
            render_width,
        )
        viewport = viewport_projector(state, height)
        if type(viewport) is not TextViewerViewport:
            raise TypeError(
                "viewer viewport projector must return a TextViewerViewport"
            )
        frame = viewport_renderer(viewport, render_capabilities)
        if type(frame) is not RenderedFrame:
            raise TypeError("viewer renderer must return a RenderedFrame")
        if frame.width != render_width:
            raise ValueError("rendered viewer width must match the live width")
        if frame.line_count > height:
            raise ValueError("rendered viewer height exceeds the live height")
        self._write_live_text(f"{_VIEWER_SCREEN_REDRAW}{frame.text}")

    def _text_viewer_geometry(self, fallback_width: int) -> tuple[int, int]:
        """Observe bounded live columns and rows without retaining resize state."""

        width = fallback_width
        height = _DEFAULT_VIEWER_HEIGHT
        try:
            os_module = self._os_provider()
            descriptor = _stream_descriptor(self._output_stream)
            terminal_size = os_module.get_terminal_size(descriptor)
            columns = terminal_size.columns
            lines = terminal_size.lines
            if type(columns) is int and columns > 0:
                width = min(columns, 120)
            if type(lines) is int and lines > 0:
                height = min(lines, _MAX_VIEWER_HEIGHT)
        except Exception:
            pass
        return width, height

    def _redraw_live_picker(
        self,
        state: PickerControlState,
        display_state: _LiveEditorState,
        capabilities: TerminalCapabilities,
        viewport_projector: _PickerViewportProjector,
        viewport_renderer: _PickerViewportRenderer,
    ) -> int:
        """Replace one transient picker frame using a freshly observed width."""

        observed = self.capabilities()
        render_width = max(1, observed.width - 1)
        render_capabilities = _capabilities_with_width(
            capabilities,
            render_width,
        )
        viewport = viewport_projector(state)
        if type(viewport) is not PickerViewport:
            raise TypeError("viewport_projector must return a PickerViewport")
        frame = viewport_renderer(viewport, render_capabilities)
        if type(frame) is not RenderedFrame:
            raise TypeError("viewport_renderer must return a RenderedFrame")
        if frame.width != render_width:
            raise ValueError("rendered picker width must match the live width")
        payload = "".join(
            (
                _CARRIAGE_RETURN,
                _CURSOR_UP * display_state.rendered_cursor_row,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                frame.text,
            )
        )
        self._write_live_text(payload)
        return frame.line_count - 1

    def _escape_continuation_is_ready(
        self,
        configuration: _TerminalConfiguration,
    ) -> bool:
        """Prefer decoded suffixes before the bounded descriptor wait."""

        reader = self._input_latch.live_reader
        if reader.has_pending_codepoint:
            return True
        try:
            return _escape_input_is_ready(configuration.input_descriptor)
        except KeyboardInterrupt:
            self._input_latch.reset_after_interrupt()
            raise

    def _read_character(self, *, before_read: Callable[[], None] | None = None) -> str:
        """Read one decoded code point from the borrowed active input stream.

        Physical EOF raises ``EOFError``. Invalid stream return shapes raise
        without normalization so the owning read boundary can restore terminal
        state and propagate the collaborator failure.
        """

        try:
            character = self._input_latch.live_reader.read_character(
                before_read=before_read,
            )
        except KeyboardInterrupt:
            self._input_latch.reset_after_interrupt()
            raise
        if character == "":
            raise EOFError
        if not isinstance(character, str) or len(character) != 1:
            raise TypeError("input reader must return one character")
        return character

    def _redraw_live_display(
        self,
        prompt: str,
        width: int,
        display_state: _LiveEditorState,
        control_state: _ControlState,
        suggestion_provider: Callable[[str], tuple[SlashCommandSuggestion, ...]],
        rendered_input: RenderedInputFrame | None,
        live_capabilities: TerminalCapabilities | None,
        palette_projector: _PaletteProjector | None,
        palette_renderer: _PaletteRenderer | None,
        input_frame_provider: _InputFrameProvider | None,
    ) -> int:
        """Replace the current editor area and return its visible cursor row.

        Dynamic prompt, buffer, and registry tokens are sanitized before one
        write. Only fixed cursor controls are emitted. A writer or flush failure
        propagates for the owning read boundary to clear and restore resources.
        """

        line_state = _control_line_state(control_state)
        active_input = _resolve_contextual_input_frame(
            rendered_input,
            input_frame_provider,
            line_state,
            live_capabilities,
            width,
        )
        render_width = max(1, width - 1) if active_input is None else width
        input_lines, cursor_row, cursor_column = _editor_input_lines(
            prompt,
            active_input,
            line_state.buffer,
            line_state.cursor,
            render_width,
        )
        panel_lines: list[str] = []
        if palette_projector is None:
            suggestions = suggestion_provider(line_state.buffer)
            if type(suggestions) is not tuple or any(
                type(item) is not SlashCommandSuggestion for item in suggestions
            ):
                raise TypeError(
                    "suggestion_provider must return SlashCommandSuggestion values"
                )
            if suggestions:
                panel_lines.extend(_wrap_editor_text(_SUGGESTION_HEADER, render_width))
                for suggestion in suggestions:
                    panel_lines.extend(
                        _wrap_editor_text(suggestion.token, render_width)
                    )
        else:
            assert palette_renderer is not None
            assert live_capabilities is not None
            if type(control_state) is not MainCommandControlState:
                raise TypeError(
                    "semantic palette projection requires MainCommandControlState"
                )
            palette = palette_projector(control_state)
            if palette is None:
                suggestions = suggestion_provider(line_state.buffer)
                if type(suggestions) is not tuple or any(
                    type(item) is not SlashCommandSuggestion for item in suggestions
                ):
                    raise TypeError(
                        "suggestion_provider must return SlashCommandSuggestion values"
                    )
                if suggestions:
                    panel_lines.extend(
                        _wrap_editor_text(_SUGGESTION_HEADER, render_width)
                    )
                    for suggestion in suggestions:
                        panel_lines.extend(
                            _wrap_editor_text(suggestion.token, render_width)
                        )
            else:
                if type(palette) is not SuggestionPalette:
                    raise TypeError(
                        "palette_projector must return a SuggestionPalette or None"
                    )
                palette_capabilities = _capabilities_with_width(
                    live_capabilities,
                    render_width,
                )
                rendered_palette = palette_renderer(
                    palette,
                    palette_capabilities,
                )
                if type(rendered_palette) is not RenderedFrame:
                    raise TypeError("palette_renderer must return a RenderedFrame")
                if rendered_palette.width != render_width:
                    raise ValueError(
                        "rendered palette width must match the live render width"
                    )
                panel_lines.extend(rendered_palette.text.split("\n"))

        output_lines = [*input_lines, *panel_lines]
        move_to_top = _CURSOR_UP * display_state.rendered_cursor_row
        move_to_cursor = _CURSOR_UP * (len(output_lines) - 1 - cursor_row)
        payload = "".join(
            (
                _CARRIAGE_RETURN,
                move_to_top,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n".join(output_lines),
                move_to_cursor,
                _CARRIAGE_RETURN,
                _CURSOR_RIGHT * cursor_column,
            )
        )
        self._write_live_text(payload)
        return cursor_row

    def _finish_live_display(
        self,
        prompt: str,
        width: int,
        display_state: _LiveEditorState,
        line_state: LineInputState,
        rendered_input: RenderedInputFrame | None,
        live_capabilities: TerminalCapabilities | None,
        input_frame_provider: _InputFrameProvider | None,
    ) -> int:
        """Remove suggestions and leave the submitted prompt line visible.

        The exact buffer is not changed; only its sanitized width-wrapped view
        is written. Output failure propagates while terminal restoration remains
        owned by ``read_line``.
        """

        active_input = _resolve_contextual_input_frame(
            rendered_input,
            input_frame_provider,
            line_state,
            live_capabilities,
            width,
        )
        render_width = max(1, width - 1) if active_input is None else width
        input_lines, _cursor_row, _cursor_column = _editor_input_lines(
            prompt,
            active_input,
            line_state.buffer,
            len(line_state.buffer),
            render_width,
        )
        move_to_top = _CURSOR_UP * display_state.rendered_cursor_row
        payload = "".join(
            (
                _CARRIAGE_RETURN,
                move_to_top,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n".join(input_lines),
                "\n",
            )
        )
        self._write_live_text(payload)
        display_state.rendered_cursor_row = 0
        return len(input_lines)

    def _clear_live_display(
        self,
        state: _LiveEditorState,
        *,
        line_break: bool,
    ) -> None:
        """Best-effort clear one active editor area after an input failure.

        ``state`` identifies the current wrapped cursor row. The output callback
        is the only mutated collaborator; failures propagate to the caller that
        decides whether an earlier active failure remains authoritative.
        """

        payload = "".join(
            (
                _CARRIAGE_RETURN,
                _CURSOR_UP * state.rendered_cursor_row,
                _CARRIAGE_RETURN,
                _CLEAR_TO_END,
                "\n" if line_break else "",
            )
        )
        self._write_live_text(payload)
        state.rendered_cursor_row = 0

    def _write_live_text(self, text: str) -> None:
        """Write and flush one trusted redraw payload or raise on short output.

        ``text`` is assembled from fixed controls and sanitized dynamic pieces.
        The borrowed output stream is not closed; short writes are surfaced so
        the active editor boundary can restore terminal state.
        """

        self.write_text(text)


def _startup_frame(
    renderer: _StartupFrameRenderer,
    index: int,
    capabilities: TerminalCapabilities,
    physical_width: int,
) -> RenderedFrame:
    """Render and validate one frame from the fixed startup vocabulary."""

    frame = renderer(index, capabilities, physical_width)
    _require_startup_frame(frame, capabilities)
    return frame


def _static_startup_capabilities(
    capabilities: TerminalCapabilities,
    *,
    decorative_sgr: bool = False,
) -> TerminalCapabilities:
    """Remove styling and cursor authority from one startup fallback profile."""

    if type(decorative_sgr) is not bool:
        raise TypeError("decorative_sgr must be a bool")
    return TerminalCapabilities(
        CapabilityProfile.ANSI if decorative_sgr else CapabilityProfile.LINE,
        capabilities.width,
        input_tty=capabilities.input_tty,
        output_tty=capabilities.output_tty,
        raw_mode=False,
        cursor_controls=False,
        decorative_sgr=decorative_sgr,
    )


def _startup_safe_projection_width(
    capabilities: TerminalCapabilities,
    physical_width: int,
) -> int:
    """Reserve the rightmost cell only for a real terminal destination."""

    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    if type(physical_width) is not int:
        raise TypeError("physical_width must be an integer")
    if physical_width <= 0:
        raise ValueError("physical_width must be positive")
    if capabilities.output_tty:
        return max(1, physical_width - 1)
    return physical_width


def _require_startup_frame(
    frame: object,
    capabilities: TerminalCapabilities,
) -> None:
    """Require startup rendering to retain the currently observed width."""

    if type(frame) is not RenderedFrame:
        raise TypeError("startup renderer must return a RenderedFrame")
    if frame.width != capabilities.width:
        raise ValueError("startup frame width must match current capabilities")


def detect_terminal_capabilities(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    termios_module: object = _DEFAULT_TERMIOS,
    os_module: object = os,
) -> TerminalCapabilities:
    """Return a safe independent stream capability and width observation.

    Failures never claim a terminal feature. A reliable positive width is
    capped at 120 columns; absent or invalid size information falls back to 80.
    No stream is read or written and no terminal attribute is changed.
    """

    resolved_termios = (
        _termios if termios_module is _DEFAULT_TERMIOS else termios_module
    )
    try:
        input_tty = _stream_is_tty(input_stream)
    except Exception:
        input_tty = False
    try:
        output_tty = _stream_is_tty(output_stream)
    except Exception:
        output_tty = False

    try:
        environment = getattr(os_module, "environ", {})
    except Exception:
        environment = {}
    try:
        terminal_name = environment.get("TERM", "")
    except Exception:
        terminal_name = ""
    term_capable = _terminal_name_is_capable(terminal_name)
    try:
        platform_name = getattr(os_module, "name", "")
    except Exception:
        platform_name = ""
    input_descriptor_available = False
    if input_tty and _callable_attribute(input_stream, "fileno"):
        try:
            _stream_descriptor(input_stream)
            input_descriptor_available = True
        except Exception:
            pass
    output_descriptor_available = False
    if output_tty and _callable_attribute(output_stream, "fileno"):
        try:
            _stream_descriptor(output_stream)
            output_descriptor_available = True
        except Exception:
            pass
    raw_capable = bool(
        _termios_interface_is_available(resolved_termios)
        and platform_name == "posix"
        and term_capable
        and input_tty
        and output_tty
        and _callable_attribute(input_stream, "read")
        and input_descriptor_available
        and _callable_attribute(output_stream, "write")
        and _callable_attribute(output_stream, "flush")
        and output_descriptor_available
        and _callable_attribute(os_module, "get_terminal_size")
    )

    width = _DEFAULT_TERMINAL_WIDTH
    if output_tty and _callable_attribute(output_stream, "fileno"):
        try:
            output_descriptor = _stream_descriptor(output_stream)
            columns = os_module.get_terminal_size(output_descriptor).columns
            if (
                isinstance(columns, int)
                and not isinstance(columns, bool)
                and columns > 0
            ):
                width = min(columns, 120)
        except Exception:
            width = _DEFAULT_TERMINAL_WIDTH

    no_color = _no_color_is_requested(environment)
    decorative_sgr = raw_capable and not no_color
    if decorative_sgr:
        profile = CapabilityProfile.ANSI
    elif raw_capable:
        profile = CapabilityProfile.PLAIN
    else:
        profile = CapabilityProfile.LINE
    return TerminalCapabilities(
        profile,
        width,
        input_tty=input_tty,
        output_tty=output_tty,
        raw_mode=raw_capable,
        cursor_controls=raw_capable,
        decorative_sgr=decorative_sgr,
    )


def _no_color_is_requested(environment: object) -> bool:
    """Treat only nonempty, nonzero NO_COLOR text as an explicit opt-out."""

    missing = object()
    try:
        value = environment.get("NO_COLOR", missing)
    except Exception:
        return True
    if value is missing:
        return False
    if type(value) is not str:
        return True
    return value.strip() not in {"", "0"}


def _stream_is_tty(stream: TextIO) -> bool:
    """Return one exact stream TTY report or a safe false result."""

    isatty = getattr(stream, "isatty", None)
    return bool(callable(isatty) and isatty())


def _callable_attribute(owner: object, name: str) -> bool:
    """Return false when one capability attribute is absent or uncertain."""

    try:
        return callable(getattr(owner, name, None))
    except Exception:
        return False


def _termios_interface_is_available(termios_module: object | None) -> bool:
    """Return whether fixed raw-mode operations have a complete interface."""

    if termios_module is None:
        return False
    if not _callable_attribute(
        termios_module,
        "tcgetattr",
    ) or not _callable_attribute(termios_module, "tcsetattr"):
        return False
    constants = (
        "ECHO",
        "ICANON",
        "TCSADRAIN",
        "TCSANOW",
        "VEOF",
        "VINTR",
        "VMIN",
        "VTIME",
    )
    for name in constants:
        try:
            value = getattr(termios_module, name, None)
        except Exception:
            return False
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    return True


def _terminal_name_is_capable(value: object) -> bool:
    """Treat missing, blank, malformed, and dumb terminal names as uncertain."""

    return bool(
        isinstance(value, str) and value.strip() and value.strip().casefold() != "dumb"
    )


def _startup_input_discard_is_supported(termios_module: object | None) -> bool:
    """Require atomic restore-and-discard support before any startup delay."""

    if not _termios_interface_is_available(termios_module):
        return False
    try:
        flush_mode = getattr(termios_module, "TCSAFLUSH", None)
    except Exception:
        return False
    return isinstance(flush_mode, int) and not isinstance(flush_mode, bool)


def _terminal_configuration(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    termios_module: object | None,
    os_module: object,
    width: int | None = None,
    decoder_spec: LiveDecoderSpec,
) -> _TerminalConfiguration | None:
    """Return a complete POSIX live-editor setup or ``None`` for fallback.

    Capability inspection consumes no input and performs no output or terminal
    mutation. Ordinary descriptor, TTY, environment, or attribute failures are
    allowed to propagate to the public reader's safe fallback decision.
    """

    if termios_module is None or getattr(os_module, "name", "") != "posix":
        return None
    environment = getattr(os_module, "environ", {})
    terminal_name = environment.get("TERM", "")
    if not _terminal_name_is_capable(terminal_name):
        return None

    for stream in (input_stream, output_stream):
        isatty = getattr(stream, "isatty", None)
        fileno = getattr(stream, "fileno", None)
        if not callable(isatty) or not callable(fileno) or not isatty():
            return None
    if not callable(getattr(input_stream, "read", None)):
        return None
    if not callable(getattr(output_stream, "write", None)) or not callable(
        getattr(output_stream, "flush", None)
    ):
        return None

    if type(decoder_spec) is not LiveDecoderSpec:
        raise TypeError("decoder_spec must be a LiveDecoderSpec")
    input_descriptor = _stream_descriptor(input_stream)
    output_descriptor = _stream_descriptor(output_stream)
    disabled_control_byte = _disabled_control_byte(
        input_descriptor,
        os_module,
    )
    original_attributes = termios_module.tcgetattr(input_descriptor)
    editor_attributes = list(original_attributes)
    editor_attributes[6] = list(original_attributes[6])
    editor_attributes[3] &= ~(termios_module.ICANON | termios_module.ECHO)
    editor_attributes[6][termios_module.VMIN] = 1
    editor_attributes[6][termios_module.VTIME] = 0
    eof_character = _configured_control_character(
        original_attributes[6][termios_module.VEOF],
        "VEOF",
        disabled_control_byte,
        decoder_spec,
    )
    interrupt_character = _configured_control_character(
        original_attributes[6][termios_module.VINTR],
        "VINTR",
        disabled_control_byte,
        decoder_spec,
    )
    if width is None:
        columns = os_module.get_terminal_size(output_descriptor).columns
        resolved_width = (
            min(columns, 120)
            if isinstance(columns, int)
            and not isinstance(columns, bool)
            and columns > 0
            else _DEFAULT_TERMINAL_WIDTH
        )
    elif type(width) is int and width > 0:
        resolved_width = width
    else:
        raise ValueError("width must be a positive integer or None")
    return _TerminalConfiguration(
        input_descriptor=input_descriptor,
        output_descriptor=output_descriptor,
        original_attributes=original_attributes,
        editor_attributes=editor_attributes,
        width=resolved_width,
        eof_character=eof_character,
        interrupt_character=interrupt_character,
        disabled_control_byte=disabled_control_byte,
        termios_module=termios_module,
    )


def _stream_descriptor(stream: TextIO) -> int:
    """Return one nonnegative integer descriptor without touching its stream.

    Invalid descriptor values raise before terminal mutation, allowing the
    owning public reader to use its established fallback safely.
    """

    descriptor = stream.fileno()
    if (
        not isinstance(descriptor, int)
        or isinstance(descriptor, bool)
        or descriptor < 0
    ):
        raise ValueError("terminal descriptor must be a nonnegative integer")
    return descriptor


def _disabled_control_byte(
    input_descriptor: int,
    os_module: object,
) -> int:
    """Resolve this terminal's platform-specific raw disabled-control byte."""

    fpathconf = getattr(os_module, "fpathconf", None)
    if not callable(fpathconf):
        raise OSError("PC_VDISABLE is unavailable")
    value = fpathconf(input_descriptor, "PC_VDISABLE")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 255:
        raise ValueError("PC_VDISABLE must be one terminal control character")
    return value


def _configured_control_character(
    value: object,
    name: str,
    disabled_byte: int,
    decoder_spec: LiveDecoderSpec,
) -> str | None:
    """Normalize one termios control byte exactly like active stream input.

    Raw disabled values become ``None`` before decoding. Enabled bytes must
    decode to exactly one code point and encode back to the same byte under the
    stream's byte-reader codec. Text-only collaborators retain their supplied
    one-code-point semantics. Any uncertainty raises before mode activation.
    """

    if isinstance(value, str) and len(value) == 1:
        return None if ord(value) == disabled_byte else value
    if isinstance(value, bytes) and len(value) == 1:
        raw_byte = value[0]
    elif isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
        raw_byte = value
    else:
        raise ValueError(f"{name} must be one terminal control character")
    if raw_byte == disabled_byte:
        return None
    if decoder_spec.encoding == "utf-8" and (
        0x80 <= raw_byte <= 0xBF or 0xC2 <= raw_byte <= 0xF4
    ):
        raise ValueError(f"{name} is context-dependent in UTF-8 live input")

    try:
        return decoder_spec.decode_control_byte(raw_byte)
    except ValueError as failure:
        raise ValueError(
            f"{name} is not one round-tripping stream-decoded code point"
        ) from failure


def _activate_terminal(configuration: _TerminalConfiguration) -> None:
    """Apply one prepared noncanonical/no-echo input mode without reading.

    Setup failure propagates to the owner, which restores the captured original
    attributes before deciding whether fallback is still safe.
    """

    termios_module = configuration.termios_module
    termios_module.tcsetattr(
        configuration.input_descriptor,
        termios_module.TCSANOW,
        configuration.editor_attributes,
    )


def _restore_terminal_or_raise(
    configuration: _TerminalConfiguration,
    active_failure: BaseException | None,
    *,
    discard_input: bool = False,
) -> None:
    """Restore exact captured attributes or raise fatal cleanup uncertainty.

    Restoration is attempted once for normal, setup-failure, and active-failure
    paths. Startup presentation may atomically discard only input accumulated
    under its bounded no-echo guard. Restoration failure takes precedence over
    ``active_failure`` and is exposed only through fixed local diagnostics; no
    stream is closed here.
    """

    if type(discard_input) is not bool:
        raise TypeError("discard_input must be a bool")
    termios_module = configuration.termios_module
    try:
        restore_mode = (
            termios_module.TCSAFLUSH if discard_input else termios_module.TCSADRAIN
        )
        termios_module.tcsetattr(
            configuration.input_descriptor,
            restore_mode,
            configuration.original_attributes,
        )
    except BaseException as restoration_failure:
        fatal = RuntimeError("interactive terminal restoration failed")
        if active_failure is not None:
            fatal.add_note(
                "Restoration failed while another interactive input failure was active."
            )
        raise fatal from restoration_failure


def _consume_escape_character(
    state: _LiveEditorState,
    character: str,
) -> tuple[KeyEvent | None, bool]:
    """Consume one escape-sequence code point into a normalized key decision.

    The returned pair is ``(event, reprocess)``. Known CSI and application
    sequences become exact key kinds. Unknown sequence suffixes are discarded;
    a standalone Escape before a non-sequence character becomes an Escape
    event and the following character is reprocessed normally.
    """

    sequence = state.escape_sequence
    assert sequence is not None

    if sequence is _DISCARDING_ESCAPE_SEQUENCE:
        if ord(character) < 0x20 or character == "\x7f" or "@" <= character <= "~":
            state.escape_sequence = None
        return None, False

    assert type(sequence) is str
    if sequence == "\x1b":
        if character in {"[", "O"}:
            state.escape_sequence = sequence + character
            return None, False
        state.escape_sequence = None
        return KeyEvent(KeyKind.ESCAPE), True

    sequence += character
    state.escape_sequence = sequence
    if ord(character) < 0x20 or character == "\x7f":
        state.escape_sequence = None
        return None, False

    if "@" <= character <= "~":
        state.escape_sequence = None
        return _escape_event(sequence), False
    if len(sequence) >= _MAX_ESCAPE_SEQUENCE_LENGTH:
        # Bound storage in a distinct discard state so a later valid-looking
        # suffix cannot be reinterpreted as a new CSI or SS3 key.
        state.escape_sequence = _DISCARDING_ESCAPE_SEQUENCE
    return None, False


def _escape_input_is_ready(input_descriptor: int) -> bool:
    """Return whether one post-Escape code point is ready within 50 ms."""

    readable, _writable, _exceptional = _select.select(
        (input_descriptor,),
        (),
        (),
        _ESCAPE_LOOKAHEAD_SECONDS,
    )
    return bool(readable)


def _escape_event(sequence: str) -> KeyEvent | None:
    """Map supported fixed cursor sequences to normalized key events.

    Unknown sequences return ``None`` and never reach the buffer. The pure
    mapping emits no terminal output and owns no cleanup.
    """

    kinds = {
        "\x1b[A": KeyKind.UP,
        "\x1b[B": KeyKind.DOWN,
        "\x1b[C": KeyKind.RIGHT,
        "\x1b[D": KeyKind.LEFT,
        "\x1b[H": KeyKind.HOME,
        "\x1b[F": KeyKind.END,
        "\x1b[1~": KeyKind.HOME,
        "\x1b[7~": KeyKind.HOME,
        "\x1b[4~": KeyKind.END,
        "\x1b[8~": KeyKind.END,
        "\x1b[3~": KeyKind.DELETE,
        "\x1b[Z": KeyKind.SHIFT_TAB,
        "\x1b[1;2A": KeyKind.SHIFT_UP,
        "\x1b[1;2B": KeyKind.SHIFT_DOWN,
        "\x1bOA": KeyKind.UP,
        "\x1bOB": KeyKind.DOWN,
        "\x1bOC": KeyKind.RIGHT,
        "\x1bOD": KeyKind.LEFT,
        "\x1bOH": KeyKind.HOME,
        "\x1bOF": KeyKind.END,
    }
    try:
        return KeyEvent(kinds[sequence])
    except KeyError:
        return None


def _key_event(
    character: str,
    configuration: _TerminalConfiguration,
) -> KeyEvent:
    """Normalize one non-escape terminal character without external effects."""

    if (
        configuration.interrupt_character is not None
        and character == configuration.interrupt_character
    ):
        return KeyEvent(KeyKind.INTERRUPT)
    if (
        configuration.eof_character is not None
        and character == configuration.eof_character
    ):
        return KeyEvent(KeyKind.EOF)
    if character in {"\r", "\n"}:
        return KeyEvent(KeyKind.ENTER)
    if character in {"\x08", "\x7f"}:
        return KeyEvent(KeyKind.BACKSPACE)
    if character == "\t":
        return KeyEvent(KeyKind.TAB)
    return KeyEvent(KeyKind.TEXT, text=character)


def _configured_control_event(
    character: str,
    configuration: _TerminalConfiguration,
) -> KeyEvent | None:
    """Prioritize VINTR, then VEOF, before every ordinary key role."""

    if (
        configuration.interrupt_character is not None
        and character == configuration.interrupt_character
    ):
        return KeyEvent(KeyKind.INTERRUPT)
    if (
        configuration.eof_character is not None
        and character == configuration.eof_character
    ):
        return KeyEvent(KeyKind.EOF)
    return None


def _apply_control_event(
    state: _ControlState,
    event: KeyEvent,
    transition: _ControlTransition,
) -> tuple[
    _ControlState,
    ControlOutcome | ReferenceActivationRequest | ReferenceInputFailure | None,
    bool,
]:
    """Apply one pure transition and report whether its complete state changed."""

    updated, outcome = transition(state, event)
    if type(updated) is not type(state):
        raise TypeError("transition must preserve the input control state type")
    if outcome is not None and type(outcome) not in {
        ControlOutcome,
        ReferenceActivationRequest,
        ReferenceInputFailure,
    }:
        raise TypeError("transition outcome must be a supported input outcome or None")
    return updated, outcome, updated != state


def _apply_picker_event(
    state: PickerControlState,
    event: KeyEvent,
    transition: _PickerTransition,
) -> tuple[PickerControlState, ControlOutcome | None, bool]:
    """Apply one pure picker transition and validate its exact result shape."""

    updated, outcome = transition(state, event)
    if type(updated) is not PickerControlState:
        raise TypeError("transition must return a PickerControlState")
    if outcome is not None and type(outcome) is not ControlOutcome:
        raise TypeError("transition outcome must be a ControlOutcome or None")
    return updated, outcome, updated != state


def _apply_viewer_event(
    state: TextViewerState,
    event: KeyEvent,
    transition: _ViewerTransition,
) -> tuple[TextViewerState, TextViewerResult | None]:
    """Apply one pure viewer transition and validate its exact result shape."""

    updated, outcome = transition(state, event)
    if type(updated) is not TextViewerState:
        raise TypeError("transition must return a TextViewerState")
    if outcome is not None and type(outcome) is not TextViewerResult:
        raise TypeError("transition outcome must be a TextViewerResult or None")
    return updated, outcome


def _control_line_state(state: _ControlState) -> LineInputState:
    """Return the sole immutable editable line from one logical control."""

    if type(state) is LineInputState:
        return state
    if type(state) is MainInputControlState:
        return state.line_state
    if type(state) is MainCommandControlState:
        return state.main_input.line_state
    raise TypeError("state must be an input control state")


def _empty_suggestion_preview(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Return no legacy preview when a semantic palette owns the redraw."""

    if type(buffer) is not str:
        raise TypeError("buffer must be a string")
    return ()


def _empty_command_completion(
    buffer: str,
    cursor: int,
) -> CommandCompletionProjection | None:
    """Keep the legacy preview-only driver route outside selectable control."""

    if type(buffer) is not str:
        raise TypeError("buffer must be a string")
    if type(cursor) is not int or cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    return None


def _line_only_capabilities(
    capabilities: TerminalCapabilities,
) -> TerminalCapabilities:
    """Preserve observed width/TTY facts while removing live ownership."""

    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be a TerminalCapabilities")
    return TerminalCapabilities(
        CapabilityProfile.LINE,
        capabilities.width,
        input_tty=capabilities.input_tty,
        output_tty=capabilities.output_tty,
        raw_mode=False,
        cursor_controls=False,
        decorative_sgr=False,
    )


def _same_terminal_configuration(
    baseline: _TerminalConfiguration,
    current: _TerminalConfiguration,
) -> bool:
    """Compare persistent live identity while allowing kernel-local state."""

    if (
        baseline.input_descriptor != current.input_descriptor
        or baseline.output_descriptor != current.output_descriptor
        or baseline.eof_character != current.eof_character
        or baseline.interrupt_character != current.interrupt_character
        or baseline.disabled_control_byte != current.disabled_control_byte
        or baseline.termios_module is not current.termios_module
    ):
        return False
    try:
        baseline_attributes = list(baseline.original_attributes)
        current_attributes = list(current.original_attributes)
        pending_input = getattr(baseline.termios_module, "PENDIN", 0)
        if (
            type(pending_input) is int
            and pending_input > 0
            and len(baseline_attributes) > 3
            and len(current_attributes) > 3
            and type(baseline_attributes[3]) is int
            and type(current_attributes[3]) is int
        ):
            # POSIX may set PENDIN while bytes arrive during the call-local raw
            # interval. It is transient line-discipline state, not a caller's
            # terminal configuration change.
            baseline_attributes[3] &= ~pending_input
            current_attributes[3] &= ~pending_input
        return baseline_attributes == current_attributes
    except Exception:
        return False


def _same_live_capabilities(
    baseline: TerminalCapabilities,
    current: TerminalCapabilities,
) -> bool:
    """Compare selected live features while allowing viewport resize."""

    return (
        baseline.profile is current.profile
        and baseline.input_tty is current.input_tty
        and baseline.output_tty is current.output_tty
        and baseline.raw_mode is current.raw_mode
        and baseline.cursor_controls is current.cursor_controls
        and baseline.decorative_sgr is current.decorative_sgr
    )


def _capabilities_with_width(
    capabilities: TerminalCapabilities,
    width: int,
) -> TerminalCapabilities:
    """Retain one live capability profile at the cursor-safe render width."""

    return TerminalCapabilities(
        capabilities.profile,
        width,
        input_tty=capabilities.input_tty,
        output_tty=capabilities.output_tty,
        raw_mode=capabilities.raw_mode,
        cursor_controls=capabilities.cursor_controls,
        decorative_sgr=capabilities.decorative_sgr,
    )


def _completed_text_or_raise(outcome: ControlOutcome | None) -> str | None:
    """Return committed text or raise the established terminal event."""

    if outcome is None:
        return None
    if outcome.kind is ControlOutcomeKind.COMMIT:
        assert outcome.text is not None
        return outcome.text
    if outcome.kind is ControlOutcomeKind.INTERRUPT:
        raise KeyboardInterrupt
    if outcome.kind is ControlOutcomeKind.EOF:
        raise EOFError
    if outcome.kind is ControlOutcomeKind.CANCEL:
        raise _PromptCancelled
    raise ValueError(f"unsupported control outcome: {outcome.kind.value}")


def _fallback_contextual_prompt(
    prompt: str,
    input_frame_provider: _InputFrameProvider | None,
    capabilities: TerminalCapabilities,
) -> str:
    """Resolve one static accessible header before line-mode input begins."""

    if input_frame_provider is None:
        return prompt
    frame = input_frame_provider("", 0, capabilities)
    _require_contextual_input_frame(frame, capabilities.width)
    return frame.plain_text


def _resolve_contextual_input_frame(
    rendered_input: RenderedInputFrame | None,
    input_frame_provider: _InputFrameProvider | None,
    line_state: LineInputState,
    capabilities: TerminalCapabilities | None,
    width: int,
) -> RenderedInputFrame | None:
    """Resolve and validate the exact semantic frame for one live buffer."""

    if input_frame_provider is None:
        return rendered_input
    if capabilities is None:
        raise ValueError("contextual input requires live capabilities")
    frame = input_frame_provider(
        line_state.buffer,
        line_state.cursor,
        capabilities,
    )
    _require_contextual_input_frame(frame, width)
    return frame


def _require_contextual_input_frame(frame: object, width: int) -> None:
    """Require a dynamic frame to preserve the active terminal geometry."""

    if type(frame) is not RenderedInputFrame:
        raise TypeError("input_frame_provider must return a RenderedInputFrame")
    if frame.width != width:
        raise ValueError("contextual input frame width must match capabilities")


def _editor_input_lines(
    prompt: str,
    rendered_input: RenderedInputFrame | None,
    buffer: str,
    cursor: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Render one legacy or trusted semantic prompt with an editable buffer."""

    if rendered_input is None:
        return _wrapped_editor_input(prompt, buffer, cursor, width)
    if rendered_input.width != width:
        raise ValueError("rendered input width changed during an active read")
    return _wrapped_rendered_input(
        rendered_input,
        buffer,
        cursor,
        width,
    )


def _wrapped_rendered_input(
    rendered_input: RenderedInputFrame,
    buffer: str,
    cursor: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Append sanitized dynamic input to a trusted semantic prompt frame."""

    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    styled_lines = rendered_input.text.split("\n")
    plain_lines = rendered_input.plain_text.split("\n")
    if len(styled_lines) != len(plain_lines):
        raise ValueError("rendered input projections must share line topology")

    safe_before_cursor = "".join(
        _editor_display_text(character) for character in buffer[:cursor]
    )
    safe_after_cursor = "".join(
        _editor_display_text(character) for character in buffer[cursor:]
    )
    plain_prompt = plain_lines[-1]
    dynamic_lines, cursor_row, cursor_column = _wrap_safe_text_with_cursor(
        f"{plain_prompt}{safe_before_cursor}{safe_after_cursor}",
        len(plain_prompt) + len(safe_before_cursor),
        width,
    )
    if not dynamic_lines[0].startswith(plain_prompt):
        raise RuntimeError("rendered input prompt did not remain the first line")
    styled_dynamic_lines = [
        f"{styled_lines[-1]}{dynamic_lines[0][len(plain_prompt) :]}",
        *dynamic_lines[1:],
    ]
    context_count = len(styled_lines) - 1
    return (
        [*styled_lines[:-1], *styled_dynamic_lines],
        context_count + cursor_row,
        cursor_column,
    )


def _wrapped_editor_input(
    prompt: str,
    buffer: str,
    cursor: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Wrap sanitized prompt/input and locate the code-point cursor in cells.

    ``buffer`` is not normalized or mutated. The result contains safe display
    lines plus a zero-based row and column for fixed-control cursor placement;
    no terminal or external resource is accessed.
    """

    if cursor < 0 or cursor > len(buffer):
        raise ValueError("cursor must identify a buffer boundary")
    safe_prompt = _editor_display_text(prompt)
    safe_before_cursor = "".join(
        _editor_display_text(character) for character in buffer[:cursor]
    )
    safe_after_cursor = "".join(
        _editor_display_text(character) for character in buffer[cursor:]
    )
    safe_text = safe_prompt + safe_before_cursor + safe_after_cursor
    cursor_offset = len(safe_prompt) + len(safe_before_cursor)
    return _wrap_safe_text_with_cursor(safe_text, cursor_offset, width)


def _wrap_editor_text(text: str, width: int) -> list[str]:
    """Return sanitized display lines whose ordinary cell width is bounded.

    Newlines and other controls become visible escapes. The pure wrapper uses
    standard-library Unicode metadata only and performs no output or mutation.
    """

    safe_text = _editor_display_text(text)
    lines, _row, _column = _wrap_safe_text_with_cursor(
        safe_text,
        len(safe_text),
        width,
    )
    return lines


def _wrap_safe_text_with_cursor(
    safe_text: str,
    cursor_offset: int,
    width: int,
) -> tuple[list[str], int, int]:
    """Wrap already-sanitized text and map one string boundary to terminal cells.

    Combining marks use zero cells, East Asian wide/fullwidth characters use
    two, and other visible characters use one. The function is deterministic,
    standard-library-only, and side-effect free.
    """

    if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
        raise ValueError("width must be a positive integer")
    if cursor_offset < 0 or cursor_offset > len(safe_text):
        raise ValueError("cursor_offset must identify a text boundary")

    lines = [""]
    line_widths = [0]
    cursor_position: tuple[int, int] | None = None
    for index, character in enumerate(safe_text):
        character_width = _display_character_width(character)
        if line_widths[-1] and line_widths[-1] + character_width > width:
            lines.append("")
            line_widths.append(0)
        if index == cursor_offset:
            cursor_position = (len(lines) - 1, line_widths[-1])
        lines[-1] += character
        line_widths[-1] += character_width
    if cursor_position is None:
        cursor_position = (len(lines) - 1, line_widths[-1])
    return lines, cursor_position[0], cursor_position[1]


def _editor_display_text(text: str) -> str:
    """Return one terminal-safe display value with line breaks made visible.

    The original text remains untouched for submission. Sanitization performs
    no I/O and ensures untrusted controls cannot become redraw instructions.
    """

    return sanitize_terminal_text(text).replace("\n", "\\x0a")


def _display_character_width(character: str) -> int:
    """Return deterministic terminal cells for one sanitized display character.

    Combining/enclosing marks occupy zero cells, East Asian wide/fullwidth
    characters occupy two, and every other safe visible character occupies one.
    The helper performs no I/O and adds no grapheme-cluster dependency.
    """

    return display_character_width(character)


__all__ = [
    "RawTerminalDriver",
    "detect_terminal_capabilities",
]
