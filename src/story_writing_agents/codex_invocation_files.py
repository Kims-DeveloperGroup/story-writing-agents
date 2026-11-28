"""Request and response files for one Codex CLI invocation."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

from .agent_context import canonical_provider_json
from .provider_adapter import ProviderInvocation, ProviderInvocationError
from .structured_output_validation import codex_native_schema

__all__ = [
    "create_invocation_files",
    "read_regular_output",
    "regular_output_exists",
    "remove_invocation_directory",
    "write_schema",
]

_MAX_CODEX_RESPONSE_UTF8_BYTES = 8 * 1_024 * 1_024


def create_invocation_files(
    make_temporary_directory: Callable[[], Callable[..., str]],
) -> tuple[Path, Path, Path]:
    """Create the invocation directory and select its schema and response paths."""
    temporary_directory = Path(
        make_temporary_directory()(prefix="story-codex-output-")
    )
    schema_path = temporary_directory / "schema.json"
    output_path = temporary_directory / "response.json"
    return temporary_directory, schema_path, output_path


def write_schema(schema_path: Path, invocation: ProviderInvocation) -> None:
    """Encode and write the schema accepted by this Codex invocation."""
    try:
        schema_json = canonical_provider_json(codex_native_schema(invocation.schema))
    except ValueError:
        raise ProviderInvocationError(
            "Codex request could not be encoded",
            taint_existing_thread=False,
        ) from None
    schema_path.write_bytes(schema_json)


def regular_output_exists(path: Path) -> bool:
    """Return whether the exact output name is currently a regular file."""

    try:
        observed = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(observed.st_mode)


def read_regular_output(path: Path) -> str:
    """Read one bounded, identity-stable regular output without following links."""

    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OSError("Codex response output is not a regular file")
        if before.st_size > _MAX_CODEX_RESPONSE_UTF8_BYTES:
            raise ProviderInvocationError(
                "Codex CLI returned oversized structured output",
                taint_existing_thread=True,
            )
        content = bytearray()
        while len(content) <= _MAX_CODEX_RESPONSE_UTF8_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    64 * 1_024,
                    _MAX_CODEX_RESPONSE_UTF8_BYTES + 1 - len(content),
                ),
            )
            if not chunk:
                break
            content.extend(chunk)
        if len(content) > _MAX_CODEX_RESPONSE_UTF8_BYTES:
            raise ProviderInvocationError(
                "Codex CLI returned oversized structured output",
                taint_existing_thread=True,
            )
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or after.st_nlink != 1:
            raise OSError("Codex response output is not a regular file")
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError("Codex response output changed while being read")
        return bytes(content).decode("utf-8", errors="strict")
    finally:
        os.close(descriptor)



def remove_invocation_directory(temporary_directory: Path) -> None:
    """Remove the invocation's temporary directory, ignoring cleanup errors."""
    shutil.rmtree(temporary_directory, ignore_errors=True)
