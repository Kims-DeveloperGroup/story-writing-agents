"""Own agent state."""

from __future__ import annotations

from ..actions import ContextSourceBoundary
from ..manifest import ContextAuthorityKind, ContextProvenance, ContextScope, ContextSourceKind
from .capture_values import (
    ContextSourceDescriptor,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceRecord,
    ContextSourceSnapshot,
    require_source_policy,
    source_canonical_json_bytes,
    source_descriptor,
    source_digest,
    source_error,
    source_record,
    source_structured_source_id,
)
from ...errors import StoryWritingAgentsError
from ...models import AgentStateEntry
from ...workspace import Workspace


class OwnAgentStateSourceAdapter:
    """Capture only the invoking agent's complete ordered session state."""

    source_kind = ContextSourceKind.OWN_AGENT_STATE

    def __init__(self, workspace: Workspace, scope: ContextScope) -> None:
        if not isinstance(workspace, Workspace) or type(scope) is not ContextScope:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        self._workspace = workspace
        self.scope = scope

    def _locator(self) -> str:
        return f"agent-state/{self.scope.agent_id}"

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = require_source_policy(policy)
        return (self.capture(self._locator(), policy).descriptor,)

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        if locator != self._locator():
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        entries: tuple[AgentStateEntry, ...] | None = None
        failed = False
        try:
            entries = self._workspace.read_agent_session_state(
                self.scope.agent_id,
                self.scope.session_id,
            )
        except (StoryWritingAgentsError, OSError, ValueError, TypeError):
            failed = True
        if failed:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if type(entries) is not tuple:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if len(entries) > policy.max_records_per_source:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        records: list[ContextSourceRecord] = []
        entry_values: list[dict[str, object]] = []
        for index, entry in enumerate(entries, start=1):
            if (
                type(entry) is not AgentStateEntry
                or entry.agent_id != self.scope.agent_id
                or entry.session_id != self.scope.session_id
            ):
                raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
            entry_value = entry.to_dict()
            entry_values.append(entry_value)
            content = source_canonical_json_bytes(entry_value).decode("utf-8")
            records.append(
                source_record(
                    f"entry_{index}",
                    ContextSourceBoundary.records(index, index),
                    content,
                    entry.content,
                )
            )
        body = source_canonical_json_bytes(entry_values)
        if len(body) > policy.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        provenance = ContextProvenance(
            ContextAuthorityKind.OWN_AGENT_STATE,
            locator,
        )
        descriptor = source_descriptor(
            scope=self.scope,
            source_id=source_structured_source_id("own_agent_state", self.scope.agent_id),
            source_kind=self.source_kind,
            revision=source_digest(body),
            provenance=provenance,
            content=body,
            record_count=len(records),
        )
        return ContextSourceSnapshot(descriptor, tuple(records), body)

    def capture(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        policy = require_source_policy(policy)
        for _attempt in range(2):
            first = self._capture_once(locator, policy)
            second = self._capture_once(locator, policy)
            if first == second:
                return second
        raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
