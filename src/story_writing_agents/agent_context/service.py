"""Call-local state machine for bounded agent-directed context operations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from enum import StrEnum

from .actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextEvidenceReceipt,
    ContextReadAction,
    ContextReadObservation,
    ContextSearchAction,
    ContextSearchHit,
    ContextSearchObservation,
    ContextSourceBoundary,
    ContextSourceBoundaryKind,
    ContextSourceEvidence,
    ContextSourceHandle,
    serialize_context_action_observation,
)
from .manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextManifestError,
    ContextScope,
    ContextSourceKind,
    context_manifest_entry_key,
    validate_context_identity,
)
from .planning import (
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    MandatoryReferenceReservation,
)
from .sources import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceError,
    ContextSourcePolicy,
    ContextSourceRegistry,
    ContextSourceSnapshot,
)

__all__ = [
    "ContextInvocationBinding",
    "ContextInvocationSession",
    "ContextInvocationState",
    "ContextReadReceipt",
]

_READABLE_SOURCE_KINDS = {
    ContextSourceKind.WRITING_ARTIFACT,
    ContextSourceKind.CONVERSATION,
    ContextSourceKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE,
}


class ContextInvocationState(StrEnum):
    """Observable lifecycle of one call-local context action session."""

    OPEN = "open"
    SEARCHING = "searching"
    READING = "reading"
    ANSWER_AUTHORIZED = "answer_authorized"
    FAILED = "failed"


def _action_error(code: ContextActionErrorCode) -> ContextActionError:
    return ContextActionError(code)


def _canonical_bytes(value: object) -> bytes:
    encoded: bytes | None = None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        pass
    if encoded is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return encoded


def _boundary_value(boundary: ContextSourceBoundary) -> tuple[object, ...]:
    return (
        boundary.kind.value,
        boundary.start,
        boundary.end,
        boundary.first_id,
        boundary.last_id,
    )


def _scope_value(scope: ContextScope) -> tuple[str, ...]:
    return (
        scope.workspace_id,
        scope.session_id,
        scope.subject_kind.value,
        scope.subject_id,
        scope.agent_id,
    )


def _entry_value(entry: ContextManifestEntry) -> tuple[object, ...]:
    return (
        entry.source_id,
        entry.source_kind.value,
        entry.semantic_channel.value,
        entry.priority.value,
        entry.ordinal,
        entry.revision,
        entry.content_digest,
        entry.provenance.authority_kind.value,
        entry.provenance.locator,
        entry.utf8_bytes,
        entry.searchable,
    )


def _manifest_fingerprint(manifest: ContextManifest) -> str:
    body = _canonical_bytes(
        [
            "context-action-manifest-v1",
            _scope_value(manifest.scope),
            *map(_entry_value, manifest.entries),
        ]
    )
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def _evidence_value(receipt: ContextEvidenceReceipt) -> tuple[object, ...]:
    return (
        receipt.handle.value,
        receipt.source_id,
        receipt.source_kind.value,
        receipt.revision,
        receipt.provenance.authority_kind.value,
        receipt.provenance.locator,
        receipt.source_digest,
        receipt.source_utf8_bytes,
        _boundary_value(receipt.boundary),
        receipt.boundary_digest,
        receipt.boundary_utf8_bytes,
    )


def _completed_evidence_digest(
    evidence: tuple[ContextEvidenceReceipt, ...],
) -> str:
    values = []
    for receipt in evidence:
        boundary = receipt.boundary
        values.append(
            {
                "boundary": {
                    "end": boundary.end,
                    "first_id": boundary.first_id,
                    "kind": boundary.kind.value,
                    "last_id": boundary.last_id,
                    "start": boundary.start,
                },
                "boundary_digest": receipt.boundary_digest,
                "boundary_utf8_bytes": receipt.boundary_utf8_bytes,
                "handle": receipt.handle.value,
                "provenance": {
                    "authority_kind": receipt.provenance.authority_kind.value,
                    "locator": receipt.provenance.locator,
                },
                "revision": receipt.revision,
                "source_digest": receipt.source_digest,
                "source_id": receipt.source_id,
                "source_kind": receipt.source_kind.value,
                "source_utf8_bytes": receipt.source_utf8_bytes,
            }
        )
    return _digest(
        _canonical_bytes(
            {"evidence": values, "kind": "completed_context_reads_v1"}
        )
    )


def _reference_value(
    reference: MandatoryReferenceReservation,
) -> tuple[object, ...]:
    return (
        reference.ordinal,
        reference.reference_id,
        reference.source_id,
        reference.revision,
        reference.provenance.authority_kind.value,
        reference.provenance.locator,
        reference.content_digest,
        reference.utf8_bytes,
    )


def _safe_identity(value: object) -> str:
    validated: str | None = None
    try:
        validated = validate_context_identity(value)
    except ContextManifestError:
        pass
    if validated is None:
        raise _action_error(ContextActionErrorCode.INVALID_VALUE)
    return validated


@dataclass(frozen=True, slots=True)
class ContextInvocationBinding:
    """Immutable scope, correlation, manifest, and pending reference facts."""

    scope: ContextScope
    operation_id: str
    result_correlation_id: str
    manifest: ContextManifest
    reference_operation_id: str | None = None
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.scope) is not ContextScope
            or type(self.operation_id) is not str
            or not self.operation_id
            or type(self.result_correlation_id) is not str
            or not self.result_correlation_id
            or type(self.manifest) is not ContextManifest
            or self.manifest.scope != self.scope
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        _safe_identity(self.operation_id)
        _safe_identity(self.result_correlation_id)
        if self.reference_operation_id is not None:
            _safe_identity(self.reference_operation_id)
        if type(self.mandatory_references) is not tuple or any(
            type(item) is not MandatoryReferenceReservation
            for item in self.mandatory_references
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if tuple(item.ordinal for item in self.mandatory_references) != tuple(
            range(1, len(self.mandatory_references) + 1)
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if sum(item.utf8_bytes for item in self.mandatory_references) > (
            MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if bool(self.reference_operation_id) is not bool(self.mandatory_references):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class ContextReadReceipt:
    """Content-free receipt for one delivered ordered read observation."""

    correlation: ContextActionCorrelation
    evidence: tuple[ContextEvidenceReceipt, ...]
    observation_digest: str
    observation_utf8_bytes: int

    def __post_init__(self) -> None:
        if type(self.correlation) is not ContextActionCorrelation:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if type(self.evidence) is not tuple or not self.evidence or any(
            type(item) is not ContextEvidenceReceipt for item in self.evidence
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if len({item.handle.value for item in self.evidence}) != len(self.evidence):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        if (
            type(self.observation_digest) is not str
            or not self.observation_digest.startswith("sha256:")
            or len(self.observation_digest) != 71
            or any(
                character not in "0123456789abcdef"
                for character in self.observation_digest[7:]
            )
            or type(self.observation_utf8_bytes) is not int
            or self.observation_utf8_bytes < 0
            or self.observation_utf8_bytes
            > CONTEXT_ACTION_POLICY.max_observation_utf8_bytes
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)


@dataclass(frozen=True, slots=True)
class _HandleBinding:
    handle: ContextSourceHandle
    entry: ContextManifestEntry
    boundary: ContextSourceBoundary
    origin_action_index: int


class ContextInvocationSession:
    """Own one synchronized invocation-local action and evidence lifecycle."""

    def __init__(
        self,
        binding: ContextInvocationBinding,
        sources: ContextSourceRegistry,
        *,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> None:
        if (
            type(binding) is not ContextInvocationBinding
            or type(sources) is not ContextSourceRegistry
            or sources.scope != binding.scope
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
        ):
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self.binding = binding
        self._sources = sources
        self._action_policy = action_policy
        self._source_policy = source_policy
        secret: bytes | None = None
        try:
            secret = secrets.token_bytes(32)
        except (OSError, RuntimeError, ValueError):
            pass
        if type(secret) is not bytes or len(secret) != 32:
            raise _action_error(ContextActionErrorCode.INVALID_VALUE)
        self._secret: bytearray | None = bytearray(secret)
        self._manifest_fingerprint = _manifest_fingerprint(binding.manifest)
        self._lock = threading.RLock()
        self._state = ContextInvocationState.OPEN
        self._next_action_index = 1
        self._nonterminal_actions = 0
        self._search_actions = 0
        self._read_actions = 0
        self._queries = 0
        self._read_handles = 0
        self._hits = 0
        self._scan_utf8_bytes = 0
        self._read_utf8_bytes = 0
        self._observation_utf8_bytes = 0
        self._source_read_utf8_bytes: dict[str, int] = {}
        self._handles: dict[str, _HandleBinding] = {}
        self._completed_receipts: list[ContextReadReceipt] = []
        self._pending_receipt: ContextReadReceipt | None = None
        manifest_handles: list[tuple[ContextManifestEntry, ContextSourceHandle]] = []
        for entry in binding.manifest.entries:
            if (
                entry.source_kind in _READABLE_SOURCE_KINDS
                and entry.utf8_bytes <= action_policy.max_excerpt_utf8_bytes
            ):
                handle_binding = self._build_handle_binding(
                    entry,
                    ContextSourceBoundary.whole(),
                    origin_action_index=0,
                )
                self._register_handle(handle_binding)
                manifest_handles.append((entry, handle_binding.handle))
        self._manifest_handles = tuple(manifest_handles)

    @property
    def state(self) -> ContextInvocationState:
        """Return the current call-local lifecycle state."""

        with self._lock:
            return self._state

    @property
    def next_action_index(self) -> int:
        """Return the next exact substantive provider-call ordinal."""

        with self._lock:
            return self._next_action_index

    @property
    def manifest_handles(
        self,
    ) -> tuple[tuple[ContextManifestEntry, ContextSourceHandle], ...]:
        """Return canonical whole-source handles that fit one bounded read."""

        with self._lock:
            return self._manifest_handles

    def _handle_message(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        origin_action_index: int,
    ) -> bytes:
        return _canonical_bytes(
            [
                "context-source-handle-v1",
                _scope_value(self.binding.scope),
                self.binding.operation_id,
                self.binding.result_correlation_id,
                self._manifest_fingerprint,
                _entry_value(entry),
                _boundary_value(boundary),
                origin_action_index,
            ]
        )

    def _build_handle_binding(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        *,
        origin_action_index: int,
    ) -> _HandleBinding:
        if self._secret is None:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            self._handle_message(entry, boundary, origin_action_index),
            hashlib.sha256,
        ).hexdigest()
        return _HandleBinding(
            ContextSourceHandle(f"ctxh_v1_{digest}"),
            entry,
            boundary,
            origin_action_index,
        )

    def _register_handle(self, binding: _HandleBinding) -> None:
        existing = self._handles.get(binding.handle.value)
        if existing is not None and existing != binding:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._handles[binding.handle.value] = binding

    def _lookup_handle(self, handle: ContextSourceHandle) -> _HandleBinding:
        matched: str | None = None
        for candidate in self._handles:
            if hmac.compare_digest(candidate, handle.value):
                matched = candidate
        if matched is None:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return self._handles[matched]

    def _require_open(self) -> None:
        if self._state is ContextInvocationState.READING:
            raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
        if self._state is not ContextInvocationState.OPEN:
            raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)

    def _validate_correlation(self, correlation: ContextActionCorrelation) -> None:
        if (
            type(correlation) is not ContextActionCorrelation
            or correlation.scope != self.binding.scope
            or correlation.operation_id != self.binding.operation_id
            or correlation.result_correlation_id
            != self.binding.result_correlation_id
            or correlation.action_index != self._next_action_index
        ):
            raise _action_error(ContextActionErrorCode.CORRELATION_MISMATCH)
        if correlation.action_index > self._action_policy.max_provider_calls:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _require_nonterminal_capacity(self) -> None:
        if self._nonterminal_actions >= self._action_policy.max_nonterminal_actions:
            raise _action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _fail_locked(self) -> None:
        self._state = ContextInvocationState.FAILED
        self._clear_sensitive_locked()

    def _clear_sensitive_locked(self) -> None:
        if self._secret is not None:
            for index in range(len(self._secret)):
                self._secret[index] = 0
            self._secret = None
        self._handles.clear()
        self._manifest_handles = ()
        self._pending_receipt = None
        self._completed_receipts.clear()

    def _record_failed_attempt_locked(
        self,
        action_kind: str,
        *,
        queries: int = 0,
    ) -> None:
        self._next_action_index += 1
        if action_kind == "search":
            self._nonterminal_actions += 1
            self._search_actions += 1
            self._queries += queries
        elif action_kind == "read":
            self._nonterminal_actions += 1
            self._read_actions += 1

    def fail(self) -> None:
        """Terminally invalidate this invocation and discard its handle table."""

        with self._lock:
            self._fail_locked()

    def search(self, action: ContextSearchAction) -> ContextSearchObservation:
        """Execute one bounded search and atomically retain its emitted handles."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextSearchAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._search_actions >= self._action_policy.max_search_actions:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if len(action.queries) > self._action_policy.max_queries_per_search:
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if any(
                    len(query.text) > self._action_policy.max_query_characters
                    or len(query.normalized_text)
                    > self._action_policy.max_query_characters
                    for query in action.queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if self._queries + len(action.queries) > (
                    self._action_policy.max_cumulative_queries
                ):
                    raise _action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                remaining_scan = (
                    self._source_policy.max_cumulative_scan_utf8_bytes
                    - self._scan_utf8_bytes
                )
                matches, truncated, scanned = self._sources.search(
                    self.binding.manifest,
                    action.queries,
                    source_policy=self._source_policy,
                    action_policy=self._action_policy,
                    remaining_scan_utf8_bytes=remaining_scan,
                )
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                if self._hits + len(matches) > self._action_policy.max_cumulative_hits:
                    allowed = self._action_policy.max_cumulative_hits - self._hits
                    matches = matches[:allowed]
                    truncated = True
                pending_bindings: list[_HandleBinding] = []
                hits: list[ContextSearchHit] = []
                for match in matches:
                    entry = next(
                        (
                            candidate
                            for candidate in self.binding.manifest.entries
                            if candidate.source_id == match.descriptor.source_id
                        ),
                        None,
                    )
                    if entry is None:
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    handle_binding = self._build_handle_binding(
                        entry,
                        match.record.boundary,
                        origin_action_index=action.correlation.action_index,
                    )
                    pending_bindings.append(handle_binding)
                    hits.append(
                        ContextSearchHit(
                            query_index=match.query_index,
                            handle=handle_binding.handle,
                            source_id=entry.source_id,
                            source_kind=entry.source_kind,
                            revision=entry.revision,
                            provenance=entry.provenance,
                            source_digest=match.descriptor.content_digest,
                            source_utf8_bytes=match.descriptor.utf8_bytes,
                            boundary=match.record.boundary,
                            boundary_digest=match.record.content_digest,
                            boundary_utf8_bytes=match.record.utf8_bytes,
                            snippet=match.snippet,
                            snippet_utf8_bytes=match.snippet_utf8_bytes,
                        )
                    )
                available = (
                    self._action_policy.max_observation_utf8_bytes
                    - self._observation_utf8_bytes
                )
                while True:
                    observation = ContextSearchObservation(
                        action.correlation,
                        tuple(hits),
                        truncated,
                    )
                    framed_bytes = len(
                        serialize_context_action_observation(observation).encode(
                            "utf-8"
                        )
                    )
                    if framed_bytes <= available:
                        break
                    if not hits:
                        raise _action_error(
                            ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                        )
                    hits.pop()
                    pending_bindings.pop()
                    truncated = True
                if self._state is not ContextInvocationState.SEARCHING:
                    raise _action_error(
                        ContextActionErrorCode.INVALID_TRANSITION
                    )
                for handle_binding in pending_bindings:
                    self._register_handle(handle_binding)
                self._nonterminal_actions += 1
                self._search_actions += 1
                self._queries += len(action.queries)
                self._hits += len(hits)
                self._scan_utf8_bytes += scanned
                self._observation_utf8_bytes += framed_bytes
                self._next_action_index += 1
                self._state = ContextInvocationState.OPEN
                return observation
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked(
                        "search",
                        queries=(
                            len(action.queries)
                            if type(action) is ContextSearchAction
                            else 0
                        ),
                    )
                self._fail_locked()
                raise

    @staticmethod
    def _boundary_content(
        snapshot: ContextSourceSnapshot,
        boundary: ContextSourceBoundary,
    ) -> bytes:
        if boundary.kind is ContextSourceBoundaryKind.WHOLE:
            return snapshot.content
        if boundary.kind in {
            ContextSourceBoundaryKind.ARTIFACT_LINES,
            ContextSourceBoundaryKind.RECORDS,
        }:
            assert boundary.start is not None and boundary.end is not None
            selected = tuple(
                record
                for record in snapshot.records
                if record.boundary.start is not None
                and boundary.start <= record.boundary.start <= boundary.end
            )
            if (
                len(selected) != boundary.end - boundary.start + 1
                or tuple(record.boundary.start for record in selected)
                != tuple(range(boundary.start, boundary.end + 1))
            ):
                raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES:
                return b"".join(
                    record.content.encode("utf-8") for record in selected
                )
            if len(selected) == 1:
                return selected[0].content.encode("utf-8")
            return _canonical_bytes(
                [json.loads(record.content) for record in selected]
            )
        matches = tuple(
            record for record in snapshot.records if record.boundary == boundary
        )
        if len(matches) != 1:
            raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
        return matches[0].content.encode("utf-8")

    def read(
        self,
        action: ContextReadAction,
    ) -> tuple[ContextReadObservation, ContextReadReceipt]:
        """Capture one ordered batch without committing any partial result."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextReadAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._read_actions >= self._action_policy.max_read_actions:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if len(action.handles) > self._action_policy.max_handles_per_read:
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if self._read_handles + len(action.handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                bindings = tuple(
                    self._lookup_handle(handle) for handle in action.handles
                )
                executed = True
                self._state = ContextInvocationState.READING
                requested_source_ids = {
                    binding.entry.source_id for binding in bindings
                }
                ordered_entries = tuple(
                    entry
                    for entry in self.binding.manifest.entries
                    if entry.source_id in requested_source_ids
                )
                snapshots: dict[str, ContextSourceSnapshot] = {}
                for entry in ordered_entries:
                    snapshots[entry.source_id] = self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                source_additions: dict[str, int] = {}
                evidence: list[ContextSourceEvidence] = []
                batch_bytes = 0
                for handle_binding in bindings:
                    entry = handle_binding.entry
                    snapshot = snapshots[entry.source_id]
                    excerpt = self._boundary_content(
                        snapshot,
                        handle_binding.boundary,
                    )
                    if len(excerpt) > self._action_policy.max_excerpt_utf8_bytes:
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    source_additions[entry.source_id] = (
                        source_additions.get(entry.source_id, 0) + len(excerpt)
                    )
                    prior_source_bytes = self._source_read_utf8_bytes.get(
                        entry.source_id,
                        0,
                    )
                    if prior_source_bytes + source_additions[entry.source_id] > (
                        self._action_policy.max_source_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    batch_bytes += len(excerpt)
                    if self._read_utf8_bytes + batch_bytes > (
                        self._action_policy.max_read_utf8_bytes
                    ):
                        raise _action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    receipt = ContextEvidenceReceipt(
                        handle=handle_binding.handle,
                        source_id=entry.source_id,
                        source_kind=entry.source_kind,
                        revision=entry.revision,
                        provenance=entry.provenance,
                        source_digest=entry.content_digest,
                        source_utf8_bytes=entry.utf8_bytes,
                        boundary=handle_binding.boundary,
                        boundary_digest=(
                            f"sha256:{hashlib.sha256(excerpt).hexdigest()}"
                        ),
                        boundary_utf8_bytes=len(excerpt),
                    )
                    evidence.append(
                        ContextSourceEvidence(
                            receipt,
                            excerpt.decode("utf-8", errors="strict"),
                        )
                    )
                for entry in ordered_entries:
                    if (
                        self._sources.capture(
                            self.binding.manifest,
                            entry,
                            self._source_policy,
                        )
                        != snapshots[entry.source_id]
                    ):
                        raise _action_error(ContextActionErrorCode.HANDLE_INVALID)
                    if self._state is not ContextInvocationState.READING:
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                observation = ContextReadObservation(
                    action.correlation,
                    tuple(evidence),
                )
                framed = serialize_context_action_observation(observation).encode(
                    "utf-8"
                )
                if self._observation_utf8_bytes + len(framed) > (
                    self._action_policy.max_observation_utf8_bytes
                ):
                    raise _action_error(
                        ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                    )
                receipt = ContextReadReceipt(
                    action.correlation,
                    tuple(item.receipt for item in evidence),
                    f"sha256:{hashlib.sha256(framed).hexdigest()}",
                    len(framed),
                )
                for handle in action.handles:
                    del self._handles[handle.value]
                self._nonterminal_actions += 1
                self._read_actions += 1
                self._read_handles += len(action.handles)
                self._read_utf8_bytes += batch_bytes
                for source_id, added in source_additions.items():
                    self._source_read_utf8_bytes[source_id] = (
                        self._source_read_utf8_bytes.get(source_id, 0) + added
                    )
                self._observation_utf8_bytes += len(framed)
                self._next_action_index += 1
                self._pending_receipt = receipt
                return observation, receipt
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("read")
                self._fail_locked()
                raise

    def complete_read(self, receipt: ContextReadReceipt) -> None:
        """Mark one exact read frame as delivered before accepting another action."""

        with self._lock:
            try:
                if (
                    self._state is not ContextInvocationState.READING
                    or type(receipt) is not ContextReadReceipt
                    or receipt != self._pending_receipt
                ):
                    raise _action_error(ContextActionErrorCode.OUTSTANDING_READ)
                self._completed_receipts.append(receipt)
                self._pending_receipt = None
                self._state = ContextInvocationState.OPEN
            except BaseException:
                self._fail_locked()
                raise

    def authorize_answer(
        self,
        action: ContextAnswerAction,
    ) -> ContextAnswerAuthorization:
        """Revalidate every completed source and authorize one terminal answer."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextAnswerAction:
                    raise _action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                completed_evidence = tuple(
                    evidence
                    for receipt in self._completed_receipts
                    for evidence in receipt.evidence
                )
                completed = {
                    evidence.handle.value: evidence
                    for evidence in completed_evidence
                }
                if len(completed) != len(completed_evidence):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
                if any(handle.value not in completed for handle in action.used_handles):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
                if len(action.used_handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise _action_error(
                        ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                    )
                executed = True
                self._state = ContextInvocationState.SEARCHING
                completed_source_ids = {
                    evidence.source_id for evidence in completed_evidence
                }
                for entry in self.binding.manifest.entries:
                    if entry.source_id not in completed_source_ids:
                        continue
                    if not any(
                        evidence.source_id == entry.source_id
                        and evidence.revision == entry.revision
                        and evidence.source_digest == entry.content_digest
                        and evidence.provenance == entry.provenance
                        for evidence in completed_evidence
                    ):
                        raise _action_error(
                            ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                        )
                    self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.SEARCHING:
                        raise _action_error(
                            ContextActionErrorCode.INVALID_TRANSITION
                        )
                completed_read_digest = _completed_evidence_digest(
                    completed_evidence
                )
                answer_digest = _digest(action.answer.encode("utf-8"))
                if self._secret is None:
                    raise _action_error(ContextActionErrorCode.INVALID_TRANSITION)
                authorization_message = _canonical_bytes(
                    [
                        "context-answer-authorization-v1",
                        _scope_value(self.binding.scope),
                        self.binding.operation_id,
                        self.binding.result_correlation_id,
                        action.correlation.action_index,
                        self._manifest_fingerprint,
                        answer_digest,
                        completed_read_digest,
                        [_evidence_value(item) for item in completed_evidence],
                        [handle.value for handle in action.used_handles],
                        self.binding.reference_operation_id,
                        [
                            _reference_value(reference)
                            for reference in self.binding.mandatory_references
                        ],
                    ]
                )
                authorization_id = (
                    "ctxa_v1_"
                    + hmac.new(
                        self._secret,
                        authorization_message,
                        hashlib.sha256,
                    ).hexdigest()
                )
                authorization = ContextAnswerAuthorization(
                    authorization_id=authorization_id,
                    correlation=action.correlation,
                    manifest_digest=self._manifest_fingerprint,
                    answer_digest=answer_digest,
                    completed_evidence=completed_evidence,
                    completed_read_digest=completed_read_digest,
                    used_evidence=tuple(
                        completed[handle.value] for handle in action.used_handles
                    ),
                    reference_operation_id=self.binding.reference_operation_id,
                    mandatory_references=self.binding.mandatory_references,
                )
                self._next_action_index += 1
                self._state = ContextInvocationState.ANSWER_AUTHORIZED
                self._clear_sensitive_locked()
                return authorization
            except BaseException:
                if executed:
                    self._record_failed_attempt_locked("answer")
                self._fail_locked()
                raise
