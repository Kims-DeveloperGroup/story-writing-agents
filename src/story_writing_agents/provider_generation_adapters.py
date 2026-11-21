"""Construct the provider adapters for an initial or staged Backend generation."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .agents import ROSTER
from .antigravity_adapter import AntigravityAdapter
from .codex_adapter import CodexAdapter
from .config import ConfigurationSnapshot
from .provider_adapter import AgentExecutionProfile


def create_codex_adapter(
    *,
    executable: str,
    timeout_seconds: float,
    configuration: ConfigurationSnapshot,
    explicit_model: str | None,
    control_root: Path,
    profiles: Mapping[str, AgentExecutionProfile],
) -> CodexAdapter:
    return CodexAdapter(
        executable=executable,
        timeout_seconds=timeout_seconds,
        configuration=configuration,
        explicit_model=explicit_model,
        control_root=control_root,
        execution_profiles={
            agent_id: profile
            for agent_id, profile in profiles.items()
            if profile.provider == "codex"
        },
        create_process=lambda: asyncio.create_subprocess_exec,
        wait_for=lambda: asyncio.wait_for,
        make_temporary_directory=lambda: tempfile.mkdtemp,
        which=lambda: shutil.which,
    )


def create_antigravity_adapter(
    *,
    executable: str,
    timeout_seconds: float,
    state_root: Path,
    explicit_model: str | None,
    explicit_effort: str | None,
    configuration: ConfigurationSnapshot,
    profiles: Mapping[str, AgentExecutionProfile],
) -> AntigravityAdapter:
    return AntigravityAdapter(
        executable=executable,
        timeout_seconds=timeout_seconds,
        state_root=state_root,
        explicit_model=explicit_model,
        explicit_effort=explicit_effort,
        system_model=configuration.system_defaults.antigravity_model,
        system_effort=configuration.system_defaults.antigravity_effort,
        agent_settings={
            agent_id: (
                configuration.agent_configs[agent_id].antigravity_model,
                configuration.agent_configs[agent_id].antigravity_effort,
            )
            for agent_id in ROSTER
        },
        execution_profiles={
            agent_id: profile
            for agent_id, profile in profiles.items()
            if profile.provider == "antigravity"
        },
        create_process=lambda: asyncio.create_subprocess_exec,
        wait_for=lambda: asyncio.wait_for,
        which=lambda: shutil.which,
    )


__all__ = ("create_antigravity_adapter", "create_codex_adapter")
