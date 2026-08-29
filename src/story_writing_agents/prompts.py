"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from .agents import AGENT_SPECS, ROSTER
from .config import ConfigurationSnapshot, ConfigManager
from .conversations import ConversationContextWindow
from .models import (
    AgentDefinition,
    AgentRequest,
    BoundCoreDocumentIdentity,
    DebateMessage,
    Phase,
    PromptFileReference,
    STORY_SESSION_CONTEXT_SCHEMA_VERSION,
    StoryFocusedContextMode,
    StorySessionContext,
    focused_story_context,
    select_owner_outcome_window,
)
from .workspace import Workspace
from .writing_artifacts import ArtifactContentSnapshot
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
        provider_route_generations: Mapping[str, int] | None = None,
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
        raw_generations = dict(provider_route_generations or {})
        if set(raw_generations).difference(ROSTER):
            raise ValueError("provider route generations contain an unknown agent")
        if any(
            type(generation) is not int or generation < 0
            for generation in raw_generations.values()
        ):
            raise ValueError("provider route generations must be non-negative integers")
        self.provider_route_generations = {
            agent_id: raw_generations.get(agent_id, 0) for agent_id in ROSTER
        }

    def build_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> AgentDefinition:
        """Resolve current config and least-context files for one binding."""

        return self._build_definition(
            session_id=session_id,
            agent_id=agent_id,
            include_context_documents=True,
        )

    def build_contextual_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> AgentDefinition:
        """Resolve one stable definition for provider-managed context loading.

        Mutable writing artifacts intentionally remain outside the binding and
        its fingerprint.  ``build_definition`` retains the established direct
        provider behavior for callers that still bind core documents eagerly.
        """

        return self._build_definition(
            session_id=session_id,
            agent_id=agent_id,
            include_context_documents=False,
        )

    def _build_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
        include_context_documents: bool,
    ) -> AgentDefinition:
        """Build one exact definition under the selected context strategy."""

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
        context_document_file_tuple: tuple[PromptFileReference, ...] = ()
        if include_context_documents:
            requested_artifacts = tuple(
                f"core_documents/{filename}" for filename in spec.context_documents
            )
            snapshots = self.artifacts.read_current(requested_artifacts)
            context_document_file_tuple = tuple(
                self._artifact_reference(snapshot) for snapshot in snapshots
            )
        fingerprint = self._fingerprint(
            agent_id=agent_id,
            backend=resolved.backend,
            language=resolved.language,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            context_document_files=context_document_file_tuple,
            provider_route_generation=self.provider_route_generations[agent_id],
            provider_managed_context=not include_context_documents,
        )
        return AgentDefinition(
            agent_id=agent_id,
            backend=resolved.backend,
            fingerprint=fingerprint,
            system_prompt_file=system_prompt_file,
            persona_file=persona_file,
            language=resolved.language,
            context_document_files=context_document_file_tuple,
            provider_managed_context=not include_context_documents,
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
        focused_context_mode: StoryFocusedContextMode | None = None,
        focused_conversation_id: str | None = None,
        definition: AgentDefinition | None = None,
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
        mode = focused_context_mode
        if mode is None:
            mode = (
                StoryFocusedContextMode.INCLUDED
                if conversation_context is not None
                else StoryFocusedContextMode.OMITTED
            )
        story_session_context = (
            None
            if definition is None
            else self.build_story_session_context(
                definition=definition,
                session_id=session_id,
                agent_id=agent_id,
                focused_context_mode=mode,
                focused_conversation_id=focused_conversation_id,
                conversation_context=conversation_context,
            )
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
            story_session_context=story_session_context,
        )

    def build_story_session_context(
        self,
        *,
        definition: AgentDefinition,
        session_id: str,
        agent_id: str,
        focused_context_mode: StoryFocusedContextMode,
        focused_conversation_id: str | None = None,
        conversation_context: ConversationContextWindow | None = None,
    ) -> StorySessionContext:
        """Build the bounded continuity envelope for one substantive turn."""

        owner_entries = self.workspace.read_agent_session_state(
            agent_id,
            session_id,
        )
        if type(definition) is not AgentDefinition:
            raise TypeError("definition must be an AgentDefinition")
        if definition.agent_id != agent_id:
            raise ValueError("definition does not match the context agent")
        return StorySessionContext(
            schema_version=STORY_SESSION_CONTEXT_SCHEMA_VERSION,
            session_id=session_id,
            agent_id=agent_id,
            owner_outcomes=select_owner_outcome_window(owner_entries),
            focused_context=focused_story_context(
                focused_context_mode,
                conversation_id=focused_conversation_id,
                context=conversation_context,
            ),
            core_documents=tuple(
                BoundCoreDocumentIdentity(
                    path=reference.path.relative_to(self.workspace.root).as_posix(),
                    sha256=reference.sha256,
                )
                for reference in definition.context_document_files
            ),
        )

    def _reference(self, relative: str) -> PromptFileReference:
        target = self.workspace.path(relative)
        return PromptFileReference.from_path(target)

    def _artifact_reference(
        self,
        snapshot: ArtifactContentSnapshot,
    ) -> PromptFileReference:
        """Bind repository-verified bytes without retaining path authority."""

        if type(snapshot) is not ArtifactContentSnapshot:
            raise TypeError("artifact snapshot has an invalid type")
        return PromptFileReference.from_bytes(
            self.workspace.root / snapshot.path,
            snapshot.content,
        )

    def _fingerprint(
        self,
        *,
        agent_id: str,
        backend: str,
        language: str,
        system_prompt_file: PromptFileReference,
        persona_file: PromptFileReference,
        context_document_files: tuple[PromptFileReference, ...],
        provider_route_generation: int = 0,
        provider_managed_context: bool = False,
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
        if type(provider_route_generation) is not int or provider_route_generation < 0:
            raise ValueError("provider route generation must be non-negative")
        if provider_route_generation:
            value["provider_route_generation"] = provider_route_generation
        if type(provider_managed_context) is not bool:
            raise TypeError("provider_managed_context must be a bool")
        if provider_managed_context:
            if context_document_files:
                raise ValueError(
                    "provider-managed context cannot bind context documents"
                )
            value["provider_managed_context"] = True
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
