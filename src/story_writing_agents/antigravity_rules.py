"""Bounded Antigravity staging rules reads and atomic installation."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4


_ExceptionState = tuple[BaseException, Any]


def _remember_rules_cleanup_control(
    retained: _ExceptionState | None,
    failure: BaseException,
) -> _ExceptionState | None:
    """Retain only the first non-ordinary rules cleanup failure."""

    if isinstance(failure, Exception) or retained is not None:
        return retained
    return failure, failure.__traceback__


def _cleanup_rules_descriptor(
    descriptor: int,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Close one rules descriptor without masking the active operation."""

    try:
        os.close(descriptor)
    except Exception:
        pass
    except BaseException as failure:
        retained = _remember_rules_cleanup_control(retained, failure)
    return retained


def _cleanup_rules_temporary(
    temporary: Path,
    retained: _ExceptionState | None,
) -> _ExceptionState | None:
    """Remove one rules temporary without following it or masking work."""

    try:
        os.unlink(temporary)
    except Exception:
        pass
    except BaseException as failure:
        retained = _remember_rules_cleanup_control(retained, failure)
    return retained


def _raise_after_rules_cleanup(
    body_failure: _ExceptionState | None,
    cleanup_control: _ExceptionState | None,
) -> None:
    """Raise body control, cleanup control, or an ordinary body failure."""

    retained = body_failure
    if body_failure is None or isinstance(body_failure[0], Exception):
        retained = cleanup_control or body_failure
    if retained is not None:
        failure, traceback = retained
        raise failure.with_traceback(traceback)


def _rules_identity(
    value: os.stat_result,
    *,
    diagnostic: str,
) -> tuple[int, int]:
    """Require one regular, singly linked file with meaningful identity."""

    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise OSError(diagnostic)
    device = value.st_dev
    inode = value.st_ino
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device <= 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode <= 0
    ):
        raise OSError(diagnostic)
    return device, inode


def _rules_descriptor_identity(
    descriptor: int,
    *,
    diagnostic: str,
) -> tuple[int, int]:
    """Return one safe rules descriptor identity with a fixed failure."""

    inspection_failed = False
    try:
        value = os.fstat(descriptor)
    except OSError:
        inspection_failed = True
    if inspection_failed:
        raise OSError(diagnostic) from None
    return _rules_identity(value, diagnostic=diagnostic)


def _rules_path_identity(
    target: Path,
    *,
    diagnostic: str,
) -> tuple[int, int]:
    """Return one safe no-follow rules pathname identity."""

    inspection_failed = False
    try:
        value = os.lstat(target)
    except OSError:
        inspection_failed = True
    if inspection_failed:
        raise OSError(diagnostic) from None
    return _rules_identity(value, diagnostic=diagnostic)


def _validate_rules_target(target: Path) -> None:
    """Accept only a missing or singly linked regular rules target."""

    inspection_failed = False
    try:
        value = os.lstat(target)
    except FileNotFoundError:
        return
    except OSError:
        inspection_failed = True
    if inspection_failed:
        raise OSError(
            "Antigravity rules target could not be inspected"
        ) from None
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise OSError("Antigravity rules target is unsafe")


def read_rules_source(source: Path) -> bytes:
    """Read one verified regular staged-rules source without following it."""

    descriptor: int | None = None
    content: bytes | None = None
    body_failure: _ExceptionState | None = None
    cleanup_control: _ExceptionState | None = None
    try:
        flags = os.O_RDONLY
        for name in (
            "O_CLOEXEC",
            "O_NOINHERIT",
            "O_BINARY",
            "O_NOFOLLOW",
            "O_NONBLOCK",
        ):
            flags |= getattr(os, name, 0)
        open_failed = False
        try:
            descriptor = os.open(source, flags)
        except OSError:
            open_failed = True
        if open_failed:
            raise OSError(
                "Antigravity rules source could not be opened"
            ) from None
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, NotImplementedError, OSError):
            pass
        diagnostic = "Antigravity rules source is unsafe"
        opened_identity = _rules_descriptor_identity(
            descriptor,
            diagnostic=diagnostic,
        )
        pathname_identity = _rules_path_identity(
            source,
            diagnostic=diagnostic,
        )
        if opened_identity != pathname_identity:
            raise OSError(diagnostic)
        chunks: list[bytes] = []
        read_failed = False
        try:
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            read_failed = True
        if read_failed:
            raise OSError(
                "Antigravity rules source could not be read"
            ) from None
        content = b"".join(chunks)
    except BaseException as failure:
        body_failure = (failure, failure.__traceback__)
    if descriptor is not None:
        cleanup_control = _cleanup_rules_descriptor(
            descriptor,
            cleanup_control,
        )
    _raise_after_rules_cleanup(body_failure, cleanup_control)
    if content is None:
        raise AssertionError("unreachable")
    return content


def _open_rules_temporary(target: Path) -> tuple[int, Path]:
    """Exclusively create one bounded-name temporary beside a rules file."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NOFOLLOW",
    ):
        flags |= getattr(os, name, 0)
    creation_failed = False
    for _attempt in range(16):
        temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, flags, 0o600)
        except FileExistsError:
            continue
        except OSError:
            creation_failed = True
            break
        return descriptor, temporary
    if creation_failed:
        raise OSError(
            "Antigravity rules temporary file could not be created"
        ) from None
    raise OSError(
        "Antigravity rules temporary file name attempts were exhausted"
    ) from None


def install_rules(target: Path, content: bytes) -> None:
    """Atomically install private rules through one verified temporary."""

    descriptor: int | None = None
    temporary: Path | None = None
    temporary_present = False
    body_failure: _ExceptionState | None = None
    cleanup_control: _ExceptionState | None = None
    try:
        _validate_rules_target(target)
        descriptor, temporary = _open_rules_temporary(target)
        temporary_present = True
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, NotImplementedError, OSError):
            pass
        diagnostic = "Antigravity rules temporary file is unsafe"
        temporary_identity = _rules_descriptor_identity(
            descriptor,
            diagnostic=diagnostic,
        )
        remaining = memoryview(content + b"\n")
        write_failed = False
        try:
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    write_failed = True
                    break
                remaining = remaining[written:]
        except OSError:
            write_failed = True
        if write_failed:
            raise OSError(
                "Antigravity rules temporary file could not be written"
            ) from None
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, NotImplementedError, OSError):
            pass
        if (
            _rules_descriptor_identity(
                descriptor,
                diagnostic=diagnostic,
            )
            != temporary_identity
        ):
            raise OSError(diagnostic)
        closing_descriptor = descriptor
        descriptor = None
        os.close(closing_descriptor)
        if (
            _rules_path_identity(
                temporary,
                diagnostic=diagnostic,
            )
            != temporary_identity
        ):
            raise OSError(diagnostic)
        _validate_rules_target(target)
        replace_failed = False
        try:
            os.replace(temporary, target)
        except OSError:
            replace_failed = True
        if replace_failed:
            raise OSError("Antigravity rules replacement failed") from None
        temporary_present = False
        installed_diagnostic = "Antigravity installed rules are unsafe"
        if (
            _rules_path_identity(
                target,
                diagnostic=installed_diagnostic,
            )
            != temporary_identity
        ):
            raise OSError(installed_diagnostic)
    except BaseException as failure:
        body_failure = (failure, failure.__traceback__)
    if descriptor is not None:
        cleanup_control = _cleanup_rules_descriptor(
            descriptor,
            cleanup_control,
        )
    if temporary_present and temporary is not None:
        cleanup_control = _cleanup_rules_temporary(
            temporary,
            cleanup_control,
        )
    _raise_after_rules_cleanup(body_failure, cleanup_control)


__all__ = ("read_rules_source", "install_rules")
