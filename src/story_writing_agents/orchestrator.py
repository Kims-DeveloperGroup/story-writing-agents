"""Provider-neutral asynchronous round-table orchestration."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .agents import (
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    SPECIALIST_IDS,
)
from .backend import AgentBackend
from .config import ConfigManager
from .directives import apply_directive as persist_directive
from .errors import BackendProtocolError
from .models import (
    AgentReply,
    AgentRequest,
    AgentStateEntry,
    DebateMessage,
    DebateResult,
    DebateStatus,
    InputKind,
    Phase,
    Verdict,
)
from .prompts import PromptAssembler
from .workspace import Workspace


class StoryRuntime:
    """Public runtime for persistent sessions and typed debate outcomes."""

    def __init__(
        self,
        workspace: Workspace | str | Path,
        backend: AgentBackend | None = None,
    ) -> None:
        self.workspace = (
            workspace if isinstance(workspace, Workspace) else Workspace(workspace)
        )
        self.backend = backend
        self.prompts = PromptAssembler(self.workspace)
        self.config = ConfigManager(self.workspace)

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

    async def run_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
    ) -> DebateResult:
        return await self._run(
            session_id=session_id,
            payload=prompt,
            input_kind=InputKind.PROMPT,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
        )

    async def run_draft(
        self,
        session_id: str,
        draft: str,
        *,
        manuscript_name: str | None = None,
        max_debate_rounds: int | None = None,
        debate_id: str | None = None,
    ) -> DebateResult:
        return await self._run(
            session_id=session_id,
            payload=draft,
            input_kind=InputKind.DRAFT,
            manuscript_name=manuscript_name,
            max_debate_rounds=max_debate_rounds,
            debate_id=debate_id,
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
    ) -> DebateResult:
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

        target_name = manuscript_name or f"{session}_{debate}.md"
        reservation_token = uuid.uuid4().hex
        self.workspace.reserve_debate(
            session,
            debate,
            input_kind=input_kind.value,
            owner_token=reservation_token,
        )
        execution = asyncio.create_task(
            self._run_reserved(
                session=session,
                debate=debate,
                payload=payload,
                input_kind=input_kind,
                target_name=target_name,
                round_limit=round_limit,
                reservation_token=reservation_token,
            ),
            name=f"debate:{session}:{debate}",
        )
        try:
            return await execution
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            try:
                self.workspace.transition_debate(
                    session,
                    debate,
                    owner_token=reservation_token,
                    status="interrupted",
                    terminal=True,
                    revision_id=None,
                    rounds_completed=0,
                    error="CancelledError: debate execution was cancelled",
                )
            except Exception:
                # Cancellation must retain its external control-flow semantics.
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
    ) -> DebateResult:
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

        while True:
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
                    error=audit_error,
                )

            conflicts = [
                agent_id
                for agent_id in SPECIALIST_IDS
                if audit_replies[agent_id].verdict is Verdict.CONFLICT
            ]
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
                    error=proposal_error,
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
    ) -> tuple[dict[str, AgentReply], str | None]:
        requests = [
            self._request(
                session=session,
                debate=debate,
                agent_id=agent_id,
                phase=Phase.AUDIT,
                round_number=round_number,
                revision_id=revision_id,
                task=(
                    "Independently audit the current candidate for objective domain "
                    "compliance. Return APPROVE with an optional concise note, or "
                    "CONFLICT with a concise actionable critique. Do not rewrite."
                ),
                user_payload=payload,
                candidate_payload=candidate,
                messages=visible_messages,
            )
            for agent_id in SPECIALIST_IDS
        ]
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_audit_reply,
            revision_id,
        )
        replies: dict[str, AgentReply] = {}
        first_error: str | None = None
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
                    first_error = content
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
    ) -> tuple[dict[str, AgentReply], str | None]:
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
                    "visible critiques in your responsible domain. The orchestrator "
                    "will synthesize the complete candidate."
                ),
                user_payload=payload,
                candidate_payload=candidate,
                messages=visible_messages,
            )
            for agent_id in responsible_agents
        ]
        raw_replies = await self._invoke_parallel(
            requests,
            self._validate_proposal_reply,
            revision_id,
        )
        replies: dict[str, AgentReply] = {}
        first_error: str | None = None
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
                    first_error = content
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
    ) -> AgentRequest:
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
        )

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

    async def _invoke(self, request: AgentRequest) -> AgentReply:
        assert self.backend is not None
        reply = await self.backend.invoke(request)
        if not isinstance(reply, AgentReply):
            raise BackendProtocolError(
                f"{request.agent_id} returned {type(reply).__name__}, "
                "expected AgentReply"
            )
        self._validate_reply_fields(reply)
        return reply

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
        core_updates: tuple[str, ...] = (),
        error: str | None = None,
        action_required: str | None = None,
    ) -> DebateResult:
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
        return result

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

    async def run_prompt(self, prompt: str, **options: Any) -> DebateResult:
        return await self.runtime.run_prompt(
            self.session_id,
            prompt,
            **options,
        )

    async def run_draft(self, draft: str, **options: Any) -> DebateResult:
        return await self.runtime.run_draft(
            self.session_id,
            draft,
            **options,
        )


Runtime = StoryRuntime
Session = RuntimeSession
