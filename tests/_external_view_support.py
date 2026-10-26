"""Deterministic borrowed-terminal fixtures and independent portable screen model."""
from __future__ import annotations
import copy
import os
import re
import unicodedata
from types import SimpleNamespace
from unittest import mock
from story_writing_agents.interactive_terminal._controls import (
    append_main_input_history, transition_line_input, transition_main_command_input,
)
from story_writing_agents.interactive_terminal._driver import RawTerminalDriver

class _Input:
    def __init__(self, characters: str, *, tty: bool = True) -> None:
        self.characters = list(characters.encode("utf-8"))
        self.tty = tty
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return 7

    def read(self, size: int) -> str:
        raise AssertionError(f"text read must not run: {size}")
    readline = read

    def read1(self, size: int) -> bytes:
        if size != 1:
            raise AssertionError(size)
        return bytes((self.characters.pop(0),)) if self.characters else b""

class _Output:
    def __init__(self, *, tty: bool = True) -> None:
        self.tty = tty
        self.records: list[str] = []
        self.failure: BaseException | None = None

    def isatty(self) -> bool:
        return self.tty

    def fileno(self) -> int:
        return 8

    def write(self, text: str) -> int:
        if self.failure is not None:
            raise self.failure
        self.records.append(text)
        return len(text)

    def flush(self) -> None:
        return None

class _Termios:
    ICANON = 0x02
    ECHO = 0x08
    ECHONL = 0x40
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self) -> None:
        self.original = [0, 0, 0, self.ICANON | self.ECHO, 0, 0, [b'\x04', b'\x03', 0, 0, 9, 9]]
        self.events: list[str] = []
        self.restoration_failure: BaseException | None = None

    def tcgetattr(self, _descriptor: int) -> list[object]:
        self.events.append("get")
        value = list(self.original)
        value[6] = list(self.original[6])  # type: ignore[arg-type]
        return value

    def tcsetattr(
        self,
        _descriptor: int,
        when: int,
        _attributes: list[object],
    ) -> None:
        event = "activate" if when == self.TCSANOW else "restore"
        self.events.append(event)
        if event == "restore" and self.restoration_failure is not None:
            raise self.restoration_failure

def _raw_driver(
    characters: str,
    *,
    tty: bool = True,
    answers: tuple[object, ...] = ("q",),
    width: int = 91,
    height: int = 27,
) -> tuple[RawTerminalDriver, _Input, _Output, mock.Mock, _Termios]:
    input_stream = _Input(characters, tty=tty)
    output_stream = _Output(tty=tty)
    fallback = mock.Mock(side_effect=answers)
    termios = _Termios()
    os_double = SimpleNamespace(
        name="posix",
        environ={"TERM": "xterm-256color"},
        get_terminal_size=lambda _descriptor: os.terminal_size((width, height)),
        fpathconf=lambda _descriptor, _name: 0,
    )
    driver = RawTerminalDriver(
        fallback,
        input_stream,  # type: ignore[arg-type]
        output_stream,  # type: ignore[arg-type]
        transition_line_input,
        transition_main_command_input,
        append_main_input_history,
        termios_provider=lambda: termios,
        os_provider=lambda: os_double,
    )
    return driver, input_stream, output_stream, fallback, termios


class PortableTerminalScreen:
    """Small ECMA-48 model with archival ED2 and eager right-margin wrapping.

    The emulated terminal owns primary and alternate buffers, while history is
    global. Erasing a populated display archives its rows, matching terminals
    where alternate-screen ED2 leaked viewer content into normal scrollback.
    No production constants or rendering helpers inform this oracle.
    """
    def __init__(self, width=91, height=27):
        self.width, self.height = width, height
        self.primary = [[" "] * width for _ in range(height)]
        prompt = "story[novel]> "
        self.primary[-2][:len(prompt)] = list(prompt)
        self.screen = self.primary
        self.cursor = (height - 2, len(prompt))
        self.saved_cursor = self.cursor
        self.history = ["earlier shell output"]
        self.alternate = False
        self.pending = ""

    def clone(self):
        return copy.deepcopy(self)

    def snapshot(self):
        return tuple(map(tuple, self.primary)), self.cursor, tuple(self.history)

    def resize(self, width, height):
        self.width, self.height = width, height
        def resized(rows):
            return [(row[:width] + [" "] * width)[:width] for row in rows[:height]] + [[" "] * width for _ in range(max(0, height - len(rows)))]
        self.primary = resized(self.primary)
        if self.alternate:
            self.screen = resized(self.screen)
        else:
            self.screen = self.primary
        self.cursor = (min(self.cursor[0], height - 1), min(self.cursor[1], width - 1))
        self.saved_cursor = (min(self.saved_cursor[0], height - 1), min(self.saved_cursor[1], width - 1))

    def _newline(self):
        row, col = self.cursor
        if row == self.height - 1:
            self.history.append("".join(self.screen.pop(0)))
            self.screen.append([" "] * self.width)
        else:
            row += 1
        self.cursor = row, col

    def write(self, value):
        self.pending += value
        while self.pending:
            if self.pending.startswith("\x1b["):
                match = re.match(r"\x1b\[([0-9;?]*)([@-~])", self.pending)
                if match is None:
                    return
                parameters, final = match.groups()
                self.pending = self.pending[match.end():]
                if parameters == "?1049" and final == "h":
                    self.saved_cursor = self.cursor
                    self.screen = [[" "] * self.width for _ in range(self.height)]
                    self.cursor, self.alternate = (0, 0), True
                elif parameters == "?1049" and final == "l":
                    self.screen, self.cursor, self.alternate = self.primary, self.saved_cursor, False
                elif final in "Hf":
                    parts = [int(p or "1") for p in parameters.split(";")]
                    self.cursor = (min((parts[0] if parts else 1) - 1, self.height - 1), min((parts[1] if len(parts) > 1 else 1) - 1, self.width - 1))
                elif final in "ABCD":
                    count = int(parameters or "1") or 1
                    row, col = self.cursor
                    row += count if final == "B" else -count if final == "A" else 0
                    col += count if final == "C" else -count if final == "D" else 0
                    self.cursor = (max(0, min(self.height - 1, row)), max(0, min(self.width - 1, col)))
                elif final == "J" and parameters in {"", "0"}:
                    row, col = self.cursor
                    self.screen[row][col:] = [" "] * (self.width - col)
                    for cleared in range(row + 1, self.height):
                        self.screen[cleared] = [" "] * self.width
                elif final == "J" and parameters == "2":
                    occupied = ["".join(row).rstrip() for row in self.screen]
                    if any(occupied):
                        self.history.extend(occupied)
                    self.screen[:] = [[" "] * self.width for _ in range(self.height)]
                elif final == "K":
                    row, col = self.cursor
                    start, end = (0, col + 1) if parameters == "1" else ((0, self.width) if parameters == "2" else (col, self.width))
                    self.screen[row][start:end] = [" "] * (end - start)
                continue
            character, self.pending = self.pending[0], self.pending[1:]
            if character == "\n":
                self._newline()
            elif character == "\r":
                self.cursor = self.cursor[0], 0
            elif unicodedata.category(character).startswith("M"):
                continue
            elif character >= " ":
                width = 2 if unicodedata.east_asian_width(character) in "WF" else 1
                row, col = self.cursor
                if col + width > self.width:
                    self.cursor = row, 0
                    self._newline()
                    row, col = self.cursor
                self.screen[row][col:col + width] = [character] + [" "] * (width - 1)
                self.cursor = row, col + width
                if self.cursor[1] == self.width:
                    self.cursor = row, 0
                    self._newline()
        return len(value)

class _Host:
    def __init__(self, *, close_failure: BaseException | None = None) -> None:
        self.closed = 0
        self.close_failure = close_failure

    def terminal_size(self) -> tuple[int, int]:
        return 80, 24

    def color_enabled(self) -> bool:
        return False

    def present(self, _frame: str) -> None:
        return None

    def read_event(self) -> str | None:
        return "close"

    def close_view(self) -> None:
        self.closed += 1
        if self.close_failure is not None:
            raise self.close_failure
