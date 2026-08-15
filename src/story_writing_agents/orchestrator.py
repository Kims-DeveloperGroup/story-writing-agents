"""Provider-neutral asynchronous round-table orchestration."""

from __future__ import annotations

import asyncio
import inspect
import json
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    ROSTER,
    SPECIALIST_IDS,
)
from .backend import AgentBackend, BoundAgent
from .config import ConfigManager
from .conversations import (
    ConversationContextWindow,
    ConversationEvent,
    ConversationEventAppend,
    ConversationEventKind,
    ConversationEventStore,
    validate_conversation_context_window,
)
from .directives import apply_directive as persist_directive
from .errors import (
    BackendProtocolError,
    ConfigurationError,
    ManuscriptExistsError,
    ProgressObserverError,
    WorkspaceFormatError,
    WorkspaceSecurityError,
)
from .models import (
    AgentDefinition,
    AgentReply,
    AgentRequest,
    AgentStateEntry,
    ConversationReply,
    ConversationRequest,
    ConversationResult,
    DebateContextMode,
    DebateContextRequest,
    DebateFailureKind,
    DebateMessage,
    DebateProgressEvent,
    DebateProgressPhase,
    DebateProgressSink,
    DebateResult,
    DebateStatus,
    InputKind,
    Phase,
    Verdict,
)
from .prompts import PromptAssembler
from .workspace import FilesystemConversationEventStore, Workspace


class _ProgressEmitter:
    """Synchronously deliver validated progress while tracking durability."""

    __slots__ = ("sink", "terminal_durable")

    def __init__(self, sink: DebateProgressSink | None) -> None:
        """Retain one prevalidated optional sink without invoking or mutating it."""

        self.sink = sink
        self.terminal_durable = False

    def emit(self, event: DebateProgressEvent) -> None:
        """Deliver one event sequentially and require a synchronous ``None`` result.

        The event itself owns schema validation. Every failure raised
        synchronously by the sink, along with any invalid return, is wrapped in
        fixed-message ``ProgressObserverError`` and retained only through
        standard exception chaining; returned coroutine objects are closed and
        never scheduled.
        """

        if self.sink is None:
            return
        try:
            outcome = self.sink(event)
        except BaseException as exc:
            raise ProgressObserverError() from exc
        if inspect.isawaitable(outcome):
            if inspect.iscoroutine(outcome):
                outcome.close()
            cause = TypeError("debate progress sink must be synchronous")
            raise ProgressObserverError() from cause
        if outcome is not None:
            cause = TypeError("debate progress sink must return None")
            raise ProgressObserverError() from cause


class _DebateContextPropagationError(Exception):
    """Mark a validated snapshot that could not enter an agent request."""


class StoryRuntime:
    """Public runtime for persistent sessions, debates, and advisory turns."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
        *,
        conversation_event_store: ConversationEventStore | None = None,
    ) -> None:
        workspace_literal = (
            str(workspace.root) if isinstance(workspace, Workspace) else str(workspace)
        )
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self.backend = backend
        self.conversation_event_store = (
            conversation_event_store
            if conversation_event_store is not None
            else FilesystemConversationEventStore(self.workspace)
        )
        self._conversation_workspace_roots = tuple(
            dict.fromkeys((workspace_literal, str(self.workspace.root)))
        )
        backend_overrides = (
            getattr(backend, "backend_overrides", {}) if backend is not None else {}
        )
        self.prompts = PromptAssembler(
            self.workspace,
            backend_overrides=backend_overrides,
        )
        self.config = ConfigManager(self.workspace)
        self._bound_agents: dict[tuple[str, str], BoundAgent] = {}
        self._binding_tasks: dict[
            tuple[str, str], asyncio.Task[BoundAgent]
        ] = {}
        self._binding_waiters: dict[tuple[str, str], int] = {}
        self._binding_lock = asyncio.Lock()
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def session(self, session_id: str) -> RuntimeSession:
        self.workspace.validate_identifier(session_id)
        return RuntimeSession(self, session_id)

    def apply_directive(self, session_id: str, text: str) -> dict[str, object]:
        return persist_directive(self.workspace, session_id, text)

    def set_runtime_overrides(
        self,
        session_id: str,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace one session's overrides only after complete validation."""

        validated = self.config.validate_runtime_overrides(overrides)
        self.workspace.save_session_overrides(session_id, validated)
        return validated

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        return self.workspace.inspect_session(session_id)

    def inspect_debate(
        self, session_id: str, debate_id: str
    ) -> dict[str, Any]:
        return self.workspace.inspect_debate(session_id, debate_id)

    def inspect_debate_context(
        self,
        session_id: str,
        debate_id: str,
    ) -> dict[str, Any] | None:
        """Return a strict context sidecar or ``None`` without mutating state."""

        return self.workspace.inspect_debate_context(session_id, debate_id)

    async def converse(
        self,
        session_id: str,
        conversation_id: str,
        agent_id: str,
        text: str,
    ) -> ConversationResult:
        """Run and persist one advisory turn in an explicit conversation."""

        session = self.workspace.validate_identifier(session_id)
        conversation = self.workspace.validate_identifier(
            conversation_id,
            label="conversation ID",
        )
        agent = self.workspace.validate_identifier(agent_id, label="agent ID")
        if agent not in ROSTER:
            raise ValueError(f"conversation agent is not canonical: {agent}")
        content = self._validate_conversation_text(text)
        if self.backend is None:
            raise BackendProtocolError(
                "running a conversation requires an AgentBackend adapter"
            )
        read_context = self._conversation_store_method("read_context")
        append = self._conversation_store_method("append")

        lock_key = (session, conversation)
        lock = self._conversation_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[lock_key] = lock
        async with lock:
            turn_id = self._new_conversation_turn_id()
            context = read_context(session, conversation)
            self._reject_async_result(context, operation="read_context")
            try:
                request = ConversationRequest(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=content,
                    context=context,
                )
            except Exception as exc:
                raise BackendProtocolError(
                    "conversation event store read_context() returned "
                    "an invalid context"
                ) from exc

            self._prepare_conversation_session(session)
            definition = self.prompts.build_definition(
                session_id=session,
                agent_id=agent,
            )
            bound_agent = await self._bound_agent(definition)
            bound_converse = getattr(bound_agent, "converse", None)
            if bound_converse is None or not callable(bound_converse):
                raise BackendProtocolError(
                    f"{agent} binding does not provide converse()"
                )
            if not inspect.iscoroutinefunction(bound_converse):
                raise BackendProtocolError(
                    f"{agent} bound converse() must be async"
                )
            self._verify_definition_files(definition)

            user_event = ConversationEventAppend(
                session_id=session,
                conversation_id=conversation,
                turn_id=turn_id,
                sender="user",
                recipient=agent,
                kind=ConversationEventKind.USER_MESSAGE,
                content=content,
            )
            self._append_conversation_event(append, user_event)

            try:
                raw_reply = await bound_converse(request)
                reply = self._validated_conversation_reply(raw_reply, request)
                reply_event = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender=agent,
                    recipient="user",
                    kind=ConversationEventKind.AGENT_REPLY,
                    content=reply.content,
                )
                result = ConversationResult(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    agent_id=agent,
                    content=reply.content,
                    context=request.context,
                )
            except asyncio.CancelledError:
                cancellation = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content="CancelledError: conversation turn was cancelled",
                )
                try:
                    self._append_conversation_event(append, cancellation)
                except BaseException:
                    pass
                raise
            except Exception as exc:
                failure = ConversationEventAppend(
                    session_id=session,
                    conversation_id=conversation,
                    turn_id=turn_id,
                    sender="runtime",
                    recipient="user",
                    kind=ConversationEventKind.RUNTIME_ERROR,
                    content=self._conversation_error_text(exc, request),
                )
                try:
                    self._append_conversation_event(append, failure)
                except Exception as persistence_exc:
                    raise persistence_exc from exc
                raise

            self._append_conversation_event(append, reply_event)
            return result

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Run a prompt debate with optional selected context and progress facts.

        Inputs are validated before session preparation. Requested context is
        captured and persisted before provider work; terminal results and state
        are durable before terminal progress. Expected workflow failures return
        a ``DebateResult`` while invalid pre-reservation inputs, observer errors,
        cancellation, and persistence uncertainty raise.
        """

        return await self._run(
            session_id=session_id,
            payload=prompt,
            input_kind=InputKind.PROMPT,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def run_draft(
        self,
        session_id: str,
        draft: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Run a supplied-draft debate with optional context and progress facts.

        Validation, context capture and sidecar durability precede provider work;
        the supplied draft produces a trusted drafted fact without a drafting
        provider call. Terminal durability, expected failures, observer errors,
        cancellation, and raised persistence uncertainty match ``run_prompt``.
        """

        return await self._run(
            session_id=session_id,
            payload=draft,
            input_kind=InputKind.DRAFT,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def _run(
        self,
        *,
        session_id: str,
        payload: str,
        input_kind: InputKind,
        manuscript_name: str | None,
        max_debate_rounds: int | None,
        debate_id: str | None,
        conversation_context: DebateContextRequest | None,
        progress_sink: DebateProgressSink | None,
    ) -> DebateResult:
        """Validate, capture requested context, reserve, and run one debate.

        Validation and synchronous session preparation precede mutation. Included
        context is read once and canonicalized before reservation; requested
        sidecars precede initiation and provider work. Cancellation and observer
        failures preserve their typed control flow with best-effort interruption.
        """

        if self.backend is None:
            raise BackendProtocolError(
                "running a debate requires an AgentBackend adapter"
            )
        session = self.workspace.validate_identifier(session_id)
        if not isinstance(payload, str) or not payload:
            raise ValueError(f"{input_kind.value} payload must be non-empty text")
        debate = (
            self.workspace.validate_identifier(debate_id, label="debate ID")
            if debate_id is not None
            else self._new_debate_id()
        )
        configured_rounds = self.config.load_system_defaults().max_debate_rounds
        round_limit = (
            configured_rounds
            if max_debate_rounds is None
            else max_debate_rounds
        )
        if (
            isinstance(round_limit, bool)
            or not isinstance(round_limit, int)
            or round_limit < 0
        ):
            raise ValueError("max_debate_rounds must be a non-negative integer")
        context_request = self._validate_debate_context_request(
            conversation_context
        )
        progress = _ProgressEmitter(self._validate_progress_sink(progress_sink))

        target_name = manuscript_name or f"{session}_{debate}.md"
        self._prepare_debate_session(session)

        context_snapshot: ConversationContextWindow | None = None
        if (
            context_request is not None
            and context_request.mode is DebateContextMode.INCLUDED
        ):
            try:
                read_context = self._conversation_store_method("read_context")
                captured = read_context(session, context_request.conversation_id)
                self._reject_async_result(captured, operation="read_context")
            except BackendProtocolError as exc:
                raise ValueError(
                    "conversation context capture must be synchronous"
                ) from exc
            context_snapshot = validate_conversation_context_window(
                captured,
                session_id=session,
                conversation_id=context_request.conversation_id,
            )

        reservation_token = uuid.uuid4().hex
        self.workspace.reserve_debate(
            session,
            debate,
            input_kind=input_kind.value,
            owner_token=reservation_token,
        )
        if context_request is not None:
            try:
                self.workspace.save_debate_context(
                    session,
                    debate,
                    conversation_id=context_request.conversation_id,
                    mode=context_request.mode,
                    snapshot=context_snapshot,
                )
            except Exception as exc:
                try:
                    self.workspace.transition_debate(
                        session,
                        debate,
                        owner_token=reservation_token,
                        status="failed",
                        terminal=True,
                        revision_id=None,
                        rounds_completed=0,
                        error=self._error_text(exc),
                    )
                except Exception:
                    pass
                raise
        execution = asyncio.create_task(
            self._run_reserved(
                session=session,
                debate=debate,
                payload=payload,
                input_kind=input_kind,
                target_name=target_name,
                round_limit=round_limit,
                reservation_token=reservation_token,
                conversation_context=context_snapshot,
                context_mode=(
                    None if context_request is None else context_request.mode
                ),
                progress=progress,
            ),
            name=f"debate:{session}:{debate}",
        )
        try:
            return await execution
        except asyncio.CancelledError as cancellation:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            interrupted = False
            try:
                state = self.workspace.transition_debate(
                    session,
                    debate,
                    owner_token=reservation_token,
                    status="interrupted",
                    terminal=True,
                    revision_id=None,
                    rounds_completed=0,
                    error="CancelledError: debate execution was cancelled",
                )
                interrupted = (
                    state["terminal"] is True
                    and state["status"] == "interrupted"
                )
            except Exception:
                # Cancellation must retain its external control-flow semantics.
                pass
            if interrupted:
                try:
                    progress.emit(
                        DebateProgressEvent(
                            session_id=session,
                            debate_id=debate,
                            input_kind=input_kind,
                            phase=DebateProgressPhase.INTERRUPTED,
                        )
                    )
                except ProgressObserverError as observer_error:
                    raise cancellation from observer_error
            raise
        except ProgressObserverError:
            if not progress.terminal_durable:
                try:
                    self.workspace.transition_debate(
                        session,
                        debate,
                        owner_token=reservation_token,
                        status="interrupted",
                        terminal=True,
                        revision_id=None,
                        rounds_completed=0,
                        error="ProgressObserverError: debate progress observer failed",
                    )
                except Exception:
                    pass
            raise
        except Exception as exc:
            try:
                self.workspace.transition_debate(
                    session,
                    debate,
                    owner_token=reservation_token,
                    status="failed",
                    terminal=True,
                    revision_id=None,
                    rounds_completed=0,
                    error=self._error_text(exc),
                )
            except Exception:
                pass
            raise

    async def _run_reserved(
        self,
        *,
        session: str,
        debate: str,
        payload: str,
        input_kind: InputKind,
        target_name: str,
        round_limit: int,
        reservation_token: str,
        conversation_context: ConversationContextWindow | None,
        context_mode: DebateContextMode | None,
        progress: _ProgressEmitter,
    ) -> DebateResult:
        """Execute a reserved debate with durable records before progress facts.

        The supplied immutable context object is reused for every phase request.
        Expected workflow failures are durably finished as typed results; record,
        artifact, progress, and unexpected failures propagate to the outer owner.
        """

        messages: list[DebateMessage] = []

        def record(
            *,
            sender: str,
            phase: str,
            round_number: int,
            revision: str,
            kind: str,
            content: str,
        ) -> DebateMessage:
            message = DebateMessage(
                sender=sender,
                phase=phase,
                round=round_number,
                revision=revision,
                kind=kind,
                content=content,
                timestamp=datetime.now(UTC).isoformat(),
            )
            self.workspace.append_debate_message(session, debate, message)
            messages.append(message)
            return message

        record(
            sender="user",
            phase="initiation",
            round_number=0,
            revision="r0",
            kind=input_kind.value,
            content=payload,
        )
        progress.emit(
            DebateProgressEvent(
                session_id=session,
                debate_id=debate,
                input_kind=input_kind,
                phase=DebateProgressPhase.STARTED,
                context_mode=context_mode,
                context_event_count=(
                    0
                    if conversation_context is None
                    else len(conversation_context.events)
                ),
                context_omitted_turns=(
                    0
                    if conversation_context is None
                    else conversation_context.omitted_turns
                ),
            )
        )

        candidate: str
        current_revision = "r0"
        revision_cycles = 0

        if input_kind is InputKind.PROMPT:
            try:
                reply = await self._invoke(
                    self._request(
                        session=session,
                        debate=debate,
                        agent_id=EXECUTION_SPECIALIST,
                        phase=Phase.DRAFT,
                        round_number=0,
                        revision_id=current_revision,
                        task=(
                            "Create a complete revision 0 candidate from the user "
                            "prompt. Do not audit it in this response."
                        ),
                        user_payload=payload,
                        messages=tuple(messages),
                        conversation_context=conversation_context,
                    )
                )
                self._validate_candidate_reply(reply, current_revision)
            except Exception as exc:
                record(
                    sender=EXECUTION_SPECIALIST,
                    phase=Phase.DRAFT.value,
                    round_number=0,
                    revision=current_revision,
                    kind="error",
                    content=self._error_text(exc),
                )
                self._remember(
                    agent_id=EXECUTION_SPECIALIST,
                    session=session,
                    debate=debate,
                    phase=Phase.DRAFT,
                    round_number=0,
                    revision=current_revision,
                    kind="error",
                    content="backend outcome failed validation or execution",
                )
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=0,
                    final_candidate=None,
                    latest_candidate=None,
                    manuscript_path=None,
                    messages=messages,
                    error=self._error_text(exc),
                    failure_kind=self._classify_debate_failure(exc),
                    progress=progress,
                )
            candidate = reply.content
            record(
                sender=EXECUTION_SPECIALIST,
                phase=Phase.DRAFT.value,
                round_number=0,
                revision=current_revision,
                kind="candidate",
                content=candidate,
            )
            self._remember(
                agent_id=EXECUTION_SPECIALIST,
                session=session,
                debate=debate,
                phase=Phase.DRAFT,
                round_number=0,
                revision=current_revision,
                kind="candidate_created",
                content="revision 0 candidate created",
            )
        else:
            candidate = payload
        progress.emit(
            DebateProgressEvent(
                session_id=session,
                debate_id=debate,
                input_kind=input_kind,
                phase=DebateProgressPhase.DRAFTED,
                revision_id="r0",
                round_number=0,
            )
        )

        while True:
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.AUDIT_STARTED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                )
            )
            audit_entries: list[DebateMessage] = []
            audit_replies, audit_error = await self._audit(
                session=session,
                debate=debate,
                payload=payload,
                candidate=candidate,
                revision_id=current_revision,
                round_number=revision_cycles,
                visible_messages=tuple(messages),
                record=record,
                entries=audit_entries,
                conversation_context=conversation_context,
            )
            if audit_error is not None:
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=self._error_text(audit_error),
                    failure_kind=self._classify_debate_failure(audit_error),
                    progress=progress,
                )

            conflicts = [
                agent_id
                for agent_id in SPECIALIST_IDS
                if audit_replies[agent_id].verdict is Verdict.CONFLICT
            ]
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.AUDIT_COMPLETED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                    conflicting_agents=tuple(conflicts),
                )
            )
            if not conflicts:
                break
            if revision_cycles >= round_limit:
                critiques = "; ".join(
                    f"{agent_id}: {audit_replies[agent_id].content}"
                    for agent_id in conflicts
                )
                action = (
                    "User ruling required. Resolve the remaining domain conflicts "
                    f"before resuming: {critiques}"
                )
                record(
                    sender=LEAD_ORCHESTRATOR,
                    phase="deadlock",
                    round_number=revision_cycles,
                    revision=current_revision,
                    kind="escalation",
                    content=action,
                )
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.DEADLOCK,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    action_required=action,
                    progress=progress,
                )

            proposal_entries: list[DebateMessage] = []
            proposals, proposal_error = await self._propose(
                session=session,
                debate=debate,
                payload=payload,
                candidate=candidate,
                revision_id=current_revision,
                round_number=revision_cycles + 1,
                responsible_agents=tuple(conflicts),
                visible_messages=tuple(messages),
                record=record,
                entries=proposal_entries,
                conversation_context=conversation_context,
            )
            if proposal_error is not None:
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=self._error_text(proposal_error),
                    failure_kind=self._classify_debate_failure(proposal_error),
                    progress=progress,
                )

            next_revision = f"r{revision_cycles + 1}"
            try:
                synthesis = await self._invoke(
                    self._request(
                        session=session,
                        debate=debate,
                        agent_id=LEAD_ORCHESTRATOR,
                        phase=Phase.SYNTHESIZE,
                        round_number=revision_cycles + 1,
                        revision_id=next_revision,
                        task=(
                            "Synthesize one complete replacement candidate using "
                            "every visible critique and every responsible-agent "
                            "proposal. Do not filter out or silently omit a conflict."
                        ),
                        user_payload=payload,
                        candidate_payload=candidate,
                        messages=tuple(messages),
                        conversation_context=conversation_context,
                    )
                )
                self._validate_candidate_reply(synthesis, next_revision)
            except Exception as exc:
                error = self._error_text(exc)
                record(
                    sender=LEAD_ORCHESTRATOR,
                    phase=Phase.SYNTHESIZE.value,
                    round_number=revision_cycles + 1,
                    revision=next_revision,
                    kind="error",
                    content=error,
                )
                self._remember(
                    agent_id=LEAD_ORCHESTRATOR,
                    session=session,
                    debate=debate,
                    phase=Phase.SYNTHESIZE,
                    round_number=revision_cycles + 1,
                    revision=next_revision,
                    kind="error",
                    content="backend outcome failed validation or execution",
                )
                return self._finish(
                    session=session,
                    debate=debate,
                    reservation_token=reservation_token,
                    input_kind=input_kind,
                    status=DebateStatus.FAILED,
                    revision_id=current_revision,
                    rounds_completed=revision_cycles,
                    final_candidate=None,
                    latest_candidate=candidate,
                    manuscript_path=None,
                    messages=messages,
                    error=error,
                    failure_kind=self._classify_debate_failure(exc),
                    progress=progress,
                )

            candidate = synthesis.content
            current_revision = next_revision
            revision_cycles += 1
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.SYNTHESIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="candidate",
                content=candidate,
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.SYNTHESIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="candidate_created",
                content=f"{current_revision} candidate synthesized",
            )
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.SYNTHESIZED,
                    revision_id=current_revision,
                    round_number=revision_cycles,
                )
            )

        artifact_commit_started = False
        try:
            finalization = await self._invoke(
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=LEAD_ORCHESTRATOR,
                    phase=Phase.FINALIZE,
                    round_number=revision_cycles,
                    revision_id=current_revision,
                    task=(
                        "The candidate is unanimously approved. Do not revise or "
                        "replace it. Return only full-text updates for core documents "
                        "that genuinely changed; an empty update map is valid."
                    ),
                    user_payload=payload,
                    candidate_payload=candidate,
                    messages=tuple(messages),
                    conversation_context=conversation_context,
                )
            )
            updates = self._validate_finalization_reply(
                finalization, current_revision, candidate
            )
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.FINALIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="core_updates",
                content=json.dumps(updates, ensure_ascii=False, sort_keys=True),
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.FINALIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="core_updates",
                content=json.dumps(sorted(updates), ensure_ascii=False),
            )
            artifact_commit_started = True
            manuscript_path, applied_updates = (
                self.workspace.commit_approved_artifacts(
                    target_name,
                    candidate,
                    updates,
                )
            )
        except Exception as exc:
            error = self._error_text(exc)
            record(
                sender=LEAD_ORCHESTRATOR,
                phase=Phase.FINALIZE.value,
                round_number=revision_cycles,
                revision=current_revision,
                kind="error",
                content=error,
            )
            self._remember(
                agent_id=LEAD_ORCHESTRATOR,
                session=session,
                debate=debate,
                phase=Phase.FINALIZE,
                round_number=revision_cycles,
                revision=current_revision,
                kind="error",
                content="finalization or artifact commit failed",
            )
            return self._finish(
                session=session,
                debate=debate,
                reservation_token=reservation_token,
                input_kind=input_kind,
                status=DebateStatus.FAILED,
                revision_id=current_revision,
                rounds_completed=revision_cycles,
                final_candidate=None,
                latest_candidate=candidate,
                manuscript_path=None,
                messages=messages,
                error=error,
                failure_kind=self._classify_debate_failure(
                    exc,
                    artifact_commit=artifact_commit_started,
                ),
                progress=progress,
            )

        record(
            sender=LEAD_ORCHESTRATOR,
            phase=Phase.FINALIZE.value,
            round_number=revision_cycles,
            revision=current_revision,
            kind="committed",
            content=manuscript_path,
        )
        return self._finish(
            session=session,
            debate=debate,
            reservation_token=reservation_token,
            input_kind=input_kind,
            status=DebateStatus.CONSENSUS,
            revision_id=current_revision,
            rounds_completed=revision_cycles,
            final_candidate=candidate,
            latest_candidate=candidate,
            manuscript_path=manuscript_path,
            core_updates=applied_updates,
            messages=messages,
            progress=progress,
        )

    async def _audit(
        self,
        *,
        session: str,
        debate: str,
        payload: str,
        candidate: str,
        revision_id: str,
        round_number: int,
        visible_messages: tuple[DebateMessage, ...],
        record: Any,
        entries: list[DebateMessage],
        conversation_context: ConversationContextWindow | None,
    ) -> tuple[dict[str, AgentReply], Exception | None]:
        """Run all specialist audits, durably record each, and return first failure.

        Requests share the supplied immutable context. Provider calls may run in
        parallel, but records and agent state are written canonically afterward;
        context-propagation and provider failures are returned for durable debate
        completion, while persistence failures propagate.
        """

        try:
            requests = [
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=agent_id,
                    phase=Phase.AUDIT,
                    round_number=round_number,
                    revision_id=revision_id,
                    task=(
                        "Independently audit the current candidate for objective "
                        "domain compliance. Return APPROVE with an optional concise "
                        "note, or CONFLICT with a concise actionable critique. Do "
                        "not rewrite."
                    ),
                    user_payload=payload,
                    candidate_payload=candidate,
                    messages=visible_messages,
                    conversation_context=conversation_context,
                )
                for agent_id in SPECIALIST_IDS
            ]
        except _DebateContextPropagationError as exc:
            return {}, exc
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_audit_reply,
            revision_id,
        )
        replies: dict[str, AgentReply] = {}
        first_error: Exception | None = None
        for agent_id, raw in zip(SPECIALIST_IDS, raw_replies, strict=True):
            try:
                if isinstance(raw, Exception):
                    raise raw
                replies[agent_id] = raw
                content = (
                    raw.content
                    if raw.verdict is Verdict.CONFLICT
                    else (raw.content or "[No Conflict]")
                )
                kind = (
                    "critique"
                    if raw.verdict is Verdict.CONFLICT
                    else "approval"
                )
            except Exception as exc:
                content = self._error_text(exc)
                kind = "error"
                if first_error is None:
                    first_error = exc
            entry = record(
                sender=agent_id,
                phase=Phase.AUDIT.value,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=content,
            )
            entries.append(entry)
            self._remember(
                agent_id=agent_id,
                session=session,
                debate=debate,
                phase=Phase.AUDIT,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=(
                    content
                    if kind in {"approval", "critique"}
                    else "backend outcome failed validation or execution"
                ),
            )
        return replies, first_error

    async def _propose(
        self,
        *,
        session: str,
        debate: str,
        payload: str,
        candidate: str,
        revision_id: str,
        round_number: int,
        responsible_agents: tuple[str, ...],
        visible_messages: tuple[DebateMessage, ...],
        record: Any,
        entries: list[DebateMessage],
        conversation_context: ConversationContextWindow | None,
    ) -> tuple[dict[str, AgentReply], Exception | None]:
        """Run responsible proposals, durably record them, and return first failure.

        Each request receives the supplied context snapshot. Provider work may be
        parallel; durable records remain canonically ordered. Context-propagation,
        provider, and reply-validation failures are returned for durable debate
        completion, while persistence failures propagate.
        """

        try:
            requests = [
                self._request(
                    session=session,
                    debate=debate,
                    agent_id=agent_id,
                    phase=Phase.PROPOSE_REVISION,
                    round_number=round_number,
                    revision_id=revision_id,
                    task=(
                        "Propose a concise, actionable revision that resolves the "
                        "visible critiques in your responsible domain. The "
                        "orchestrator will synthesize the complete candidate."
                    ),
                    user_payload=payload,
                    candidate_payload=candidate,
                    messages=visible_messages,
                    conversation_context=conversation_context,
                )
                for agent_id in responsible_agents
            ]
        except _DebateContextPropagationError as exc:
            return {}, exc
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_proposal_reply,
            revision_id,
        )
        replies: dict[str, AgentReply] = {}
        first_error: Exception | None = None
        for agent_id, raw in zip(
            responsible_agents, raw_replies, strict=True
        ):
            try:
                if isinstance(raw, Exception):
                    raise raw
                replies[agent_id] = raw
                content = raw.content
                kind = "proposal"
            except Exception as exc:
                content = self._error_text(exc)
                kind = "error"
                if first_error is None:
                    first_error = exc
            entry = record(
                sender=agent_id,
                phase=Phase.PROPOSE_REVISION.value,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=content,
            )
            entries.append(entry)
            self._remember(
                agent_id=agent_id,
                session=session,
                debate=debate,
                phase=Phase.PROPOSE_REVISION,
                round_number=round_number,
                revision=revision_id,
                kind=kind,
                content=(
                    content
                    if kind == "proposal"
                    else "backend outcome failed validation or execution"
                ),
            )
        return replies, first_error

    def _request(
        self,
        *,
        session: str,
        debate: str,
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
        """Build one agent request and mark included-context propagation errors.

        Prompt and state reads remain owned by ``PromptAssembler``. Validation
        failures caused while an included snapshot is attached receive a fixed
        internal stage marker for trusted failure classification; configuration
        and workspace exceptions retain their original public type.
        """

        try:
            return self.prompts.build_request(
                session_id=session,
                debate_id=debate,
                agent_id=agent_id,
                phase=phase,
                round_number=round_number,
                revision_id=revision_id,
                task=task,
                user_payload=user_payload,
                candidate_payload=candidate_payload,
                messages=messages,
                conversation_context=conversation_context,
            )
        except (TypeError, ValueError) as exc:
            if conversation_context is not None:
                raise _DebateContextPropagationError(
                    "conversation context propagation failed"
                ) from exc
            raise

    def _remember(
        self,
        *,
        agent_id: str,
        session: str,
        debate: str,
        phase: Phase,
        round_number: int,
        revision: str,
        kind: str,
        content: str,
    ) -> None:
        maximum = 4096
        bounded = (
            content
            if len(content) <= maximum
            else content[: maximum - 16] + " …[truncated]"
        )
        self.workspace.append_agent_state(
            AgentStateEntry(
                agent_id=agent_id,
                session_id=session,
                debate_id=debate,
                phase=phase.value,
                round=round_number,
                revision=revision,
                kind=kind,
                content=bounded,
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

    def _conversation_store_method(self, name: str) -> Callable[..., Any]:
        """Return one declared-synchronous store callable without invoking it.

        Coroutine and async-generator declarations are rejected here; dynamic
        awaitable or async-generator results are rejected after one invocation.
        """

        method = getattr(self.conversation_event_store, name, None)
        if method is None or not callable(method):
            raise BackendProtocolError(
                f"conversation event store does not provide {name}()"
            )
        method_call = getattr(method, "__call__", None)
        if (
            inspect.iscoroutinefunction(method)
            or inspect.isasyncgenfunction(method)
            or inspect.iscoroutinefunction(method_call)
            or inspect.isasyncgenfunction(method_call)
        ):
            raise BackendProtocolError(
                f"conversation event store {name}() must be synchronous"
            )
        return method

    def _prepare_conversation_session(self, session_id: str) -> None:
        assert self.backend is not None
        prepare_session = getattr(self.backend, "prepare_session", None)
        if prepare_session is None:
            return
        if not callable(prepare_session):
            raise BackendProtocolError("backend prepare_session must be callable")
        if inspect.iscoroutinefunction(prepare_session) or inspect.iscoroutinefunction(
            getattr(prepare_session, "__call__", None)
        ):
            raise BackendProtocolError("backend prepare_session must be synchronous")
        outcome = prepare_session(session_id)
        self._reject_async_result(outcome, operation="backend prepare_session")

    def _prepare_debate_session(self, session_id: str) -> None:
        """Synchronously prepare a validated debate session without provider work.

        An absent compatibility hook is accepted. A non-callable, declared
        asynchronous, awaitable- or async-generator-returning, or failing hook
        raises before reservation, sidecar, progress, or provider mutation.
        """

        assert self.backend is not None
        prepare_session = getattr(self.backend, "prepare_session", None)
        if prepare_session is None:
            return
        if not callable(prepare_session):
            raise BackendProtocolError("backend prepare_session must be callable")
        prepare_call = getattr(prepare_session, "__call__", None)
        if (
            inspect.iscoroutinefunction(prepare_session)
            or inspect.isasyncgenfunction(prepare_session)
            or inspect.iscoroutinefunction(prepare_call)
            or inspect.isasyncgenfunction(prepare_call)
        ):
            raise BackendProtocolError("backend prepare_session must be synchronous")
        outcome = prepare_session(session_id)
        self._reject_async_result(outcome, operation="backend prepare_session")

    @staticmethod
    def _validate_debate_context_request(
        request: DebateContextRequest | None,
    ) -> DebateContextRequest | None:
        """Return a canonical optional context request without reading storage."""

        if request is None:
            return None
        if type(request) is not DebateContextRequest:
            raise TypeError(
                "conversation_context must be a DebateContextRequest or None"
            )
        try:
            return DebateContextRequest(
                conversation_id=request.conversation_id,
                mode=request.mode,
            )
        except AttributeError as exc:
            raise TypeError("conversation_context is invalid") from exc

    @staticmethod
    def _validate_progress_sink(
        sink: DebateProgressSink | None,
    ) -> DebateProgressSink | None:
        """Return a declared-synchronous optional sink without invoking it.

        ``None`` and ordinary callables are accepted without mutation. A
        non-callable, coroutine or async-generator function, or callable object
        with a coroutine or async-generator ``__call__`` raises ``TypeError``
        before debate preparation or durability.
        """

        if sink is None:
            return None
        if not callable(sink):
            raise TypeError("progress_sink must be callable or None")
        sink_call = getattr(sink, "__call__", None)
        if (
            inspect.iscoroutinefunction(sink)
            or inspect.isasyncgenfunction(sink)
            or inspect.iscoroutinefunction(sink_call)
            or inspect.isasyncgenfunction(sink_call)
        ):
            raise TypeError("progress_sink must be synchronous")
        return sink

    @staticmethod
    def _reject_async_result(value: Any, *, operation: str) -> None:
        """Reject awaitable or async-generator results without running them."""

        if not inspect.isawaitable(value) and not inspect.isasyncgen(value):
            return
        if inspect.iscoroutine(value):
            value.close()
        raise BackendProtocolError(f"{operation}() must be synchronous")

    @staticmethod
    def _append_conversation_event(
        append: Callable[[ConversationEventAppend], ConversationEvent],
        requested: ConversationEventAppend,
    ) -> ConversationEvent:
        stored = append(requested)
        StoryRuntime._reject_async_result(stored, operation="append")
        if type(stored) is not ConversationEvent:
            raise BackendProtocolError(
                "conversation event store append() returned an invalid event"
            )
        try:
            canonical = ConversationEvent(
                schema_version=stored.schema_version,
                session_id=stored.session_id,
                conversation_id=stored.conversation_id,
                turn_id=stored.turn_id,
                sequence=stored.sequence,
                timestamp=stored.timestamp,
                sender=stored.sender,
                recipient=stored.recipient,
                kind=stored.kind,
                content=stored.content,
            )
        except Exception as exc:
            raise BackendProtocolError(
                "conversation event store append() returned an invalid event"
            ) from exc
        caller_fields = (
            "session_id",
            "conversation_id",
            "turn_id",
            "sender",
            "recipient",
            "kind",
            "content",
        )
        if any(
            getattr(canonical, field) != getattr(requested, field)
            for field in caller_fields
        ):
            raise BackendProtocolError(
                "conversation event store append() changed caller-owned fields"
            )
        return canonical

    @staticmethod
    def _validated_conversation_reply(
        reply: Any,
        request: ConversationRequest,
    ) -> ConversationReply:
        if type(reply) is not ConversationReply:
            raise BackendProtocolError(
                f"{request.agent_id} returned {type(reply).__name__}, "
                "expected ConversationReply"
            )
        try:
            canonical = ConversationReply(
                session_id=reply.session_id,
                conversation_id=reply.conversation_id,
                turn_id=reply.turn_id,
                content=reply.content,
            )
        except Exception as exc:
            raise BackendProtocolError("conversation reply is invalid") from exc
        for field in ("session_id", "conversation_id", "turn_id"):
            if getattr(canonical, field) != getattr(request, field):
                raise BackendProtocolError(
                    f"conversation reply {field} does not match its request"
                )
        return canonical

    def _conversation_error_text(
        self,
        exc: Exception,
        request: ConversationRequest,
    ) -> str:
        exception_name = type(exc).__name__
        replacements: dict[str, str] = {}
        normalized_replacements: dict[str, str] = {}
        sensitive_values = (
            request.content,
            *(event.content for event in request.context.events),
        )
        for value in sensitive_values:
            for variant in (
                value,
                json.dumps(value, ensure_ascii=False)[1:-1],
                json.dumps(value, ensure_ascii=True)[1:-1],
            ):
                if variant:
                    replacements.setdefault(variant, "<redacted>")
                normalized = self._normalize_conversation_diagnostic(variant)
                if normalized:
                    normalized_replacements.setdefault(normalized, "<redacted>")
        for root in self._conversation_workspace_roots:
            if root:
                replacements.setdefault(root, "<workspace>")
            normalized_root = self._normalize_conversation_diagnostic(root)
            if normalized_root:
                normalized_replacements.setdefault(normalized_root, "<workspace>")

        try:
            detail = str(exc)
        except Exception:
            detail = "unavailable diagnostic"
            include_detail = True
        else:
            include_detail = bool(
                self._sanitize_conversation_diagnostic(
                    detail,
                    replacements=replacements,
                    normalized_replacements=normalized_replacements,
                )
            )
        raw_complete = (
            f"{exception_name}: {detail}" if include_detail else exception_name
        )
        complete = self._sanitize_conversation_diagnostic(
            raw_complete,
            replacements=replacements,
            normalized_replacements=normalized_replacements,
        )
        try:
            complete.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            safe_name = self._sanitize_conversation_diagnostic(
                exception_name,
                replacements=replacements,
                normalized_replacements=normalized_replacements,
            )
            try:
                safe_name.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                safe_name = "Exception"
            complete = self._sanitize_conversation_diagnostic(
                f"{safe_name or 'Exception'}: unavailable diagnostic",
                replacements=replacements,
                normalized_replacements=normalized_replacements,
            )
        if not complete:
            complete = "Exception"
        return self._bounded_conversation_error(complete)

    @staticmethod
    def _sanitize_conversation_diagnostic(
        value: str,
        *,
        replacements: Mapping[str, str],
        normalized_replacements: Mapping[str, str],
    ) -> str:
        for sensitive, replacement in sorted(
            replacements.items(),
            key=lambda item: (-len(item[0]), item[1] != "<redacted>", item[0]),
        ):
            value = value.replace(sensitive, replacement)
        value = StoryRuntime._normalize_conversation_diagnostic(value)
        for sensitive, replacement in sorted(
            normalized_replacements.items(),
            key=lambda item: (-len(item[0]), item[1] != "<redacted>", item[0]),
        ):
            value = value.replace(sensitive, replacement)
        return value

    @staticmethod
    def _normalize_conversation_diagnostic(value: str) -> str:
        normalized = "".join(
            " " if unicodedata.category(character) in {"Cc", "Cf"} else character
            for character in value
        )
        return " ".join(normalized.split())

    @staticmethod
    def _bounded_conversation_error(value: str) -> str:
        maximum = 4096
        suffix = " …[truncated]"
        if len(value) <= maximum:
            return value
        return value[: maximum - len(suffix)] + suffix

    @staticmethod
    def _validate_conversation_text(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("conversation text must be a string")
        if not value:
            raise ValueError("conversation text must not be empty")
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("conversation text must be valid UTF-8") from exc
        return value

    @staticmethod
    def _new_conversation_turn_id() -> str:
        return f"t_{uuid.uuid4().hex}"

    async def _invoke(self, request: AgentRequest) -> AgentReply:
        definition = self.prompts.build_definition(
            session_id=request.session_id,
            agent_id=request.agent_id,
        )
        bound_agent = await self._bound_agent(definition)
        reply = await bound_agent.invoke(request)
        if not isinstance(reply, AgentReply):
            raise BackendProtocolError(
                f"{request.agent_id} returned {type(reply).__name__}, "
                "expected AgentReply"
            )
        self._validate_reply_fields(reply)
        return reply

    async def _bound_agent(self, definition: AgentDefinition) -> BoundAgent:
        """Reuse or single-flight one immutable provider-side binding."""

        key = (definition.agent_id, definition.fingerprint)
        async with self._binding_lock:
            cached = self._bound_agents.get(key)
            if cached is not None:
                return cached
            task = self._binding_tasks.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._bind_definition(definition),
                    name=f"bind:{definition.agent_id}:{definition.fingerprint[:12]}",
                )
                self._binding_tasks[key] = task
            self._binding_waiters[key] = self._binding_waiters.get(key, 0) + 1

        try:
            # A debate or peer invocation may be cancelled without cancelling
            # a binding still awaited by another invocation.
            bound_agent = await asyncio.shield(task)
            async with self._binding_lock:
                if (
                    self._binding_tasks.get(key) is task
                    and self._binding_waiters.get(key, 0) > 0
                ):
                    self._bound_agents[key] = bound_agent
            return bound_agent
        finally:
            task_to_join: asyncio.Task[BoundAgent] | None = None
            async with self._binding_lock:
                remaining = self._binding_waiters.get(key, 0) - 1
                if remaining > 0:
                    self._binding_waiters[key] = remaining
                else:
                    self._binding_waiters.pop(key, None)
                    if self._binding_tasks.get(key) is task:
                        self._binding_tasks.pop(key, None)
                        if not task.done():
                            task.cancel()
                        task_to_join = task
            if task_to_join is not None:
                await asyncio.gather(task_to_join, return_exceptions=True)

    async def _bind_definition(
        self,
        definition: AgentDefinition,
    ) -> BoundAgent:
        assert self.backend is not None
        bind = getattr(self.backend, "bind", None)
        if bind is None or not callable(bind):
            raise BackendProtocolError(
                "backend does not provide bind()"
            )
        if not inspect.iscoroutinefunction(bind):
            raise BackendProtocolError("backend bind() must be async")

        self._verify_definition_files(definition)
        bound_agent = await bind(definition)
        invoke = getattr(bound_agent, "invoke", None)
        if invoke is None or not callable(invoke):
            raise BackendProtocolError(
                f"{definition.agent_id} binding does not provide invoke()"
            )
        if not inspect.iscoroutinefunction(invoke):
            raise BackendProtocolError(
                f"{definition.agent_id} bound invoke() must be async"
            )
        self._verify_definition_files(definition)
        return bound_agent

    @staticmethod
    def _verify_definition_files(definition: AgentDefinition) -> None:
        for reference in definition.file_references():
            reference.read_text()

    async def _invoke_parallel(
        self,
        requests: list[AgentRequest],
        validator: Callable[[AgentReply, str], None],
        revision_id: str,
    ) -> tuple[AgentReply | Exception, ...]:
        """Invoke one phase concurrently and close every peer on first failure."""

        async def invoke_and_validate(request: AgentRequest) -> AgentReply:
            reply = await self._invoke(request)
            validator(reply, revision_id)
            return reply

        tasks = [
            asyncio.create_task(
                invoke_and_validate(request),
                name=f"{request.phase.value}:{request.agent_id}",
            )
            for request in requests
        ]
        pending: set[asyncio.Task[AgentReply]] = set(tasks)
        peer_failure = False
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if any(
                    task.cancelled()
                    or (
                        task.exception() is not None
                        and isinstance(task.exception(), Exception)
                    )
                    for task in done
                ):
                    peer_failure = True
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    break
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        outcomes: list[AgentReply | Exception] = []
        for request, task in zip(requests, tasks, strict=True):
            if task.cancelled():
                detail = (
                    "cancelled after peer failure"
                    if peer_failure
                    else "backend call cancelled"
                )
                outcomes.append(
                    BackendProtocolError(f"{request.agent_id} {detail}")
                )
                continue
            try:
                outcomes.append(task.result())
            except Exception as exc:
                outcomes.append(exc)
        return tuple(outcomes)

    @staticmethod
    def _validate_reply_fields(reply: AgentReply) -> None:
        if not isinstance(reply.revision_id, str):
            raise BackendProtocolError("reply revision_id must be a string")
        if not isinstance(reply.content, str):
            raise BackendProtocolError("reply content must be a string")
        if reply.verdict is not None and not isinstance(reply.verdict, Verdict):
            raise BackendProtocolError("reply verdict must be Verdict or None")
        if not isinstance(reply.core_updates, Mapping):
            raise BackendProtocolError("reply core_updates must be a mapping")
        if any(
            not isinstance(name, str) or not isinstance(content, str)
            for name, content in reply.core_updates.items()
        ):
            raise BackendProtocolError(
                "reply core_updates must map string filenames to string content"
            )

    @staticmethod
    def _validate_candidate_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None:
            raise BackendProtocolError("candidate reply must not include a verdict")
        if not reply.content:
            raise BackendProtocolError("candidate reply must contain text")
        if reply.core_updates:
            raise BackendProtocolError(
                "core updates are allowed only during finalization"
            )

    @staticmethod
    def _validate_audit_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict not in (Verdict.APPROVE, Verdict.CONFLICT):
            raise BackendProtocolError("audit reply requires APPROVE or CONFLICT")
        if reply.core_updates:
            raise BackendProtocolError(
                "specialists cannot write or propose core updates"
            )
        if reply.verdict is Verdict.CONFLICT and not reply.content:
            raise BackendProtocolError("conflict verdict requires a critique")

    @staticmethod
    def _validate_proposal_reply(reply: AgentReply, revision_id: str) -> None:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None or reply.core_updates:
            raise BackendProtocolError(
                "revision proposal must contain only proposal text"
            )
        if not reply.content:
            raise BackendProtocolError("revision proposal must contain text")

    @staticmethod
    def _validate_finalization_reply(
        reply: AgentReply,
        revision_id: str,
        approved_candidate: str,
    ) -> dict[str, str]:
        StoryRuntime._validate_revision(reply, revision_id)
        if reply.verdict is not None:
            raise BackendProtocolError(
                "finalization reply must not include a verdict"
            )
        if reply.content and reply.content != approved_candidate:
            raise BackendProtocolError(
                "orchestrator cannot alter the candidate after final approval"
            )
        return dict(reply.core_updates)

    @staticmethod
    def _validate_revision(reply: AgentReply, revision_id: str) -> None:
        if reply.revision_id != revision_id:
            raise BackendProtocolError(
                f"reply targets {reply.revision_id!r}, expected {revision_id!r}"
            )

    def _finish(
        self,
        *,
        session: str,
        debate: str,
        reservation_token: str,
        input_kind: InputKind,
        status: DebateStatus,
        revision_id: str | None,
        rounds_completed: int,
        final_candidate: str | None,
        latest_candidate: str | None,
        manuscript_path: str | None,
        messages: list[DebateMessage],
        progress: _ProgressEmitter,
        core_updates: tuple[str, ...] = (),
        error: str | None = None,
        action_required: str | None = None,
        failure_kind: DebateFailureKind | None = None,
    ) -> DebateResult:
        """Persist one terminal result/state before emitting terminal progress.

        The supplied result fields retain the established ``DebateResult`` JSON.
        Result creation, create-only result persistence, and terminal state
        transition complete before the emitter is marked durable. Consensus emits
        committed artifacts then consensus; deadlock and handled failure emit one
        typed fact. Persistence and observer failures propagate without rewriting
        a successfully terminal state.
        """

        if status is DebateStatus.FAILED:
            if failure_kind is None:
                raise ValueError("failed debate completion requires a failure kind")
        elif failure_kind is not None:
            raise ValueError("failure kind is allowed only for failed completion")
        result = DebateResult(
            session_id=session,
            debate_id=debate,
            input_kind=input_kind,
            status=status,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            final_candidate=final_candidate,
            latest_candidate=latest_candidate,
            manuscript_path=manuscript_path,
            core_updates=core_updates,
            messages=tuple(messages),
            error=error,
            action_required=action_required,
        )
        self.workspace.save_debate_result(result)
        self.workspace.transition_debate(
            session,
            debate,
            owner_token=reservation_token,
            status=status.value,
            terminal=True,
            revision_id=revision_id,
            rounds_completed=rounds_completed,
            error=error,
        )
        progress.terminal_durable = True
        if status is DebateStatus.CONSENSUS:
            assert revision_id is not None
            assert manuscript_path is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.ARTIFACTS_COMMITTED,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                    manuscript_path=manuscript_path,
                    core_updates=core_updates,
                )
            )
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.CONSENSUS,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                )
            )
        elif status is DebateStatus.DEADLOCK:
            assert revision_id is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.DEADLOCK,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                )
            )
        else:
            assert status is DebateStatus.FAILED
            assert failure_kind is not None
            progress.emit(
                DebateProgressEvent(
                    session_id=session,
                    debate_id=debate,
                    input_kind=input_kind,
                    phase=DebateProgressPhase.FAILED,
                    revision_id=revision_id,
                    round_number=rounds_completed,
                    failure_kind=failure_kind,
                )
            )
        return result

    @staticmethod
    def _classify_debate_failure(
        failure: Exception,
        *,
        artifact_commit: bool = False,
        context_propagation: bool = False,
    ) -> DebateFailureKind:
        """Classify a handled failure by trusted type and execution stage only."""

        if context_propagation:
            return DebateFailureKind.CONTEXT
        if isinstance(failure, _DebateContextPropagationError):
            return DebateFailureKind.CONTEXT
        if artifact_commit:
            if isinstance(failure, ManuscriptExistsError):
                return DebateFailureKind.ARTIFACT_CONFLICT
            return DebateFailureKind.ARTIFACT_FAILURE
        if isinstance(failure, ConfigurationError):
            return DebateFailureKind.PREFLIGHT
        if isinstance(failure, BackendProtocolError):
            return DebateFailureKind.PROTOCOL
        if isinstance(
            failure,
            (WorkspaceSecurityError, WorkspaceFormatError, OSError),
        ):
            return DebateFailureKind.WORKSPACE
        return DebateFailureKind.INTERNAL

    @staticmethod
    def _new_debate_id() -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        return f"d{timestamp}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


class RuntimeSession:
    """Bound-session convenience API; state remains filesystem-backed."""

    def __init__(self, runtime: StoryRuntime, session_id: str) -> None:
        self.runtime = runtime
        self.session_id = session_id

    def apply_directive(self, text: str) -> dict[str, object]:
        return self.runtime.apply_directive(self.session_id, text)

    def set_overrides(self, overrides: Mapping[str, Any]) -> dict[str, Any]:
        return self.runtime.set_runtime_overrides(self.session_id, overrides)

    def inspect(self) -> dict[str, Any]:
        return self.runtime.inspect_session(self.session_id)

    def inspect_debate(self, debate_id: str) -> dict[str, Any]:
        return self.runtime.inspect_debate(self.session_id, debate_id)

    def inspect_debate_context(self, debate_id: str) -> dict[str, Any] | None:
        """Return this session's strict context sidecar without mutation."""

        return self.runtime.inspect_debate_context(self.session_id, debate_id)

    async def converse(
        self,
        conversation_id: str,
        agent_id: str,
        text: str,
    ) -> ConversationResult:
        return await self.runtime.converse(
            self.session_id,
            conversation_id,
            agent_id,
            text,
        )

    async def run_prompt(
        self,
        prompt: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Delegate a prompt debate with optional context and progress reporting.

        The bound session ID is supplied without local mutation; durability and
        failure behavior are exactly those of ``StoryRuntime.run_prompt``.
        """

        return await self.runtime.run_prompt(
            self.session_id,
            prompt,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )

    async def run_draft(
        self,
        draft: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
        conversation_context: DebateContextRequest | None = None,
        progress_sink: DebateProgressSink | None = None,
    ) -> DebateResult:
        """Delegate a draft debate with optional context and progress reporting.

        The bound session ID is supplied without local mutation; durability and
        failure behavior are exactly those of ``StoryRuntime.run_draft``.
        """

        return await self.runtime.run_draft(
            self.session_id,
            draft,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
            conversation_context=conversation_context,
            progress_sink=progress_sink,
        )


Runtime = StoryRuntime
Session = RuntimeSession
