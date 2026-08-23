"""Provider-free end-to-end smoke workflow used only by the CLI."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .agents import (
    AGENT_SPECS,
    CHARACTER_CUSTODIAN,
    CORE_DOCUMENTS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
)
from .conversation_retrieval import (
    ConversationRetrievalAction,
    ConversationRetrievalActionHandler,
    ConversationRetrievalActionKind,
    ConversationRetrievalAnswerAction,
    ConversationRetrievalObservation,
    ConversationRetrievalProgressEvent,
    ConversationRetrievalProgressPhase,
    ConversationRetrievalReadAction,
    ConversationRetrievalReadObservation,
    ConversationRetrievalReply,
    ConversationRetrievalRequest,
    ConversationRetrievalResult,
    ConversationRetrievalSearchAction,
    ConversationRetrievalSearchObservation,
)
from .conversation_search import (
    ConversationMemoryService,
    ConversationSearchHit,
    ConversationSearchMatchKind,
    ConversationSearchQuery,
    ConversationSearchScope,
)
from .debug_log import (
    DebugComponent,
    DebugOperation,
    DebugOutcome,
    DebugPhase,
    WorkspaceDebugLog,
)
from .conversations import (
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventKind,
    ConversationMetadata,
    ConversationStatus,
)
from .models import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    ConversationReply,
    ConversationRequest,
    ConversationResult,
    DebateContextMode,
    DebateContextRequest,
    DebateResult,
    DebateStatus,
    Phase,
)
from .orchestrator import StoryRuntime
from .session_lifecycle import (
    SessionAccessState,
    SessionDurableState,
    SessionLifecycleState,
)
from .workspace import (
    WORKSPACE_DIRECTORIES,
    FilesystemConversationCatalog,
    FilesystemConversationEventStore,
    Workspace,
    initialize_workspace,
)
from .writing_artifacts.repository import WritingArtifactRepository


_PROMPT = "Run the deterministic provider-free smoke workflow."
_REVISION_ZERO = "Synthetic smoke candidate revision zero."
_PLOT_CRITIQUE = "Synthetic plot conflict requires one revision."
_PLOT_PROPOSAL = "Resolve the synthetic plot conflict in revision one."
_REVISION_ONE = "Synthetic smoke candidate revision one."
_SOURCE_SUBJECT = "Smoke luminous compass continuity"
_RETRIEVAL_SUBJECT = "Smoke bounded retrieval destination"
_LEAD_QUESTION = "Map the luminous compass for the continuity check."
_LEAD_REPLY = "The luminous compass points toward the western archive."
_SPECIALIST_QUESTION = "Audit the luminous compass archive detail."
_SPECIALIST_REPLY = "The western archive detail is internally consistent."
_RETRIEVAL_QUESTION = "Retrieve the luminous compass source with citations."
_RETRIEVAL_REPLY = "The cited source confirms the requested compass location."
_VISIBLE_SEARCH_TEXT = "western archive"
_NOTE = "No LLM or external provider was called."
_CHECK_NAMES = (
    "workspace_initialized",
    "directive_persisted",
    "conversation_catalog_persisted",
    "conversation_routing",
    "conversation_search",
    "conversation_continuity",
    "conversation_retrieval",
    "consensus_context",
    "revision_workflow",
    "consensus_persisted",
    "manuscript_committed",
    "core_documents_unchanged",
)


class _SmokeInvariantError(Exception):
    """Name one deterministic smoke invariant without hiding other failures."""

    def __init__(self, failed_check: str) -> None:
        """Store one check name on a new exception without I/O or validation."""

        super().__init__(failed_check)
        self.failed_check = failed_check


@dataclass(frozen=True, slots=True)
class _SmokeSearchEvidence:
    """Retain the exact subject and transcript hits for continuity comparison."""

    subject_hit: ConversationSearchHit
    transcript_hit: ConversationSearchHit


@dataclass(frozen=True, slots=True)
class _SmokeWorkflowResult:
    """Return consensus plus collaborators needed for synchronous verification."""

    debate_result: DebateResult
    backend: _SmokeBackend
    context_snapshot: ConversationContextWindow


class _SmokeBackend:
    """Strict provider-free backend for conversation, retrieval, and consensus."""

    def __init__(self, session_id: str, debate_id: str) -> None:
        """Given run IDs, initialize revision-stage checks and empty observations."""

        self.session_id = session_id
        self.debate_id = debate_id
        self._binding_failed_check = "revision_workflow"
        self.definitions: list[AgentDefinition] = []
        self.requests: list[AgentRequest] = []
        self.conversation_requests: list[ConversationRequest] = []
        self.retrieval_requests: list[ConversationRetrievalRequest] = []
        self.retrieval_actions: list[ConversationRetrievalAction] = []
        self.retrieval_outcomes: list[
            ConversationRetrievalObservation | ConversationRetrievalReply
        ] = []
        self.expected_consensus_context: ConversationContextWindow | None = None
        self.audit_active = 0
        self.maximum_audit_active = 0
        self._access_held = False

    def validate_provider_configuration(self, session_id: str) -> bool:
        """Validate the fixed smoke session without provider or storage access."""

        self._expect(session_id == self.session_id)
        return True

    def get_session_lifecycle(self, session_id: str) -> SessionLifecycleState:
        """Return deterministic ready facts for the in-memory smoke session."""

        self._expect(session_id == self.session_id)
        return self._lifecycle_state()

    def acquire_session_access(self, session_id: str) -> SessionLifecycleState:
        """Mark deterministic in-memory smoke access held and return its facts."""

        self._expect(session_id == self.session_id)
        self._access_held = True
        return self._lifecycle_state()

    def release_session_access(self, session_id: str) -> SessionLifecycleState:
        """Clear deterministic in-memory smoke access and return its facts."""

        self._expect(session_id == self.session_id)
        self._access_held = False
        return self._lifecycle_state()

    def _lifecycle_state(self) -> SessionLifecycleState:
        """Build one exact lifecycle value without external side effects."""

        return SessionLifecycleState(
            session_id=self.session_id,
            durable_state=SessionDurableState.READY,
            access_state=(
                SessionAccessState.HELD_HERE
                if self._access_held
                else SessionAccessState.CLAIMABLE
            ),
            owner_hostname="provider-free-smoke" if self._access_held else None,
            owner_pid=1 if self._access_held else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    async def bind(self, definition: AgentDefinition) -> _SmokeBoundAgent:
        """Read and record one definition, returning a local validated binding.

        Prompt-file read failures propagate; an invalid definition raises the
        active workflow invariant, and no provider or durable state is touched.
        """

        for reference in definition.file_references():
            reference.read_text()
        self._expect(
            definition.agent_id in ROSTER
            and definition.language == "EN"
            and definition.system_prompt_file.path.name == "AGENTS.md"
            and definition.system_prompt_file.path.parent.name
            == definition.agent_id,
            self._binding_failed_check,
        )
        self.definitions.append(definition)
        return _SmokeBoundAgent(self, definition)

    async def _invoke(self, request: AgentRequest) -> AgentReply:
        """Record a debate request and return its exact deterministic reply.

        The in-memory trace and audit counters may change; invalid requests raise
        a named invariant and no external provider or direct persistence is used.
        """

        self.requests.append(request)
        self._validate_common(request)

        if request.phase is Phase.DRAFT:
            self._expect(
                request.agent_id == EXECUTION_SPECIALIST
                and request.revision_id == "r0"
                and request.candidate_payload is None
                and self._message_kinds(request) == ("prompt",)
                and not request.agent_state
            )
            return AgentReply.candidate("r0", _REVISION_ZERO)

        if request.phase is Phase.AUDIT:
            reply = self._audit_reply(request)
            self.audit_active += 1
            self.maximum_audit_active = max(
                self.maximum_audit_active,
                self.audit_active,
            )
            try:
                await asyncio.sleep(0)
                return reply
            finally:
                self.audit_active -= 1

        if request.phase is Phase.PROPOSE_REVISION:
            self._expect(
                request.agent_id == PLOT_ARCHITECT
                and request.revision_id == "r0"
                and request.candidate_payload == _REVISION_ZERO
                and self._message_kinds(request)
                == ("prompt", "candidate", "critique", "approval", "approval")
                and self._state_kinds(request) == ("critique",)
            )
            return AgentReply.candidate("r0", _PLOT_PROPOSAL)

        if request.phase is Phase.SYNTHESIZE:
            self._expect(
                request.agent_id == LEAD_ORCHESTRATOR
                and request.revision_id == "r1"
                and request.candidate_payload == _REVISION_ZERO
                and self._message_kinds(request)
                == (
                    "prompt",
                    "candidate",
                    "critique",
                    "approval",
                    "approval",
                    "proposal",
                )
                and any(
                    message.content == _PLOT_CRITIQUE
                    for message in request.messages
                )
                and any(
                    message.content == _PLOT_PROPOSAL
                    for message in request.messages
                )
                and not request.agent_state
            )
            return AgentReply.candidate("r1", _REVISION_ONE)

        if request.phase is Phase.FINALIZE:
            self._expect(
                request.agent_id == LEAD_ORCHESTRATOR
                and request.revision_id == "r1"
                and request.candidate_payload == _REVISION_ONE
                and self._message_kinds(request)
                == (
                    "prompt",
                    "candidate",
                    "critique",
                    "approval",
                    "approval",
                    "proposal",
                    "candidate",
                    "approval",
                    "approval",
                    "approval",
                )
                and self._state_kinds(request) == ("candidate_created",)
            )
            return AgentReply.finalization("r1")

        raise _SmokeInvariantError("revision_workflow")

    def _audit_reply(self, request: AgentRequest) -> AgentReply:
        """Return the scripted verdict for one audit without mutation or I/O.

        A request outside the accepted revision sequence raises the revision
        invariant.
        """

        self._expect(request.agent_id in SPECIALIST_IDS)
        if request.revision_id == "r0":
            expected_state = {
                PLOT_ARCHITECT: (),
                CHARACTER_CUSTODIAN: (),
                EXECUTION_SPECIALIST: ("candidate_created",),
            }
            self._expect(
                request.candidate_payload == _REVISION_ZERO
                and self._message_kinds(request) == ("prompt", "candidate")
                and self._state_kinds(request)
                == expected_state[request.agent_id]
            )
            if request.agent_id == PLOT_ARCHITECT:
                return AgentReply.conflict("r0", _PLOT_CRITIQUE)
            return AgentReply.approval("r0")

        expected_state = {
            PLOT_ARCHITECT: ("critique", "proposal"),
            CHARACTER_CUSTODIAN: ("approval",),
            EXECUTION_SPECIALIST: (
                "candidate_created",
                "approval",
            ),
        }
        self._expect(
            request.revision_id == "r1"
            and request.candidate_payload == _REVISION_ONE
            and self._message_kinds(request)
            == (
                "prompt",
                "candidate",
                "critique",
                "approval",
                "approval",
                "proposal",
                "candidate",
            )
            and self._state_kinds(request) == expected_state[request.agent_id]
        )
        return AgentReply.approval("r1")

    def _validate_common(self, request: AgentRequest) -> None:
        """Validate one debate request's identity and context without mutation.

        Success returns ``None``; a context or revision mismatch raises its named
        invariant and performs no I/O.
        """

        self._expect(
            self.expected_consensus_context is not None
            and request.conversation_context == self.expected_consensus_context,
            "consensus_context",
        )
        self._expect(
            request.session_id == self.session_id
            and request.debate_id == self.debate_id
            and request.user_payload == _PROMPT
            and all(
                entry.agent_id == request.agent_id
                and entry.session_id == self.session_id
                and entry.debate_id == self.debate_id
                for entry in request.agent_state
            )
        )

    @staticmethod
    def _message_kinds(request: AgentRequest) -> tuple[str, ...]:
        """Return one request's ordered message kinds without mutation or failure."""

        return tuple(message.kind for message in request.messages)

    @staticmethod
    def _state_kinds(request: AgentRequest) -> tuple[str, ...]:
        """Return one request's ordered state kinds without mutation or failure."""

        return tuple(entry.kind for entry in request.agent_state)

    @staticmethod
    def _expect(
        condition: bool,
        failed_check: str = "revision_workflow",
    ) -> None:
        """Given a condition, return without I/O or raise its named invariant."""

        if not condition:
            raise _SmokeInvariantError(failed_check)


class _SmokeBoundAgent:
    """Bind one canonical definition to the deterministic smoke backend."""

    def __init__(
        self,
        backend: _SmokeBackend,
        definition: AgentDefinition,
    ) -> None:
        """Retain supplied collaborators on a new binding without I/O or failure."""

        self.backend = backend
        self.definition = definition

    async def invoke(self, request: AgentRequest) -> AgentReply:
        """Route a debate request and return its reply without direct persistence.

        The backend records the request; a wrong binding or invalid sequence raises
        a named invariant, and backend read failures propagate.
        """

        self.backend._expect(request.agent_id == self.definition.agent_id)
        return await self.backend._invoke(request)

    async def converse(self, request: ConversationRequest) -> ConversationReply:
        """Record one direct request and return its correlated scripted reply.

        Only backend memory changes here; wrong routing, text, or context raises the
        conversation invariant, while the runtime owns transcript durability.
        """

        self.backend._expect(
            request.agent_id == self.definition.agent_id,
            "conversation_routing",
        )
        self.backend.conversation_requests.append(request)
        if request.agent_id == LEAD_ORCHESTRATOR:
            self.backend._expect(
                request.content == _LEAD_QUESTION
                and request.context.events == ()
                and request.context.total_complete_turns == 0,
                "conversation_routing",
            )
            content = _LEAD_REPLY
        elif request.agent_id == PLOT_ARCHITECT:
            self.backend._expect(
                request.content == _SPECIALIST_QUESTION,
                "conversation_routing",
            )
            content = _SPECIALIST_REPLY
        else:
            raise _SmokeInvariantError("conversation_routing")
        return ConversationReply(
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            content=content,
        )

    async def converse_with_retrieval(
        self,
        request: ConversationRetrievalRequest,
        handler: ConversationRetrievalActionHandler,
    ) -> ConversationRetrievalReply:
        """Submit exact search/read/answer actions and return the authorized reply.

        Backend traces and runtime-owned memory reads change; invalid observations
        raise the retrieval invariant, while handler/storage failures propagate.
        """

        self.backend._expect(
            request.agent_id == self.definition.agent_id
            and request.agent_id == PLOT_ARCHITECT
            and request.content == _RETRIEVAL_QUESTION,
            "conversation_retrieval",
        )
        self.backend.retrieval_requests.append(request)

        search_action = ConversationRetrievalSearchAction(
            kind=ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=1,
            query=_SOURCE_SUBJECT,
        )
        search_outcome = handler.handle_conversation_retrieval_action(search_action)
        self.backend.retrieval_actions.append(search_action)
        self.backend.retrieval_outcomes.append(search_outcome)
        self.backend._expect(
            type(search_outcome) is ConversationRetrievalSearchObservation
            and len(search_outcome.hits) == 1,
            "conversation_retrieval",
        )

        read_action = ConversationRetrievalReadAction(
            kind=ConversationRetrievalActionKind.READ_TRANSCRIPT,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=2,
            citation_id="s1h1",
            start_sequence=1,
            end_sequence=2,
        )
        read_outcome = handler.handle_conversation_retrieval_action(read_action)
        self.backend.retrieval_actions.append(read_action)
        self.backend.retrieval_outcomes.append(read_outcome)
        self.backend._expect(
            type(read_outcome) is ConversationRetrievalReadObservation
            and len(read_outcome.events) == 2,
            "conversation_retrieval",
        )

        answer_action = ConversationRetrievalAnswerAction(
            kind=ConversationRetrievalActionKind.ANSWER,
            session_id=request.session_id,
            conversation_id=request.conversation_id,
            turn_id=request.turn_id,
            action_index=3,
            content=_RETRIEVAL_REPLY,
            citation_ids=tuple(
                event.citation_id for event in read_outcome.events
            ),
        )
        answer_outcome = handler.handle_conversation_retrieval_action(answer_action)
        self.backend.retrieval_actions.append(answer_action)
        self.backend.retrieval_outcomes.append(answer_outcome)
        self.backend._expect(
            type(answer_outcome) is ConversationRetrievalReply,
            "conversation_retrieval",
        )
        return answer_outcome


def _trace_smoke(
    debug_log: WorkspaceDebugLog | None,
    operation: DebugOperation,
    phase: DebugPhase,
    *,
    session_id: str | None = None,
    debate_id: str | None = None,
    outcome: DebugOutcome | None = None,
) -> None:
    """Record a smoke trace without allowing diagnostics to alter behavior."""

    if debug_log is None:
        return
    try:
        debug_log.trace(
            component=DebugComponent.SMOKE,
            operation=operation,
            phase=phase,
            session_id=session_id,
            debate_id=debate_id,
            outcome=outcome,
        )
    except BaseException:
        pass


def _create_smoke_debug_log(workspace: Workspace) -> WorkspaceDebugLog | None:
    """Create one optional smoke logger without changing smoke behavior."""

    try:
        return WorkspaceDebugLog(workspace)
    except BaseException:
        return None


def run_smoke(workspace: Workspace | None = None) -> tuple[dict[str, Any], int]:
    """Run one provider-free smoke and return its report plus exit code.

    An omitted workspace creates and removes a temporary workspace; a supplied
    initialized workspace is borrowed and retained. Named invariant failures
    return code one, while workspace, storage, protocol, and setup failures
    propagate to the CLI.
    """

    if workspace is None:
        with tempfile.TemporaryDirectory(
            prefix="story-writing-agents-smoke-"
        ) as temporary:
            temporary_workspace = initialize_workspace(temporary)
            return _run_provider_free_smoke_in_workspace(
                temporary_workspace,
                mode="temporary",
                retained=False,
            )
    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace or None")
    return _run_provider_free_smoke_in_workspace(
        workspace,
        mode="persistent",
        retained=True,
    )


def _run_provider_free_smoke_in_workspace(
    workspace: Workspace,
    *,
    mode: str,
    retained: bool,
) -> tuple[dict[str, Any], int]:
    """Use one initialized workspace to run an event loop and build a report.

    The supplied mode metadata is reported unchanged. The workflow mutates only
    normal smoke artifacts, returns code zero or one for named invariants, and
    lets all non-invariant failures propagate.
    """

    debug_log = _create_smoke_debug_log(workspace)
    _trace_smoke(
        debug_log,
        DebugOperation.WORKSPACE_INITIALIZATION,
        DebugPhase.SUCCEEDED,
        outcome=DebugOutcome.READY,
    )
    token = uuid.uuid4().hex
    session_id = f"smoke_session_{token}"
    debate_id = f"smoke_debate_{token}"
    output_path = f"smoke-{token}/revision-01.md"
    manuscript_path = f"manuscript/{output_path}"
    completed_checks: list[str] = []
    result: DebateResult | None = None
    _trace_smoke(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.STARTED,
        session_id=session_id,
        debate_id=debate_id,
    )

    try:
        _verify_workspace_initialized(workspace)
        completed_checks.append(_CHECK_NAMES[0])

        StoryRuntime(workspace).apply_directive(
            session_id,
            "[SYS: LANG=EN]",
        )
        fresh_runtime = StoryRuntime(workspace)
        _verify_directive(fresh_runtime, session_id)
        completed_checks.append(_CHECK_NAMES[1])

        core_before = _read_core_document_bytes(workspace)
        manuscripts_before = _snapshot_manuscript_files(workspace)
        workflow = asyncio.run(
            _exercise_provider_free_conversation_and_consensus(
                workspace,
                session_id=session_id,
                debate_id=debate_id,
                output_path=output_path,
                core_before=core_before,
                manuscripts_before=manuscripts_before,
                completed_checks=completed_checks,
            )
        )
        result = workflow.debate_result

        _verify_consensus_context(
            workspace,
            workflow.backend,
            workflow.context_snapshot,
            result,
        )
        completed_checks.append(_CHECK_NAMES[7])

        _verify_revision_workflow(workspace, workflow.backend, result)
        completed_checks.append(_CHECK_NAMES[8])

        _verify_consensus(workspace, result)
        completed_checks.append(_CHECK_NAMES[9])

        _require(
            WritingArtifactRepository(workspace.root).read_text(manuscript_path)
            == _REVISION_ONE
            and _snapshot_manuscript_files(workspace)
            == tuple(
                sorted(
                    (
                        *manuscripts_before,
                        (
                            manuscript_path,
                            _REVISION_ONE.encode("utf-8"),
                        ),
                    )
                )
            ),
            _CHECK_NAMES[10],
        )
        completed_checks.append(_CHECK_NAMES[10])

        _require(
            _read_core_document_bytes(workspace) == core_before,
            _CHECK_NAMES[11],
        )
        completed_checks.append(_CHECK_NAMES[11])
    except _SmokeInvariantError as exc:
        if debug_log is not None:
            try:
                debug_log.error(
                    DebugComponent.SMOKE,
                    DebugOperation.CLI_COMMAND,
                    exc,
                    session_id=session_id,
                    debate_id=debate_id,
                    outcome=DebugOutcome.FAILED,
                )
            except BaseException:
                pass
        return (
            _report(
                workspace=workspace,
                mode=mode,
                retained=retained,
                session_id=session_id,
                debate_id=debate_id,
                result=result,
                status="failed",
                checks=completed_checks,
                failed_check=exc.failed_check,
            ),
            1,
        )
    except BaseException as exc:
        if debug_log is not None:
            try:
                debug_log.error(
                    DebugComponent.SMOKE,
                    DebugOperation.CLI_COMMAND,
                    exc,
                    phase=(
                        DebugPhase.CANCELLED
                        if isinstance(
                            exc,
                            (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                        )
                        else DebugPhase.FAILED
                    ),
                    session_id=session_id,
                    debate_id=debate_id,
                    outcome=(
                        DebugOutcome.INTERRUPTED
                        if isinstance(
                            exc,
                            (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                        )
                        else DebugOutcome.FAILED
                    ),
                )
            except BaseException:
                pass
        raise

    _trace_smoke(
        debug_log,
        DebugOperation.CLI_COMMAND,
        DebugPhase.SUCCEEDED,
        session_id=session_id,
        debate_id=debate_id,
        outcome=DebugOutcome.COMPLETED,
    )
    return (
        _report(
            workspace=workspace,
            mode=mode,
            retained=retained,
            session_id=session_id,
            debate_id=debate_id,
            result=result,
            status="passed",
            checks=completed_checks,
        ),
        0,
    )


async def _exercise_provider_free_conversation_and_consensus(
    workspace: Workspace,
    *,
    session_id: str,
    debate_id: str,
    output_path: str,
    core_before: dict[str, bytes],
    manuscripts_before: tuple[tuple[str, bytes], ...],
    completed_checks: list[str],
) -> _SmokeWorkflowResult:
    """Exercise conversation memory, retrieval, and consensus in one event loop.

    The supplied workspace and unique identities receive two conversations, three
    advisory turns, and one consensus run. Successful stage names are appended to
    ``completed_checks`` and a cohesive result is returned; named invariant
    failures raise ``_SmokeInvariantError`` and collaborator faults propagate.
    """

    catalog = FilesystemConversationCatalog(workspace)
    event_store = FilesystemConversationEventStore(workspace, catalog=catalog)
    conversation_backend = _SmokeBackend(session_id, debate_id)
    conversation_backend._binding_failed_check = _CHECK_NAMES[3]
    conversation_runtime = StoryRuntime(
        workspace,
        conversation_backend,
        conversation_event_store=event_store,
    )

    source = catalog.create(session_id, _SOURCE_SUBJECT)
    retrieval = catalog.create(session_id, _RETRIEVAL_SUBJECT)
    _verify_conversation_catalog(catalog, session_id, source, retrieval)
    completed_checks.append(_CHECK_NAMES[2])

    lead_result = await conversation_runtime.converse(
        session_id,
        source.conversation_id,
        LEAD_ORCHESTRATOR,
        _LEAD_QUESTION,
    )
    specialist_result = await conversation_runtime.converse(
        session_id,
        source.conversation_id,
        PLOT_ARCHITECT,
        _SPECIALIST_QUESTION,
    )
    source_events = event_store.read(session_id, source.conversation_id)
    retrieval_events = event_store.read(session_id, retrieval.conversation_id)
    _verify_conversation_routing(
        conversation_backend,
        source,
        retrieval,
        lead_result,
        specialist_result,
        source_events,
        retrieval_events,
    )
    _verify_preconsensus_artifact_isolation(
        workspace,
        session_id=session_id,
        debate_id=debate_id,
        core_before=core_before,
        manuscripts_before=manuscripts_before,
        failed_check=_CHECK_NAMES[3],
    )
    completed_checks.append(_CHECK_NAMES[3])

    memory = ConversationMemoryService(
        ConversationSearchScope(session_id),
        catalog,
        event_store,
    )
    search_evidence = _search_source_conversation(
        memory,
        source,
        failed_check=_CHECK_NAMES[4],
    )
    completed_checks.append(_CHECK_NAMES[4])

    fresh_catalog = FilesystemConversationCatalog(workspace)
    fresh_store = FilesystemConversationEventStore(
        workspace,
        catalog=fresh_catalog,
    )
    fresh_backend = _SmokeBackend(session_id, debate_id)
    fresh_backend._binding_failed_check = _CHECK_NAMES[6]
    fresh_runtime = StoryRuntime(
        workspace,
        fresh_backend,
        conversation_event_store=fresh_store,
    )
    fresh_memory = ConversationMemoryService(
        ConversationSearchScope(session_id),
        fresh_catalog,
        fresh_store,
    )
    _verify_persisted_conversation_state(
        fresh_runtime,
        fresh_catalog,
        fresh_store,
        fresh_memory,
        session_id=session_id,
        source=source,
        retrieval=retrieval,
        source_events=source_events,
        retrieval_events=retrieval_events,
        search_evidence=search_evidence,
    )
    completed_checks.append(_CHECK_NAMES[5])

    progress: list[ConversationRetrievalProgressEvent] = []
    retrieval_result = await fresh_runtime.converse_with_retrieval(
        session_id,
        retrieval.conversation_id,
        PLOT_ARCHITECT,
        _RETRIEVAL_QUESTION,
        memory_service=fresh_memory,
        progress_sink=progress.append,
    )
    _verify_conversation_retrieval(
        fresh_backend,
        fresh_store,
        retrieval_result,
        progress,
        session_id=session_id,
        source=source,
        retrieval=retrieval,
        source_events=source_events,
    )
    _verify_preconsensus_artifact_isolation(
        workspace,
        session_id=session_id,
        debate_id=debate_id,
        core_before=core_before,
        manuscripts_before=manuscripts_before,
        failed_check=_CHECK_NAMES[6],
    )
    completed_checks.append(_CHECK_NAMES[6])

    context_snapshot = fresh_store.read_context(
        session_id,
        source.conversation_id,
    )
    _require(
        context_snapshot.events == source_events
        and context_snapshot.total_complete_turns == 2
        and context_snapshot.retained_turns == 2
        and context_snapshot.omitted_turns == 0
        and context_snapshot.truncated is False,
        _CHECK_NAMES[7],
    )
    fresh_backend.expected_consensus_context = context_snapshot
    fresh_backend._binding_failed_check = _CHECK_NAMES[8]
    debate_result = await fresh_runtime.run_prompt(
        session_id,
        _PROMPT,
        debate_id=debate_id,
        output_path=output_path,
        conversation_context=DebateContextRequest(
            conversation_id=source.conversation_id,
            mode=DebateContextMode.INCLUDED,
        ),
    )
    return _SmokeWorkflowResult(
        debate_result=debate_result,
        backend=fresh_backend,
        context_snapshot=context_snapshot,
    )


def _read_core_document_bytes(workspace: Workspace) -> dict[str, bytes]:
    """Return supplied workspace core files as bytes without writing them.

    Workspace security and filesystem read failures propagate unchanged.
    """

    result: dict[str, bytes] = {}
    for filename in CORE_DOCUMENTS:
        target = workspace.path(f"core_documents/{filename}")
        if target.is_file():
            result[filename] = target.read_bytes()
    return result


def _snapshot_manuscript_files(
    workspace: Workspace,
) -> tuple[tuple[str, bytes], ...]:
    """Return stable manuscript paths and bytes from a supplied workspace.

    The operation is read-only; workspace and filesystem failures propagate.
    """

    return tuple(
        (
            path.relative_to(workspace.root).as_posix(),
            path.read_bytes(),
        )
        for path in sorted(workspace.path("manuscript").rglob("*"))
        if path.is_file() and path.name != ".gitkeep"
    )


def _has_uuid_hex_suffix(value: str, prefix: str) -> bool:
    """Return whether supplied text has a prefix and canonical UUID hex suffix.

    The pure check performs no mutation and converts malformed values to ``False``.
    """

    if not value.startswith(prefix):
        return False
    suffix = value[len(prefix) :]
    try:
        parsed = uuid.UUID(hex=suffix)
    except (AttributeError, ValueError):
        return False
    return len(suffix) == 32 and parsed.hex == suffix


def _verify_conversation_catalog(
    catalog: FilesystemConversationCatalog,
    session_id: str,
    source: ConversationMetadata,
    retrieval: ConversationMetadata,
) -> None:
    """Validate two supplied active catalog entries and their deterministic order.

    The operation only reads the catalog and returns ``None``; a mismatch raises
    the catalog invariant while storage failures propagate.
    """

    listed = catalog.list(session_id)
    _require(
        listed == (source, retrieval)
        and catalog.list(session_id, include_archived=True) == listed
        and source.conversation_id != retrieval.conversation_id
        and source.subject == _SOURCE_SUBJECT
        and retrieval.subject == _RETRIEVAL_SUBJECT
        and source.status is ConversationStatus.ACTIVE
        and retrieval.status is ConversationStatus.ACTIVE
        and source.subject_revision == retrieval.subject_revision == 1
        and catalog.get(session_id, source.conversation_id) == source
        and catalog.get(session_id, retrieval.conversation_id) == retrieval
        and _has_uuid_hex_suffix(source.conversation_id, "c_")
        and _has_uuid_hex_suffix(retrieval.conversation_id, "c_"),
        _CHECK_NAMES[2],
    )


def _verify_conversation_routing(
    backend: _SmokeBackend,
    source: ConversationMetadata,
    retrieval: ConversationMetadata,
    lead_result: ConversationResult,
    specialist_result: ConversationResult,
    source_events: tuple[ConversationEvent, ...],
    retrieval_events: tuple[ConversationEvent, ...],
) -> None:
    """Validate supplied isolated turns, transcripts, routes, and contexts.

    The check mutates nothing and returns ``None``; any mismatch raises the routing
    invariant.
    """

    request_agents = tuple(
        request.agent_id for request in backend.conversation_requests
    )
    definition_agents = tuple(
        definition.agent_id for definition in backend.definitions
    )
    source_contents = tuple(event.content for event in source_events)
    source_kinds = tuple(event.kind for event in source_events)
    source_actors = tuple(
        (event.sender, event.recipient) for event in source_events
    )
    source_turn_ids = tuple(event.turn_id for event in source_events)
    _require(
        request_agents == (LEAD_ORCHESTRATOR, PLOT_ARCHITECT)
        and definition_agents == (LEAD_ORCHESTRATOR, PLOT_ARCHITECT)
        and lead_result.session_id == specialist_result.session_id == source.session_id
        and lead_result.conversation_id
        == specialist_result.conversation_id
        == source.conversation_id
        and lead_result.agent_id == LEAD_ORCHESTRATOR
        and specialist_result.agent_id == PLOT_ARCHITECT
        and lead_result.reference_operation_id is None
        and specialist_result.reference_operation_id is None
        and lead_result.content == _LEAD_REPLY
        and specialist_result.content == _SPECIALIST_REPLY
        and lead_result.context.events == ()
        and lead_result.context.total_complete_turns == 0
        and lead_result.context.retained_turns == 0
        and lead_result.context.omitted_turns == 0
        and lead_result.context.retained_utf8_bytes == 0
        and lead_result.context.truncated is False
        and specialist_result.context.events == source_events[:2]
        and specialist_result.context.total_complete_turns == 1
        and specialist_result.context.retained_turns == 1
        and specialist_result.context.omitted_turns == 0
        and specialist_result.context.truncated is False
        and len(source_events) == 4
        and tuple(event.sequence for event in source_events) == (1, 2, 3, 4)
        and source_contents
        == (
            _LEAD_QUESTION,
            _LEAD_REPLY,
            _SPECIALIST_QUESTION,
            _SPECIALIST_REPLY,
        )
        and source_kinds
        == (
            ConversationEventKind.USER_MESSAGE,
            ConversationEventKind.AGENT_REPLY,
            ConversationEventKind.USER_MESSAGE,
            ConversationEventKind.AGENT_REPLY,
        )
        and source_actors
        == (
            ("user", LEAD_ORCHESTRATOR),
            (LEAD_ORCHESTRATOR, "user"),
            ("user", PLOT_ARCHITECT),
            (PLOT_ARCHITECT, "user"),
        )
        and source_turn_ids
        == (
            lead_result.turn_id,
            lead_result.turn_id,
            specialist_result.turn_id,
            specialist_result.turn_id,
        )
        and lead_result.turn_id != specialist_result.turn_id
        and _has_uuid_hex_suffix(lead_result.turn_id, "t_")
        and _has_uuid_hex_suffix(specialist_result.turn_id, "t_")
        and retrieval.session_id == source.session_id
        and retrieval_events == (),
        _CHECK_NAMES[3],
    )


def _verify_preconsensus_artifact_isolation(
    workspace: Workspace,
    *,
    session_id: str,
    debate_id: str,
    core_before: dict[str, bytes],
    manuscripts_before: tuple[tuple[str, bytes], ...],
    failed_check: str,
) -> None:
    """Check supplied run IDs changed no protected pre-consensus artifacts.

    The operation is read-only and returns ``None``; a mismatch raises the supplied
    check name while workspace and filesystem failures propagate.
    """

    runtime = StoryRuntime(workspace)
    _require(
        runtime.inspect_session(session_id)["debates"] == []
        and runtime.inspect_debate_context(session_id, debate_id) is None
        and all(
            workspace.read_agent_state(agent_id, session_id, debate_id) == ()
            for agent_id in ROSTER
        )
        and _snapshot_manuscript_files(workspace) == manuscripts_before
        and _read_core_document_bytes(workspace) == core_before,
        failed_check,
    )


def _search_source_conversation(
    memory: ConversationMemoryService,
    source: ConversationMetadata,
    *,
    failed_check: str,
) -> _SmokeSearchEvidence:
    """Search supplied memory by exact subject and prose, returning cited hits.

    Search adapters may read but must not write; a result mismatch raises the
    supplied invariant and collaborator failures propagate.
    """

    subject_result = memory.search(ConversationSearchQuery(_SOURCE_SUBJECT))
    transcript_result = memory.search(
        ConversationSearchQuery(_VISIBLE_SEARCH_TEXT)
    )
    _require(
        len(subject_result.hits) == 1
        and len(transcript_result.hits) == 1,
        failed_check,
    )
    subject_hit = subject_result.hits[0]
    transcript_hit = transcript_result.hits[0]
    _require(
        subject_hit.citation.session_id == source.session_id
        and subject_hit.citation.conversation_id == source.conversation_id
        and subject_hit.citation.turn_id is None
        and subject_hit.citation.sequence is None
        and subject_hit.citation.event_kind is None
        and subject_hit.match_kind is ConversationSearchMatchKind.EXACT_SUBJECT
        and transcript_hit.citation.session_id == source.session_id
        and transcript_hit.citation.conversation_id == source.conversation_id
        and transcript_hit.citation.sequence == 2
        and transcript_hit.citation.event_kind
        is ConversationEventKind.AGENT_REPLY
        and transcript_hit.match_kind is ConversationSearchMatchKind.TRANSCRIPT
        and _VISIBLE_SEARCH_TEXT in transcript_hit.snippet,
        failed_check,
    )
    return _SmokeSearchEvidence(subject_hit, transcript_hit)


def _verify_persisted_conversation_state(
    runtime: StoryRuntime,
    catalog: FilesystemConversationCatalog,
    event_store: FilesystemConversationEventStore,
    memory: ConversationMemoryService,
    *,
    session_id: str,
    source: ConversationMetadata,
    retrieval: ConversationMetadata,
    source_events: tuple[ConversationEvent, ...],
    retrieval_events: tuple[ConversationEvent, ...],
    search_evidence: _SmokeSearchEvidence,
) -> None:
    """Validate supplied fresh collaborators see identical persisted state.

    The operation performs only supported reads and returns ``None``; a mismatch
    raises the persistence invariant and collaborator failures propagate.
    """

    _verify_directive(runtime, session_id)
    persisted_search = _search_source_conversation(
        memory,
        source,
        failed_check=_CHECK_NAMES[5],
    )
    _require(
        catalog.list(session_id) == (source, retrieval)
        and catalog.get(session_id, source.conversation_id) == source
        and catalog.get(session_id, retrieval.conversation_id) == retrieval
        and event_store.read(session_id, source.conversation_id) == source_events
        and event_store.read(session_id, retrieval.conversation_id)
        == retrieval_events
        and persisted_search.subject_hit.citation
        == search_evidence.subject_hit.citation
        and persisted_search.transcript_hit.citation
        == search_evidence.transcript_hit.citation
        and runtime.inspect_session(session_id)["debates"] == [],
        _CHECK_NAMES[5],
    )


def _verify_conversation_retrieval(
    backend: _SmokeBackend,
    event_store: FilesystemConversationEventStore,
    result: ConversationRetrievalResult,
    progress: list[ConversationRetrievalProgressEvent],
    *,
    session_id: str,
    source: ConversationMetadata,
    retrieval: ConversationMetadata,
    source_events: tuple[ConversationEvent, ...],
) -> None:
    """Validate supplied retrieval traces, progress, sources, and persistence.

    Verification is read-only and returns ``None``; mismatches raise the retrieval
    invariant while transcript read failures propagate.
    """

    _require(
        len(backend.retrieval_requests) == 1
        and len(backend.retrieval_actions) == 3
        and len(backend.retrieval_outcomes) == 3,
        _CHECK_NAMES[6],
    )
    request = backend.retrieval_requests[0]
    search_action, read_action, answer_action = backend.retrieval_actions
    search_outcome, read_outcome, answer_outcome = backend.retrieval_outcomes
    _require(
        result.reference_operation_id is None
        and type(search_action) is ConversationRetrievalSearchAction
        and type(read_action) is ConversationRetrievalReadAction
        and type(answer_action) is ConversationRetrievalAnswerAction
        and tuple(action.action_index for action in backend.retrieval_actions)
        == (1, 2, 3)
        and tuple(action.kind for action in backend.retrieval_actions)
        == (
            ConversationRetrievalActionKind.SEARCH_CONVERSATIONS,
            ConversationRetrievalActionKind.READ_TRANSCRIPT,
            ConversationRetrievalActionKind.ANSWER,
        )
        and search_action.query == _SOURCE_SUBJECT
        and read_action.citation_id == "s1h1"
        and read_action.start_sequence == 1
        and read_action.end_sequence == 2
        and answer_action.content == _RETRIEVAL_REPLY
        and answer_action.citation_ids == ("r2e1", "r2e2")
        and type(search_outcome) is ConversationRetrievalSearchObservation
        and type(read_outcome) is ConversationRetrievalReadObservation
        and type(answer_outcome) is ConversationRetrievalReply,
        _CHECK_NAMES[6],
    )
    assert type(search_outcome) is ConversationRetrievalSearchObservation
    assert type(read_outcome) is ConversationRetrievalReadObservation
    assert type(answer_outcome) is ConversationRetrievalReply
    search_hit = search_outcome.hits[0]
    read_citations = tuple(event.citation for event in read_outcome.events)
    source_citations = tuple(item.citation for item in answer_outcome.sources)
    _require(
        request.session_id == session_id
        and request.conversation_id == retrieval.conversation_id
        and request.agent_id == PLOT_ARCHITECT
        and request.content == _RETRIEVAL_QUESTION
        and request.context.events == ()
        and request.context.total_complete_turns == 0
        and search_hit.source.subject == _SOURCE_SUBJECT
        and search_hit.source.citation.conversation_id == source.conversation_id
        and search_hit.match_kind is ConversationSearchMatchKind.EXACT_SUBJECT
        and tuple(event.citation.sequence for event in read_outcome.events)
        == (1, 2)
        and tuple(event.content for event in read_outcome.events)
        == tuple(event.content for event in source_events[:2])
        and read_outcome.source.citation == search_hit.source.citation
        and read_outcome.truncated is False
        and source_citations == read_citations
        and result.sources == answer_outcome.sources
        and result.content == _RETRIEVAL_REPLY
        and result.session_id == session_id
        and result.conversation_id == retrieval.conversation_id
        and result.agent_id == PLOT_ARCHITECT
        and result.turn_id == request.turn_id
        and _has_uuid_hex_suffix(result.turn_id, "t_")
        and result.turn_id not in {event.turn_id for event in source_events}
        and backend.requests == [],
        _CHECK_NAMES[6],
    )
    _require(
        tuple(event.phase for event in progress)
        == (
            ConversationRetrievalProgressPhase.SEARCH_STARTED,
            ConversationRetrievalProgressPhase.SEARCH_COMPLETED,
            ConversationRetrievalProgressPhase.READ_STARTED,
            ConversationRetrievalProgressPhase.READ_COMPLETED,
        )
        and tuple(event.action_index for event in progress) == (1, 1, 2, 2)
        and all(
            event.session_id == session_id
            and event.conversation_id == retrieval.conversation_id
            and event.turn_id == result.turn_id
            and event.agent_id == PLOT_ARCHITECT
            for event in progress
        )
        and progress[1].search_hit_count == 1
        and progress[1].observation_utf8_bytes > 0
        and progress[3].transcript_turn_count == 1
        and progress[3].transcript_event_count == 2
        and progress[3].observation_utf8_bytes > 0
        and all(event.truncated is False for event in progress),
        _CHECK_NAMES[6],
    )
    persisted_retrieval = event_store.read(
        session_id,
        retrieval.conversation_id,
    )
    _require(
        event_store.read(session_id, source.conversation_id) == source_events
        and len(persisted_retrieval) == 2
        and tuple(event.turn_id for event in persisted_retrieval)
        == (result.turn_id, result.turn_id)
        and tuple(event.kind for event in persisted_retrieval)
        == (
            ConversationEventKind.USER_MESSAGE,
            ConversationEventKind.AGENT_REPLY,
        )
        and tuple(event.content for event in persisted_retrieval)
        == (_RETRIEVAL_QUESTION, _RETRIEVAL_REPLY),
        _CHECK_NAMES[6],
    )


def _verify_consensus_context(
    workspace: Workspace,
    backend: _SmokeBackend,
    context: ConversationContextWindow,
    result: DebateResult,
) -> None:
    """Verify supplied debate traces and sidecar use one captured context.

    The operation reads public persistence without writes and returns ``None``;
    mismatches raise the context invariant and storage failures propagate.
    """

    _require(bool(context.events), _CHECK_NAMES[7])
    conversation_id = context.events[0].conversation_id
    sidecar = StoryRuntime(workspace).inspect_debate_context(
        result.session_id,
        result.debate_id,
    )
    expected_snapshot = {
        "events": [event.to_dict() for event in context.events],
        "total_complete_turns": context.total_complete_turns,
        "retained_turns": context.retained_turns,
        "omitted_turns": context.omitted_turns,
        "retained_utf8_bytes": context.retained_utf8_bytes,
        "truncated": context.truncated,
    }
    persisted_events = FilesystemConversationEventStore(workspace).read(
        result.session_id,
        conversation_id,
    )
    _require(
        len(backend.requests) == 10
        and all(
            request.conversation_context == context
            for request in backend.requests
        )
        and persisted_events == context.events
        and sidecar
        == {
            "schema_version": 1,
            "session_id": result.session_id,
            "debate_id": result.debate_id,
            "conversation_id": conversation_id,
            "mode": DebateContextMode.INCLUDED.value,
            "snapshot": expected_snapshot,
        },
        _CHECK_NAMES[7],
    )


def _verify_workspace_initialized(workspace: Workspace) -> None:
    """Validate supplied workspace directories and prompts without mutation.

    Success returns ``None``; missing artifacts raise the initialization invariant
    and filesystem read failures propagate.
    """

    ready = all(
        workspace.path(relative).is_dir()
        for relative in WORKSPACE_DIRECTORIES
        if relative != "debug_logs"
    ) and all(
        workspace.path(AGENT_SPECS[agent_id].system_prompt_file).is_file()
        for agent_id in ROSTER
    )
    _require(ready, _CHECK_NAMES[0])


def _verify_directive(runtime: StoryRuntime, session_id: str) -> None:
    """Validate a supplied runtime/session persisted the English directive.

    The check performs supported reads only and returns ``None``; a mismatch raises
    the directive invariant while configuration failures propagate.
    """

    overrides = runtime.workspace.load_session_overrides(session_id)
    resolved = runtime.config.resolve(EXECUTION_SPECIALIST, overrides)
    _require(
        overrides.get("language") == "EN" and resolved.language == "EN",
        _CHECK_NAMES[1],
    )


def _verify_revision_workflow(
    workspace: Workspace,
    backend: _SmokeBackend,
    result: DebateResult,
) -> None:
    """Validate supplied debate phases, bindings, concurrency, and agent state.

    The check reads persistence without writes and returns ``None``; mismatches
    raise the revision invariant while workspace failures propagate.
    """

    counts = Counter(request.phase for request in backend.requests)
    expected = {
        Phase.DRAFT: 1,
        Phase.AUDIT: 6,
        Phase.PROPOSE_REVISION: 1,
        Phase.SYNTHESIZE: 1,
        Phase.FINALIZE: 1,
    }
    expected_state_kinds = {
        LEAD_ORCHESTRATOR: ("candidate_created", "core_updates"),
        PLOT_ARCHITECT: ("critique", "proposal", "approval"),
        CHARACTER_CUSTODIAN: ("approval", "approval"),
        EXECUTION_SPECIALIST: (
            "candidate_created",
            "approval",
            "approval",
        ),
    }
    states_are_exact = all(
        tuple(
            entry.kind
            for entry in workspace.read_agent_state(
                agent_id,
                result.session_id,
                result.debate_id,
            )
        )
        == expected_state_kinds[agent_id]
        for agent_id in ROSTER
    )
    _require(
        counts == expected
        and Counter(
            definition.agent_id for definition in backend.definitions
        )
        == Counter({agent_id: 1 for agent_id in ROSTER})
        and backend.maximum_audit_active == len(SPECIALIST_IDS)
        and states_are_exact,
        _CHECK_NAMES[8],
    )


def _verify_consensus(workspace: Workspace, result: DebateResult) -> None:
    """Validate a supplied terminal result against persisted debate inspection.

    The operation is read-only and returns ``None``; mismatches raise the consensus
    invariant and workspace failures propagate.
    """

    inspected = StoryRuntime(workspace).inspect_debate(
        result.session_id,
        result.debate_id,
    )
    persisted = inspected["result"]
    _require(
        result.status is DebateStatus.CONSENSUS
        and result.reference_operation_id is None
        and result.revision_id == "r1"
        and result.rounds_completed == 1
        and result.final_candidate == _REVISION_ONE
        and persisted is not None
        and "reference_operation_id" not in persisted
        and persisted["status"] == DebateStatus.CONSENSUS.value
        and persisted["revision_id"] == "r1"
        and inspected["status"] == DebateStatus.CONSENSUS.value
        and inspected["terminal"] is True
        and len(inspected["messages"]) == len(result.messages)
        and bool(inspected["messages"]),
        _CHECK_NAMES[9],
    )


def _require(condition: bool, failed_check: str) -> None:
    """Given a condition, return without mutation or raise its stable check name."""

    if not condition:
        raise _SmokeInvariantError(failed_check)


def _report(
    *,
    workspace: Workspace,
    mode: str,
    retained: bool,
    session_id: str,
    debate_id: str,
    result: DebateResult | None,
    status: str,
    checks: list[str],
    failed_check: str | None = None,
) -> dict[str, Any]:
    """Return a compatible report from supplied state without I/O or mutation.

    Inputs are trusted internal values; construction has no expected failure and
    adds only ``failed_check`` when supplied.
    """

    report: dict[str, Any] = {
        "command": "smoke",
        "status": status,
        "provider_tested": False,
        "workspace": {
            "mode": mode,
            "path": str(workspace.root),
            "retained": retained,
        },
        "session_id": session_id,
        "debate_id": debate_id,
        "debate_status": (
            result.status.value if result is not None else "not_run"
        ),
        "revision_id": result.revision_id if result is not None else None,
        "rounds_completed": (
            result.rounds_completed if result is not None else 0
        ),
        "checks": list(checks),
        "note": _NOTE,
    }
    if failed_check is not None:
        report["failed_check"] = failed_check
    return report
