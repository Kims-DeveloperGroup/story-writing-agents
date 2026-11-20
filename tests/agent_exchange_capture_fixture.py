"""Shared in-memory fixtures for provider exchange capture tests."""
from __future__ import annotations

import json
import re
import tempfile
import threading
from unittest import mock

from story_writing_agents.agent_context import ProviderCallKind, ProviderUsage
from story_writing_agents.agent_exchanges import AgentExchangeProvider, AgentExchangeRecordInput, AgentExchangeScopeKind
from story_writing_agents.agent_exchanges import capture as capture_module
from story_writing_agents.agent_exchanges.capture import AgentExchangeCaptureLifecycle, WorkspaceAgentExchangeCapture
from story_writing_agents.agents import ROSTER
from story_writing_agents.provider_adapter import ProviderInvocation, ProviderResult
from story_writing_agents.workspace import Workspace


_EXCHANGE_ID = re.compile(r"exchange_[0-9a-f]{32}")


class _RecordingStore:
    """Minimal append seam with deterministic per-attempt failures."""

    def __init__(self) -> None:
        self.attempts: list[AgentExchangeRecordInput] = []
        self.records: list[AgentExchangeRecordInput] = []
        self.failures: dict[int, BaseException] = {}
        self._lock = threading.Lock()

    def append(self, record: AgentExchangeRecordInput) -> object:
        with self._lock:
            self.attempts.append(record)
            attempt = len(self.attempts)
            failure = self.failures.get(attempt)
            if failure is not None:
                raise failure
            self.records.append(record)
        return object()


def _invocation(
    *,
    payload: object | None = None,
    call_kind: ProviderCallKind = ProviderCallKind.DEBATE,
    thread_id: str | None = "prior_thread",
) -> ProviderInvocation:
    return ProviderInvocation(
        session_id="session_1",
        schema={"required": ["answer"], "type": "object"},
        payload=(
            {"answer": "PRIVATE_REQUEST_한🙂", "enabled": True}
            if payload is None
            else payload  # type: ignore[arg-type]
        ),
        thread_id=thread_id,
        call_kind=call_kind,)


def _result(*, usage: ProviderUsage | None = None) -> ProviderResult:
    return ProviderResult(
        thread_id="result_thread",
        response_text="PRIVATE_RESPONSE_한🙂",
        usage=usage,
        actual_transport_utf8_bytes=777,)


def _capture(store: _RecordingStore) -> WorkspaceAgentExchangeCapture:
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Workspace(temporary)
        with mock.patch.object(
            capture_module,
            "WorkspaceAgentExchangeStore",
            return_value=store,
        ):
            return WorkspaceAgentExchangeCapture(workspace)


def _begin(
    store: _RecordingStore,
    *,
    invocation: ProviderInvocation | None = None,
    diagnostic_call_id: str | None = None,
) -> AgentExchangeCaptureLifecycle:
    return _capture(store).begin(
        session_id="session_1",
        scope_kind=AgentExchangeScopeKind.DEBATE,
        scope_id="debate_1",
        agent_id=ROSTER[0],
        provider=AgentExchangeProvider.CODEX,
        invocation=invocation or _invocation(),
        diagnostic_call_id=diagnostic_call_id,
    )


def _decoded(records: list[AgentExchangeRecordInput]) -> list[object]:
    return [json.loads(record.content) for record in records]


def _started_capture(**options):
    store = _RecordingStore()
    return store, _begin(store, **options)
