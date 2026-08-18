"""Pure responsive rendering for safe plain and fixed-theme terminal views."""

from __future__ import annotations

import re

from ._model import (
    CapabilityProfile,
    Choice,
    Component,
    Field,
    ListView,
    Message,
    Notice,
    Panel,
    Picker,
    Prompt,
    Record,
    RenderedFrame,
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
    SemanticRole.AGENT: "\x1b[34m",
}
_DECORATIVE_SGR = re.compile(r"\x1b\[(?:0|1|3[1-7])m")


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


def strip_decorative_sgr(text: str) -> str:
    """Remove exactly the fixed decorative SGR vocabulary."""

    if type(text) is not str:
        raise TypeError("text must be a string")
    return _DECORATIVE_SGR.sub("", text)


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
