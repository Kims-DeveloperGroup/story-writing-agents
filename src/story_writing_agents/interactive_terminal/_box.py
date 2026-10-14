"""Pure Unicode box geometry shared by terminal renderers."""

from __future__ import annotations

from dataclasses import dataclass

from ._model import display_character_width, sanitize_terminal_text


def _cell_width(text: str) -> int:
    """Measure the same display cells as the owning terminal model."""
    return sum(display_character_width(character) for character in text)


def _fit_cells(text: str, width: int) -> str:
    """Retain sanitized glyphs and attached combining marks within the limit."""
    fitted: list[str] = []
    used = 0
    for character in sanitize_terminal_text(text):
        cells = display_character_width(character)
        if used + cells > max(0, width):
            break
        fitted.append(character)
        used += cells
    return "".join(fitted)


@dataclass(frozen=True, slots=True)
class Box:
    """Fit plain borders and padded rows without owning style or terminal state."""

    width: int
    rounded: bool = False

    def __post_init__(self) -> None:
        """Validate immutable geometry choices independently of capabilities."""
        if type(self.width) is not int or self.width < 1:
            raise ValueError("box width must be a positive integer")
        if type(self.rounded) is not bool:
            raise TypeError("rounded must be a bool")

    @property
    def body_prefix(self) -> str:
        """Return the fixed plain left body rail and its padding."""
        return "│ "

    def body_width(self, *, right_marker: str | None = None) -> int:
        """Retain the viewer's minimum one-cell content capacity."""
        suffix = " │" if right_marker is None else f" {right_marker}│"
        return max(1, self.width - _cell_width(self.body_prefix + suffix))

    def body_row(self, text: str, *, right_marker: str | None = None) -> str:
        """Clip and pad one plain row, optionally retaining a viewer scrollbar."""
        capacity = self.body_width(right_marker=right_marker)
        content = _fit_cells(text, capacity)
        padding = " " * max(0, capacity - _cell_width(content))
        suffix = " │" if right_marker is None else f" {right_marker}│"
        return _fit_cells(self.body_prefix + content + padding + suffix, self.width)

    def top(self, title: str = "", *, right_title: str = "") -> str:
        """Fit a title and optional right counter inside one opening rail."""
        return self._rail("╭" if self.rounded else "┌", "╮" if self.rounded else "┐",
                          title, right_title)

    def divider(self, title: str = "") -> str:
        """Fit a labeled separator without opening another box."""
        return self._rail("├", "┤", title, "")

    def bottom(self, title: str = "") -> str:
        """Fit optional viewer hints inside one closing rail."""
        return self._rail("╰" if self.rounded else "└", "╯" if self.rounded else "┘",
                          title, "")

    def _rail(self, left: str, right: str, title: str, right_title: str) -> str:
        if self.width == 1:
            return left
        capacity = self.width - 2
        title = sanitize_terminal_text(title).replace("\n", " ")
        right_title = sanitize_terminal_text(right_title).replace("\n", " ")
        if capacity < 3:
            return left + "─" * capacity + right
        if title and right_title and _cell_width(f"─ {title}  {right_title} ─") > capacity:
            right_title = ""
        trailing = (f" {_fit_cells(right_title, capacity - 3)} ─" if right_title
                    else "─" if title else "")
        remaining = capacity - _cell_width(trailing)
        leading = (f"─ {_fit_cells(title, remaining - 3)} "
                   if title and remaining >= 3 else "")
        fill = "─" * max(0, capacity - _cell_width(leading + trailing))
        return left + leading + fill + trailing + right
