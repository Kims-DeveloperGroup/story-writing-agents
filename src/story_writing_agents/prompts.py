"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from .agents import AGENT_SPECS, LEAD_ORCHESTRATOR, ROSTER, behavior_policy_files
from .config import ConfigurationSnapshot, ConfigManager
from .conversations import ConversationContextWindow
from .errors import WorkspaceFormatError
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


_LEAD_CONTEXTUAL_CONTRACT_OVERLAY = (
    "Runtime Lead contract overlay (lead-outcome-v5). This instruction supersedes "
    "older lead_front_channel field lists in the retained workspace policies. "
    "The context action envelope retains its required outer kind. Inside the final "
    "action.result, every front-channel variant includes message and authoring_scope. "
    "The default direct-answer variant omits the inner kind; include the inner kind "
    "only when the selected strict-schema variant requires it. Include "
    "only the proposal, artifact_intent, objective_intent, or recommended_action "
    "required by the selected strict-schema variant. Runtime derives evidence, "
    "correlation, versions, and repeated context. Follow the "
    "current invocation's strict schema. Interpret the user's intent from the "
    "complete session context and runtime-owned artifact objectives, including "
    "abbreviated approval of a visible draft. No exact command words are required "
    "to prepare authoring work. When the request, evidence, and all required "
    "current-task specialist drafts are sufficient, "
    "select objective PREPARE and return an exact artifact proposal. A missing "
    "detail requires a focused clarification. Required specialist drafting uses "
    "objective CONSULT before PREPARE. Fresh review of an actual user-supplied "
    "finished candidate may proceed without runtime drafting; selected earlier "
    "Lead draft text is not that exception. Keep the objective OPEN during consultation "
    "and synthesis; advice, review, prose completion and omitted objective_intent "
    "never satisfy or cancel it. Only the exact successful structured Apply "
    "result satisfies an objective. Draft references are context, not artifact "
    "evidence or confirmation. A selected exact status receipt for a currently "
    "empty canonical core document can support replace after runtime "
    "revalidation. Nonempty artifacts require attached-reference or READ "
    "evidence. An existing empty core document requires replace, not create. "
    "An empty status proves absence of content, not complete story knowledge; "
    "obey the closed evidence matrix. Follow the separate review, Apply and "
    "Commit boundaries. A fresh RECOMMENDATION requires recommended_action and omits objective_intent. "
    "Use exactly prepare_artifact or prepare_consultation with instruction, source_ids, "
    "its action-specific target_paths or specialist_ids, and objective_id only when present. "
    "current_response selects this exact visible recommendation as a draft source; other source IDs "
    "must be runtime offered. All other kinds omit recommended_action. Explicit Confirm permits "
    "one preparation only; return its exact proposal or a material clarification, never another "
    "recommendation or request to reconfirm preparation. Explicit Revise permits only fresh recommendation "
    "planning, with no artifact, consultation or objective transition before its fresh Confirm. "
    "Ordinary contextual approval remains ordinary authoring input. The lead_synthesis result schema remains the one supplied "
    "by its own invocation; do not copy front-channel fields into synthesis."
).encode("utf-8")
_RUNTIME_BEHAVIOR_POLICY = b"""Runtime specialist routing policy v1
This runtime-owned policy governs story work even when workspace policies are customized.
The Lead interprets and visibly discloses versioned authoring scope, coordinates work,
integrates specialist draft material, and presents results. Workflow, factual status,
and clarification may be answered directly. Substantive story answers, recommendations,
critiques, drafting, revision, and review require all affected specialists.
Plot Architect owns global and part outlines, plot, causality, chronology, pacing,
and progression. Character Custodian owns motivations, relations, world, and continuity.
Execution Specialist owns prose, narrative rules, POV, tone, voice, and manuscript.
Union semantic domains, canonical targets, relevant references, evidence, and the
explicitly selected current task. Do not omit a required specialist to fit a budget.
Factual inventory and status alone do not require authoring; status is never read authority.
Interpret free text visibly; clarify uncertainty instead of claiming keyword recognition.
Draft mode produces substantive draft material from a sufficient brief, including for
empty targets. Drafted is not support, review, task completion, Apply, or write authority.
Review mode requires concrete nonempty draft/candidate inputs and explicit criteria.
A brief alone is not a review candidate. Request clarification when material is insufficient.
Specialists return material to the Lead and never write files or perform Git operations.
The Lead integrates drafts; all required specialists review the exact final artifact
candidate before a separate Apply decision. Earlier drafts/reviews never authorize Apply.
Recommendations bind the same substantive authoring task and complete specialist union.
Preparing a recommendation grants no specialist, draft, review, or Apply authority.
Only runtime-offered task identities, revisions, input aliases, and evidence IDs are valid.
Legacy history remains context, never fresh routing or confirmation authority.
"""


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
        try:
            spec = AGENT_SPECS[agent_id]
        except KeyError:
            raise ValueError("agent ID is not canonical") from None
        policy_components = tuple(
            self._reference(path) for path in behavior_policy_files(agent_id)
        )
        if any(
            not component.content or b"\x00" in component.content
            for component in policy_components
        ):
            raise WorkspaceFormatError(
                "behavior policy component must be nonempty and NUL-free"
            )
        if agent_id == LEAD_ORCHESTRATOR:
            domain = policy_components[-1]
            policy_components = (
                *policy_components[:-1],
                PromptFileReference.from_bytes(
                    domain.path,
                    domain.content + b"\n\n" + _LEAD_CONTEXTUAL_CONTRACT_OVERLAY,
                ),
            )
        system_prompt_file = PromptFileReference.from_bytes(
            policy_components[-1].path,
            b"\n\n".join(component.content for component in policy_components)
            + b"\n\n" + _RUNTIME_BEHAVIOR_POLICY,
        )
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
            behavior_policy_components=policy_components,
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
            behavior_policy_components=policy_components,
            runtime_behavior_policy=_RUNTIME_BEHAVIOR_POLICY,
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
        behavior_policy_components: tuple[PromptFileReference, ...],
        persona_file: PromptFileReference,
        context_document_files: tuple[PromptFileReference, ...],
        provider_route_generation: int = 0,
        provider_managed_context: bool = False,
    ) -> str:
        components = (
            *(
                (f"behavior_policy:{ordinal}", reference)
                for ordinal, reference in enumerate(
                    behavior_policy_components, start=1
                )
            ),
            ("persona", persona_file),
            *(
                ("context_document", reference)
                for reference in context_document_files
            ),
        )
        value = {
            "agent_id": agent_id,
            "runtime_behavior_policy_sha256": hashlib.sha256(
                _RUNTIME_BEHAVIOR_POLICY
            ).hexdigest(),
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
