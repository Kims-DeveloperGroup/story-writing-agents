"""Creation and permission hardening of Antigravity-owned directories."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

__all__ = ["prepare_owned_directory", "prepare_owned_directory_chain"]


def prepare_owned_directory_chain(*components: str, state_root: Path) -> Path:
    """Create and best-effort harden one adapter-owned directory chain."""

    directory = state_root
    prepare_owned_directory(directory)
    for component in components:
        directory = directory / component
        prepare_owned_directory(directory)
    return directory


def prepare_owned_directory(directory: Path) -> bool:
    """Require one no-follow directory; report whether it already existed."""

    existed = True
    try:
        os.mkdir(directory, mode=0o700)
        existed = False
    except FileExistsError:
        pass
    value = os.lstat(directory)
    if not stat.S_ISDIR(value.st_mode):
        raise NotADirectoryError("Antigravity state directory is unsafe")
    _harden_owned_directory(directory)
    return existed


def _harden_owned_directory(directory: Path) -> None:
    """Best-effort tighten a verified directory without following links."""

    supports_follow_symlinks = getattr(
        os,
        "supports_follow_symlinks",
        (),
    )
    no_follow_operations = {
        getattr(operation, "__name__", None)
        for operation in supports_follow_symlinks
    }
    if "chmod" in no_follow_operations:
        try:
            os.chmod(directory, 0o700, follow_symlinks=False)
        except (NotImplementedError, OSError):
            pass
        return

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_only = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory_only or not hasattr(os, "fchmod"):
        return
    flags = os.O_RDONLY | no_follow | directory_only
    for name in (
        "O_CLOEXEC",
        "O_NOINHERIT",
        "O_BINARY",
        "O_NONBLOCK",
    ):
        flags |= getattr(os, name, 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        try:
            value = os.lstat(directory)
        except OSError:
            return
        if not stat.S_ISDIR(value.st_mode):
            raise NotADirectoryError(
                "Antigravity state directory is unsafe"
            )
        return

    active_control: tuple[BaseException, Any] | None = None
    try:
        try:
            os.set_inheritable(descriptor, False)
        except (AttributeError, NotImplementedError, OSError):
            pass
        try:
            os.fchmod(descriptor, 0o700)
        except (AttributeError, NotImplementedError, OSError):
            pass
    except BaseException as failure:
        active_control = (failure, failure.__traceback__)
    try:
        os.close(descriptor)
    except OSError:
        pass
    except BaseException:
        if active_control is None:
            raise
    if active_control is not None:
        failure, traceback = active_control
        raise failure.with_traceback(traceback)
