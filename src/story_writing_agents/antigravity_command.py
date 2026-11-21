"""Antigravity CLI command arguments, path checks, and headless capability probe."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from .agent_context import canonical_provider_json
from .provider_adapter import ProviderInvocationError
from .provider_execution_workspace import ProviderExecutionWorkspace


class _CommandSettings(Protocol):
    """Adapter settings read while preparing an Antigravity command."""

    @property
    def executable(self) -> str: ...

    @property
    def timeout_seconds(self) -> float: ...

    def invocation_settings(self, agent_id: str | None) -> tuple[str | None, str | None]: ...

    def executable_available(self) -> bool: ...


_HELP_LONG_OPTION = re.compile(
    r"(?<![A-Za-z0-9_-])--[A-Za-z0-9][A-Za-z0-9_-]*(?![A-Za-z0-9_-])"
)
_REQUIRED_HEADLESS_FLAGS = (
    "--agent",
    "--conversation",
    "--disable-slash-commands",
    "--effort",
    "--input-format",
    "--json-schema",
    "--mode",
    "--model",
    "--new-project",
    "--output-format",
    "--print",
    "--print-timeout",
    "--sandbox",
)

CapabilityRun = Callable[..., subprocess.CompletedProcess[bytes]]


def command(
    settings: _CommandSettings,
    *,
    provider_state_directory: Path,
    schema: Mapping[str, Any],
    thread_id: str | None,
    agent_id: str,
    output_format: str,
    native_agent_id: str,
    agent_id_pattern: re.Pattern[str],
) -> list[str]:
    """Build Antigravity options from the already rendered transport schema."""

    if (
        agent_id_pattern.fullmatch(agent_id) is None
        or agent_id_pattern.fullmatch(native_agent_id) is None
    ):
        raise ValueError("Antigravity custom agent ID is invalid")
    command = [
        settings.executable,
        # agy retains this inherited option spelling.
        f"--gemini_dir={provider_state_directory}",
        "--input-format",
        "text",
        "--output-format",
        output_format,
        "--json-schema",
        canonical_provider_json(schema).decode(
            "utf-8", errors="strict"
        ),
        "--agent",
        native_agent_id,
        "--sandbox",
        "--mode",
        "plan",
        "--disable-slash-commands",
        "--print-timeout",
        f"{settings.timeout_seconds:g}s",
    ]
    model, effort = settings.invocation_settings(agent_id)
    if model is not None:
        command.extend(["--model", model])
    if effort is not None:
        command.extend(["--effort", effort])
    if thread_id is None:
        command.append("--new-project")
    else:
        command.extend(["--conversation", thread_id])
    return command


def execution_command(
    command: list[str],
    *,
    provider_state_directory: Path,
    execution_workspace: ProviderExecutionWorkspace,
    projected_role: Path,
    settings: _CommandSettings,
    which: Callable[[], Callable[[str], str | None]],
) -> list[str]:
    """Resolve one native command for the exact execution workspace."""

    session_root, _ = require_state_workspace_disjoint(
        provider_state_directory,
        execution_workspace,
    )
    resolved = which()(settings.executable)
    if resolved is None:
        raise ProviderInvocationError(
            "Antigravity CLI executable was not found",
            taint_existing_thread=False,
        )
    executable = Path(os.path.realpath(resolved))
    role = Path(os.path.realpath(projected_role))
    agents_root = session_root / ".agents"
    agents_directory = agents_root / "agents"
    selected_agent_directory = agents_directory / projected_role.parent.name
    if (
        session_root != execution_workspace.worktree_root
        or role != projected_role
        or role != selected_agent_directory / "agent.md"
    ):
        raise ProviderInvocationError(
            "Antigravity execution workspace is unavailable",
            taint_existing_thread=False,
        )
    resolved_command = list(command)
    resolved_command[0] = os.fspath(executable)
    return resolved_command


def require_state_workspace_disjoint(
    provider_state_directory: Path,
    execution_workspace: ProviderExecutionWorkspace,
) -> tuple[Path, Path]:
    """Require canonical provider state outside the live session root."""

    execution_workspace.require_current()
    session_root = Path(os.path.realpath(execution_workspace.worktree_root))
    native_state = Path(os.path.realpath(provider_state_directory))
    if (
        session_root != execution_workspace.worktree_root
        or native_state == session_root
        or session_root in native_state.parents
        or native_state in session_root.parents
    ):
        raise ProviderInvocationError(
            "Antigravity execution workspace is unavailable",
            taint_existing_thread=False,
        )
    return session_root, native_state


def executable_available(
    which: Callable[[], Callable[[str], str | None]],
    settings: _CommandSettings,
) -> bool:
    """Return whether the configured Antigravity executable resolves."""

    return which()(settings.executable) is not None


def headless_capabilities_available(
    settings: _CommandSettings,
    *,
    state_root: Path,
    capability_run: Callable[[], CapabilityRun],
) -> bool:
    """Run one bounded, provider-free check for required headless flags."""

    probe_directory = state_root / "capability-probe"
    try:
        if not settings.executable_available():
            return False
        try:
            result = capability_run()(
                [
                    settings.executable,
                    f"--gemini_dir={probe_directory}",
                    "--help",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=min(5.0, settings.timeout_seconds),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        try:
            output = (result.stdout + result.stderr).decode(
                "utf-8",
                errors="strict",
            )
        except UnicodeDecodeError:
            return False
        available_flags = frozenset(_HELP_LONG_OPTION.findall(output))
        return all(flag in available_flags for flag in _REQUIRED_HEADLESS_FLAGS)
    finally:
        try:
            shutil.rmtree(probe_directory, ignore_errors=True)
        except Exception:
            pass
