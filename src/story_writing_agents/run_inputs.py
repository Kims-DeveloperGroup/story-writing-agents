"""Shared strict draft-file input loading."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ConfigurationError


def read_draft_file(path: str | os.PathLike[str]) -> str:
    """Read one caller-relative draft exactly as strict UTF-8 text.

    ``~`` is expanded while absolute paths, caller-CWD relative paths, symlinks,
    and literal metacharacters retain normal ``Path`` semantics. The operation
    performs no writes and returns empty text unchanged; invalid UTF-8 or an OS
    read failure raises the established ``ConfigurationError`` message.
    """

    draft_path = Path(path).expanduser()
    try:
        return draft_path.read_bytes().decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"draft file is not valid UTF-8: {draft_path}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"cannot read draft file {draft_path}: {exc}"
        ) from exc


__all__ = ["read_draft_file"]
