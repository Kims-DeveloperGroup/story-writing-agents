"""Pure responsive rendering for safe plain and fixed-theme terminal views."""

from __future__ import annotations

import json
import re

from ._model import (
    CapabilityProfile,
    Choice,
    Component,
    Field,
    InputPromptView,
    ListView,
    Message,
    Notice,
    Panel,
    Picker,
    PickerControlState,
    PickerViewport,
    Prompt,
    Record,
    RenderedFrame,
    RenderedInputFrame,
    Section,
    SemanticRole,
    SuggestionPalette,
    TerminalCapabilities,
    ViewState,
    display_character_width,
    sanitize_terminal_text,
)


_RESET = "\x1b[0m"
_ROLE_SGR = {
    SemanticRole.DEFAULT: "",
    SemanticRole.EMPHASIS: "\x1b[1m",
    SemanticRole.MUTED: "\x1b[37m",
    SemanticRole.INFO: "\x1b[36m",
    SemanticRole.SUCCESS: "\x1b[32m",
    SemanticRole.WARNING: "\x1b[33m",
    SemanticRole.ERROR: "\x1b[31m",
    SemanticRole.COMMAND: "\x1b[35m",
    SemanticRole.AGENT: "\x1b[1m",
}
_DECORATIVE_SGR = re.compile(r"\x1b\[(?:0|1|3[1-7])m")
_ADVISORY_PROGRESS_FRAMES = ("◐", "◓", "◑", "◒")


def render_advisory_progress_frame(frame: int) -> str:
    """Render one pure fixed-cell advisory progress frame."""

    if type(frame) is not int:
        raise TypeError("frame must be an integer")
    if frame < 0 or frame >= len(_ADVISORY_PROGRESS_FRAMES):
        raise ValueError("frame must identify one advisory progress glyph")
    return _ADVISORY_PROGRESS_FRAMES[frame]


def format_agent_reply_content(content: str) -> str:
    """Pretty-print one complete strict JSON object or array, if valid."""

    if type(content) is not str:
        raise TypeError("content must be a string")
    try:
        value = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_object_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
        return content
    if type(value) not in {dict, list}:
        return content
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return content


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject duplicate JSON object keys while preserving source order."""

    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _reject_nonfinite_constant(value: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""

    raise ValueError(f"non-finite JSON constant: {value}")


def render_component(
    component: Component,
    capabilities: TerminalCapabilities,
) -> RenderedFrame:
    """Render one closed semantic component without writing or mutation."""

    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    lines = _component_lines(component, capabilities.width, indent=0)
    if not lines:
        lines = [("", SemanticRole.DEFAULT)]
    plain = "\n".join(line for line, _role in lines)
    if capabilities.decorative_sgr:
        text = "\n".join(_decorate(line, role) for line, role in lines)
        if strip_decorative_sgr(text) != plain:
            raise RuntimeError("decorative rendering changed semantic text")
    else:
        text = plain
    return RenderedFrame(text, capabilities.width, len(lines))


def render_plain(component: Component, *, width: int = 80) -> RenderedFrame:
    """Render one deterministic structured plain frame for tests or callbacks."""

    return render_component(
        component,
        TerminalCapabilities(
            CapabilityProfile.LINE,
            width,
            input_tty=False,
            output_tty=False,
            raw_mode=False,
            cursor_controls=False,
            decorative_sgr=False,
        ),
    )


def render_input_prompt(
    view: InputPromptView,
    capabilities: TerminalCapabilities,
) -> RenderedInputFrame:
    """Render ordered context and one final focused prompt for input."""

    if type(view) is not InputPromptView:
        raise TypeError("view must be an InputPromptView")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")

    lines: list[tuple[str, SemanticRole]] = []
    for item in view.context:
        lines.extend(_component_lines(item, capabilities.width, indent=0))
    lines.extend(_input_prompt_lines(view.prompt, capabilities.width))
    plain = "\n".join(line for line, _role in lines)
    if not plain.endswith(" "):
        raise RuntimeError("input rendering lost its trailing space")

    if capabilities.decorative_sgr:
        decorated = [
            _decorate(line, role) for line, role in lines[:-1]
        ]
        final_line, final_role = lines[-1]
        decorated.append(f"{_decorate(final_line[:-1], final_role)} ")
        text = "\n".join(decorated)
        if strip_decorative_sgr(text) != plain:
            raise RuntimeError("decorative input rendering changed semantic text")
    else:
        text = plain
    return RenderedInputFrame(
        text,
        plain,
        capabilities.width,
        len(lines),
    )


def render_picker_viewport(
    viewport: PickerViewport,
    capabilities: TerminalCapabilities,
) -> RenderedFrame:
    """Render one bounded live picker without exposing choice identities."""

    if type(viewport) is not PickerViewport:
        raise TypeError("viewport must be a PickerViewport")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    lines = _picker_viewport_lines(viewport, capabilities.width)
    return _render_semantic_lines(lines, capabilities)


def render_picker_fallback(
    state: PickerControlState,
    capabilities: TerminalCapabilities,
) -> RenderedInputFrame:
    """Render the complete numbered picker and one final fallback prompt."""

    if type(state) is not PickerControlState:
        raise TypeError("state must be a PickerControlState")
    if type(capabilities) is not TerminalCapabilities:
        raise TypeError("capabilities must be TerminalCapabilities")
    lines = _picker_fallback_lines(state.picker, capabilities.width)
    plain = "\n".join(line for line, _role in lines)
    if not plain.endswith(" "):
        raise RuntimeError("picker fallback rendering lost its trailing space")
    if capabilities.decorative_sgr:
        decorated = [_decorate(line, role) for line, role in lines[:-1]]
        final_line, final_role = lines[-1]
        decorated.append(f"{_decorate(final_line[:-1], final_role)} ")
        text = "\n".join(decorated)
        if strip_decorative_sgr(text) != plain:
            raise RuntimeError(
                "decorative picker fallback rendering changed semantic text"
            )
    else:
        text = plain
    return RenderedInputFrame(
        text,
        plain,
        capabilities.width,
        len(lines),
    )


def strip_decorative_sgr(text: str) -> str:
    """Remove exactly the fixed decorative SGR vocabulary."""

    if type(text) is not str:
        raise TypeError("text must be a string")
    return _DECORATIVE_SGR.sub("", text)


def _render_semantic_lines(
    lines: list[tuple[str, SemanticRole]],
    capabilities: TerminalCapabilities,
) -> RenderedFrame:
    """Render validated semantic lines with equivalent fixed decoration."""

    if not lines:
        lines = [("", SemanticRole.DEFAULT)]
    plain = "\n".join(line for line, _role in lines)
    if capabilities.decorative_sgr:
        text = "\n".join(_decorate(line, role) for line, role in lines)
        if strip_decorative_sgr(text) != plain:
            raise RuntimeError("decorative rendering changed semantic text")
    else:
        text = plain
    return RenderedFrame(text, capabilities.width, len(lines))


def _picker_viewport_lines(
    viewport: PickerViewport,
    width: int,
) -> list[tuple[str, SemanticRole]]:
    """Project one live picker window into safe semantic rows."""

    picker = viewport.picker
    lines = _prompt_lines(
        picker.prompt,
        width,
        indent=0,
        additional_state=picker.state,
    )
    lines.extend(
        _picker_header_lines(
            picker,
            viewport.start,
            viewport.stop,
            width,
        )
    )
    child_indent = _child_indent(width, 0)
    for index in range(viewport.start, viewport.stop):
        choice = picker.choices[index]
        lines.extend(
            _numbered_picker_choice_lines(
                choice,
                index + 1,
                width,
                indent=child_indent,
                highlighted=choice.identity == picker.highlighted_id,
                selected=choice.identity == picker.selected_id,
            )
        )
    lines.extend(
        _picker_empty_or_notice_lines(
            picker,
            width,
            indent=child_indent,
        )
    )
    if picker.selected_id is not None:
        selected_index = next(
            index
            for index, choice in enumerate(picker.choices)
            if choice.identity == picker.selected_id
        )
        if not viewport.start <= selected_index < viewport.stop:
            selected = picker.choices[selected_index]
            lines.extend(
                (line, SemanticRole.SUCCESS)
                for line in _wrap(
                    selected.label,
                    width,
                    indent=child_indent,
                    initial_prefix=(
                        "Selected outside view: "
                        f"{selected_index + 1}. [selected] "
                    ),
                    continuation_prefix="  ",
                )
            )
    return lines


def _picker_fallback_lines(
    picker: Picker,
    width: int,
) -> list[tuple[str, SemanticRole]]:
    """Project a complete numbered list followed by one active input prompt."""

    lines = _picker_header_lines(picker, 0, len(picker.choices), width)
    child_indent = _child_indent(width, 0)
    for index, choice in enumerate(picker.choices):
        lines.extend(
            _numbered_picker_choice_lines(
                choice,
                index + 1,
                width,
                indent=child_indent,
                highlighted=choice.identity == picker.highlighted_id,
                selected=choice.identity == picker.selected_id,
            )
        )
    lines.extend(
        _picker_empty_or_notice_lines(
            picker,
            width,
            indent=child_indent,
        )
    )
    lines.extend(
        (line, SemanticRole.MUTED)
        for line in _wrap(
            "Enter a choice number, press Enter for the highlighted choice, "
            "or enter /cancel.",
            width,
            indent=0,
            initial_prefix="[input] ",
            continuation_prefix="        ",
        )
    )
    lines.extend(_input_prompt_lines(picker.prompt, width))
    return lines


def _picker_header_lines(
    picker: Picker,
    start: int,
    stop: int,
    width: int,
) -> list[tuple[str, SemanticRole]]:
    """Render total, absolute highlight, and optional window range facts."""

    total = len(picker.choices)
    if picker.highlighted_id is None:
        facts = f"{total} items"
    else:
        highlighted = next(
            index
            for index, choice in enumerate(picker.choices)
            if choice.identity == picker.highlighted_id
        )
        facts = f"highlighted {highlighted + 1}/{total}"
    if total > stop - start:
        facts += f"; showing {start + 1}-{stop}/{total}"
    return [
        (line, SemanticRole.EMPHASIS)
        for line in _wrap(
            f"Choices ({facts}){_state_suffix(picker.state)}",
            width,
            indent=0,
        )
    ]


def _numbered_picker_choice_lines(
    choice: Choice,
    ordinal: int,
    width: int,
    *,
    indent: int,
    highlighted: bool,
    selected: bool,
) -> list[tuple[str, SemanticRole]]:
    """Render one absolute ordinal with explicit non-color-only state cues."""

    if not choice.enabled:
        cues = "[disabled]"
        role = SemanticRole.MUTED
    else:
        states: list[str] = []
        if selected:
            states.append("[selected]")
        if highlighted:
            states.append("[highlighted]")
        cues = " ".join(states) if states else "[available]"
        role = (
            SemanticRole.SUCCESS
            if selected
            else SemanticRole.INFO
            if highlighted
            else SemanticRole.DEFAULT
        )
    text = choice.label
    if choice.description is not None:
        text += f" — {choice.description}"
    if choice.disabled_reason is not None:
        text += f" (disabled: {choice.disabled_reason})"
    prefix = f"{ordinal}. {cues} "
    return [
        (line, role)
        for line in _wrap(
            text,
            width,
            indent=indent,
            initial_prefix=prefix,
            continuation_prefix=" " * len(prefix),
        )
    ]


def _picker_empty_or_notice_lines(
    picker: Picker,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    """Render exactly one explicit empty or supplied semantic notice."""

    if picker.notice is not None:
        return _notice_lines(picker.notice, width, indent=indent)
    if not picker.choices:
        return [
            (line, SemanticRole.MUTED)
            for line in _wrap(
                "[empty] No choices available.",
                width,
                indent=indent,
            )
        ]
    return []


def _component_lines(
    component: Component,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    """Project one validated component into safe unstyled visible lines."""

    if type(component) is Prompt:
        return _prompt_lines(component, width, indent=indent)
    if type(component) is Panel:
        return _panel_lines(component, width, indent=indent)
    if type(component) is Section:
        return _section_lines(component, width, indent=indent)
    if type(component) is Field:
        return _field_lines(component, width, indent=indent)
    if type(component) is Notice:
        return _notice_lines(component, width, indent=indent)
    if type(component) is Message:
        return _message_lines(component, width, indent=indent)
    if type(component) is Record:
        return _record_lines(component, width, indent=indent)
    if type(component) is ListView:
        return _list_lines(component, width, indent=indent)
    if type(component) is SuggestionPalette:
        return _palette_lines(component, width, indent=indent)
    if type(component) is Picker:
        return _picker_lines(component, width, indent=indent)
    raise TypeError("component is not part of the closed semantic vocabulary")


def _prompt_lines(
    prompt: Prompt,
    width: int,
    *,
    indent: int,
    additional_state: ViewState | None = None,
) -> list[tuple[str, SemanticRole]]:
    scope = f" [{prompt.scope}]" if prompt.scope is not None else ""
    states = [prompt.state]
    if additional_state is not None and additional_state not in states:
        states.append(additional_state)
    state = "".join(_state_suffix(value) for value in states)
    lines = _wrap(f"{prompt.name}{scope}{state} >", width, indent=indent)
    rendered = [(line, SemanticRole.COMMAND) for line in lines]
    if prompt.instruction is not None:
        rendered.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap(prompt.instruction, width, indent=indent + 2)
        )
    if prompt.default is not None:
        rendered.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap(
                f"Default: {prompt.default}",
                width,
                indent=indent + 2,
            )
        )
    return rendered


def _input_prompt_lines(
    prompt: Prompt,
    width: int,
) -> list[tuple[str, SemanticRole]]:
    """Render prompt guidance first and its active input identity last."""

    lines: list[tuple[str, SemanticRole]] = []
    if prompt.instruction is not None:
        lines.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap(prompt.instruction, width, indent=0)
        )
    if prompt.default is not None:
        lines.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap(f"Default: {prompt.default}", width, indent=0)
        )
    scope = f" [{_inline(prompt.scope)}]" if prompt.scope is not None else ""
    identity = f"{_inline(prompt.name)}{scope}"
    if not identity.endswith(">"):
        identity = f"{identity} >"
    lines.extend(
        (line, SemanticRole.COMMAND)
        for line in _wrap(f"{identity} ", width, indent=0)
    )
    return lines


def _panel_lines(
    panel: Panel,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    lines = [
        (line, SemanticRole.EMPHASIS)
        for line in _wrap(
            f"{panel.title}{_state_suffix(panel.state)}",
            width,
            indent=indent,
        )
    ]
    child_indent = _child_indent(width, indent)
    for item in panel.items:
        lines.extend(_component_lines(item, width, indent=child_indent))
    return lines


def _section_lines(
    section: Section,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    lines = [
        (line, SemanticRole.EMPHASIS)
        for line in _wrap(
            f"{section.title}{_state_suffix(section.state)}",
            width,
            indent=indent,
        )
    ]
    child_indent = _child_indent(width, indent)
    for item in section.items:
        lines.extend(_component_lines(item, width, indent=child_indent))
    return lines


def _field_lines(
    field: Field,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    safe_label = (
        f"{_inline(field.label)}"
        f"{_semantic_suffix(field.state, field.role)}"
    )
    if width - indent < 40:
        lines = [
            (line, SemanticRole.EMPHASIS)
            for line in _wrap(f"{safe_label}:", width, indent=indent)
        ]
        lines.extend(
            (line, field.role)
            for line in _wrap(field.value, width, indent=_child_indent(width, indent))
        )
        return lines
    return [
        (line, field.role)
        for line in _wrap(
            field.value,
            width,
            indent=indent,
            initial_prefix=f"{safe_label}: ",
            continuation_prefix=" " * (len(safe_label) + 2),
        )
    ]


def _notice_lines(
    notice: Notice,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    label = notice.label
    if label is None:
        label = (
            _state_label(notice.state)
            if notice.state is not ViewState.IDLE
            else _role_label(notice.role)
        )
    prefix = f"[{_inline(label)}] "
    return [
        (line, notice.role)
        for line in _wrap(
            notice.text,
            width,
            indent=indent,
            initial_prefix=prefix,
            continuation_prefix=" " * len(prefix),
        )
    ]


def _message_lines(
    message: Message,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    prefix = (
        f"{_inline(message.sender)}"
        f"{_semantic_suffix(message.state, message.role)}> "
    )
    return [
        (line, message.role)
        for line in _wrap(
            message.content,
            width,
            indent=indent,
            initial_prefix=prefix,
            continuation_prefix=" " * len(prefix),
            preserve_lines=True,
        )
    ]


def _record_lines(
    record: Record,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    heading = f"[{_inline(record.identity)}]"
    if record.title is not None:
        heading += f" {_inline(record.title)}"
    heading += _semantic_suffix(record.state, record.role)
    lines = [(line, record.role) for line in _wrap(heading, width, indent=indent)]
    child_indent = _child_indent(width, indent)
    for field in record.fields:
        lines.extend(_field_lines(field, width, indent=child_indent))
    if record.content is not None:
        lines.extend(
            (line, record.role)
            for line in _wrap(
                record.content,
                width,
                indent=child_indent,
                preserve_lines=True,
            )
        )
    return lines


def _list_lines(
    view: ListView,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    total = len(view.items) if view.total_count is None else view.total_count
    position = f" {view.position}/{total}" if view.position is not None else f" {total}"
    lines = [
        (line, SemanticRole.EMPHASIS)
        for line in _wrap(
            f"{view.title} ({position.strip()}){_state_suffix(view.state)}",
            width,
            indent=indent,
        )
    ]
    child_indent = _child_indent(width, indent)
    if not view.items:
        assert view.empty_notice is not None
        lines.extend(_notice_lines(view.empty_notice, width, indent=child_indent))
        return lines
    for item in view.items:
        lines.extend(_component_lines(item, width, indent=child_indent))
    return lines


def _palette_lines(
    palette: SuggestionPalette,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    lines = [
        (line, SemanticRole.COMMAND)
        for line in _wrap(
            f"Suggestions{_state_suffix(palette.state)}",
            width,
            indent=indent,
        )
    ]
    child_indent = _child_indent(width, indent)
    if not palette.candidates:
        lines.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap("[empty] No suggestions.", width, indent=child_indent)
        )
        return lines
    for choice in palette.candidates:
        highlighted = choice.identity == palette.highlighted_id
        lines.extend(
            _choice_lines(
                choice,
                width,
                indent=child_indent,
                highlighted=highlighted,
                selected=False,
            )
        )
    return lines


def _picker_lines(
    picker: Picker,
    width: int,
    *,
    indent: int,
) -> list[tuple[str, SemanticRole]]:
    lines = _prompt_lines(
        picker.prompt,
        width,
        indent=indent,
        additional_state=picker.state,
    )
    child_indent = _child_indent(width, indent)
    if not picker.choices:
        text = (
            picker.notice.text
            if picker.notice is not None
            else "No choices available."
        )
        lines.extend(
            (line, SemanticRole.MUTED)
            for line in _wrap(f"[empty] {text}", width, indent=child_indent)
        )
        return lines
    for choice in picker.choices:
        lines.extend(
            _choice_lines(
                choice,
                width,
                indent=child_indent,
                highlighted=choice.identity == picker.highlighted_id,
                selected=choice.identity == picker.selected_id,
            )
        )
    if picker.notice is not None:
        lines.extend(_notice_lines(picker.notice, width, indent=child_indent))
    return lines


def _choice_lines(
    choice: Choice,
    width: int,
    *,
    indent: int,
    highlighted: bool,
    selected: bool,
) -> list[tuple[str, SemanticRole]]:
    if not choice.enabled:
        marker = "- [disabled] "
        role = SemanticRole.MUTED
    elif selected:
        marker = "* [selected] "
        role = SemanticRole.SUCCESS
    elif highlighted:
        marker = "> [highlighted] "
        role = SemanticRole.INFO
    else:
        marker = "- "
        role = SemanticRole.DEFAULT
    text = choice.label
    if choice.description is not None:
        text += f" — {choice.description}"
    if choice.disabled_reason is not None:
        text += f" ({choice.disabled_reason})"
    return [
        (line, role)
        for line in _wrap(
            text,
            width,
            indent=indent,
            initial_prefix=marker,
            continuation_prefix=" " * len(marker),
        )
    ]


def _state_label(state: ViewState) -> str:
    """Return one always-visible textual state cue."""

    return state.value


def _state_suffix(state: ViewState) -> str:
    """Return a visible non-default state annotation."""

    return "" if state is ViewState.IDLE else f" [{state.value}]"


def _role_label(role: SemanticRole) -> str:
    """Return a visible notice label for one semantic role."""

    if role in {
        SemanticRole.INFO,
        SemanticRole.SUCCESS,
        SemanticRole.WARNING,
        SemanticRole.ERROR,
    }:
        return role.value
    return "notice"


def _role_suffix(role: SemanticRole) -> str:
    """Return a visible status-role annotation when color may reinforce it."""

    if role in {
        SemanticRole.INFO,
        SemanticRole.SUCCESS,
        SemanticRole.WARNING,
        SemanticRole.ERROR,
    }:
        return f" [{role.value}]"
    return ""


def _semantic_suffix(state: ViewState, role: SemanticRole) -> str:
    """Return nonduplicated visible state and status-role annotations."""

    state_suffix = _state_suffix(state)
    if role.value == state.value:
        return state_suffix
    return f"{state_suffix}{_role_suffix(role)}"


def _child_indent(width: int, indent: int) -> int:
    """Reduce indentation deterministically before semantic content is lost."""

    remaining = width - indent
    if remaining >= 40:
        return indent + 2
    if remaining >= 20:
        return indent + 1
    return indent


def _inline(text: str) -> str:
    """Sanitize one inline value and make structural newlines visible."""

    return sanitize_terminal_text(text).replace("\n", "\\x0a")


def _wrap(
    value: str,
    width: int,
    *,
    indent: int,
    initial_prefix: str = "",
    continuation_prefix: str = "",
    preserve_lines: bool = False,
) -> list[str]:
    """Wrap sanitized text by physical cells with visible continuation."""

    safe = sanitize_terminal_text(value)
    if not preserve_lines:
        safe = safe.replace("\n", "\\x0a")
    source_lines = safe.split("\n") if preserve_lines else [safe]
    rendered: list[str] = []
    base_indent = " " * max(0, min(indent, max(width - 1, 0)))
    for source_index, source in enumerate(source_lines):
        prefix = initial_prefix if source_index == 0 else continuation_prefix
        rendered.extend(
            _prefixed_cell_lines(
                source,
                width,
                first_prefix=f"{base_indent}{prefix}",
                continuation_prefix=f"{base_indent}{continuation_prefix}",
            )
        )
    return rendered or [base_indent.rstrip()]


def _prefixed_cell_lines(
    text: str,
    width: int,
    *,
    first_prefix: str,
    continuation_prefix: str,
) -> list[str]:
    """Fit fixed structure and safe content without prefix overflow."""

    if not text:
        prefix_lines = _cell_chunks(first_prefix, width)
        visible = [line.rstrip() for line in prefix_lines if line.rstrip()]
        return visible or [""]

    rendered: list[str] = []
    remaining = text
    prefix = first_prefix
    while remaining:
        prefix_chunks = _cell_chunks(prefix, width)
        active_prefix = ""
        if prefix_chunks:
            for prefix_chunk in prefix_chunks[:-1]:
                if prefix_chunk.rstrip():
                    rendered.append(prefix_chunk.rstrip())
            active_prefix = prefix_chunks[-1]
            if _cell_width(active_prefix) >= width:
                if active_prefix.rstrip():
                    rendered.append(active_prefix.rstrip())
                active_prefix = ""

        available = max(1, width - _cell_width(active_prefix))
        content_chunks = _cell_chunks(remaining, available)
        content = content_chunks[0]
        rendered.append(f"{active_prefix}{content}")
        remaining = remaining[len(content) :]
        prefix = continuation_prefix
    return rendered


def _cell_chunks(text: str, width: int) -> list[str]:
    """Split visible safe text into deterministic physical-cell chunks."""

    if not text:
        return []
    chunks: list[str] = []
    current: list[str] = []
    cells = 0
    for character in text:
        character_width = display_character_width(character)
        if current and cells + character_width > width:
            chunks.append("".join(current))
            current = []
            cells = 0
        current.append(character)
        cells += character_width
    if current:
        chunks.append("".join(current))
    return chunks


def _cell_width(text: str) -> int:
    """Measure one already-safe prefix without interpreting ANSI."""

    return sum(display_character_width(character) for character in text)


def _decorate(text: str, role: SemanticRole) -> str:
    """Apply one fixed local style without parsing dynamic text as markup."""

    sgr = _ROLE_SGR[role]
    if not sgr or not text:
        return text
    return f"{sgr}{text}{_RESET}"


__all__ = ["render_component", "render_plain", "strip_decorative_sgr"]
