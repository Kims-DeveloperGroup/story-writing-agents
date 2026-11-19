"""Read and validate exact current and published workspace scaffold bytes."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

import story_writing_agents.workspace.schema as schema

from ..config import validate_persisted_configuration_bytes
from ..errors import ConfigurationError, WorkspaceFormatError, WorkspaceSecurityError
from ..git import GitBaselineManifest, GitManifestEntry
from .schema import WorkspaceScaffoldEntry, validate_workspace_template_name
from ._policy_baselines_editorial import (
    PRE_EDITORIAL_V2_POLICY_FILES,
    PRE_MIXED_ROLE_V2_POLICY_FILES,
)
from ._policy_baselines_guidance import (
    MAIN_V2_POLICY_FILES,
    PRE_GUIDANCE_V2_POLICY_FILES,
)
from ._policy_baselines_legacy import LEGACY_V2_POLICY_FILES, OBJECTIVE_V2_POLICY_FILES
from .root_filesystem import (
    meaningful_directory_identity,
    open_existing_root,
    read_relative_regular,
    require_lexical_root,
    verify_named_identity,
    walk_directories,
)


MUTABLE_CONFIGURATION_PATHS = frozenset(
    {"config/system_defaults.json", "config/agent_configs.json"}
)


def validated_configuration_allowance(root: Path) -> tuple[str, ...]:
    """Validate mutable configuration bytes before granting exact Git tolerance."""

    try:
        identity = meaningful_directory_identity(
            os.stat(root, follow_symlinks=False)
        )
    except OSError:
        raise WorkspaceSecurityError("workspace root cannot be inspected") from None
    if identity is None:
        raise WorkspaceSecurityError("workspace root is not a safe directory")
    descriptor = open_existing_root(root, identity)
    try:
        system_content, agent_content = _read_mutable_configuration(descriptor)
        validate_mutable_configuration(
            system_content,
            agent_content,
            persona_exists=lambda name: persona_exists(descriptor, name),
        )
    finally:
        os.close(descriptor)
    return tuple(sorted(MUTABLE_CONFIGURATION_PATHS))


def known_initializer_scaffold(
    descriptor: int,
) -> tuple[WorkspaceScaffoldEntry, ...]:
    """Select only current or exact published v2 policy bytes from a bound root."""

    scaffold = schema.canonical_initializer_scaffold()
    legacy_policies = dict(LEGACY_V2_POLICY_FILES)
    policy_scaffold = tuple(
        entry for entry in scaffold if entry.path in legacy_policies
    )
    observed = required_scaffold_projection(descriptor, policy_scaffold)
    current_policies = {entry.path: entry.content for entry in policy_scaffold}
    if observed == current_policies:
        return scaffold
    for published in (
        legacy_policies,
        dict(OBJECTIVE_V2_POLICY_FILES),
        dict(MAIN_V2_POLICY_FILES),
        dict(PRE_GUIDANCE_V2_POLICY_FILES),
        dict(PRE_EDITORIAL_V2_POLICY_FILES),
        dict(PRE_MIXED_ROLE_V2_POLICY_FILES),
    ):
        if observed == published:
            return tuple(
                WorkspaceScaffoldEntry(
                    entry.path,
                    published.get(entry.path, entry.content),
                )
                for entry in scaffold
            )
    raise WorkspaceFormatError("workspace scaffold requires recovery")


def known_baseline_manifest(main_root: Path) -> GitBaselineManifest:
    """Read the main root's known policy baseline without adopting session edits."""

    main_root = require_lexical_root(main_root)
    try:
        identity = meaningful_directory_identity(
            os.stat(main_root, follow_symlinks=False)
        )
    except OSError:
        raise WorkspaceSecurityError("workspace root cannot be inspected") from None
    if identity is None:
        raise WorkspaceSecurityError("workspace root is not a safe directory")
    descriptor = open_existing_root(main_root, identity)
    try:
        scaffold = known_initializer_scaffold(descriptor)
        verify_named_identity(main_root, descriptor, identity)
        return baseline_manifest(scaffold)
    finally:
        os.close(descriptor)


def baseline_manifest(
    scaffold: tuple[WorkspaceScaffoldEntry, ...],
) -> GitBaselineManifest:
    return GitBaselineManifest(
        tuple(
            GitManifestEntry(entry.path, hashlib.sha256(entry.content).hexdigest())
            for entry in scaffold
        )
    )


def required_scaffold_projection(
    descriptor: int,
    scaffold: tuple[WorkspaceScaffoldEntry, ...],
) -> dict[str, bytes]:
    """Read only the exact required scaffold leaves through the bound root."""

    result: dict[str, bytes] = {}
    try:
        for entry in scaffold:
            result[entry.path] = read_relative_regular(descriptor, entry.path)
    except (OSError, WorkspaceFormatError):
        raise WorkspaceFormatError("workspace scaffold requires recovery") from None
    return result


def _read_mutable_configuration(descriptor: int) -> tuple[bytes, bytes]:
    """Read only the two exact mutable configuration leaves."""

    try:
        return (
            read_relative_regular(descriptor, "config/system_defaults.json"),
            read_relative_regular(descriptor, "config/agent_configs.json"),
        )
    except (OSError, WorkspaceFormatError):
        raise WorkspaceFormatError("workspace configuration is missing") from None


def persona_exists(descriptor: int, name: str) -> bool:
    """Check only the two exact no-follow locations for one referenced persona."""

    for relative in (
        f"config/persona_templates/{name}.md",
        f"config/persona_templates/alternative_templates/{name}.md",
    ):
        parts = Path(relative).parts
        try:
            parent = walk_directories(descriptor, parts[:-1], create=False)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            for option in ("O_CLOEXEC", "O_NOINHERIT", "O_BINARY", "O_NONBLOCK"):
                flags |= getattr(os, option, 0)
            try:
                candidate = os.open(
                    parts[-1],
                    flags,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                continue
            except OSError:
                return False
            try:
                try:
                    value = os.fstat(candidate)
                except OSError:
                    return False
                return stat.S_ISREG(value.st_mode)
            finally:
                os.close(candidate)
        finally:
            os.close(parent)
    return False


def validate_mutable_configuration(
    system_content: bytes,
    agent_content: bytes,
    *,
    persona_exists: Callable[[str], bool],
) -> None:
    """Validate targeted mutable configuration and referenced persona presence."""

    try:
        validate_persisted_configuration_bytes(
            system_content,
            agent_content,
            validate_persona_name=validate_workspace_template_name,
            persona_exists=persona_exists,
        )
    except (
        ConfigurationError,
        TypeError,
        WorkspaceFormatError,
        WorkspaceSecurityError,
    ):
        raise WorkspaceFormatError("workspace configuration is invalid") from None
