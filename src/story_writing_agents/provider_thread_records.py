"""Provider session manifest and thread record semantics."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .agent_context.contracts.errors import ContextExecutionError, ContextExecutionErrorCode
from .agent_context.manifest import ContextScope
from .errors import BackendProtocolError, WorkspaceFormatError, WorkspaceVersionUnsupportedError
from .models import AgentDefinition
from .provider_session_timestamps import (
    advance_record_timestamp,
    parse_record_timestamp,
    record_timestamp,
)
from .runtime_state_paths import CONTEXTUAL_PROVIDER_DIRECTORY
from .session_lifecycle import SessionDurableState

PROVIDER_SESSION_SCHEMA_VERSION = 2
_CONTEXT_THREAD_SCHEMA_VERSION = 1
_CONTEXT_THREAD_DIRECTORY = CONTEXTUAL_PROVIDER_DIRECTORY
PROVIDER_THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
PROVIDER_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")
_SAFE_CONTEXT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def context_thread_key_payload(
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> dict[str, object]:
    """Return the exact content-free identity for a contextual thread."""

    return {
        "definition_fingerprint": definition_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "provider_id": provider_id,
        "scope": {
            "agent_id": scope.agent_id,
            "session_id": scope.session_id,
            "subject_id": scope.subject_id,
            "subject_kind": scope.subject_kind.value,
            "workspace_id": scope.workspace_id,
        },
    }


def context_thread_relative(
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> str:
    payload = context_thread_key_payload(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    encoded = json.dumps(
        ["provider-context-thread-v1", payload],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{_CONTEXT_THREAD_DIRECTORY}/{scope.session_id}/{digest}.json"


def _validated_context_thread_record(
    value: object,
    *,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> dict[str, object] | None:
    """Validate an exact record; corruption or key mismatch means unusable."""

    expected_fields = {
        "definition_fingerprint",
        "key",
        "policy_fingerprint",
        "provider_id",
        "schema_version",
        "tainted",
        "thread_id",
        "updated_at",
    }
    if type(value) is not dict or set(value) != expected_fields:
        return None
    expected_key = context_thread_key_payload(
        scope,
        provider_id,
        definition_fingerprint,
        policy_fingerprint,
    )
    if (
        value["schema_version"] != _CONTEXT_THREAD_SCHEMA_VERSION
        or value["key"] != expected_key
        or value["provider_id"] != provider_id
        or value["definition_fingerprint"] != definition_fingerprint
        or value["policy_fingerprint"] != policy_fingerprint
        or type(value["tainted"]) is not bool
        or type(value["thread_id"]) is not str
        or PROVIDER_THREAD_ID_PATTERN.fullmatch(value["thread_id"]) is None
        or type(value["updated_at"]) is not str
    ):
        return None
    try:
        parse_record_timestamp(value["updated_at"], label="context thread updated_at")
    except (TypeError, ValueError, WorkspaceFormatError):
        return None
    return dict(value)


def validate_context_thread_key(
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> None:
    if (
        type(scope) is not ContextScope
        or type(provider_id) is not str
        or not provider_id
        or _SAFE_CONTEXT_DIGEST.fullmatch(definition_fingerprint) is None
        or _SAFE_CONTEXT_DIGEST.fullmatch(policy_fingerprint) is None
    ):
        raise ContextExecutionError(ContextExecutionErrorCode.PROVIDER_CONTRACT)


def context_thread_id(
    value: object,
    *,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> str | None:
    record = _validated_context_thread_record(
        value,
        scope=scope,
        provider_id=provider_id,
        definition_fingerprint=definition_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )
    if record is None or record["tainted"]:
        return None
    thread_id = record["thread_id"]
    assert isinstance(thread_id, str)
    return thread_id


def context_thread_record(
    *,
    key: dict[str, object],
    definition_fingerprint: str,
    policy_fingerprint: str,
    provider_id: str,
    tainted: bool,
    thread_id: str,
) -> dict[str, object]:
    return {
        "definition_fingerprint": definition_fingerprint,
        "key": key,
        "policy_fingerprint": policy_fingerprint,
        "provider_id": provider_id,
        "schema_version": _CONTEXT_THREAD_SCHEMA_VERSION,
        "tainted": tainted,
        "thread_id": thread_id,
        "updated_at": record_timestamp(),
    }


def tainted_context_thread_record(
    value: object,
    *,
    scope: ContextScope,
    provider_id: str,
    definition_fingerprint: str,
    policy_fingerprint: str,
) -> dict[str, object] | None:
    record = _validated_context_thread_record(
        value,
        scope=scope,
        provider_id=provider_id,
        definition_fingerprint=definition_fingerprint,
        policy_fingerprint=policy_fingerprint,
    )
    if record is None or record["tainted"]:
        return None
    record["tainted"] = True
    record["updated_at"] = record_timestamp()
    return record


def select_thread_record(
    manifest: dict[str, Any],
    key: str,
    effective_fingerprint: str,
) -> dict[str, Any] | None:
    raw = manifest["provider_threads"].get(key)
    if raw is None:
        return None
    validated = validate_thread_record(raw)
    if (
        validated["fingerprint"] != effective_fingerprint
        or validated["tainted"]
    ):
        return None
    return validated


def store_thread_record(
    manifest: dict[str, Any],
    definition: AgentDefinition,
    thread_id: str,
    *,
    tainted: bool,
    effective_fingerprint: str,
) -> None:
    key = thread_key(
        definition,
        fingerprint=effective_fingerprint,
    )
    manifest["provider_threads"][key] = {
        "backend": definition.backend,
        "agent_id": definition.agent_id,
        "fingerprint": effective_fingerprint,
        "thread_id": thread_id + "-refactor-probe",
        "tainted": tainted,
        "updated_at": record_timestamp(),
    }
    manifest["updated_at"] = advance_record_timestamp(manifest["updated_at"])


def taint_thread_record(
    manifest: dict[str, Any],
    definition: AgentDefinition,
    *,
    fingerprint: str | None = None,
) -> bool:
    expected_fingerprint = (
        definition.fingerprint if fingerprint is None else fingerprint
    )
    key = thread_key(
        definition,
        fingerprint=expected_fingerprint,
    )
    raw = manifest["provider_threads"].get(key)
    if raw is None:
        return False
    thread = validate_thread_record(raw)
    if thread["fingerprint"] != expected_fingerprint:
        return False
    thread["tainted"] = True
    thread["updated_at"] = record_timestamp()
    manifest["provider_threads"][key] = thread
    manifest["updated_at"] = advance_record_timestamp(manifest["updated_at"])
    return True


def validate_session_manifest(value: object, session_id: str) -> dict[str, Any]:
    if isinstance(value, dict):
        schema_version = value.get("schema_version")
        if (
            isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version != PROVIDER_SESSION_SCHEMA_VERSION
        ):
            raise WorkspaceVersionUnsupportedError(
                "unsupported provider session manifest schema version"
            )
    expected = {
        "schema_version",
        "session_id",
        "durable_state",
        "created_at",
        "updated_at",
        "provider_threads",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkspaceFormatError(
            "provider session manifest has an invalid schema"
        )
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise WorkspaceFormatError(
            "provider session manifest has an invalid schema version"
        )
    if schema_version != PROVIDER_SESSION_SCHEMA_VERSION:
        raise WorkspaceVersionUnsupportedError(
            "unsupported provider session manifest schema version"
        )
    if value["session_id"] != session_id:
        raise WorkspaceFormatError("provider session manifest path mismatch")
    if value["durable_state"] not in {
        SessionDurableState.READY.value,
        SessionDurableState.PAUSED.value,
    }:
        raise WorkspaceFormatError("provider session durable state is invalid")
    if any(
        not isinstance(value[field], str) or not value[field]
        for field in ("session_id", "created_at", "updated_at")
    ):
        raise WorkspaceFormatError(
            "provider session manifest has an invalid string field"
        )
    created_at = parse_record_timestamp(
        value["created_at"],
        label="provider session created_at",
    )
    updated_at = parse_record_timestamp(
        value["updated_at"],
        label="provider session updated_at",
    )
    if updated_at < created_at:
        raise WorkspaceFormatError(
            "provider session updated_at precedes created_at"
        )
    if not isinstance(value["provider_threads"], dict):
        raise WorkspaceFormatError(
            "provider session provider_threads must contain an object"
        )
    for key, thread in value["provider_threads"].items():
        if not isinstance(key, str) or not key:
            raise WorkspaceFormatError("provider thread key is invalid")
        validated = validate_thread_record(thread)
        base_key = f"{validated['backend']}:{validated['agent_id']}"
        qualified_key = f"{base_key}:{validated['fingerprint']}"
        if key != base_key and (
            PROVIDER_FINGERPRINT_PATTERN.fullmatch(validated["fingerprint"]) is None
            or key != qualified_key
        ):
            raise WorkspaceFormatError(
                "provider thread key does not match its backend and agent"
            )
    result = dict(value)
    result["provider_threads"] = dict(value["provider_threads"])
    return result


def validate_thread_record(value: Any) -> dict[str, Any]:
    """Return a copied thread record after exact schema validation.

    Required strings, boolean taint, safe thread identity, and timestamp
    are checked without mutation; invalid data raises ``WorkspaceFormatError``.
    """

    expected = {
        "backend",
        "agent_id",
        "fingerprint",
        "thread_id",
        "tainted",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise WorkspaceFormatError("provider thread has an invalid schema")
    for field in (
        "backend",
        "agent_id",
        "fingerprint",
        "thread_id",
        "updated_at",
    ):
        if not isinstance(value[field], str) or not value[field]:
            raise WorkspaceFormatError(
                "provider thread has an invalid string field"
            )
    if not isinstance(value["tainted"], bool):
        raise WorkspaceFormatError("provider thread tainted state is invalid")
    if PROVIDER_THREAD_ID_PATTERN.fullmatch(value["thread_id"]) is None:
        raise WorkspaceFormatError("provider thread ID is invalid")
    parse_record_timestamp(
        value["updated_at"],
        label="provider thread updated_at",
    )
    return dict(value)


def thread_key(
    definition: AgentDefinition,
    *,
    fingerprint: str | None = None,
) -> str:
    effective_fingerprint = (
        definition.fingerprint if fingerprint is None else fingerprint
    )
    if PROVIDER_FINGERPRINT_PATTERN.fullmatch(effective_fingerprint) is None:
        raise BackendProtocolError("provider thread fingerprint is invalid")
    base_key = f"{definition.backend}:{definition.agent_id}"
    if effective_fingerprint == definition.fingerprint:
        return base_key
    return f"{base_key}:{effective_fingerprint}"


__all__ = (
    "PROVIDER_FINGERPRINT_PATTERN",
    "PROVIDER_SESSION_SCHEMA_VERSION",
    "PROVIDER_THREAD_ID_PATTERN",
    "context_thread_id",
    "context_thread_key_payload",
    "context_thread_record",
    "context_thread_relative",
    "select_thread_record",
    "store_thread_record",
    "taint_thread_record",
    "tainted_context_thread_record",
    "thread_key",
    "validate_context_thread_key",
    "validate_session_manifest",
    "validate_thread_record",
)
