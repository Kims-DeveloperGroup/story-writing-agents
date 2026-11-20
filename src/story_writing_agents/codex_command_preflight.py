"""Codex command arguments, executable resolution, and path preflight."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from .agents import ROSTER
from .config import ConfigurationSnapshot
from .errors import ConfigurationError
from .git.session_contracts import GIT_SESSION_WORKTREE_SIBLING_SUFFIX
from .provider_adapter import AgentExecutionProfile, ProviderInvocationError
from .provider_execution_workspace import ProviderExecutionWorkspace

__all__ = [
    "command",
    "executable_available",
    "execution_command",
    "invocation_settings",
    "model",
    "native_state_directory",
    "reasoning_effort",
]

# Leave substantial headroom below Darwin's aggregate process-argument ceiling.
_MAX_DEVELOPER_INSTRUCTIONS_ARGUMENT_UTF8_BYTES = 512 * 1_024


def execution_command(
    staging_directory: Path,
    schema_path: Path,
    output_path: Path,
    thread_id: str | None,
    agent_id: str | None,
    execution_workspace: ProviderExecutionWorkspace,
    *,
    which: Callable[[], Callable[[str], str | None]],
    get_executable: Callable[[], str],
    get_control_root: Callable[[], Path],
    get_native_state_directory: Callable[[], Callable[..., Path]],
    invocation_settings: Callable[[str | None], tuple[str | None, str | None]],
) -> list[str]:
    """Build the native Codex command for the exact execution workspace."""

    execution_workspace.require_current()
    resolved_executable = which()(get_executable())
    if resolved_executable is None:
        raise ProviderInvocationError(
            f"Codex CLI executable was not found: {get_executable()}",
            taint_existing_thread=False,
        )
    executable = Path(os.path.realpath(resolved_executable))
    staging = Path(os.path.realpath(staging_directory))
    schema = Path(os.path.realpath(schema_path))
    output = Path(os.path.realpath(output_path))
    session_root = Path(os.path.realpath(execution_workspace.worktree_root))
    session_container = get_control_root().with_name(
        get_control_root().name + GIT_SESSION_WORKTREE_SIBLING_SUFFIX
    )
    if session_root != session_container / execution_workspace.session_id:
        raise ProviderInvocationError(
            "Codex execution workspace is unavailable",
            taint_existing_thread=False,
        )
    get_native_state_directory()(
        session_root=session_root,
        session_container=session_container,
        staging=staging,
        output=output,
    )
    definition_path = staging / "AGENTS.md"
    try:
        developer_instructions = definition_path.read_text(
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, UnicodeError) as exc:
        raise ProviderInvocationError(
            "Codex bound instructions are unavailable",
            taint_existing_thread=False,
        ) from exc
    developer_instructions_argument = (
        "developer_instructions="
        + json.dumps(developer_instructions, ensure_ascii=False)
    )
    if (
        len(developer_instructions_argument.encode("utf-8", errors="strict"))
        > _MAX_DEVELOPER_INSTRUCTIONS_ARGUMENT_UTF8_BYTES
    ):
        raise ProviderInvocationError(
            "Codex bound instructions exceed the safe process argument limit",
            taint_existing_thread=False,
        )
    command = [
        os.fspath(executable),
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        "./",
        "exec",
    ]
    model, reasoning_effort = invocation_settings(agent_id)
    if model is not None:
        command.extend(["--model", model])
    if reasoning_effort is not None:
        command.extend(
            ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        )
    if thread_id is not None:
        command.append("resume")
    command.extend(
        [
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "apps",
            "--disable",
            "multi_agent",
            "--disable",
            "shell_tool",
            "--disable",
            "web_search",
            "--config",
            'web_search="disabled"',
            "--config",
            "project_root_markers=[]",
            "--config",
            developer_instructions_argument,
            "--output-schema",
            os.fspath(schema),
            "--output-last-message",
            os.fspath(output),
        ]
    )
    if thread_id is not None:
        command.append(thread_id)
    command.append("-")
    execution_workspace.require_current()
    return command


def native_state_directory(
    *,
    session_root: Path,
    session_container: Path,
    staging: Path,
    output: Path,
    get_control_root: Callable[[], Path],
) -> Path:
    """Return a narrow, owned Codex state root or fail before launch."""

    configured_native_state = Path(
        os.environ.get("CODEX_HOME", os.fspath(Path.home() / ".codex"))
    )
    if not configured_native_state.is_absolute():
        raise ProviderInvocationError(
            "Codex native state directory is unsafe",
            taint_existing_thread=False,
        )
    native_state = Path(os.path.realpath(configured_native_state))
    home = Path(os.path.realpath(Path.home()))

    def overlaps(first: Path, second: Path) -> bool:
        return bool(
            first == second
            or first in second.parents
            or second in first.parents
        )

    try:
        observed = os.stat(native_state, follow_symlinks=False)
    except OSError:
        observed = None
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if (
        observed is None
        or not stat.S_ISDIR(observed.st_mode)
        or effective_uid is None
        or observed.st_uid != effective_uid
        or stat.S_IMODE(observed.st_mode) & 0o022
        or native_state == Path(native_state.anchor)
        or native_state == home
        or any(
            overlaps(native_state, protected)
            for protected in (
                session_root,
                session_container,
                staging,
                output.parent,
                get_control_root(),
            )
        )
    ):
        raise ProviderInvocationError(
            "Codex native state directory is unsafe",
            taint_existing_thread=False,
        )
    return native_state


def command(
    executable: str,
    staging_directory: Path,
    schema_path: Path,
    output_path: Path,
    thread_id: str | None,
    agent_id: str | None,
    *,
    invocation_settings: Callable[[str | None], tuple[str | None, str | None]],
) -> list[str]:
    """Build the established value-preserving Codex command line."""

    command = [
        executable,
        "--ask-for-approval",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(staging_directory),
        "exec",
    ]
    model, reasoning_effort = invocation_settings(agent_id)
    if model is not None:
        command.extend(["--model", model])
    if reasoning_effort is not None:
        command.extend(
            [
                "--config",
                f'model_reasoning_effort="{reasoning_effort}"',
            ]
        )
    if thread_id is not None:
        command.append("resume")
    command.extend(
        [
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--disable",
            "apps",
            "--disable",
            "multi_agent",
            "--disable",
            "shell_tool",
            "--disable",
            "web_search",
            "--config",
            'web_search="disabled"',
            "--config",
            "project_root_markers=[]",
            "--config",
            (
                "project_doc_max_bytes="
                f"{max(32768, (staging_directory / 'AGENTS.md').stat().st_size + 1)}"
            ),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
    )
    if thread_id is not None:
        command.append(thread_id)
    command.append("-")
    return command


def invocation_settings(
    agent_id: str,
    *,
    execution_profiles: Mapping[str, AgentExecutionProfile] | None,
    explicit_model: str | None,
    configuration: ConfigurationSnapshot,
) -> tuple[str | None, str | None]:
    """Resolve Codex flags from the construction snapshot for one agent."""

    if agent_id not in ROSTER:
        raise ConfigurationError(f"unknown agent: {agent_id}")
    if execution_profiles is not None:
        profile = execution_profiles.get(agent_id)
        if profile is None:
            raise ConfigurationError(
                f"Codex execution profile is unavailable: {agent_id}"
            )
        return profile.model, profile.effort
    agent = configuration.agent_configs[agent_id]
    model = (
        explicit_model
        if explicit_model is not None
        else agent.codex_model
        or configuration.system_defaults.codex_model
    )
    reasoning_effort = (
        agent.codex_reasoning_effort
        or configuration.system_defaults.codex_reasoning_effort
    )
    return model, reasoning_effort


def model(explicit_model: str | None, configuration: ConfigurationSnapshot) -> str | None:
    """Return the construction-time effective system Codex model."""

    return (
        explicit_model
        if explicit_model is not None
        else configuration.system_defaults.codex_model
    )


def reasoning_effort(configuration: ConfigurationSnapshot) -> str | None:
    """Return the construction-time system Codex reasoning effort."""

    return configuration.system_defaults.codex_reasoning_effort


def executable_available(
    which: Callable[[], Callable[[str], str | None]],
    get_executable: Callable[[], str],
) -> bool:
    """Return whether the configured Codex executable resolves."""

    return which()(get_executable()) is not None
