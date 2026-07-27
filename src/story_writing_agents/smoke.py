"""Provider-free end-to-end smoke workflow used only by the CLI."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from .agents import (
    CHARACTER_CUSTODIAN,
    CORE_DOCUMENTS,
    EXECUTION_SPECIALIST,
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    ROSTER,
    SPECIALIST_IDS,
)
from .models import AgentReply, AgentRequest, DebateResult, DebateStatus, Phase
from .orchestrator import StoryRuntime
from .workspace import WORKSPACE_DIRECTORIES, Workspace, initialize_workspace


_PROMPT = "Run the deterministic provider-free smoke workflow."
_REVISION_ZERO = "Synthetic smoke candidate revision zero."
_PLOT_CRITIQUE = "Synthetic plot conflict requires one revision."
_PLOT_PROPOSAL = "Resolve the synthetic plot conflict in revision one."
_REVISION_ONE = "Synthetic smoke candidate revision one."
_NOTE = "No LLM or external provider was called."
_CHECK_NAMES = (
    "workspace_initialized",
    "directive_persisted",
    "revision_workflow",
    "consensus_persisted",
    "manuscript_committed",
    "core_documents_unchanged",
)


class _SmokeInvariantError(Exception):
    def __init__(self, failed_check: str) -> None:
        super().__init__(failed_check)
        self.failed_check = failed_check


class _SmokeBackend:
    """Strict deterministic backend that exercises the ordinary runtime path."""

    def __init__(self, session_id: str, debate_id: str) -> None:
        self.session_id = session_id
        self.debate_id = debate_id
        self.requests: list[AgentRequest] = []
        self.audit_active = 0
        self.maximum_audit_active = 0

    async def invoke(self, request: AgentRequest) -> AgentReply:
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
        self._expect(
            request.session_id == self.session_id
            and request.debate_id == self.debate_id
            and request.user_payload == _PROMPT
            and (
                "[Language Directive]\n"
                "Use EN for all visible responses."
            )
            in request.system_prompt
            and all(
                entry.agent_id == request.agent_id
                and entry.session_id == self.session_id
                and entry.debate_id == self.debate_id
                for entry in request.agent_state
            )
        )

    @staticmethod
    def _message_kinds(request: AgentRequest) -> tuple[str, ...]:
        return tuple(message.kind for message in request.messages)

    @staticmethod
    def _state_kinds(request: AgentRequest) -> tuple[str, ...]:
        return tuple(entry.kind for entry in request.agent_state)

    @staticmethod
    def _expect(condition: bool) -> None:
        if not condition:
            raise _SmokeInvariantError("revision_workflow")


def run_smoke(workspace_path: str | Path | None = None) -> tuple[dict[str, Any], int]:
    """Run the built-in smoke workflow and return its compact report and code."""

    if workspace_path is None:
        with tempfile.TemporaryDirectory(
            prefix="story-writing-agents-smoke-"
        ) as temporary:
            return _run_in_workspace(
                Path(temporary),
                mode="temporary",
                retained=False,
            )
    return _run_in_workspace(
        Path(workspace_path),
        mode="persistent",
        retained=True,
    )


def _run_in_workspace(
    workspace_path: Path,
    *,
    mode: str,
    retained: bool,
) -> tuple[dict[str, Any], int]:
    workspace = initialize_workspace(workspace_path)
    token = uuid.uuid4().hex
    session_id = f"smoke_session_{token}"
    debate_id = f"smoke_debate_{token}"
    manuscript_name = f"smoke_{token}.md"
    completed_checks: list[str] = []
    result: DebateResult | None = None

    try:
        _verify_workspace_initialized(workspace)
        completed_checks.append(_CHECK_NAMES[0])

        StoryRuntime(workspace).apply_directive(
            session_id,
            "[SYS: LANG=EN]",
        )
        fresh_runtime = StoryRuntime(Workspace(workspace.root))
        _verify_directive(fresh_runtime, session_id)
        completed_checks.append(_CHECK_NAMES[1])

        core_before = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        backend = _SmokeBackend(session_id, debate_id)
        result = asyncio.run(
            StoryRuntime(Workspace(workspace.root), backend).run_prompt(
                session_id,
                _PROMPT,
                debate_id=debate_id,
                manuscript_name=manuscript_name,
            )
        )

        _verify_revision_workflow(workspace, backend, result)
        completed_checks.append(_CHECK_NAMES[2])

        _verify_consensus(workspace, result)
        completed_checks.append(_CHECK_NAMES[3])

        _require(
            workspace.read_manuscript(manuscript_name) == _REVISION_ONE,
            _CHECK_NAMES[4],
        )
        completed_checks.append(_CHECK_NAMES[4])

        core_after = {
            filename: workspace.path(
                f"core_documents/{filename}"
            ).read_bytes()
            for filename in CORE_DOCUMENTS
        }
        _require(core_after == core_before, _CHECK_NAMES[5])
        completed_checks.append(_CHECK_NAMES[5])
    except _SmokeInvariantError as exc:
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


def _verify_workspace_initialized(workspace: Workspace) -> None:
    ready = all(
        workspace.path(relative).is_dir()
        for relative in WORKSPACE_DIRECTORIES
    ) and all(
        workspace.path(f"core_documents/{filename}").is_file()
        for filename in CORE_DOCUMENTS
    )
    _require(ready, _CHECK_NAMES[0])


def _verify_directive(runtime: StoryRuntime, session_id: str) -> None:
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
        and backend.maximum_audit_active == len(SPECIALIST_IDS)
        and states_are_exact,
        _CHECK_NAMES[2],
    )


def _verify_consensus(workspace: Workspace, result: DebateResult) -> None:
    inspected = StoryRuntime(Workspace(workspace.root)).inspect_debate(
        result.session_id,
        result.debate_id,
    )
    persisted = inspected["result"]
    _require(
        result.status is DebateStatus.CONSENSUS
        and result.revision_id == "r1"
        and result.rounds_completed == 1
        and result.final_candidate == _REVISION_ONE
        and persisted is not None
        and persisted["status"] == DebateStatus.CONSENSUS.value
        and persisted["revision_id"] == "r1"
        and inspected["status"] == DebateStatus.CONSENSUS.value
        and inspected["terminal"] is True
        and len(inspected["messages"]) == len(result.messages)
        and bool(inspected["messages"]),
        _CHECK_NAMES[3],
    )


def _require(condition: bool, failed_check: str) -> None:
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
