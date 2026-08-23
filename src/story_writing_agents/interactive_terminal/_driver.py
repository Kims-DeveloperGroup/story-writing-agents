"""Own raw terminal capability, key, stream, and restoration mechanics."""

from __future__ import annotations

import asyncio
import codecs
import os
import select as _select
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TextIO, TypeAlias, TypeVar

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

try:
    import termios as _termios
except ImportError:  # pragma: no cover - exercised on non-POSIX hosts
    _termios = None


_SUGGESTION_HEADER = "[suggestions]"
_CARRIAGE_RETURN = "\r"
_CLEAR_TO_END = "\x1b[J"
_CURSOR_UP = "\x1b[A"
_CURSOR_RIGHT = "\x1b[C"
_DEFAULT_TERMINAL_WIDTH = 80
_MAX_ESCAPE_SEQUENCE_LENGTH = 64
_ESCAPE_LOOKAHEAD_SECONDS = 0.05
_DEFAULT_TERMIOS = object()
_PROGRESS_FRAME_COUNT = 4
_PROGRESS_INTERVAL_SECONDS = 0.08
_STARTUP_WELCOME_FRAME_COUNT = 4
_STARTUP_WELCOME_INTERVAL_SECONDS = 0.4
_STARTUP_NOTICE_DURATION_SECONDS = 0.8
_REDUCED_MOTION_ENVIRONMENT_KEY = "STORY_REDUCED_MOTION"
_T = TypeVar("_T")


class AdvisoryProgressError(RuntimeError):
    """Mark a terminal-owned indicator failure for fatal controller cleanup."""


@dataclass(slots=True)
class _LiveEditorState:
    """Retain only call-local escape decoding and redraw position."""

    escape_sequence: str | None = None
    rendered_cursor_row: int = 0


@dataclass(slots=True)
class _StartupFrameState:
    """Track one displayed or attempted startup frame for exact cleanup."""

    physical_line_count: int = 0


@dataclass(frozen=True, slots=True)
class _TerminalConfiguration:
    """Capture one validated POSIX terminal mode and its exact restoration."""

    input_descriptor: int
    original_attributes: list[object]
    editor_attributes: list[object]
    width: int
    eof_character: str
    interrupt_character: str
    termios_module: object


class _PromptCancelled(Exception):
    """Carry one raw Escape through the shared line cleanup boundary."""


_ControlState: TypeAlias = (
    LineInputState | MainInputControlState | MainCommandControlState
)
_ControlTransition: TypeAlias = Callable[
    [_ControlState, KeyEvent],
    tuple[_ControlState, ControlOutcome | None],
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
_PickerTransition: TypeAlias = Callable[
    [PickerControlState, KeyEvent],
    tuple[PickerControlState, ControlOutcome | None],
]
_PickerFallbackTransition: TypeAlias = Callable[
    [PickerControlState, str],
    tuple[PickerControlState, ControlOutcome],
]
_PickerViewportProjector: TypeAlias = Callable[
    [PickerControlState], PickerViewport
]
_PickerViewportRenderer: TypeAlias = Callable[
    [PickerViewport, TerminalCapabilities], RenderedFrame
]
_PickerFallbackRenderer: TypeAlias = Callable[
    [PickerControlState, TerminalCapabilities], RenderedInputFrame
]


class RawTerminalDriver:
    """Own one stream pair's live editing and raw terminal resource lifecycle."""

    __slots__ = (
        "_fallback_read_line",
        "_history_append",
        "_history_state",
        "_input_stream",
        "_line_transition",
        "_main_transition",
        "_os_provider",
        "_output_stream",
        "_progress_active",
        "_progress_frame",
        "_progress_renderer",
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
        ``read_live_line`` resolves live-terminal capabilities at call time;
        ``read_line`` delegates ordinary fallback input, whose callback remains
        responsible for its own I/O lifecycle.
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
        self._input_stream = input_stream
        self._output_stream = output_stream
        self._line_transition = line_transition
        self._main_transition = main_transition
        self._history_append = history_append
        self._history_state = MainInputHistoryState()
        self._progress_active = False
        self._progress_frame = 0
        self._progress_renderer: Callable[[int], str] | None = None
        self._termios_provider = (
            (lambda: _termios) if termios_provider is None else termios_provider
        )
        self._os_provider = (lambda: os) if os_provider is None else os_provider

    def read_line(self, prompt: str) -> str:
        """Use the borrowed line fallback without acquiring raw resources."""

        return self._fallback_read_line(prompt)

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

    def _write_raw_text(self, text: str) -> None:
        """Write one payload without progress interception."""

        written = self._output_stream.write(text)
        if written is not None and written != len(text):
            raise OSError("interactive terminal write was incomplete")
        self._output_stream.flush()

    def _clear_progress_frame(self) -> None:
        """Clear the current one-cell indicator without adding a row."""

        self._write_raw_text("\r\x1b[K")

    def _draw_progress_frame(
        self,
        frame_renderer: Callable[[int], str] | None = None,
    ) -> None:
        """Draw the current fixed indicator frame at the cursor cell."""

        frame_renderer = (
            self._progress_renderer
            if frame_renderer is None
            else frame_renderer
        )
        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        frame = frame_renderer(self._progress_frame)
        if type(frame) is not str or len(frame) != 1:
            raise TypeError("frame_renderer must return one display cell")
        self._write_raw_text(f"\r{frame}")

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

        self._progress_active = True
        self._progress_frame = 0
        self._progress_renderer = frame_renderer
        try:
            self._draw_progress_frame(frame_renderer)
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
            self._run_progress_frames(frame_renderer)
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

    async def _run_progress_frames(
        self,
        frame_renderer: Callable[[int], str],
    ) -> None:
        """Animate fixed frames until the joined operation finishes."""

        while True:
            await asyncio.sleep(_PROGRESS_INTERVAL_SECONDS)
            self._progress_frame = (
                self._progress_frame + 1
            ) % _PROGRESS_FRAME_COUNT
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
        frame_renderer: Callable[[int, TerminalCapabilities], RenderedFrame],
        sleeper: Callable[[float], object],
    ) -> None:
        """Render the one fixed bounded startup sequence or its static final."""

        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        base_capabilities = self.capabilities()
        if not self._startup_animation_is_safe(base_capabilities):
            static_capabilities = _static_startup_capabilities(base_capabilities)
            final_frame = _startup_frame(
                frame_renderer,
                _STARTUP_WELCOME_FRAME_COUNT - 1,
                static_capabilities,
            )
            self._write_raw_text(f"{final_frame.text}\n")
            return

        live_state = _StartupFrameState()
        active_failure: BaseException | None = None
        try:
            for index in range(_STARTUP_WELCOME_FRAME_COUNT):
                render_capabilities = self._startup_render_capabilities(
                    base_capabilities
                )
                frame = _startup_frame(
                    frame_renderer,
                    index,
                    render_capabilities,
                )
                self._replace_startup_frame(frame, live_state)
                if index < _STARTUP_WELCOME_FRAME_COUNT - 1:
                    sleeper(_STARTUP_WELCOME_INTERVAL_SECONDS)
            self._write_raw_text("\n")
            live_state.physical_line_count = 0
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

        if not callable(frame_renderer):
            raise TypeError("frame_renderer must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        base_capabilities = self.capabilities()
        if not self._startup_animation_is_safe(base_capabilities):
            static_capabilities = _static_startup_capabilities(base_capabilities)
            frame = frame_renderer(static_capabilities)
            _require_startup_frame(frame, static_capabilities)
            self._write_raw_text(f"{frame.text}\n")
            return
        render_capabilities = self._startup_render_capabilities(
            base_capabilities
        )
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
        self._write_raw_text(f"{_CARRIAGE_RETURN}\n")
        self.run_startup_notice(frame_renderer, sleeper)

    def _startup_animation_is_safe(
        self,
        capabilities: TerminalCapabilities,
    ) -> bool:
        """Require known ANSI cursor support and no reduced-motion request."""

        if (
            capabilities.profile is not CapabilityProfile.ANSI
            or not capabilities.raw_mode
            or not capabilities.cursor_controls
            or not capabilities.decorative_sgr
        ):
            return False
        try:
            os_module = self._os_provider()
            environment = getattr(os_module, "environ", {})
            return _REDUCED_MOTION_ENVIRONMENT_KEY not in environment
        except Exception:
            return False

    def _replace_startup_frame(
        self,
        frame: RenderedFrame,
        state: _StartupFrameState,
    ) -> None:
        """Replace one purpose-bound startup frame without claiming the screen."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        if state.physical_line_count:
            self._clear_startup_frame(state.physical_line_count)
            state.physical_line_count = 0
        state.physical_line_count = frame.line_count
        self._write_raw_text(frame.text)

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

    def _clear_startup_state(self, state: _StartupFrameState) -> None:
        """Clear an attempted frame using its stored write-time geometry."""

        if type(state) is not _StartupFrameState:
            raise TypeError("state must be a _StartupFrameState")
        if not state.physical_line_count:
            return
        self._clear_startup_frame(state.physical_line_count)
        state.physical_line_count = 0

    def _clear_startup_frame(self, line_count: int) -> None:
        """Clear one complete call-local startup frame without leaving a row."""

        if type(line_count) is not int or line_count <= 0:
            raise ValueError("line_count must be positive")
        self._write_raw_text(
            "".join(
                (
                    _CARRIAGE_RETURN,
                    _CURSOR_UP * (line_count - 1),
                    _CARRIAGE_RETURN,
                    _CLEAR_TO_END,
                )
            )
        )

    def read_live_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one history-free Unicode buffer with a preview provider."""

        return self._read_control_line(
            prompt,
            suggestion_provider,
            LineInputState(),
            self._line_transition,
        )

    def read_input_line(
        self,
        view: InputPromptView,
        renderer: _InputRenderer,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
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

        capabilities = self.capabilities()
        rendered_input = renderer(view, capabilities)
        if type(rendered_input) is not RenderedInputFrame:
            raise TypeError("renderer must return a RenderedInputFrame")
        if rendered_input.width != capabilities.width:
            raise ValueError("rendered input width must match current capabilities")
        if not capabilities.raw_mode:
            return self._fallback_read_line(rendered_input.plain_text)
        return self._read_control_line(
            rendered_input.plain_text,
            suggestion_provider,
            LineInputState(),
            self._line_transition,
            rendered_input=rendered_input,
            known_width=capabilities.width,
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

        capabilities = self.capabilities()
        rendered_input = renderer(view, capabilities)
        if type(rendered_input) is not RenderedInputFrame:
            raise TypeError("renderer must return a RenderedInputFrame")
        if rendered_input.width != capabilities.width:
            raise ValueError("rendered input width must match current capabilities")
        try:
            line = (
                self._read_control_line(
                    rendered_input.plain_text,
                    _empty_suggestion_preview,
                    LineInputState(),
                    transition,
                    rendered_input=rendered_input,
                    known_width=capabilities.width,
                    transient=transient,
                )
                if capabilities.raw_mode
                else self._fallback_read_line(rendered_input.plain_text)
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
        return ControlOutcome(ControlOutcomeKind.COMMIT, text=line)

    def read_main_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
    ) -> str:
        """Read one main line and retain its successful exact nonblank commit.

        Each read starts with inactive navigation over the driver's one
        process-local history store. The current preview list is not an open
        selectable overlay, so Up and Down remain history keys in this slice.
        History changes only after fallback success or live display cleanup and
        terminal restoration have completed successfully.
        """

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[_ControlState, ControlOutcome | None]:
            if type(state) is not MainCommandControlState:
                raise TypeError(
                    "main transition requires MainCommandControlState"
                )
            return self._main_transition(
                state,
                event,
                self._history_state,
                _empty_command_completion,
            )

        line = self._read_control_line(
            prompt,
            suggestion_provider,
            MainCommandControlState(),
            transition,
            transient=True,
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
        """Read one selectable semantic main command through call-local seams."""

        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        for name, collaborator in (
            ("completion_provider", completion_provider),
            ("palette_projector", palette_projector),
            ("palette_renderer", palette_renderer),
        ):
            if not callable(collaborator):
                raise TypeError(f"{name} must be callable")

        capabilities = self.capabilities()
        if not capabilities.raw_mode:
            line = self._fallback_read_line(prompt)
            self._append_completed_main_history(line)
            return line

        def transition(
            state: _ControlState,
            event: KeyEvent,
        ) -> tuple[_ControlState, ControlOutcome | None]:
            if type(state) is not MainCommandControlState:
                raise TypeError(
                    "main command transition requires MainCommandControlState"
                )
            return self._main_transition(
                state,
                event,
                self._history_state,
                completion_provider,
            )

        line = self._read_control_line(
            prompt,
            _empty_suggestion_preview,
            MainCommandControlState(),
            transition,
            known_width=capabilities.width,
            live_capabilities=capabilities,
            palette_projector=palette_projector,
            palette_renderer=palette_renderer,
            transient=True,
        )
        self._append_completed_main_history(line)
        return line

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

        capabilities = self.capabilities()
        if not capabilities.raw_mode:
            return self._read_picker_fallback(
                state,
                capabilities,
                fallback_transition,
                fallback_renderer,
                transient=transient,
            )
        try:
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
                termios_module=self._termios_provider(),
                os_module=self._os_provider(),
                width=capabilities.width,
            )
        except Exception:
            return self._read_picker_fallback(
                state,
                capabilities,
                fallback_transition,
                fallback_renderer,
                transient=transient,
            )
        if configuration is None:
            return self._read_picker_fallback(
                state,
                capabilities,
                fallback_transition,
                fallback_renderer,
                transient=transient,
            )

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            if isinstance(setup_failure, Exception):
                return self._read_picker_fallback(
                    state,
                    capabilities,
                    fallback_transition,
                    fallback_renderer,
                    transient=transient,
                )
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
            if outcome.kind in {
                ControlOutcomeKind.COMMIT,
                ControlOutcomeKind.CANCEL,
            } and not transient:
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
                raise TypeError(
                    "fallback_renderer must return a RenderedInputFrame"
                )
            if frame.width != capabilities.width:
                raise ValueError(
                    "rendered picker fallback width must match capabilities"
                )
            try:
                answer = self._fallback_read_line(frame.plain_text)
            except KeyboardInterrupt:
                return ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                return ControlOutcome(ControlOutcomeKind.EOF)
            if not isinstance(answer, str):
                raise TypeError("read_line must return a string")
            answer = str.__str__(answer)
            if "\r" in answer or "\n" in answer:
                raise ValueError(
                    "read_line must return one line without CR or LF"
                )
            updated, outcome = transition(state, answer)
            if type(updated) is not PickerControlState:
                raise TypeError(
                    "fallback_transition must return a PickerControlState"
                )
            if type(outcome) is not ControlOutcome:
                raise TypeError(
                    "fallback_transition must return a ControlOutcome"
                )
            state = updated
            if outcome.kind is ControlOutcomeKind.REJECTED:
                continue
            if outcome.kind in {
                ControlOutcomeKind.COMMIT,
                ControlOutcomeKind.CANCEL,
            } and not transient:
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
                raise TypeError(
                    "history_append must return a MainInputHistoryState"
                )
            self._history_state = updated_history

    def _read_control_line(
        self,
        prompt: str,
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
        initial_state: _ControlState,
        transition: _ControlTransition,
        *,
        rendered_input: RenderedInputFrame | None = None,
        known_width: int | None = None,
        live_capabilities: TerminalCapabilities | None = None,
        palette_projector: _PaletteProjector | None = None,
        palette_renderer: _PaletteRenderer | None = None,
        transient: bool = False,
    ) -> str:
        """Read one exact buffer through one pure logical-state transition.

        A capable POSIX terminal is placed in call-local noncanonical/no-echo
        mode and restored after submission or every failure. Unsupported or
        safely recoverable setup uses the borrowed fallback before consuming
        input. Active failures propagate after cleanup; uncertain restoration
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
        if type(transient) is not bool:
            raise TypeError("transient must be a bool")
        if (
            rendered_input is not None
            and type(rendered_input) is not RenderedInputFrame
        ):
            raise TypeError("rendered_input must be a RenderedInputFrame or None")
        if known_width is not None and (
            type(known_width) is not int or known_width <= 0
        ):
            raise ValueError("known_width must be a positive integer or None")
        if rendered_input is not None and known_width is None:
            raise ValueError("known_width is required with rendered_input")
        if rendered_input is not None and rendered_input.width != known_width:
            raise ValueError("rendered input and known width must match")
        if live_capabilities is not None and type(
            live_capabilities
        ) is not TerminalCapabilities:
            raise TypeError(
                "live_capabilities must be a TerminalCapabilities or None"
            )
        if live_capabilities is not None and (
            known_width is None or live_capabilities.width != known_width
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

        try:
            configuration = _terminal_configuration(
                self._input_stream,
                self._output_stream,
                termios_module=self._termios_provider(),
                os_module=self._os_provider(),
                width=known_width,
            )
        except Exception:
            return self._fallback_read_line(prompt)
        if configuration is None:
            return self._fallback_read_line(prompt)

        try:
            _activate_terminal(configuration)
        except BaseException as setup_failure:
            _restore_terminal_or_raise(configuration, setup_failure)
            if isinstance(setup_failure, Exception):
                return self._fallback_read_line(prompt)
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
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
        initial_state: _ControlState,
        transition: _ControlTransition,
        rendered_input: RenderedInputFrame | None,
        live_capabilities: TerminalCapabilities | None,
        palette_projector: _PaletteProjector | None,
        palette_renderer: _PaletteRenderer | None,
    ) -> tuple[str, LineInputState]:
        """Edit one code-point buffer until submit, interrupt, EOF, or failure.

        The immutable logical state is the sole buffer, cursor, navigation, and
        focus authority. ``display_state`` owns only physical escape decoding
        and redraw position. Stream failures propagate to the owning read
        boundary for display and attribute cleanup; no parser, story provider,
        persistence, or history append occurs here.
        """

        control_state = initial_state
        character_reader = _single_character_reader(self._input_stream)
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
        )
        while True:
            character = self._read_character(character_reader)

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
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                if not _escape_input_is_ready(configuration.input_descriptor):
                    control_state, outcome, changed = _apply_control_event(
                        control_state,
                        KeyEvent(KeyKind.ESCAPE),
                        transition,
                    )
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
                )

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

        character_reader = _single_character_reader(self._input_stream)
        display_state.rendered_cursor_row = self._redraw_live_picker(
            state,
            display_state,
            capabilities,
            viewport_projector,
            viewport_renderer,
        )
        while True:
            try:
                character = self._read_character(character_reader)
            except KeyboardInterrupt:
                return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
            except EOFError:
                return state, ControlOutcome(ControlOutcomeKind.EOF)

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
                        display_state.rendered_cursor_row = (
                            self._redraw_live_picker(
                                state,
                                display_state,
                                capabilities,
                                viewport_projector,
                                viewport_renderer,
                            )
                        )
                if not reprocess:
                    continue

            if character == "\x1b":
                if not _escape_input_is_ready(configuration.input_descriptor):
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
                        display_state.rendered_cursor_row = (
                            self._redraw_live_picker(
                                state,
                                display_state,
                                capabilities,
                                viewport_projector,
                                viewport_renderer,
                            )
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
            raise TypeError(
                "viewport_projector must return a PickerViewport"
            )
        frame = viewport_renderer(viewport, render_capabilities)
        if type(frame) is not RenderedFrame:
            raise TypeError(
                "viewport_renderer must return a RenderedFrame"
            )
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

    def _read_character(
        self,
        reader: Callable[[], object] | None = None,
    ) -> str:
        """Read one decoded code point from the borrowed active input stream.

        Physical EOF raises ``EOFError``. Invalid stream return shapes raise
        without normalization so the owning read boundary can restore terminal
        state and propagate the collaborator failure.
        """

        character = (
            self._input_stream.read(1) if reader is None else reader()
        )
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
        suggestion_provider: Callable[
            [str], tuple[SlashCommandSuggestion, ...]
        ],
        rendered_input: RenderedInputFrame | None,
        live_capabilities: TerminalCapabilities | None,
        palette_projector: _PaletteProjector | None,
        palette_renderer: _PaletteRenderer | None,
    ) -> int:
        """Replace the current editor area and return its visible cursor row.

        Dynamic prompt, buffer, and registry tokens are sanitized before one
        write. Only fixed cursor controls are emitted. A writer or flush failure
        propagates for the owning read boundary to clear and restore resources.
        """

        render_width = (
            max(1, width - 1) if rendered_input is None else width
        )
        line_state = _control_line_state(control_state)
        input_lines, cursor_row, cursor_column = _editor_input_lines(
            prompt,
            rendered_input,
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
                panel_lines.extend(
                    _wrap_editor_text(_SUGGESTION_HEADER, render_width)
                )
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
            if palette is not None:
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
        move_to_cursor = _CURSOR_UP * (
            len(output_lines) - 1 - cursor_row
        )
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
    ) -> int:
        """Remove suggestions and leave the submitted prompt line visible.

        The exact buffer is not changed; only its sanitized width-wrapped view
        is written. Output failure propagates while terminal restoration remains
        owned by ``read_line``.
        """

        render_width = (
            max(1, width - 1) if rendered_input is None else width
        )
        input_lines, _cursor_row, _cursor_column = _editor_input_lines(
            prompt,
            rendered_input,
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
    renderer: Callable[[int, TerminalCapabilities], RenderedFrame],
    index: int,
    capabilities: TerminalCapabilities,
) -> RenderedFrame:
    """Render and validate one frame from the fixed startup vocabulary."""

    frame = renderer(index, capabilities)
    _require_startup_frame(frame, capabilities)
    return frame


def _static_startup_capabilities(
    capabilities: TerminalCapabilities,
) -> TerminalCapabilities:
    """Remove styling and cursor authority from one startup fallback profile."""

    return TerminalCapabilities(
        CapabilityProfile.LINE,
        capabilities.width,
        input_tty=capabilities.input_tty,
        output_tty=capabilities.output_tty,
        raw_mode=False,
        cursor_controls=False,
        decorative_sgr=False,
    )


def _require_startup_frame(
    frame: object,
    capabilities: TerminalCapabilities,
) -> None:
    """Require startup rendering to retain the currently observed width."""

    if type(frame) is not RenderedFrame:
        raise TypeError("startup renderer must return a RenderedFrame")
    if frame.width != capabilities.width:
        raise ValueError("startup frame width must match current capabilities")


def _single_character_reader(input_stream: TextIO) -> Callable[[], object]:
    """Return one call-local code-point reader without terminal read-ahead.

    ``TextIOWrapper.read(1)`` may prefetch an entire escape sequence into its
    character buffer, which makes descriptor readiness incorrectly report a
    standalone Escape. When its byte buffer offers ``read1``, this reader asks
    for exactly one byte and incrementally restores the stream's configured
    text decoding. Other stream collaborators keep their established
    one-code-point ``read(1)`` contract.
    """

    byte_stream = getattr(input_stream, "buffer", None)
    read_one_byte = getattr(byte_stream, "read1", None)
    encoding = getattr(input_stream, "encoding", None)
    if not callable(read_one_byte) or not isinstance(encoding, str) or not encoding:
        return lambda: input_stream.read(1)
    errors = getattr(input_stream, "errors", None)
    if not isinstance(errors, str) or not errors:
        errors = "strict"
    decoder = codecs.getincrementaldecoder(encoding)(errors=errors)

    def read_character() -> object:
        while True:
            chunk = read_one_byte(1)
            if chunk == b"":
                remainder = decoder.decode(b"", final=True)
                return remainder if remainder else ""
            if type(chunk) is not bytes or len(chunk) != 1:
                raise TypeError("input byte stream read1(1) must return one byte")
            character = decoder.decode(chunk, final=False)
            if character:
                return character

    return read_character


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

    no_color = False
    try:
        no_color = "NO_COLOR" in environment
    except Exception:
        no_color = True
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
        isinstance(value, str)
        and value.strip()
        and value.strip().casefold() != "dumb"
    )


def _terminal_configuration(
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    termios_module: object | None,
    os_module: object,
    width: int | None = None,
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

    input_descriptor = _stream_descriptor(input_stream)
    output_descriptor = _stream_descriptor(output_stream)
    original_attributes = termios_module.tcgetattr(input_descriptor)
    editor_attributes = list(original_attributes)
    editor_attributes[6] = list(original_attributes[6])
    editor_attributes[3] &= ~(termios_module.ICANON | termios_module.ECHO)
    editor_attributes[6][termios_module.VMIN] = 1
    editor_attributes[6][termios_module.VTIME] = 0
    eof_character = _configured_control_character(
        original_attributes[6][termios_module.VEOF],
        "VEOF",
    )
    interrupt_character = _configured_control_character(
        original_attributes[6][termios_module.VINTR],
        "VINTR",
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
        original_attributes=original_attributes,
        editor_attributes=editor_attributes,
        width=resolved_width,
        eof_character=eof_character,
        interrupt_character=interrupt_character,
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


def _configured_control_character(value: object, name: str) -> str:
    """Decode one termios control character for active input comparison.

    The returned code point is not displayed. Unsupported termios shapes raise
    before mode activation so fallback can proceed without cleanup.
    """

    if isinstance(value, bytes) and len(value) == 1:
        return value.decode("latin-1")
    if isinstance(value, str) and len(value) == 1:
        return value
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255:
        return chr(value)
    raise ValueError(f"{name} must be one terminal control character")


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
) -> None:
    """Restore exact captured attributes or raise fatal cleanup uncertainty.

    Restoration is attempted once for normal, setup-failure, and active-failure
    paths. Its failure takes precedence over ``active_failure`` and is exposed
    only through fixed local diagnostics; no stream is closed here.
    """

    termios_module = configuration.termios_module
    try:
        termios_module.tcsetattr(
            configuration.input_descriptor,
            termios_module.TCSADRAIN,
            configuration.original_attributes,
        )
    except BaseException as restoration_failure:
        fatal = RuntimeError("interactive terminal restoration failed")
        if active_failure is not None:
            fatal.add_note(
                "Restoration failed while another interactive input failure "
                "was active."
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
        return None, True

    if sequence.startswith("\x1bO"):
        state.escape_sequence = None
        return _escape_event(sequence), False

    if "@" <= character <= "~":
        state.escape_sequence = None
        return _escape_event(sequence), False
    if len(sequence) >= _MAX_ESCAPE_SEQUENCE_LENGTH:
        # Keep consuming the unknown CSI until its final byte so no suffix can
        # leak into the submitted buffer.
        state.escape_sequence = "\x1b["
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

    if character in {"\r", "\n"}:
        return KeyEvent(KeyKind.ENTER)
    if character == configuration.interrupt_character:
        return KeyEvent(KeyKind.INTERRUPT)
    if character == configuration.eof_character:
        return KeyEvent(KeyKind.EOF)
    if character in {"\x08", "\x7f"}:
        return KeyEvent(KeyKind.BACKSPACE)
    if character == "\t":
        return KeyEvent(KeyKind.TAB)
    return KeyEvent(KeyKind.TEXT, text=character)


def _apply_control_event(
    state: _ControlState,
    event: KeyEvent,
    transition: _ControlTransition,
) -> tuple[_ControlState, ControlOutcome | None, bool]:
    """Apply one pure transition and report whether its complete state changed."""

    updated, outcome = transition(state, event)
    if type(updated) is not type(state):
        raise TypeError("transition must preserve the input control state type")
    if outcome is not None and type(outcome) is not ControlOutcome:
        raise TypeError("transition outcome must be a ControlOutcome or None")
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
        f"{styled_lines[-1]}{dynamic_lines[0][len(plain_prompt):]}",
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
