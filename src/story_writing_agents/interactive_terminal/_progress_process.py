"""A leased, bounded progress writer with an independent process clock.

This file is also the helper's standalone entry point. It deliberately imports
only the standard library so starting it never imports the runtime package.
"""

from __future__ import annotations

import json
import math
import os
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path


_INTERVAL = 0.08
_MAX_RECORD = 65_536
_MAX_ROWS = 200
_MAX_COLUMNS = 120
_MAX_CONTROL = 512
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_FRAMES = ("█▓▒░░░░░", "░█▓▒░░░░", "░░█▓▒░░░", "░░░█▓▒░░", "░░░░█▓▒░")
_FAILURE = "Progress animation unavailable; the request continues."


def _safe(text: object) -> bool:
    if type(text) is not str:
        return False
    return all(unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
               and c not in {"\u2028", "\u2029"} for c in _SGR.sub("", text))


def _fit(text: str, width: int) -> str:
    """Fit the small header; body rows have already been fitted by the parent."""
    result, cells = [], 0
    for character in text:
        size = 0 if unicodedata.combining(character) else 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if cells + size > width:
            break
        result.append(character)
        cells += size
    return "".join(result)


@dataclass(frozen=True, slots=True)
class ProgressContent:
    started_at: float
    frames: tuple[str, ...]
    suffix: str

    def __post_init__(self) -> None:
        if (type(self.started_at) not in {int, float} or not math.isfinite(self.started_at)
                or self.started_at < 0 or type(self.frames) is not tuple
                or not 1 <= len(self.frames) <= 32
                or any(not _safe(frame) or "\x1b" in frame or len(frame) > 32 for frame in self.frames)
                or not _safe(self.suffix) or "\x1b" in self.suffix or len(self.suffix) > 512):
            raise ValueError("invalid progress content")

    def header(self, now: float) -> str:
        elapsed = max(0, int(now - self.started_at))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        frame = int(max(0.0, now - self.started_at) / _INTERVAL) % len(self.frames)
        return f"[{self.frames[frame]}] {hours:02d}:{minutes:02d}:{seconds:02d}{self.suffix}"


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    columns: int
    height: int
    lines: tuple[str, ...]
    cursor_row: int
    cursor_column: int
    header_row: int
    header_prefix: str
    content: ProgressContent

    def __post_init__(self) -> None:
        if (type(self.columns) is not int or not 2 <= self.columns <= _MAX_COLUMNS
                or type(self.height) is not int or not 1 <= self.height <= 100_000
                or type(self.lines) is not tuple or not 1 <= len(self.lines) <= min(_MAX_ROWS, self.height)
                or any(not _safe(row) or len(row.encode("utf-8")) > 8192 for row in self.lines)
                or type(self.cursor_row) is not int or not 0 <= self.cursor_row < len(self.lines)
                or type(self.cursor_column) is not int or not 0 <= self.cursor_column < self.columns
                or type(self.header_row) is not int or not 0 <= self.header_row < len(self.lines)
                or not _safe(self.header_prefix) or "\x1b" in self.header_prefix
                or len(self.header_prefix) > 16 or type(self.content) is not ProgressContent):
            raise ValueError("invalid prepared progress snapshot")

    def record(self, epoch: int, revision: int) -> dict[str, object]:
        return {"epoch": epoch, "revision": revision, "columns": self.columns,
                "height": self.height, "lines": self.lines, "cursor_row": self.cursor_row,
                "cursor_column": self.cursor_column, "header_row": self.header_row,
                "header_prefix": self.header_prefix, "started_at": self.content.started_at,
                "frames": self.content.frames, "suffix": self.content.suffix}

    @classmethod
    def decode(cls, record: dict[str, object]) -> ProgressSnapshot:
        return cls(record["columns"], record["height"], tuple(record["lines"]),
                   record["cursor_row"], record["cursor_column"], record["header_row"],
                   record["header_prefix"], ProgressContent(record["started_at"],
                   tuple(record["frames"]), record["suffix"]))


@dataclass(frozen=True, slots=True)
class ProgressRelease:
    confirmed: bool
    uncertain: bool = True
    rows: int = 0
    cursor_row: int = 0
    cursor_column: int = 0
    columns: int = 0
    height: int = 0
    failure: str | None = None

    def __post_init__(self) -> None:
        if (type(self.confirmed) is not bool or type(self.uncertain) is not bool
                or any(type(value) is not int for value in (self.rows, self.cursor_row,
                    self.cursor_column, self.columns, self.height))
                or not 0 <= self.rows <= _MAX_ROWS
                or not 0 <= self.cursor_row <= max(0, self.rows - 1)
                or not 0 <= self.cursor_column < _MAX_COLUMNS
                or not 0 <= self.columns <= _MAX_COLUMNS or not 0 <= self.height <= 100_000
                or (self.failure is not None and (not _safe(self.failure) or len(self.failure) > 120))):
            raise ValueError("invalid progress release")


def _record(value: dict[str, object]) -> bytes:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > _MAX_RECORD:
        raise ValueError("progress snapshot exceeds its transport bound")
    return struct.pack("!I", len(data)) + data


class _Reader:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.buffer = bytearray()
        self.eof = False

    def read(self) -> list[dict[str, object]]:
        result = []
        # Both I/O and decoding are bounded before the ticker/control pass.
        for _ in range(4):
            try:
                data = os.read(self.descriptor, 16_384)
            except BlockingIOError:
                break
            if not data:
                self.eof = True
                break
            self.buffer.extend(data)
            while len(self.buffer) >= 4:
                size = struct.unpack_from("!I", self.buffer)[0]
                if size > _MAX_RECORD:
                    raise ValueError("oversized progress record")
                if len(self.buffer) < size + 4:
                    break
                value = json.loads(bytes(self.buffer[4:size + 4]))
                del self.buffer[:size + 4]
                if type(value) is not dict:
                    raise ValueError("invalid progress record")
                result.append(value)
            if len(self.buffer) > _MAX_RECORD + 4:
                raise ValueError("progress transport overflow")
        return result


class ProgressProcess:
    """Parent-owned process and writer lease; publication never waits for paint."""

    def __init__(self, process: subprocess.Popen, command: int, snapshot: int, status: int) -> None:
        self._process, self._command, self._snapshot, self._status = process, command, snapshot, status
        self._reader = _Reader(status)
        self._epoch = 0
        self._active = False
        self._ready = False
        self._disabled = False
        self._failure: str | None = None
        self._release: ProgressRelease | None = None
        self._revision = 0
        self._inflight = b""
        self._offset = 0
        self._latest = b""
        self._closed = False

    @classmethod
    def prestart(cls, output_fd: int) -> ProgressProcess | None:
        """Open a separate terminal description, preserving borrowed fd flags."""
        if os.name != "posix" or not os.isatty(output_fd):
            return None
        descriptors: list[int] = []
        process = None
        owner = None
        try:
            original = os.fstat(output_fd)
            output = os.open(os.ttyname(output_fd), os.O_WRONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC)
            descriptors.append(output)
            opened = os.fstat(output)
            if (not stat.S_ISCHR(opened.st_mode)
                    or (opened.st_dev, opened.st_ino, opened.st_rdev)
                    != (original.st_dev, original.st_ino, original.st_rdev)):
                raise OSError("progress terminal identity changed")
            command_r, command_w = os.pipe()
            descriptors.extend((command_r, command_w))
            snapshot_r, snapshot_w = os.pipe()
            descriptors.extend((snapshot_r, snapshot_w))
            status_r, status_w = os.pipe()
            descriptors.extend((status_r, status_w))
            for descriptor in descriptors:
                os.set_inheritable(descriptor, False)
                os.set_blocking(descriptor, False)
            process = subprocess.Popen(
                (sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--worker",
                 str(command_r), str(snapshot_r), str(status_w), str(output), str(os.getpid())),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, pass_fds=(command_r, snapshot_r, status_w, output),
                env={},  # Absolute interpreter + explicit UTF-8 I/O need no provider environment.
            )
            owner = cls(process, command_w, snapshot_w, status_r)
            for descriptor in (command_w, snapshot_w, status_r):
                descriptors.remove(descriptor)
            # Warm-up occurs before input/session admission, never on a request's
            # frame delivery path. A late helper is disabled for this session.
            deadline = time.monotonic() + 0.25
            while not owner._ready and owner._process.poll() is None and time.monotonic() < deadline:
                owner.poll_status()
                if not owner._ready:
                    select.select((status_r,), (), (), max(0.0, min(0.02, deadline - time.monotonic())))
            if not owner._ready:
                owner._fail()
                owner.close()
            return owner
        except BaseException as failure:
            if owner is not None:
                try:
                    owner.close(timeout=0.1)
                except BaseException as cleanup_failure:
                    failure.add_note(f"progress startup cleanup failed: {type(cleanup_failure).__name__}")
            elif process is not None:
                try:
                    process.kill()
                    process.wait(timeout=0.1)
                except BaseException as cleanup_failure:
                    failure.add_note(f"progress startup cleanup failed: {type(cleanup_failure).__name__}")
            if isinstance(failure, (OSError, ValueError, subprocess.SubprocessError)):
                return None
            raise
        finally:
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    @property
    def available(self) -> bool:
        return self._ready and not self._disabled and not self._closed

    @property
    def active(self) -> bool:
        return self._active

    @property
    def failure(self) -> str | None:
        return self._failure

    def _fail(self) -> None:
        self._disabled = True
        self._failure = _FAILURE

    def disable(self) -> None:
        """Disable future publication without waiting on the active writer."""
        self._fail()

    def _send_command(self, value: dict[str, object]) -> bool:
        try:
            data = _record(value)
            if len(data) > min(_MAX_CONTROL, os.fpathconf(self._command, "PC_PIPE_BUF")):
                return False
            return os.write(self._command, data) == len(data)
        except (OSError, ValueError):
            return False

    def begin(self, snapshot: ProgressSnapshot, *, owned_rows: int = 0,
              cursor_row: int = 0, cursor_column: int = 0) -> bool:
        self.poll_status()
        if not self.available or self._active:
            return False
        self._epoch += 1
        self._release = None
        self._revision = 0
        # A previous epoch may have put only a record prefix into the pipe.
        # Finish that record before the new one; the child ignores its stale
        # epoch. Dropping a started tail would corrupt length framing.
        if not self._inflight or self._offset == 0:
            self._inflight = b""
            self._offset = 0
        self._latest = b""
        if not self._send_command({"op": "begin", "epoch": self._epoch,
                "origin": snapshot.content.started_at, "columns": snapshot.columns,
                "height": snapshot.height, "owned_rows": owned_rows,
                "cursor_row": cursor_row, "cursor_column": cursor_column}):
            self._fail()
            return False
        self._active = True
        self.publish(snapshot)
        return True

    def publish(self, snapshot: ProgressSnapshot) -> bool:
        """Retain one partial record and replace only the latest pending view."""
        if not self._active or self._disabled:
            return False
        try:
            self._revision += 1
            self._latest = _record(snapshot.record(self._epoch, self._revision))
            return self.flush_pending()
        except (OSError, ValueError, TypeError):
            self._fail()
            return False

    def flush_pending(self) -> bool:
        """Advance bounded pending IPC without rebuilding or serializing a view."""
        if not self._active or self._disabled:
            return False
        try:
            for _ in range(2):
                if not self._inflight:
                    self._inflight, self._latest = self._latest, b""
                    self._offset = 0
                if not self._inflight:
                    break
                try:
                    count = os.write(self._snapshot, self._inflight[self._offset:])
                except BlockingIOError:
                    break
                if count <= 0:
                    raise OSError("progress transport short write")
                self._offset += count
                if self._offset == len(self._inflight):
                    self._inflight = b""
            return True
        except (OSError, ValueError, TypeError):
            self._fail()
            return False

    def poll_status(self) -> None:
        if self._closed:
            return
        try:
            for value in self._reader.read():
                if value.get("op") == "ready":
                    self._ready = True
                elif value.get("op") == "failed":
                    self._fail()
                elif value.get("op") == "released" and value.get("epoch") == self._epoch:
                    self._release = ProgressRelease(True, value.get("uncertain", True),
                        value.get("rows", 0), value.get("cursor_row", 0), value.get("cursor_column", 0),
                        value.get("columns", 0), value.get("height", 0), self._failure)
            if self._reader.eof or self._process.poll() is not None:
                self._fail()
        except (OSError, ValueError, TypeError, KeyError):
            self._fail()

    def quiesce(self, timeout: float = 0.25) -> ProgressRelease:
        """Reclaim only after a control ACK or confirmed owned-process exit."""
        try:
            return self._quiesce_once(timeout)
        except BaseException as failure:
            self._fail()
            try:
                confirmed = self._terminate(0.05)
            except BaseException as cleanup_failure:
                failure.add_note(f"progress termination failed: {type(cleanup_failure).__name__}")
                confirmed = self._process.poll() is not None
            self._active = not confirmed
            raise

    def _quiesce_once(self, timeout: float) -> ProgressRelease:
        if not self._active:
            return ProgressRelease(True, failure=self._failure)
        deadline = time.monotonic() + max(0.0, timeout)
        sent = self._send_command({"op": "quiesce", "epoch": self._epoch})
        while sent and time.monotonic() < deadline:
            self.poll_status()
            if self._release is not None:
                self._active = False
                self._latest = b""
                if self._offset == 0:
                    self._inflight = b""
                return self._release
            if self._process.poll() is not None:
                break
            try:
                select.select((self._status,), (), (), max(0.0, min(0.01, deadline - time.monotonic())))
            except (OSError, ValueError):
                break
        self._fail()
        confirmed = self._terminate(max(0.0, deadline - time.monotonic()))
        self._active = not confirmed
        return ProgressRelease(confirmed, failure=self._failure)

    def _terminate(self, timeout: float) -> bool:
        if self._process.poll() is not None:
            return True
        try:
            # No terminal I/O is needed to stop a backpressured writer.
            self._process.kill()
            self._process.wait(timeout=max(0.02, min(0.1, timeout)))
        except (OSError, subprocess.SubprocessError):
            pass
        return self._process.poll() is not None

    def close(self, timeout: float = 0.25) -> ProgressRelease:
        if self._closed:
            if self._active and self._terminate(min(0.1, timeout)):
                self._active = False
            return ProgressRelease(not self._active, failure=self._failure)
        release = ProgressRelease(False, failure=self._failure)
        primary: BaseException | None = None
        try:
            release = self.quiesce(timeout)
        except BaseException as failure:
            primary = failure
        try:
            confirmed = self._terminate(min(0.1, timeout))
        except BaseException as failure:
            confirmed = self._process.poll() is not None
            if primary is None:
                primary = failure
            else:
                primary.add_note(f"progress termination failed: {type(failure).__name__}")
        self._active = self._active and not confirmed
        for descriptor in (self._command, self._snapshot, self._status):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._closed = True
        if primary is not None:
            raise primary
        return release if confirmed and release.confirmed else ProgressRelease(confirmed, failure=self._failure)


class _Writer:
    """Child-local cursor accounting; no semantic/history/input ownership."""

    def __init__(self, output: int) -> None:
        self.output = output
        self.epoch = 0
        self.active = False
        self.content = ProgressContent(time.monotonic(), _FRAMES, " · Working")
        self.geometry = (80, 24)
        self.rows = self.cursor_row = self.cursor_column = 0
        self.header_row = 0
        self.header_prefix = ""
        self.snapshot: ProgressSnapshot | None = None
        self.revision = 0
        self.painted_revision = -1
        self.pending = b""
        self.offset = 0
        self.commit: tuple[int, int, int] | None = None
        self.uncertain = False
        self.failure = False

    def _queue(self, text: str, rows: int, row: int, column: int) -> None:
        if self.pending:
            return
        data = text.encode("utf-8")
        if len(data) > _MAX_RECORD:
            raise ValueError("progress output frame exceeds its bound")
        self.pending, self.offset, self.commit = data, 0, (rows, row, column)

    def begin(self, value: dict[str, object]) -> None:
        if self.active or self.pending:
            raise ValueError("overlapping progress lease")
        self.epoch = value["epoch"]
        self.content = ProgressContent(value["origin"], _FRAMES, " · Working")
        self.geometry = (value["columns"], value["height"])
        self.rows = max(0, min(self.geometry[1], value["owned_rows"]))
        self.cursor_row = max(0, min(max(0, self.rows - 1), value["cursor_row"]))
        self.cursor_column = value["cursor_column"]
        self.snapshot = None
        self.revision, self.painted_revision = 0, -1
        self.uncertain, self.failure, self.active = False, False, True
        header = _fit(self.content.header(time.monotonic()), max(0, self.geometry[0] - 1))
        prefix = "\r" + "\x1b[B" * max(0, self.rows - 1 - self.cursor_row)
        if self.rows:
            prefix += "\n"
        new_rows = min(self.geometry[1], self.rows + 1)
        self.header_row, self.header_prefix = new_rows - 1, ""
        self._queue(prefix + header + "\r", new_rows, new_rows - 1, 0)

    def update(self, value: dict[str, object]) -> None:
        if value.get("epoch") != self.epoch or not self.active:
            return
        revision = value.get("revision")
        if type(revision) is not int or revision <= self.revision:
            return
        snapshot = ProgressSnapshot.decode(value)
        self.snapshot, self.revision = snapshot, revision

    def tick(self, now: float) -> None:
        observed = os.get_terminal_size(self.output)
        geometry = (min(_MAX_COLUMNS, observed.columns), observed.lines)
        if geometry[0] < 2 or geometry[1] < 1:
            return
        if geometry != self.geometry:
            # Reflow invalidates old offsets. Start a safe one-row surface;
            # only a fresh parent snapshot may restore the larger preview.
            self.uncertain = True
            self.pending, self.offset, self.commit = b"", 0, None
            self.geometry = geometry
            self.snapshot = None
            self.painted_revision = -1
            self.header_row, self.header_prefix = 0, ""
            self._queue("\x18\x1b[0m\r\n" + _fit(self.content.header(now), geometry[0] - 1) + "\r", 1, 0, 0)
            return
        if self.pending:
            return
        snapshot = self.snapshot
        if snapshot is not None and (snapshot.columns, snapshot.height) == geometry and self.painted_revision != self.revision:
            self.content = snapshot.content
            self.header_row, self.header_prefix = snapshot.header_row, snapshot.header_prefix
            lines = list(snapshot.lines)
            lines[self.header_row] = _fit(self.header_prefix + self.content.header(now), geometry[0] - 1)
            prefix = "\r" + "\x1b[A" * self.cursor_row + "\r"
            if self.rows:
                prefix += "\x1b[2K\x1b[B\r" * (self.rows - 1) + "\x1b[2K" + "\x1b[A" * (self.rows - 1) + "\r"
            text = prefix + "\n".join(lines) + "\x1b[A" * max(0, len(lines) - 1 - snapshot.cursor_row)
            text += "\r" + "\x1b[C" * snapshot.cursor_column
            self._queue(text, len(lines), snapshot.cursor_row, snapshot.cursor_column)
            self.painted_revision = self.revision
            return
        difference = self.header_row - self.cursor_row
        move = ("\x1b[B" if difference > 0 else "\x1b[A") * abs(difference)
        restore = ("\x1b[A" if difference > 0 else "\x1b[B") * abs(difference)
        text = "\r" + move + "\x1b[2K" + _fit(self.header_prefix + self.content.header(now), geometry[0] - 1)
        self._queue(text + "\r" + restore + "\x1b[C" * self.cursor_column,
                    self.rows, self.cursor_row, self.cursor_column)

    def write(self) -> None:
        if not self.pending:
            return
        try:
            count = os.write(self.output, self.pending[self.offset:self.offset + 4096])
        except BlockingIOError:
            return
        if count <= 0:
            raise OSError("progress terminal short write")
        self.offset += count
        if self.offset == len(self.pending):
            self.rows, self.cursor_row, self.cursor_column = self.commit
            self.pending, self.offset, self.commit = b"", 0, None

    def release(self) -> dict[str, object]:
        self.active = False
        self.uncertain = self.uncertain or bool(self.pending and self.offset)
        self.pending, self.offset, self.commit = b"", 0, None
        self.snapshot = None
        self.content = ProgressContent(time.monotonic(), _FRAMES, " · Working")
        return {"op": "released", "epoch": self.epoch, "uncertain": self.uncertain,
                "rows": self.rows, "cursor_row": self.cursor_row,
                "cursor_column": self.cursor_column, "columns": self.geometry[0], "height": self.geometry[1]}


def _worker(command: int, snapshot: int, status: int, output: int, parent: int) -> None:
    # The parent keeps input/signal policy. This peer stays in the foreground
    # process group so job-control suspension also suspends terminal painting.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)
    commands, snapshots, writer = _Reader(command), _Reader(snapshot), _Writer(output)
    pending_status = _record({"op": "ready"})
    next_tick = time.monotonic()
    while os.getppid() == parent:
        # EOF and parent identity detect death; a blocked parent remains alive.
        for value in commands.read():
            operation = value.get("op")
            if operation == "begin":
                writer.begin(value)
            elif operation == "quiesce" and value.get("epoch") == writer.epoch:
                pending_status = _record(writer.release())
            else:
                raise ValueError("invalid progress control")
        if commands.eof:
            return
        for value in snapshots.read():
            writer.update(value)
        now = time.monotonic()
        if writer.active and now >= next_tick:
            try:
                writer.tick(now)
            except (OSError, ValueError, TypeError):
                writer.failure = True
                writer.release()
                pending_status = _record({"op": "failed", "epoch": writer.epoch})
            next_tick = now + _INTERVAL
        if writer.active:
            try:
                writer.write()
            except OSError:
                writer.failure = True
                writer.release()
                pending_status = _record({"op": "failed", "epoch": writer.epoch})
        if pending_status:
            try:
                count = os.write(status, pending_status)
                if count == len(pending_status):
                    pending_status = b""
            except BlockingIOError:
                pass
        writable = ([output] if writer.active and writer.pending else []) + ([status] if pending_status else [])
        select.select((command, snapshot), writable, (), max(0.0, min(_INTERVAL, next_tick - time.monotonic())) if writer.active else _INTERVAL)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 7 or sys.argv[1] != "--worker":
            raise ValueError("invalid progress worker arguments")
        _worker(*(int(value) for value in sys.argv[2:]))
    except (OSError, ValueError, TypeError, KeyError, EOFError):
        sys.exit(1)
