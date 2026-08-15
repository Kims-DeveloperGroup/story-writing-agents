"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from .agents import AGENT_SPECS
from .config import ConfigManager
from .conversations import ConversationContextWindow
from .models import (
    AgentDefinition,
    AgentRequest,
    DebateMessage,
    Phase,
    PromptFileReference,
)
from .workspace import Workspace


class PromptAssembler:
    """Build content-addressed definitions and prompt-free turn requests."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        backend_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = ConfigManager(workspace)
        self.backend_overrides = dict(backend_overrides or {})

    def build_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> AgentDefinition:
        """Resolve current config and least-context files for one binding."""

        runtime_overrides = self.workspace.load_session_overrides(session_id)
        resolved = self.config.resolve(
            agent_id,
            runtime_overrides,
            self.backend_overrides,
        )
        spec = AGENT_SPECS[agent_id]
        system_prompt_file = self._reference(spec.system_prompt_file)
        persona_file = self._reference(
            self.workspace.persona_path(resolved.persona).relative_to(
                self.workspace.root
            ).as_posix()
        )
        context_document_files = tuple(
            self._reference(f"core_documents/{filename}")
            for filename in spec.context_documents
        )
        fingerprint = self._fingerprint(
            agent_id=agent_id,
            backend=resolved.backend,
            language=resolved.language,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            context_document_files=context_document_files,
        )
        return AgentDefinition(
            agent_id=agent_id,
            backend=resolved.backend,
            fingerprint=fingerprint,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            language=resolved.language,
            context_document_files=context_document_files,
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
