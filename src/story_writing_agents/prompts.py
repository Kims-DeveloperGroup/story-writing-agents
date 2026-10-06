"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations
from .provider_wire import WIRE_VERSION, common_contract_guide

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


_LEAD_RUNTIME_BEHAVIOR_POLICY = b"""Runtime Lead authority policy v1
This runtime-owned policy takes precedence over workspace role policies and
retained history. The current invocation schema owns available stage outcomes
and exact fields; it supersedes stale workspace response forms.
Explicit user-selected exploration is provisional, one-turn, and grants no specialist,
artifact, objective, Apply or Commit authority. Only actual artifact READ receipts
support grounded observations; user prose, manifests, SEARCH and old ideas do not.
Adoption is host-bound whole-idea input to a fresh AUTHORING DRAFT/REVISE task with
exact targets, owner drafts, full candidate review and separate Confirm/Apply/Commit.
An offered unresolved-premise consultation is candidate-free provisional analysis.
Use every affected analyst and preserve every actual reply's disposition, reason
and residual cost in whole options. Analysis ends before a distinct USER choice.
Only the exact USER whole-option receipt, or a report-bound USER minor-delegation
allowlist containing the supplied recommendation, selects a premise. Confirm never
delegates that choice. Reject and defer create no authoring authority. The choice
is an immutable input to fresh disclosed DRAFT/REVISE authoring with new Confirm,
current owner drafts and full candidate review. Preserve distinct cost lineages
and actual submitted-call identities; a fresh question never refunds old spend.
Dependent draft conflicts hold drafting proof. A provider cannot change the USER
premise or disguise a direction change as successful drafting or review.
Lead is the sole ordinary user-facing coordinator. Interpret the intended
result visibly, coordinate every affected specialist, and integrate required
current-task drafts before proposing the exact final candidate for review.
Substantive story judgments, questions, critiques, and recommendations about
canonical documents or manuscripts are story work, not workflow mechanics.
A fresh complete user-supplied candidate may enter review directly with a
visible interpretation; an earlier Lead draft does not bypass specialist work.
Preserve exact offered task/revision, source, evidence, and candidate identities.
Status is not a READ receipt. Nonempty artifact changes require current attached
reference or completed authorized READ evidence. Empty-file status proves only
the exact empty target, never complete story knowledge.
Confirm authorizes only the exact disclosed step. Proposal preparation,
specialist execution, candidate review, Apply, and Commit have separate authority
boundaries. Confirmed preparation returns its exact proposal or asks a material
clarification; it does not repeat confirmation or silently downgrade to advice.
Prose approval and retained decisions grant no current execution or write authority.
A document-change request remains pending through advice, drafts, review,
rejection, and failure. Only its exact successful Apply (applied or unchanged)
satisfies it; explicit cancellation closes it without writing.
Lead does not vote. Preserve unsupported findings, unresolved concerns, and
conflicting reviews; never use a majority to erase a specialist's concern.
Continuation must cover all original inputs and reply-event inputs within its
confirmed scope and disclosed budget. Never request a continuation the current
schema does not offer. Artifact review preserves the exact immutable candidate
and authorizes neither Apply nor Commit.
"""

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

    def build_contextual_definition_with_policy_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
        policy_snapshot: tuple[tuple[str, bytes], ...],
    ) -> AgentDefinition:
        """Build contextual definition from exact descriptor-captured policy bytes."""

        expected_paths = behavior_policy_files(agent_id)
        if (
            type(policy_snapshot) is not tuple
            or len(policy_snapshot) != len(expected_paths)
            or any(
                type(component) is not tuple
                or len(component) != 2
                or type(component[0]) is not str
                or type(component[1]) is not bytes
                for component in policy_snapshot
            )
            or tuple(component[0] for component in policy_snapshot) != expected_paths
        ):
            raise WorkspaceFormatError(
                "behavior policy snapshot does not match canonical composition"
            )
        policy_components = tuple(
            PromptFileReference.from_bytes(
                self.workspace.root / path,
                content,
            )
            for path, content in policy_snapshot
        )
        return self._build_definition(
            session_id=session_id,
            agent_id=agent_id,
            include_context_documents=False,
            behavior_policy_components=policy_components,
        )

    def _build_definition(
        self,
        *,
        session_id: str,
        agent_id: str,
        include_context_documents: bool,
        behavior_policy_components: tuple[PromptFileReference, ...] | None = None,
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
        policy_components = (
            tuple(self._reference(path) for path in behavior_policy_files(agent_id))
            if behavior_policy_components is None
            else behavior_policy_components
        )
        if any(
            not component.content or b"\x00" in component.content
            for component in policy_components
        ):
            raise WorkspaceFormatError(
                "behavior policy component must be nonempty and NUL-free"
            )
        runtime_behavior_policy = _RUNTIME_BEHAVIOR_POLICY
        if agent_id == LEAD_ORCHESTRATOR:
            runtime_behavior_policy += b"\n\n" + _LEAD_RUNTIME_BEHAVIOR_POLICY
        system_prompt_file = PromptFileReference.from_bytes(
            policy_components[-1].path,
            b"\n\n".join(component.content for component in policy_components)
            + b"\n\n" + runtime_behavior_policy,
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
            runtime_behavior_policy=runtime_behavior_policy,
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
            runtime_behavior_policy=runtime_behavior_policy,
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
        runtime_behavior_policy: bytes,
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
            "provider_wire_version": WIRE_VERSION,
            "provider_wire_guide_sha256": hashlib.sha256(common_contract_guide().encode("utf-8")).hexdigest(),
            "runtime_behavior_policy_sha256": hashlib.sha256(
                runtime_behavior_policy
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
