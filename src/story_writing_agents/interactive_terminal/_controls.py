"""Pure foundational control transitions for interactive terminal input."""

from __future__ import annotations

from collections.abc import Callable

from ._model import (
    Choice,
    CommandCompletionCandidate,
    CommandCompletionProjection,
    ComposerState,
    ControlOutcome,
    ControlOutcomeKind,
    KeyEvent,
    KeyKind,
    LineInputState,
    MainCommandControlState,
    MainCommandPaletteState,
    MainInputControlState,
    MainInputHistoryState,
    Notice,
    Picker,
    PickerControlState,
    PickerViewport,
    SemanticRole,
    SlashCommandSuggestion,
    SuggestionPalette,
    ViewState,
)


_COMPOSITION_SUGGESTION_TOKENS = ("/send", "/cancel")
_MAIN_INPUT_HISTORY_LIMIT = 100
_PICKER_WINDOW_SIZE = 7
_PICKER_EMPTY_TEXT = "No choices available."
_PICKER_VALIDATION_TEXT = (
    "Enter an enabled choice number, press Enter for the highlighted choice, "
    "or enter /cancel."
)


def transition_line_input(
    state: LineInputState,
    event: KeyEvent,
) -> tuple[LineInputState, ControlOutcome | None]:
    """Apply one normalized editing key without I/O, parsing, or persistence.

    History, palette navigation, and picker behavior are deliberately absent
    from this foundation. Their reserved keys leave the state unchanged until
    their owning delivery slices extend the pure control boundary.
    """

    if type(state) is not LineInputState:
        raise TypeError("state must be a LineInputState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")

    buffer = state.buffer
    cursor = state.cursor
    kind = event.kind
    if kind is KeyKind.TEXT:
        assert event.text is not None
        return (
            LineInputState(
                f"{buffer[:cursor]}{event.text}{buffer[cursor:]}",
                cursor + 1,
            ),
            None,
        )
    if kind is KeyKind.BACKSPACE:
        if cursor == 0:
            return state, None
        return (
            LineInputState(
                f"{buffer[: cursor - 1]}{buffer[cursor:]}",
                cursor - 1,
            ),
            None,
        )
    if kind is KeyKind.DELETE:
        if cursor == len(buffer):
            return state, None
        return LineInputState(f"{buffer[:cursor]}{buffer[cursor + 1:]}", cursor), None
    if kind is KeyKind.LEFT:
        return (LineInputState(buffer, max(0, cursor - 1)), None)
    if kind is KeyKind.RIGHT:
        return (LineInputState(buffer, min(len(buffer), cursor + 1)), None)
    if kind is KeyKind.HOME:
        return LineInputState(buffer, 0), None
    if kind is KeyKind.END:
        return LineInputState(buffer, len(buffer)), None
    if kind is KeyKind.ENTER:
        return state, ControlOutcome(ControlOutcomeKind.COMMIT, text=buffer)
    if kind is KeyKind.INTERRUPT:
        return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
    if kind is KeyKind.EOF:
        if buffer:
            return state, ControlOutcome(ControlOutcomeKind.COMMIT, text=buffer)
        return state, ControlOutcome(ControlOutcomeKind.EOF)
    return state, None


def append_main_input_history(
    history: MainInputHistoryState,
    text: str,
) -> MainInputHistoryState:
    """Append one exact nonblank main commit with oldest-first eviction."""

    if type(history) is not MainInputHistoryState:
        raise TypeError("history must be a MainInputHistoryState")
    if type(text) is not str:
        raise TypeError("text must be a string")
    if not text or text.isspace():
        return history
    entries = (*history.entries, text)
    return MainInputHistoryState(entries[-_MAIN_INPUT_HISTORY_LIMIT:])


def transition_main_input(
    state: MainInputControlState,
    event: KeyEvent,
    history: MainInputHistoryState,
) -> tuple[MainInputControlState, ControlOutcome | None]:
    """Apply one main-input key with pure history and overlay routing."""

    if type(state) is not MainInputControlState:
        raise TypeError("state must be a MainInputControlState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")
    if type(history) is not MainInputHistoryState:
        raise TypeError("history must be a MainInputHistoryState")
    if state.history_index is not None and state.history_index >= len(history.entries):
        raise ValueError("history_index must identify a stored history entry")

    if event.kind in {KeyKind.UP, KeyKind.DOWN}:
        if state.overlay_open:
            return state, None
        if event.kind is KeyKind.UP:
            if not history.entries:
                return state, None
            if state.history_index == 0:
                return state, None
            history_index = (
                len(history.entries) - 1
                if state.history_index is None
                else max(0, state.history_index - 1)
            )
            saved_draft = (
                state.line_state
                if state.saved_draft is None
                else state.saved_draft
            )
            recalled = history.entries[history_index]
            return (
                MainInputControlState(
                    LineInputState(recalled, len(recalled)),
                    history_index,
                    saved_draft,
                    overlay_open=state.overlay_open,
                ),
                None,
            )
        if state.history_index is None:
            return state, None
        if state.history_index == len(history.entries) - 1:
            assert state.saved_draft is not None
            return (
                MainInputControlState(
                    state.saved_draft,
                    overlay_open=state.overlay_open,
                ),
                None,
            )
        history_index = state.history_index + 1
        recalled = history.entries[history_index]
        return (
            MainInputControlState(
                LineInputState(recalled, len(recalled)),
                history_index,
                state.saved_draft,
                overlay_open=state.overlay_open,
            ),
            None,
        )

    line_state, outcome = transition_line_input(state.line_state, event)
    if line_state is state.line_state:
        return state, outcome
    return (
        MainInputControlState(
            line_state,
            state.history_index,
            state.saved_draft,
            overlay_open=state.overlay_open,
        ),
        outcome,
    )


def transition_main_command_input(
    state: MainCommandControlState,
    event: KeyEvent,
    history: MainInputHistoryState,
    completion_provider: Callable[
        [str, int], CommandCompletionProjection | None
    ],
) -> tuple[MainCommandControlState, ControlOutcome | None]:
    """Apply one selectable main-command key without I/O or persistence."""

    if type(state) is not MainCommandControlState:
        raise TypeError("state must be a MainCommandControlState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")
    if type(history) is not MainInputHistoryState:
        raise TypeError("history must be a MainInputHistoryState")
    if not callable(completion_provider):
        raise TypeError("completion_provider must be callable")
    if (
        state.main_input.history_index is not None
        and state.main_input.history_index >= len(history.entries)
    ):
        raise ValueError("history_index must identify a stored history entry")

    if event.kind is KeyKind.SHIFT_TAB:
        return state, None
    if event.kind in {KeyKind.UP, KeyKind.DOWN} and state.palette is not None:
        return _move_main_command_highlight(state, event.kind), None
    if event.kind is KeyKind.ESCAPE and state.palette is not None:
        restored = (
            state.main_input.line_state
            if state.palette.snapshot is None
            else state.palette.snapshot
        )
        main_input = _main_input_with_line(
            state.main_input,
            restored,
            overlay_open=False,
        )
        return MainCommandControlState(main_input, dismissal_latch=restored), None
    if event.kind is KeyKind.TAB:
        return _apply_main_command_completion(
            state,
            completion_provider,
        ), None

    updated_main, outcome = transition_main_input(
        state.main_input,
        event,
        history,
    )
    if outcome is not None:
        return (
            MainCommandControlState(
                _main_input_with_line(
                    updated_main,
                    updated_main.line_state,
                    overlay_open=False,
                )
            ),
            outcome,
        )
    if updated_main == state.main_input:
        return state, None

    history_recalled = event.kind in {KeyKind.UP, KeyKind.DOWN}
    line_changed = updated_main.line_state != state.main_input.line_state
    dismissal_latch = state.dismissal_latch
    if line_changed or history_recalled:
        dismissal_latch = None
    closed_main = _main_input_with_line(
        updated_main,
        updated_main.line_state,
        overlay_open=False,
    )
    if dismissal_latch == closed_main.line_state:
        return MainCommandControlState(
            closed_main,
            dismissal_latch=dismissal_latch,
        ), None
    return (
        _project_main_command_state(
            closed_main,
            completion_provider,
            previous_palette=state.palette,
        ),
        None,
    )


def project_main_suggestion_palette(
    state: MainCommandControlState,
) -> SuggestionPalette | None:
    """Project one open command control into the semantic palette vocabulary."""

    if type(state) is not MainCommandControlState:
        raise TypeError("state must be a MainCommandControlState")
    palette = state.palette
    if palette is None:
        return None
    choices = tuple(
        Choice(
            _main_command_choice_identity(candidate),
            candidate.replacement,
        )
        for candidate in palette.projection.candidates
    )
    highlighted_id = (
        None
        if palette.highlighted is None
        else _main_command_choice_identity(palette.highlighted)
    )
    return SuggestionPalette(
        state.main_input.line_state.buffer,
        choices,
        highlighted_id=highlighted_id,
        state=ViewState.OPEN,
    )


def initialize_picker(picker: Picker) -> PickerControlState:
    """Initialize one validated picker with deterministic highlight precedence."""

    if type(picker) is not Picker:
        raise TypeError("picker must be a Picker")
    if picker.state not in {ViewState.OPEN, ViewState.ERROR}:
        raise ValueError("an initial picker must be open or error")
    if picker.state is ViewState.ERROR and picker.notice is None:
        raise ValueError("an error picker requires an explicit notice")

    enabled = tuple(choice for choice in picker.choices if choice.enabled)
    highlighted_id = picker.highlighted_id
    if highlighted_id is None and picker.selected_id is not None:
        highlighted_id = picker.selected_id
    if highlighted_id is None and enabled:
        highlighted_id = enabled[0].identity

    if picker.state is ViewState.ERROR:
        state = ViewState.ERROR
        notice = picker.notice
    elif not picker.choices:
        state = ViewState.EMPTY
        notice = picker.notice or Notice(
            _PICKER_EMPTY_TEXT,
            label="empty",
            role=SemanticRole.MUTED,
            state=ViewState.EMPTY,
        )
    elif not enabled:
        state = ViewState.DISABLED
        notice = picker.notice
    else:
        state = ViewState.OPEN
        notice = picker.notice

    initialized = Picker(
        picker.prompt,
        picker.choices,
        highlighted_id=highlighted_id,
        selected_id=picker.selected_id,
        notice=notice,
        state=state,
    )
    window_start = _picker_window_start(
        0,
        _picker_highlighted_index(initialized),
        len(initialized.choices),
    )
    return PickerControlState(initialized, window_start)


def reconcile_picker(
    state: PickerControlState,
    choices: tuple[Choice, ...],
) -> PickerControlState:
    """Reconcile refreshed ordered choices by stable identity and former ordinal."""

    if type(state) is not PickerControlState:
        raise TypeError("state must be a PickerControlState")
    if state.picker.state in {ViewState.COMPLETED, ViewState.CANCELLED}:
        raise ValueError("a completed or cancelled picker cannot be reconciled")
    if type(choices) is not tuple or any(
        type(choice) is not Choice for choice in choices
    ):
        raise TypeError("choices must be a tuple of Choice values")

    picker = state.picker
    # Constructing the candidate vocabulary validates identity uniqueness before
    # any reconciliation result is derived.
    Picker(picker.prompt, choices)
    enabled_by_id = {
        choice.identity: choice for choice in choices if choice.enabled
    }
    selected_id = (
        picker.selected_id
        if picker.selected_id in enabled_by_id
        else None
    )
    highlighted_id = (
        picker.highlighted_id
        if picker.highlighted_id in enabled_by_id
        else None
    )
    former_index = _picker_highlighted_index(picker)
    if former_index is None:
        former_index = 0
    if highlighted_id is None:
        highlighted_id = _fallback_picker_highlight(choices, former_index)

    if picker.state is ViewState.ERROR:
        picker_state = ViewState.ERROR
    elif not choices:
        picker_state = ViewState.EMPTY
    elif not enabled_by_id:
        picker_state = ViewState.DISABLED
    else:
        picker_state = ViewState.OPEN
    notice = (
        None
        if choices and _is_default_picker_empty_notice(picker.notice)
        else picker.notice
    )
    if picker_state is ViewState.EMPTY and notice is None:
        notice = Notice(
            _PICKER_EMPTY_TEXT,
            label="empty",
            role=SemanticRole.MUTED,
            state=ViewState.EMPTY,
        )

    reconciled = Picker(
        picker.prompt,
        choices,
        highlighted_id=highlighted_id,
        selected_id=selected_id,
        notice=notice,
        state=picker_state,
    )
    clamped_start = min(
        state.window_start,
        max(0, len(choices) - _PICKER_WINDOW_SIZE),
    )
    window_start = _picker_window_start(
        clamped_start,
        _picker_highlighted_index(reconciled),
        len(choices),
    )
    return PickerControlState(reconciled, window_start)


def transition_picker(
    state: PickerControlState,
    event: KeyEvent,
) -> tuple[PickerControlState, ControlOutcome | None]:
    """Apply one raw picker key without history, completion, or side effects."""

    if type(state) is not PickerControlState:
        raise TypeError("state must be a PickerControlState")
    if type(event) is not KeyEvent:
        raise TypeError("event must be a KeyEvent")

    if event.kind is KeyKind.INTERRUPT:
        return state, ControlOutcome(ControlOutcomeKind.INTERRUPT)
    if event.kind is KeyKind.EOF:
        return state, ControlOutcome(ControlOutcomeKind.EOF)
    if event.kind is KeyKind.ESCAPE:
        cancelled = _picker_with_state(
            state,
            ViewState.CANCELLED,
            clear_validation=True,
        )
        return cancelled, ControlOutcome(ControlOutcomeKind.CANCEL)
    if state.picker.state is not ViewState.OPEN:
        return state, None
    if event.kind is KeyKind.ENTER:
        highlighted_id = state.picker.highlighted_id
        if highlighted_id is None:
            return state, None
        return _commit_picker(state, highlighted_id)
    if event.kind not in {KeyKind.UP, KeyKind.DOWN}:
        return state, None

    highlighted_index = _picker_highlighted_index(state.picker)
    if highlighted_index is None:
        return state, None
    if event.kind is KeyKind.UP:
        candidates = range(highlighted_index - 1, -1, -1)
    else:
        candidates = range(highlighted_index + 1, len(state.picker.choices))
    moved_index = next(
        (
            index
            for index in candidates
            if state.picker.choices[index].enabled
        ),
        None,
    )
    if moved_index is None:
        return state, None

    picker = state.picker
    moved = Picker(
        picker.prompt,
        picker.choices,
        highlighted_id=picker.choices[moved_index].identity,
        selected_id=picker.selected_id,
        notice=picker.notice,
        state=picker.state,
    )
    visible_start = project_picker_viewport(state).start
    return (
        PickerControlState(
            moved,
            _picker_window_start(
                visible_start,
                moved_index,
                len(moved.choices),
            ),
        ),
        None,
    )


def transition_picker_fallback(
    state: PickerControlState,
    answer: str,
) -> tuple[PickerControlState, ControlOutcome]:
    """Classify one exact numbered fallback answer without external effects."""

    if type(state) is not PickerControlState:
        raise TypeError("state must be a PickerControlState")
    if not isinstance(answer, str):
        raise TypeError("answer must be a string")
    answer = str.__str__(answer)
    if "\r" in answer or "\n" in answer:
        raise ValueError("answer must not contain CR or LF")

    if answer == "/cancel":
        cancelled = _picker_with_state(
            state,
            ViewState.CANCELLED,
            clear_validation=True,
        )
        return cancelled, ControlOutcome(ControlOutcomeKind.CANCEL)
    if state.picker.state is not ViewState.OPEN:
        return _reject_picker(state)
    if answer == "":
        if state.picker.highlighted_id is None:
            return _reject_picker(state)
        return _commit_picker(state, state.picker.highlighted_id)
    if (
        not answer.isascii()
        or not answer.isdecimal()
        or answer.startswith("0")
    ):
        return _reject_picker(state)

    try:
        ordinal = int(answer)
    except ValueError:
        return _reject_picker(state)
    if ordinal > len(state.picker.choices):
        return _reject_picker(state)
    choice = state.picker.choices[ordinal - 1]
    if not choice.enabled:
        return _reject_picker(state)
    return _commit_picker(state, choice.identity)


def project_picker_viewport(state: PickerControlState) -> PickerViewport:
    """Project an at-most-seven-row window that minimally contains highlight."""

    if type(state) is not PickerControlState:
        raise TypeError("state must be a PickerControlState")
    choice_count = len(state.picker.choices)
    if choice_count == 0:
        return PickerViewport(state.picker, 0, 0)
    start = _picker_window_start(
        min(
            state.window_start,
            max(0, choice_count - _PICKER_WINDOW_SIZE),
        ),
        _picker_highlighted_index(state.picker),
        choice_count,
    )
    return PickerViewport(
        state.picker,
        start,
        min(choice_count, start + _PICKER_WINDOW_SIZE),
    )


def _fallback_picker_highlight(
    choices: tuple[Choice, ...],
    former_index: int,
) -> str | None:
    """Choose the next enabled identity at/after an ordinal, then before it."""

    for index in range(min(former_index, len(choices)), len(choices)):
        if choices[index].enabled:
            return choices[index].identity
    for index in range(min(former_index, len(choices)) - 1, -1, -1):
        if choices[index].enabled:
            return choices[index].identity
    return None


def _picker_highlighted_index(picker: Picker) -> int | None:
    """Return the absolute zero-based highlighted index when present."""

    if picker.highlighted_id is None:
        return None
    return next(
        index
        for index, choice in enumerate(picker.choices)
        if choice.identity == picker.highlighted_id
    )


def _picker_window_start(
    start: int,
    highlighted_index: int | None,
    choice_count: int,
) -> int:
    """Clamp a window and minimally shift it around one highlighted ordinal."""

    if choice_count == 0:
        return 0
    start = min(start, max(0, choice_count - _PICKER_WINDOW_SIZE))
    if highlighted_index is None:
        return start
    if highlighted_index < start:
        return highlighted_index
    if highlighted_index >= start + _PICKER_WINDOW_SIZE:
        return highlighted_index - (_PICKER_WINDOW_SIZE - 1)
    return start


def _commit_picker(
    state: PickerControlState,
    identity: str,
) -> tuple[PickerControlState, ControlOutcome]:
    """Select and highlight one enabled identity in a completed picker."""

    picker = state.picker
    committed_index = next(
        index
        for index, choice in enumerate(picker.choices)
        if choice.identity == identity and choice.enabled
    )
    notice = (
        None
        if _is_picker_validation_notice(picker.notice)
        else picker.notice
    )
    committed = Picker(
        picker.prompt,
        picker.choices,
        highlighted_id=identity,
        selected_id=identity,
        notice=notice,
        state=ViewState.COMPLETED,
    )
    visible_start = project_picker_viewport(state).start
    return (
        PickerControlState(
            committed,
            _picker_window_start(
                visible_start,
                committed_index,
                len(committed.choices),
            ),
        ),
        ControlOutcome(ControlOutcomeKind.COMMIT, text=identity),
    )


def _reject_picker(
    state: PickerControlState,
) -> tuple[PickerControlState, ControlOutcome]:
    """Preserve picker facts while scheduling one validation notice."""

    picker = state.picker
    rejected = Picker(
        picker.prompt,
        picker.choices,
        highlighted_id=picker.highlighted_id,
        selected_id=picker.selected_id,
        notice=Notice(
            _PICKER_VALIDATION_TEXT,
            label="validation",
            role=SemanticRole.ERROR,
            state=ViewState.ERROR,
        ),
        state=picker.state,
    )
    return (
        PickerControlState(rejected, state.window_start),
        ControlOutcome(ControlOutcomeKind.REJECTED),
    )


def _picker_with_state(
    state: PickerControlState,
    picker_state: ViewState,
    *,
    clear_validation: bool,
) -> PickerControlState:
    """Copy one picker to a terminal state while preserving its selection."""

    picker = state.picker
    notice = picker.notice
    if clear_validation and _is_picker_validation_notice(notice):
        notice = None
    return PickerControlState(
        Picker(
            picker.prompt,
            picker.choices,
            highlighted_id=picker.highlighted_id,
            selected_id=picker.selected_id,
            notice=notice,
            state=picker_state,
        ),
        state.window_start,
    )


def _is_picker_validation_notice(notice: Notice | None) -> bool:
    """Identify only the fixed fallback validation notice."""

    return bool(
        notice is not None
        and notice.text == _PICKER_VALIDATION_TEXT
        and notice.label == "validation"
        and notice.role is SemanticRole.ERROR
        and notice.state is ViewState.ERROR
    )


def _is_default_picker_empty_notice(notice: Notice | None) -> bool:
    """Identify only the initializer-owned empty-state notice."""

    return bool(
        notice is not None
        and notice.text == _PICKER_EMPTY_TEXT
        and notice.label == "empty"
        and notice.role is SemanticRole.MUTED
        and notice.state is ViewState.EMPTY
    )


def _apply_main_command_completion(
    state: MainCommandControlState,
    completion_provider: Callable[
        [str, int], CommandCompletionProjection | None
    ],
) -> MainCommandControlState:
    """Apply the current highlight and retain the cycle's first undo point."""

    palette = state.palette
    if palette is None or palette.highlighted is None:
        return state
    line = state.main_input.line_state
    projection = palette.projection
    replacement = palette.highlighted.replacement
    if (
        line.buffer[projection.start : projection.end] == replacement
        and line.cursor == projection.end
    ):
        return state

    replaced = LineInputState(
        "".join(
            (
                line.buffer[: projection.start],
                replacement,
                line.buffer[projection.end :],
            )
        ),
        projection.start + len(replacement),
    )
    closed_main = _main_input_with_line(
        state.main_input,
        replaced,
        overlay_open=False,
    )
    snapshot = line if palette.snapshot is None else palette.snapshot
    return _project_main_command_state(
        closed_main,
        completion_provider,
        previous_palette=palette,
        snapshot=snapshot,
    )


def _project_main_command_state(
    main_input: MainInputControlState,
    completion_provider: Callable[
        [str, int], CommandCompletionProjection | None
    ],
    *,
    previous_palette: MainCommandPaletteState | None,
    snapshot: LineInputState | None = None,
) -> MainCommandControlState:
    """Open, filter, and correlate one projected command-completion cycle."""

    line = main_input.line_state
    projection = completion_provider(line.buffer, line.cursor)
    if projection is None:
        return MainCommandControlState(main_input)
    if type(projection) is not CommandCompletionProjection:
        raise TypeError(
            "completion_provider must return a CommandCompletionProjection or None"
        )
    if not (
        0 <= projection.start <= line.cursor <= projection.end <= len(line.buffer)
    ):
        raise ValueError("completion projection must contain the active cursor")

    prefix = line.buffer[projection.start : line.cursor]
    candidates = tuple(
        candidate
        for candidate in projection.candidates
        if _completion_candidate_matches(candidate.replacement, prefix)
    )
    filtered = CommandCompletionProjection(
        projection.position,
        projection.start,
        projection.end,
        candidates,
    )
    same_cycle = (
        previous_palette is not None
        and previous_palette.projection.position == projection.position
    )
    retained_snapshot = snapshot
    if retained_snapshot is None and same_cycle:
        retained_snapshot = previous_palette.snapshot
    highlighted = None
    if candidates:
        previous_highlight = (
            previous_palette.highlighted if same_cycle else None
        )
        highlighted = (
            previous_highlight
            if previous_highlight in candidates
            else candidates[0]
        )
    palette = MainCommandPaletteState(
        filtered,
        highlighted,
        retained_snapshot if same_cycle else None,
    )
    return MainCommandControlState(
        _main_input_with_line(main_input, line, overlay_open=True),
        palette,
    )


def _completion_candidate_matches(replacement: str, prefix: str) -> bool:
    """Match exact prefixes, accepting bare help prefixes for slash literals."""

    if replacement.startswith("/") and not prefix.startswith("/"):
        return replacement[1:].startswith(prefix)
    return replacement.startswith(prefix)


def _move_main_command_highlight(
    state: MainCommandControlState,
    kind: KeyKind,
) -> MainCommandControlState:
    """Move one open highlight by one bounded choice without line mutation."""

    palette = state.palette
    assert palette is not None
    candidates = palette.projection.candidates
    if not candidates:
        return state
    assert palette.highlighted is not None
    index = candidates.index(palette.highlighted)
    offset = -1 if kind is KeyKind.UP else 1
    moved = candidates[min(len(candidates) - 1, max(0, index + offset))]
    if moved is palette.highlighted:
        return state
    return MainCommandControlState(
        state.main_input,
        MainCommandPaletteState(
            palette.projection,
            moved,
            palette.snapshot,
        ),
    )


def _main_input_with_line(
    state: MainInputControlState,
    line: LineInputState,
    *,
    overlay_open: bool,
) -> MainInputControlState:
    """Copy one exact history cursor around a selected line and overlay flag."""

    return MainInputControlState(
        line,
        state.history_index,
        state.saved_draft,
        overlay_open=overlay_open,
    )


def _main_command_choice_identity(
    candidate: CommandCompletionCandidate,
) -> str:
    """Return one stable semantic identity from grammar position and literal."""

    return f"{candidate.position}:{candidate.replacement}"


def project_slash_suggestions(
    buffer: str,
    command_tokens: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    exclusions: frozenset[str],
) -> tuple[SlashCommandSuggestion, ...]:
    """Project exact case-sensitive prefix matches from supplied metadata.

    The caller remains the owner of canonical command metadata. This pure
    projection does not parse the buffer and has no provider, filesystem,
    terminal, lifecycle, or persistence dependency.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    if type(command_tokens) is not tuple:
        raise TypeError("command_tokens must be a tuple")
    if not isinstance(exclusions, frozenset) or any(
        type(value) is not str for value in exclusions
    ):
        raise TypeError("exclusions must be a frozenset of strings")
    if not buffer.startswith("/") or any(
        character.isspace() for character in buffer
    ):
        return ()

    matches: list[SlashCommandSuggestion] = []
    for entry in command_tokens:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not str
            or type(entry[1]) is not tuple
            or any(type(alias) is not str for alias in entry[1])
        ):
            raise TypeError("each command token entry must contain a token and aliases")
        canonical_token, aliases = entry
        if canonical_token in exclusions:
            continue
        if canonical_token.startswith(buffer):
            matches.append(
                SlashCommandSuggestion(canonical_token, canonical_token)
            )
        for alias in aliases:
            if alias.startswith(buffer):
                matches.append(SlashCommandSuggestion(alias, canonical_token))
    return tuple(matches)


def project_composition_suggestions(
    buffer: str,
) -> tuple[SlashCommandSuggestion, ...]:
    """Project the fixed ordered composition controls for one input buffer.

    Matching is whole-buffer, case-sensitive, and prefix-only. This pure
    projection performs no parser, terminal, provider, or storage operation.
    """

    if not isinstance(buffer, str):
        raise TypeError("buffer must be a string")
    if not buffer.startswith("/") or any(
        character.isspace() for character in buffer
    ):
        return ()
    return tuple(
        SlashCommandSuggestion(token, token)
        for token in _COMPOSITION_SUGGESTION_TOKENS
        if token.startswith(buffer)
    )


def transition_composer_line(
    state: ComposerState,
    line: str,
) -> tuple[ComposerState, ControlOutcome | None]:
    """Apply one exact composer line without I/O or external side effects."""

    if type(state) is not ComposerState:
        raise TypeError("state must be a ComposerState")
    if not isinstance(line, str):
        raise TypeError("line must be a string")
    line = str.__str__(line)
    if "\r" in line or "\n" in line:
        raise ValueError("line must not contain CR or LF")

    if line == "/send":
        if not state.content or state.content.isspace():
            return state, ControlOutcome(ControlOutcomeKind.REJECTED)
        return state, ControlOutcome(
            ControlOutcomeKind.COMMIT,
            text=state.content,
        )
    if line == "/cancel":
        return state, ControlOutcome(ControlOutcomeKind.CANCEL)
    if line == "/say /send":
        line = "/send"
    elif line == "/say /cancel":
        line = "/cancel"
    return ComposerState((*state.lines, line)), None


def transition_confirmation_answer(
    answer: str,
    *,
    default: bool,
) -> ControlOutcome:
    """Classify one exact confirmation answer without reading or writing."""

    if not isinstance(answer, str):
        raise TypeError("answer must be a string")
    answer = str.__str__(answer)
    if "\r" in answer or "\n" in answer:
        raise ValueError("answer must not contain CR or LF")
    if type(default) is not bool:
        raise TypeError("default must be a bool")

    normalized = answer.strip().casefold()
    if not normalized:
        normalized = "yes" if default else "no"
    elif normalized == "y":
        normalized = "yes"
    elif normalized == "n":
        normalized = "no"
    if normalized in {"yes", "no"}:
        return ControlOutcome(ControlOutcomeKind.COMMIT, text=normalized)
    return ControlOutcome(ControlOutcomeKind.REJECTED)


__all__ = [
    "transition_line_input",
    "append_main_input_history",
    "transition_main_input",
    "project_slash_suggestions",
    "project_composition_suggestions",
]
