"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Mapping

from .agents import AGENT_SPECS
from .config import ConfigurationSnapshot, ConfigManager
from .conversations import ConversationContextWindow
from .errors import WorkspaceFormatError
from .models import (
    AgentDefinition,
    AgentRequest,
    DebateMessage,
    Phase,
    PromptFileReference,
)
from .workspace import Workspace
from .writing_artifacts.repository import WritingArtifactRepository


class PromptAssembler:
    """Build content-addressed definitions and prompt-free turn requests."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        artifact_repository: WritingArtifactRepository | None = None,
        backend_overrides: Mapping[str, str] | None = None,
        configuration_snapshot: ConfigurationSnapshot | None = None,
    ) -> None:
        self.workspace = workspace
        self.artifacts = (
            artifact_repository
            if artifact_repository is not None
            else WritingArtifactRepository(workspace.root)
        )
        self.config = ConfigManager(workspace)
        self.backend_overrides = dict(backend_overrides or {})
        if configuration_snapshot is not None and type(
            configuration_snapshot
        ) is not ConfigurationSnapshot:
            raise TypeError(
                "configuration_snapshot must be a ConfigurationSnapshot or None"
            )
        self.configuration_snapshot = configuration_snapshot

    def build_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> AgentDefinition:
        """Resolve current config and least-context files for one binding."""

        runtime_overrides = self.workspace.load_session_overrides(session_id)
        resolved = (
            self.config.resolve(
                agent_id,
                runtime_overrides,
                self.backend_overrides,
            )
            if self.configuration_snapshot is None
            else self.config.resolve_from_snapshot(
                self.configuration_snapshot,
                agent_id,
                runtime_overrides,
                self.backend_overrides,
            )
        )
        spec = AGENT_SPECS[agent_id]
        system_prompt_file = self._reference(spec.system_prompt_file)
        persona_file = self._reference(
            self.workspace.persona_path(resolved.persona).relative_to(
                self.workspace.root
            ).as_posix()
        )
        artifact_catalog = self.artifacts.catalog()
        context_document_files: list[PromptFileReference] = []
        for filename in spec.context_documents:
            artifact_path = f"core_documents/{filename}"
            entry = artifact_catalog.entries.get(artifact_path)
            if entry is None:
                continue
            reference = self._reference(artifact_path)
            if not hmac.compare_digest(reference.sha256, entry.sha256):
                raise WorkspaceFormatError(
                    "core document changed after artifact provenance verification"
                )
            context_document_files.append(reference)
        context_document_file_tuple = tuple(context_document_files)
        fingerprint = self._fingerprint(
            agent_id=agent_id,
            backend=resolved.backend,
            language=resolved.language,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            context_document_files=context_document_file_tuple,
        )
        return AgentDefinition(
            agent_id=agent_id,
            backend=resolved.backend,
            fingerprint=fingerprint,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            language=resolved.language,
            context_document_files=context_document_file_tuple,
        )

    def build_request(
        self,
        *,
        session_id: str,
        debate_id: str,
        agent_id: str,
        phase: Phase,
        round_number: int,
        revision_id: str,
        task: str,
        user_payload: str,
        candidate_payload: str | None = None,
        messages: tuple[DebateMessage, ...] = (),
        conversation_context: ConversationContextWindow | None = None,
    ) -> AgentRequest:
        """Build a prompt-free turn with optional immutable conversation context.

        Agent history is read from the workspace and returned in a validated
        ``AgentRequest``; no state is written. Workspace or context validation
        failures propagate before any provider invocation.
        """

        agent_state = self.workspace.read_agent_state(
            agent_id,
            session_id,
            debate_id,
        )
        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=agent_id,
            phase=phase,
            round_number=round_number,
            revision_id=revision_id,
            task=(
                f"{task} Return only the requested structured result. "
                "Do not reveal or provide hidden chain-of-thought."
            ),
            user_payload=user_payload,
            candidate_payload=candidate_payload,
            messages=messages,
            agent_state=agent_state,
            conversation_context=conversation_context,
        )

    def _reference(self, relative: str) -> PromptFileReference:
        target = self.workspace.path(relative)
        reference = PromptFileReference.from_path(target)
        # Re-check the logical category after reading in case an untrusted
        # workspace was changed concurrently.
        self.workspace.path(relative)
        return reference

    def _fingerprint(
        self,
        *,
        agent_id: str,
        backend: str,
        language: str,
        system_prompt_file: PromptFileReference,
        persona_file: PromptFileReference,
        context_document_files: tuple[PromptFileReference, ...],
    ) -> str:
        components = (
            ("system_prompt", system_prompt_file),
            ("persona", persona_file),
            *(
                ("context_document", reference)
                for reference in context_document_files
            ),
        )
        value = {
            "agent_id": agent_id,
            "backend": backend,
            "language": language,
            "components": [
                {
                    "kind": kind,
                    "identity": reference.path.relative_to(
                        self.workspace.root
                    ).as_posix(),
                    "sha256": reference.sha256,
                }
                for kind, reference in components
            ],
        }
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
