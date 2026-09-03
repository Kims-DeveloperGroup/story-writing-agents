"""Pure canonical schema-v2 workspace definition and scaffold bytes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ..agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
)
from ..errors import (
    SessionIdError,
    WorkspaceFormatError,
    WorkspaceVersionUnsupportedError,
)
from ..git.session_contracts import (
    GIT_SESSION_BRANCH_PREFIX as SESSION_BRANCH_PREFIX,
    GIT_SESSION_INTERNAL_REF_PREFIX as SESSION_INTERNAL_REF_PREFIX,
    GIT_SESSION_WORKTREE_SIBLING_SUFFIX as SESSION_WORKTREE_SIBLING_SUFFIX,
)


WORKSPACE_SCHEMA_VERSION = 2
WORKSPACE_MARKER_PATH = "config/workspace.json"
WORKSPACE_INITIALIZATION_JOURNAL_PATH = ".story-writing-agents-initialize-v2.json"
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_DIGEST = re.compile(r"[0-9a-f]{64}")

CANONICAL_CORE_DOCUMENT_PATHS = (
    "core_documents/character_sheets.md",
    "core_documents/global_outline.md",
    "core_documents/narrative_rules.md",
    "core_documents/part_outlines.md",
)

WORKSPACE_DIRECTORIES: tuple[str, ...] = (
    "config/agents/lead_orchestrator",
    "config/agents/plot_architect",
    "config/agents/character_custodian",
    "config/agents/execution_specialist",
    "config/persona_templates/alternative_templates",
    "core_documents",
    "agent_states/orchestrator_state/conversation_defaults",
    "agent_states/orchestrator_state/sessions",
    "agent_states/orchestrator_state/provider_sessions",
    "agent_states/plot_architect_state",
    "agent_states/character_custodian_state",
    "agent_states/execution_specialist_state",
    "debug_logs",
    "discussion_logs",
    "manuscript",
)

DEFAULT_SYSTEM_CONFIG = MappingProxyType(
    {
        "language": "KO",
        "persona": "default_kim_eun_hee",
        "max_debate_rounds": 1,
        "codex_model": None,
        "codex_reasoning_effort": None,
        "antigravity_model": None,
        "antigravity_effort": None,
        "default_backend": None,
        "conversation_storage_backend": "filesystem",
        "show_message_details": False,
        "conversation_rag_provider": None,
        "conversation_rag_automatic": False,
    }
)

DEFAULT_AGENT_CONFIGS = MappingProxyType(
    {
        agent_id: MappingProxyType(
            {
                "backend": None,
                "language": None,
                "persona": None,
                "codex_model": None,
                "codex_reasoning_effort": None,
                "antigravity_model": None,
                "antigravity_effort": None,
            }
        )
        for agent_id in ROSTER
    }
)

DEFAULT_PERSONA = """# Default Kim Eun-hee Persona

Act as a strict, logical, and candid veteran writing-room collaborator.
Prioritize domain consistency over polite agreement. State only actionable
conclusions and concise evidence; never provide hidden chain-of-thought.
"""

DEFAULT_AGENT_PROMPTS = MappingProxyType(
    {
        LEAD_ORCHESTRATOR: """# Lead Orchestrator

Moderate the shared debate, preserve every specialist critique, synthesize
revisions without discarding conflicts, and return only structured updates for
the allowlisted core documents.
""",
        PLOT_ARCHITECT: """# Plot & Structure Architect

Audit narrative flow, pacing, chapter progression, logical leaps, plot holes,
and compliance with the global and part outlines.
""",
        CHARACTER_CUSTODIAN: """# Character & World Custodian

Audit character behavior, relationships, world continuity, and
secondary-character consistency against the character sheets.
""",
        EXECUTION_SPECIALIST: """# Tone & Execution Specialist

Draft and audit prose, point of view, tone, grammar, formatting, and compliance
with the narrative rules.
""",
    }
)

_GITKEEP_PATHS = (
    "agent_states/character_custodian_state/.gitkeep",
    "agent_states/execution_specialist_state/.gitkeep",
    "agent_states/orchestrator_state/.gitkeep",
    "agent_states/orchestrator_state/conversation_defaults/.gitkeep",
    "agent_states/orchestrator_state/sessions/.gitkeep",
    "agent_states/plot_architect_state/.gitkeep",
    "config/persona_templates/alternative_templates/.gitkeep",
    "debug_logs/.gitkeep",
    "discussion_logs/.gitkeep",
    "manuscript/.gitkeep",
)


@dataclass(frozen=True, slots=True)
class WorkspaceSessionTopology:
    """The one schema-v2 session naming policy, without materialized resources."""

    branch_prefix: str = SESSION_BRANCH_PREFIX
    internal_ref_prefix: str = SESSION_INTERNAL_REF_PREFIX
    worktree_sibling_suffix: str = SESSION_WORKTREE_SIBLING_SUFFIX

    def __post_init__(self) -> None:
        if (
            self.branch_prefix != SESSION_BRANCH_PREFIX
            or self.internal_ref_prefix != SESSION_INTERNAL_REF_PREFIX
            or self.worktree_sibling_suffix != SESSION_WORKTREE_SIBLING_SUFFIX
        ):
            raise WorkspaceFormatError("workspace topology is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceV2Marker:
    """The exact schema-v2 workspace marker value."""

    schema_version: int = WORKSPACE_SCHEMA_VERSION
    session_topology: WorkspaceSessionTopology = field(
        default_factory=WorkspaceSessionTopology
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise WorkspaceFormatError("workspace schema version is invalid")
        if self.schema_version != WORKSPACE_SCHEMA_VERSION:
            raise WorkspaceVersionUnsupportedError(
                "workspace version is unsupported; migration and compatibility "
                "are unsupported, so create a new workspace"
            )
        if type(self.session_topology) is not WorkspaceSessionTopology:
            raise WorkspaceFormatError("workspace topology is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceInitializationJournal:
    """The exact durable initialization recovery marker."""

    manifest_digest: str
    schema_version: int = WORKSPACE_SCHEMA_VERSION
    state: str = "initializing"

    def __post_init__(self) -> None:
        if (
            type(self.manifest_digest) is not str
            or _DIGEST.fullmatch(self.manifest_digest) is None
        ):
            raise WorkspaceFormatError("workspace initialization journal is invalid")
        if type(self.schema_version) is not int:
            raise WorkspaceFormatError("workspace initialization journal is invalid")
        if self.schema_version != WORKSPACE_SCHEMA_VERSION:
            raise WorkspaceVersionUnsupportedError(
                "workspace version is unsupported; migration and compatibility "
                "are unsupported, so create a new workspace"
            )
        if self.state != "initializing":
            raise WorkspaceFormatError("workspace initialization journal is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceScaffoldEntry:
    """One exact initializer-owned regular file."""

    path: str
    content: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path:
            raise WorkspaceFormatError("workspace scaffold path is invalid")
        if type(self.content) is not bytes:
            raise WorkspaceFormatError("workspace scaffold content is invalid")


def validate_workspace_identifier(value: str, *, label: str = "session ID") -> str:
    """Validate the exact session identifier grammar recorded by schema v2."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise SessionIdError(f"{label} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")
    return value


def encode_workspace_marker(marker: WorkspaceV2Marker | None = None) -> bytes:
    """Encode the one canonical marker as strict UTF-8 with a trailing newline."""

    supplied = marker or WorkspaceV2Marker()
    if type(supplied) is not WorkspaceV2Marker:
        raise WorkspaceFormatError("workspace marker is invalid")
    try:
        if type(supplied.session_topology) is not WorkspaceSessionTopology:
            raise WorkspaceFormatError("workspace topology is invalid")
        value = WorkspaceV2Marker(
            schema_version=supplied.schema_version,
            session_topology=WorkspaceSessionTopology(
                branch_prefix=supplied.session_topology.branch_prefix,
                internal_ref_prefix=supplied.session_topology.internal_ref_prefix,
                worktree_sibling_suffix=(
                    supplied.session_topology.worktree_sibling_suffix
                ),
            ),
        )
    except WorkspaceFormatError:
        raise
    except Exception:
        raise WorkspaceFormatError("workspace marker is invalid") from None
    payload = {
        "schema_version": value.schema_version,
        "session_topology": {
            "branch_prefix": value.session_topology.branch_prefix,
            "internal_ref_prefix": value.session_topology.internal_ref_prefix,
            "worktree_sibling_suffix": value.session_topology.worktree_sibling_suffix,
        },
    }
    return _canonical_json(payload)


def decode_workspace_marker(payload: bytes) -> WorkspaceV2Marker:
    """Decode only the exact canonical schema-v2 marker bytes."""

    value = _decode_exact_json(payload, label="workspace marker")
    if (
        isinstance(value, dict)
        and type(value.get("schema_version")) is int
        and value["schema_version"] != WORKSPACE_SCHEMA_VERSION
    ):
        raise WorkspaceVersionUnsupportedError(
            "workspace version is unsupported; migration and compatibility "
            "are unsupported, so create a new workspace"
        )
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "session_topology",
    }:
        raise WorkspaceFormatError("workspace marker is invalid")
    version = value["schema_version"]
    if type(version) is not int:
        raise WorkspaceFormatError("workspace schema version is invalid")
    if version != WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceVersionUnsupportedError(
            "workspace version is unsupported; migration and compatibility "
            "are unsupported, so create a new workspace"
        )
    topology = value["session_topology"]
    if not isinstance(topology, dict) or set(topology) != {
        "branch_prefix",
        "internal_ref_prefix",
        "worktree_sibling_suffix",
    }:
        raise WorkspaceFormatError("workspace topology is invalid")
    marker = WorkspaceV2Marker(
        schema_version=version,
        session_topology=WorkspaceSessionTopology(
            branch_prefix=topology["branch_prefix"],
            internal_ref_prefix=topology["internal_ref_prefix"],
            worktree_sibling_suffix=topology["worktree_sibling_suffix"],
        ),
    )
    if encode_workspace_marker(marker) != payload:
        raise WorkspaceFormatError("workspace marker is not canonical")
    return marker


def encode_initialization_journal(
    journal: WorkspaceInitializationJournal,
) -> bytes:
    """Encode the exact initialization journal with a trailing newline."""

    if type(journal) is not WorkspaceInitializationJournal:
        raise WorkspaceFormatError("workspace initialization journal is invalid")
    try:
        value = WorkspaceInitializationJournal(
            manifest_digest=journal.manifest_digest,
            schema_version=journal.schema_version,
            state=journal.state,
        )
    except WorkspaceFormatError:
        raise
    except Exception:
        raise WorkspaceFormatError(
            "workspace initialization journal is invalid"
        ) from None
    return _canonical_json(
        {
            "manifest_digest": value.manifest_digest,
            "schema_version": value.schema_version,
            "state": value.state,
        }
    )


def decode_initialization_journal(payload: bytes) -> WorkspaceInitializationJournal:
    """Decode only one exact canonical initialization journal."""

    value = _decode_exact_json(payload, label="workspace initialization journal")
    if not isinstance(value, dict) or set(value) != {
        "manifest_digest",
        "schema_version",
        "state",
    }:
        raise WorkspaceFormatError("workspace initialization journal is invalid")
    journal = WorkspaceInitializationJournal(
        manifest_digest=value["manifest_digest"],
        schema_version=value["schema_version"],
        state=value["state"],
    )
    if encode_initialization_journal(journal) != payload:
        raise WorkspaceFormatError("workspace initialization journal is not canonical")
    return journal


def canonical_initializer_scaffold() -> tuple[WorkspaceScaffoldEntry, ...]:
    """Return the complete exact 22-file initializer manifest selection."""

    entries: dict[str, bytes] = {
        WORKSPACE_MARKER_PATH: encode_workspace_marker(),
        "config/system_defaults.json": _canonical_json(dict(DEFAULT_SYSTEM_CONFIG)),
        "config/agent_configs.json": _canonical_json(
            {agent_id: dict(DEFAULT_AGENT_CONFIGS[agent_id]) for agent_id in ROSTER}
        ),
        "config/persona_templates/default_kim_eun_hee.md": DEFAULT_PERSONA.encode(),
        **{
            AGENT_SPECS[agent_id].system_prompt_file: DEFAULT_AGENT_PROMPTS[
                agent_id
            ].encode()
            for agent_id in ROSTER
        },
        **{path: b"" for path in _GITKEEP_PATHS},
        **{path: b"" for path in CANONICAL_CORE_DOCUMENT_PATHS},
    }
    result = tuple(
        WorkspaceScaffoldEntry(path, entries[path])
        for path in sorted(entries, key=lambda item: item.encode("utf-8"))
    )
    if len(result) != 22:
        raise WorkspaceFormatError("workspace scaffold definition is invalid")
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        raise WorkspaceFormatError("workspace schema value is invalid") from None


def _decode_exact_json(payload: bytes, *, label: str) -> Any:
    if type(payload) is not bytes:
        raise WorkspaceFormatError(f"{label} is invalid")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise WorkspaceFormatError(f"{label} is invalid") from None


__all__: list[str] = []
