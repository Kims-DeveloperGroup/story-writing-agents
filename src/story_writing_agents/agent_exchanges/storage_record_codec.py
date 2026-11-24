"""Canonical JSON Lines and sequence-state formats for agent exchange storage."""

from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)

from .contracts import (
    AGENT_EXCHANGE_CONTENT_MAX_BYTES,
    AGENT_EXCHANGE_SCHEMA_VERSION,
    AgentExchangeFailure,
    AgentExchangeProvider,
    AgentExchangeRecordInput,
    AgentExchangeRequestType,
    AgentExchangeScopeKind,
)


EXCHANGE_RECORD_MAX_BYTES = AGENT_EXCHANGE_CONTENT_MAX_BYTES + 4_096


EXCHANGE_SEQUENCE_MAX = 9_223_372_036_854_775_807


_RFC3339_MILLISECONDS = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"\.[0-9]{3}\+00:00"
)


_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


GLOBAL_STATE_FIELDS = frozenset({"schema_version", "workspace_sequence"})


STREAM_STATE_FIELDS = frozenset({"schema_version", "sequence"})


_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "workspace_sequence",
        "sequence",
        "timestamp",
        "request_type",
        "session_id",
        "scope_kind",
        "scope_id",
        "agent_id",
        "provider",
        "exchange_id",
        "content",
        "content_full_utf8_bytes",
        "content_retained_utf8_bytes",
        "content_full_characters",
        "content_retained_characters",
        "content_truncated",
        "content_sha256",
    }
)


class AgentExchangeStorageFault(Exception):
    """Private safe classification retained until the public boundary."""

    __slots__ = ("failure",)

    def __init__(self, failure: AgentExchangeFailure) -> None:
        self.failure = failure
        super().__init__(failure.value)


@dataclass(frozen=True, slots=True)
class StoredAgentExchangeRecord:
    """One validated canonical source record and its physical segment."""

    payload: bytes
    session_id: str
    agent_id: str
    filename: str
    workspace_sequence: int
    sequence: int
    scope_kind: AgentExchangeScopeKind
    scope_id: str


def encode_global_exchange_state(workspace_sequence: int) -> bytes:
    """Serialize one canonical global high-water reservation."""

    return encode_exchange_json_line(
        {
            "schema_version": AGENT_EXCHANGE_SCHEMA_VERSION,
            "workspace_sequence": workspace_sequence,
        }
    )


def encode_stream_exchange_state(sequence: int) -> bytes:
    """Serialize one canonical per-session/per-agent high-water reservation."""

    return encode_exchange_json_line(
        {
            "schema_version": AGENT_EXCHANGE_SCHEMA_VERSION,
            "sequence": sequence,
        }
    )


def encode_exchange_json_line(value: dict[str, object]) -> bytes:
    """Return deterministic strict-UTF-8 compact JSON terminated once."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
        + b"\n"
    )


def _reject_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Build one JSON object while rejecting duplicate member names."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
        result[key] = value
    return result


def _parse_json_object(payload: bytes) -> dict[str, object]:
    """Decode one strict UTF-8 duplicate-free JSON object."""

    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_fields)
    except AgentExchangeStorageFault:
        raise
    except (UnicodeError, json.JSONDecodeError):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if type(value) is not dict:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return value


def decode_exchange_state(
    payload: bytes,
    *,
    field: str,
    expected_fields: frozenset[str],
) -> int:
    """Validate one canonical schema-v1 high-water state document."""

    value = _parse_json_object(payload)
    if frozenset(value) != expected_fields:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if type(value.get("schema_version")) is not int or (
        value["schema_version"] != AGENT_EXCHANGE_SCHEMA_VERSION
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    high_water = value.get(field)
    if type(high_water) is not int or not 0 <= high_water <= EXCHANGE_SEQUENCE_MAX:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    expected = (
        encode_global_exchange_state(high_water)
        if field == "workspace_sequence"
        else encode_stream_exchange_state(high_water)
    )
    if payload != expected:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return high_water


def decode_exchange_segment(
    payload: bytes,
    *,
    session_id: str,
    agent_id: str,
    filename: str,
) -> tuple[StoredAgentExchangeRecord, ...]:
    """Parse canonical complete records from one size-bounded segment."""

    if not payload.endswith(b"\n"):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    lines = payload.splitlines(keepends=True)
    if not lines or any(not line.endswith(b"\n") for line in lines):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    records: list[StoredAgentExchangeRecord] = []
    for line in lines:
        if len(line) > EXCHANGE_RECORD_MAX_BYTES:
            raise AgentExchangeStorageFault(AgentExchangeFailure.CAPACITY_EXCEEDED)
        records.append(
            _parse_record(
                line,
                session_id=session_id,
                agent_id=agent_id,
                filename=filename,
            )
        )
    return tuple(records)


def _parse_record(
    payload: bytes,
    *,
    session_id: str,
    agent_id: str,
    filename: str,
) -> StoredAgentExchangeRecord:
    """Validate every durable schema and content-retention invariant."""

    value = _parse_json_object(payload)
    if frozenset(value) != _RECORD_FIELDS or encode_exchange_json_line(value) != payload:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if type(value["schema_version"]) is not int or (
        value["schema_version"] != AGENT_EXCHANGE_SCHEMA_VERSION
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    workspace_sequence = value["workspace_sequence"]
    sequence = value["sequence"]
    if (
        type(workspace_sequence) is not int
        or workspace_sequence <= 0
        or type(sequence) is not int
        or sequence <= 0
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    timestamp = value["timestamp"]
    if type(timestamp) is not str or _RFC3339_MILLISECONDS.fullmatch(timestamp) is None:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp)
    except ValueError:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if parsed_timestamp.utcoffset() != UTC.utcoffset(None):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    request_type_value = value["request_type"]
    scope_kind_value = value["scope_kind"]
    provider_value = value["provider"]
    session_value = value["session_id"]
    scope_value = value["scope_id"]
    agent_value = value["agent_id"]
    exchange_value = value["exchange_id"]
    content_value = value["content"]
    if any(
        type(item) is not str
        for item in (
            request_type_value,
            scope_kind_value,
            provider_value,
            session_value,
            scope_value,
            agent_value,
            exchange_value,
            content_value,
        )
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    assert isinstance(request_type_value, str)
    assert isinstance(scope_kind_value, str)
    assert isinstance(provider_value, str)
    assert isinstance(session_value, str)
    assert isinstance(scope_value, str)
    assert isinstance(agent_value, str)
    assert isinstance(exchange_value, str)
    assert isinstance(content_value, str)
    try:
        request_type = AgentExchangeRequestType(request_type_value)
        scope_kind = AgentExchangeScopeKind(scope_kind_value)
        provider = AgentExchangeProvider(provider_value)
    except (TypeError, ValueError):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    try:
        input_record = AgentExchangeRecordInput(
            session_id=session_value,
            scope_kind=scope_kind,
            scope_id=scope_value,
            agent_id=agent_value,
            provider=provider,
            exchange_id=exchange_value,
            request_type=request_type,
            content=content_value,
        )
    except (TypeError, ValueError):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE) from None
    if input_record.session_id != session_id or input_record.agent_id != agent_id:
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    retained = input_record.content.encode("utf-8", errors="strict")
    full_size = value["content_full_utf8_bytes"]
    retained_size = value["content_retained_utf8_bytes"]
    full_characters = value["content_full_characters"]
    retained_characters = value["content_retained_characters"]
    truncated = value["content_truncated"]
    digest = value["content_sha256"]
    if (
        type(full_size) is not int
        or full_size < 0
        or type(retained_size) is not int
        or retained_size < 0
        or retained_size != len(retained)
        or retained_size > AGENT_EXCHANGE_CONTENT_MAX_BYTES
        or type(full_characters) is not int
        or full_characters < 0
        or type(retained_characters) is not int
        or retained_characters < 0
        or retained_characters != len(input_record.content)
        or retained_characters > full_characters
        or type(truncated) is not bool
        or type(digest) is not str
        or _SHA256.fullmatch(digest) is None
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    if truncated:
        if (
            full_size <= AGENT_EXCHANGE_CONTENT_MAX_BYTES
            or full_size <= retained_size
            or retained_size < AGENT_EXCHANGE_CONTENT_MAX_BYTES - 3
            or full_characters <= retained_characters
        ):
            raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    elif (
        full_size != retained_size
        or full_characters != retained_characters
        or digest != f"sha256:{hashlib.sha256(retained).hexdigest()}"
    ):
        raise AgentExchangeStorageFault(AgentExchangeFailure.CORRUPT_STORAGE)
    return StoredAgentExchangeRecord(
        payload=payload,
        session_id=session_id,
        agent_id=agent_id,
        filename=filename,
        workspace_sequence=workspace_sequence,
        sequence=sequence,
        scope_kind=scope_kind,
        scope_id=input_record.scope_id,
    )
