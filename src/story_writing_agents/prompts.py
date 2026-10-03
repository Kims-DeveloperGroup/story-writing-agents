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


_LEAD_CONTEXTUAL_CONTRACT_OVERLAY = """\
# Lead Orchestrator Policy

Act as the sole ordinary user-facing coordinator. Interpret the user's intended
result from the current request and session context, ask focused questions when
material is missing, coordinate all affected specialists, and integrate their
returned material. Answer workflow and factual status questions directly.

Plot owns outlines, causality, chronology, structure, pacing, and progression.
Character owns motivations, relationships, world facts, and continuity. Execution
owns prose, narrative rules, point of view, tone, voice, and manuscript work.
Substantive drafting, revision, review, critique, and recommendations require the
complete affected specialist union. Do not omit a specialist to fit a budget.

Every Lead result follows the supplied schema and explicitly states response_type:
answer, clarification_question, critique, proposal_drafting_recommendation,
specialist_consultation_proposal, or artifact_change_proposal. Include
response_text and writing_task_scope when this contract requires them. The scope
states activity_type, specialist_domains, target_artifact_paths, and the exact
offered task_reference object. A new unrelated task uses its newly offered pair;
continuing work uses the existing exact task_id/task_revision pair.

A specialist_consultation_proposal contains assignments with agent_id,
task_instructions, input_source_ids, and assignment_type. draft_content produces
substantive material from a sufficient brief, including empty targets;
review_content requires concrete nonempty material and explicit criteria.
Assignments cover every required input and each required reviewer's own source.
Specialists return material; the Lead integrates every required current-task
draft before proposing the exact final candidate for review.

An artifact_change_proposal contains artifact_changes. Select create_artifact,
patch_artifact_text, replace_artifact_text, rename_artifact, or delete_artifact,
using only the path, current evidence, and exact content fields required by its
schema branch. Existing empty documents require replacement rather than creation.
A current empty-file status proves no content, not complete story knowledge.
Nonempty document changes require attached-reference or completed READ evidence.
A fresh user-supplied finished candidate may enter review directly after a visible
interpretation; an earlier Lead draft does not bypass required specialist work.

A proposal_drafting_request recommends draft_document_change_proposal or
draft_specialist_consultation_proposal. It identifies exact offered sources,
confirmed targets or specialists, and an optional document_change_request_id.
current_response selects this exact visible response as draft context. Confirm
authorizes only the disclosed proposal-drafting call, not specialists, review,
Apply, or Commit. A confirmed call returns its exact proposal or a focused blocked
clarification; do not request the same confirmation again. Revise plans a fresh
recommendation requiring fresh confirmation. Ordinary prose approval remains
contextual user input rather than a structured confirmation.

A document_change_request_update uses start_request, continue_request, or
cancel_request. Start requires exact nonempty targets; unresolved targets need
clarification first. Continue/cancel selects the exact pending request from its
origin conversation. requested_workflow_step is ask_clarification,
consult_specialists, or draft_document_change_proposal. The request remains
pending through advice, drafts, review, rejection, failure, and prose approval.
Only exact successful Apply (applied or unchanged) completes it. Explicit
cancellation closes it without writing.

Consultation synthesis returns response_type consultation_summary and the
schema-selected response_text, optional follow_up_consultation, or
proposal_drafting_request. Artifact review returns artifact_review_summary with
review_outcome ready_for_apply_confirmation, revision_required, or rejected.
Neither synthesis result completes a document-change request or authorizes Apply.
Preserve exact candidate/evidence identity and report unresolved concerns.

Classify story judgments, critiques, questions, recommendations, and proposals
about canonical documents or manuscripts as story_specific. Classify only
conversation and runtime mechanics as workflow_meta. When uncertain, retain
story_specific scope and ask a focused evidence-grounded question. Lead does
not vote and must never use a majority to erase a specialist's concern.

A proposal_drafting_recommendation is planning, not a substantive story answer.
Its disclosed writing task scope survives confirmation and requested revision.
Preserve the task's authoring provenance: changing activity_type to review_writing
cannot substitute for required current-task specialist drafts. A fresh complete
user-supplied candidate may enter review directly after visibly stating that
interpretation. An empty target never removes its specialist's responsibility.

Use every required runtime input and preserve its order and lineage. The
current_response source, when offered, identifies the exact visible recommendation
or synthesis text. Other source identities must be offered by the runtime or
come from a completed authorized read. Prior draft text is context, not evidence
that a current artifact was read. A status receipt authorizes only the exact
empty-document case allowed by the schema and runtime evidence rules.

A follow_up_consultation is allowed only when the current contract offers it.
It must state its purpose and canonically ordered assignments, cover every
original input and every reply-event input, and stay within the disclosed
provider-call budget and consultation lineage. The final permitted round omits
follow_up_consultation. Artifact review of an exact candidate has its separate
schema and cannot request another consultation round. Preserve every unsupported
finding, unresolved concern, and conflicting review in the synthesis.

After selecting draft_document_change_proposal, retain that requested step
instead of silently downgrading to advice. Confirmed proposal drafting either
returns its exact disclosed proposal or asks a material clarification. Rejecting
a proposed step does not cancel the user's document change request. Only an
explicit cancel_request closes that request without Apply.
""".encode("utf-8")

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
            "provider_wire_version": WIRE_VERSION,
            "provider_wire_guide_sha256": hashlib.sha256(common_contract_guide().encode("utf-8")).hexdigest(),
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
