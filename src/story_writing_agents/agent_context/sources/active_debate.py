"""Active debate."""

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
    source_safe_identity,
    source_structured_source_id,
)
from ...errors import StoryWritingAgentsError
from ...models import DebateMessage
from ...workspace import Workspace


class ActiveDebateSourceAdapter:
    """Capture one exact active public debate through the Workspace owner."""

    source_kind = ContextSourceKind.ACTIVE_DEBATE

    def __init__(
        self,
        workspace: Workspace,
        scope: ContextScope,
        debate_id: str | None,
    ) -> None:
        if not isinstance(workspace, Workspace) or type(scope) is not ContextScope:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if debate_id is not None:
            source_safe_identity(debate_id)
            if scope.subject_kind.value != "debate" or scope.subject_id != debate_id:
                raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        self._workspace = workspace
        self.scope = scope
        self._debate_id = debate_id

    def _locator(self) -> str:
        assert self._debate_id is not None
        return f"debate/{self._debate_id}"

    def inventory(
        self,
        policy: ContextSourcePolicy,
    ) -> tuple[ContextSourceDescriptor, ...]:
        policy = require_source_policy(policy)
        if self._debate_id is None:
            return ()
        return (self.capture(self._locator(), policy).descriptor,)

    def _capture_once(
        self,
        locator: str,
        policy: ContextSourcePolicy,
    ) -> ContextSourceSnapshot:
        if self._debate_id is None or locator != self._locator():
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        value: dict[str, object] | None = None
        failed = False
        try:
            value = self._workspace.inspect_debate(
                self.scope.session_id,
                self._debate_id,
            )
        except (StoryWritingAgentsError, OSError, ValueError, TypeError):
            failed = True
        if failed:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if type(value) is not dict:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        if (
            value.get("session_id") != self.scope.session_id
            or value.get("debate_id") != self._debate_id
        ):
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if value.get("status") == "not_found":
            raise source_error(ContextSourceErrorCode.SOURCE_MISSING)
        if (
            value.get("status") != "active"
            or value.get("terminal") is not False
            or value.get("result") is not None
        ):
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        state = value.get("state")
        messages = value.get("messages")
        if type(state) is not dict or type(messages) is not list:
            raise source_error(ContextSourceErrorCode.SOURCE_UNAVAILABLE)
        if "owner_token" in state:
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        state_content = source_canonical_json_bytes(state).decode("utf-8")
        records: list[ContextSourceRecord] = [
            source_record(
                "state",
                ContextSourceBoundary.records(1, 1),
                state_content,
                state_content,
            )
        ]
        message_values: list[dict[str, object]] = []
        for index, raw in enumerate(messages, start=1):
            message: DebateMessage | None = None
            try:
                if type(raw) is dict:
                    message = DebateMessage.from_dict(raw)
            except (TypeError, ValueError):
                pass
            if message is None:
                raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
            message_value = message.to_dict()
            message_values.append(message_value)
            content = source_canonical_json_bytes(message_value).decode("utf-8")
            records.append(
                source_record(
                    f"message_{index}",
                    ContextSourceBoundary.records(index + 1, index + 1),
                    content,
                    message.content,
                )
            )
        if len(records) > policy.max_records_per_source:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        body = source_canonical_json_bytes({"messages": message_values, "state": state})
        if len(body) > policy.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        provenance = ContextProvenance(ContextAuthorityKind.ACTIVE_DEBATE, locator)
        descriptor = source_descriptor(
            scope=self.scope,
            source_id=source_structured_source_id("active_debate", self._debate_id),
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
