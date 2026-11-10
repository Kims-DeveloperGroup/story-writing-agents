"""Invocation session."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionCorrelation,
    ContextActionError,
    ContextActionErrorCode,
    ContextActionPolicy,
    ContextAnswerAction,
    ContextAnswerAuthorization,
    ContextCallReadFact,
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
    ContextSourceStatusReceipt,
    ContextZeroHitSearchReceipt,
    serialize_context_action_observation,
)
from ..contracts.call_offers import ContextCallOffers, ContextInvocationState
from ..contracts.context_values import (
    SEARCH_SOURCE_KIND_ORDER,
    action_error,
    authorized_source_fingerprint,
    boundary_value,
    canonical_bytes,
    completed_evidence_digest,
    digest,
    entry_value,
    evidence_value,
    manifest_fingerprint,
    reference_value,
    safe_identity,
    scope_value,
    status_value,
    zero_hit_value,
)
from ..contracts.errors import ContextExecutionErrorCode, execution_error
from ..contracts.planning_inputs import context_source_uses_catalog_alias
from ..contracts.session import ContextInvocationBinding, ContextReadReceipt
from .provider import ContextReferencePreludeRequest, ContextReferencePreludeResult
from ..manifest import ContextManifest, ContextManifestEntry, ContextSourceKind
from ..progress import ContextActionProgressSource
from ..sources.capture_values import (
    CONTEXT_SOURCE_POLICY,
    ContextSourcePolicy,
    ContextSourceSnapshot,
)
from ..sources.registry import ContextSourceRegistry


@dataclass(frozen=True, slots=True)
class _HandleBinding:
    handle: ContextSourceHandle
    entry: ContextManifestEntry
    boundary: ContextSourceBoundary
    origin_action_index: int
    safe_locator: str

class ContextInvocationSession:
    """Own one synchronized invocation-local action and evidence lifecycle."""

    def __init__(
        self,
        binding: ContextInvocationBinding,
        sources: ContextSourceRegistry,
        *,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
        source_aliases: Mapping[str, str] | None = None,
        allowed_search_source_kinds: tuple[ContextSourceKind, ...] = (
            SEARCH_SOURCE_KIND_ORDER
        ),
        physical_call_limit: int | None = None,
        core_statuses: tuple[ContextSourceStatusReceipt, ...] = (),
    ) -> None:
        if (
            type(binding) is not ContextInvocationBinding
            or type(sources) is not ContextSourceRegistry
            or sources.scope != binding.scope
            or type(action_policy) is not ContextActionPolicy
            or type(source_policy) is not ContextSourcePolicy
            or type(allowed_search_source_kinds) is not tuple
            or not allowed_search_source_kinds
            or any(
                type(kind) is not ContextSourceKind
                or kind not in SEARCH_SOURCE_KIND_ORDER
                for kind in allowed_search_source_kinds
            )
            or allowed_search_source_kinds
            != tuple(
                kind
                for kind in SEARCH_SOURCE_KIND_ORDER
                if kind in allowed_search_source_kinds
            )
            or (
                physical_call_limit is not None
                and (
                    type(physical_call_limit) is not int
                    or physical_call_limit < 1
                    or physical_call_limit > action_policy.max_provider_calls
                )
            )
            or type(core_statuses) is not tuple
            or any(
                type(item) is not ContextSourceStatusReceipt for item in core_statuses
            )
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        if source_aliases is not None:
            aliases = dict(source_aliases)
            readable_ids = {
                entry.source_id
                for entry in binding.manifest.entries
                if context_source_uses_catalog_alias(entry)
            }
            if (
                set(aliases) != readable_ids
                or any(
                    type(alias) is not str
                    or not alias.startswith("s")
                    or not alias[1:].isdigit()
                    or alias[1:].startswith("0")
                    for alias in aliases.values()
                )
                or len(set(aliases.values())) != len(aliases)
            ):
                raise action_error(ContextActionErrorCode.INVALID_VALUE)
            self._source_aliases: dict[str, str] | None = aliases
        else:
            self._source_aliases = None
        self._allowed_search_source_kinds = allowed_search_source_kinds
        self._physical_call_limit = (
            action_policy.max_provider_calls
            if physical_call_limit is None
            else physical_call_limit
        )
        self._remaining_provider_calls: int | None = None
        self._minimum_completion_calls = 1
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
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        self._secret: bytearray | None = bytearray(secret)
        self._manifest_fingerprint = manifest_fingerprint(binding.manifest)
        if any(
            item.manifest_digest != self._manifest_fingerprint for item in core_statuses
        ):
            raise action_error(ContextActionErrorCode.INVALID_VALUE)
        self._core_statuses = core_statuses
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
        self._wire_handle_by_full: dict[str, str] = {}
        self._full_handle_by_wire: dict[str, str] = {}
        self._safe_locator_by_full: dict[str, str] = {}
        self._completed_receipts: list[ContextReadReceipt] = []
        self._zero_hit_receipts: list[ContextZeroHitSearchReceipt] = []
        self._pending_receipt: ContextReadReceipt | None = None

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

    def contract_offers(
        self, call_id: str, *, allowed_actions: tuple[str, ...],
        delivery_read: ContextReadReceipt | None = None,
        reference_prelude: ContextReferencePreludeResult | None = None,
        reference_request: ContextReferencePreludeRequest | None = None,
    ) -> ContextCallOffers:
        with self._lock:
            if delivery_read is not None and delivery_read is not self._pending_receipt:
                raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            reference_facts = ()
            reference_digest = None
            if self.binding.mandatory_references:
                reference_facts, reference_digest = (
                    ContextReferencePreludeResult.validate_reference_binding(
                        reference_prelude,
                        reference_request=reference_request,
                        scope=self.binding.scope,
                        operation_id=self.binding.operation_id,
                        result_correlation_id=self.binding.result_correlation_id,
                        reference_operation_id=self.binding.reference_operation_id,
                        mandatory_references=self.binding.mandatory_references,
                    )
                )
            elif reference_prelude is not None or reference_request is not None:
                raise execution_error(ContextExecutionErrorCode.PROVIDER_CONTRACT)
            names = ("nonterminal_actions", "search_actions", "read_actions", "queries_per_search",
                     "cumulative_queries", "handles_per_read", "cumulative_handles", "query_characters")
            limits = tuple(zip(names, self.action_limits()))
            completed = reference_facts + tuple(ContextCallReadFact.from_evidence(evidence)
                for receipt in self._completed_receipts for evidence in receipt.evidence)
            delivery = (() if delivery_read is None else tuple(
                ContextCallReadFact.from_evidence(evidence) for evidence in delivery_read.evidence))
            offer = ContextCallOffers(
                call_id, self.binding.scope, self._next_action_index, allowed_actions, limits,
                self._allowed_search_source_kinds, tuple(self._full_handle_by_wire),
                completed, delivery, self._core_statuses, remaining_provider_calls=self._remaining_provider_calls)
            if reference_digest is not None:
                object.__setattr__(offer, "reference_read_digest", reference_digest)
                offer.__post_init__()
            return offer

    @property
    def completed_handles(self) -> tuple[ContextSourceHandle, ...]:
        """Return locally completed READ handles in deterministic receipt order."""

        with self._lock:
            return tuple(
                evidence.handle
                for receipt in self._completed_receipts
                for evidence in receipt.evidence
            )

    def _handle_message(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        origin_action_index: int,
    ) -> bytes:
        return canonical_bytes(
            [
                "context-source-handle-v1",
                scope_value(self.binding.scope),
                self.binding.operation_id,
                self.binding.result_correlation_id,
                self._manifest_fingerprint,
                entry_value(entry),
                boundary_value(boundary),
                origin_action_index,
            ]
        )

    def _build_handle_binding(
        self,
        entry: ContextManifestEntry,
        boundary: ContextSourceBoundary,
        *,
        origin_action_index: int,
        safe_locator: str,
    ) -> _HandleBinding:
        if self._secret is None:
            raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
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
            safe_locator,
        )

    def _register_handle(self, binding: _HandleBinding) -> None:
        existing = self._handles.get(binding.handle.value)
        if existing is not None and existing != binding:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._handles[binding.handle.value] = binding
        self._safe_locator_by_full[binding.handle.value] = binding.safe_locator

    def _register_wire_handle(self, handle: ContextSourceHandle) -> str:
        """Return the invocation alias for one already authorized full handle."""

        existing = self._wire_handle_by_full.get(handle.value)
        if existing is not None:
            return existing
        alias = self._wire_handle_alias(handle)
        collision = self._full_handle_by_wire.get(alias)
        if collision is not None and not hmac.compare_digest(
            collision,
            handle.value,
        ):
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        self._wire_handle_by_full[handle.value] = alias
        self._full_handle_by_wire[alias] = handle.value
        return alias

    def _wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Derive one invocation-bound 96-bit opaque provider handle."""

        if self._secret is None:
            raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
        digest = hmac.new(
            self._secret,
            b"context-wire-handle-alias-v5\x00"
            + handle.value.encode("ascii", errors="strict"),
            hashlib.sha256,
        ).digest()[:12]
        return "h" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _preview_handle_aliases(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        """Project exact aliases for a not-yet-committed search observation."""

        aliases = dict(self._wire_handle_by_full)
        for binding in pending:
            if binding.handle.value not in aliases:
                alias = self._wire_handle_alias(binding.handle)
                if alias in aliases.values():
                    raise action_error(ContextActionErrorCode.HANDLE_INVALID)
                aliases[binding.handle.value] = alias
        return aliases

    def _preview_safe_locators(
        self,
        pending: tuple[_HandleBinding, ...],
    ) -> dict[str, str]:
        locators = dict(self._safe_locator_by_full)
        locators.update(
            (binding.handle.value, binding.safe_locator) for binding in pending
        )
        return locators

    def resolve_wire_handle(self, value: object) -> ContextSourceHandle:
        """Resolve one emitted invocation alias to its full opaque authority."""

        if self._source_aliases is None:
            return ContextSourceHandle(value)
        candidate = "" if type(value) is not str else value
        matched: str | None = None
        for alias in self._full_handle_by_wire:
            if hmac.compare_digest(alias, candidate):
                matched = alias
        if matched is None:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        return ContextSourceHandle(self._full_handle_by_wire[matched])

    def progress_sources(
        self,
        handles: tuple[ContextSourceHandle, ...],
        *,
        utf8_bytes: Mapping[str, int] | None = None,
    ) -> tuple[ContextActionProgressSource, ...]:
        """Describe authorized handles without exposing their opaque authority."""

        with self._lock:
            bindings = tuple(self._lookup_handle(handle) for handle in handles)
            return tuple(
                ContextActionProgressSource(
                    source_kind=binding.entry.source_kind,
                    locator=binding.safe_locator,
                    boundary=binding.boundary,
                    utf8_bytes=(
                        None
                        if utf8_bytes is None
                        else utf8_bytes.get(binding.handle.value)
                    ),
                )
                for binding in bindings
            )

    def serialize_observation(
        self,
        observation: ContextSearchObservation | ContextReadObservation,
    ) -> str:
        """Serialize one observation with this invocation's compact aliases."""

        if self._source_aliases is None:
            return serialize_context_action_observation(observation)
        return serialize_context_action_observation(
            observation,
            source_aliases=self._source_aliases,
            handle_aliases=self._wire_handle_by_full,
            safe_locators=self._safe_locator_by_full,
        )

    def source_alias(self, source_id: str) -> str:
        """Return the compact source alias configured for this invocation."""

        if self._source_aliases is None:
            return source_id
        alias = self._source_aliases.get(source_id)
        if alias is None:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    @property
    def core_statuses(self) -> tuple[ContextSourceStatusReceipt, ...]:
        """Return the invocation-bound content-free canonical core statuses."""

        return self._core_statuses

    def wire_handle_alias(self, handle: ContextSourceHandle) -> str:
        """Return an already-emitted compact alias for one full handle."""

        if self._source_aliases is None:
            return handle.value
        alias = self._wire_handle_by_full.get(handle.value)
        if alias is None:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        return alias

    def _lookup_handle(self, handle: ContextSourceHandle) -> _HandleBinding:
        matched: str | None = None
        for candidate in self._handles:
            if hmac.compare_digest(candidate, handle.value):
                matched = candidate
        if matched is None:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
        return self._handles[matched]

    def _require_open(self) -> None:
        if self._state is ContextInvocationState.READING:
            raise action_error(ContextActionErrorCode.OUTSTANDING_READ)
        if self._state is not ContextInvocationState.OPEN:
            raise action_error(ContextActionErrorCode.INVALID_TRANSITION)

    def _validate_correlation(self, correlation: ContextActionCorrelation) -> None:
        if (
            type(correlation) is not ContextActionCorrelation
            or correlation.scope != self.binding.scope
            or correlation.operation_id != self.binding.operation_id
            or correlation.result_correlation_id != self.binding.result_correlation_id
            or correlation.action_index != self._next_action_index
        ):
            raise action_error(ContextActionErrorCode.CORRELATION_MISMATCH)
        if correlation.action_index > self._physical_call_limit:
            raise action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def _require_nonterminal_capacity(self) -> None:
        if self._nonterminal_actions >= self._action_policy.max_nonterminal_actions:
            raise action_error(ContextActionErrorCode.ACTION_LIMIT_EXCEEDED)

    def advance_group_control(self, correlation: ContextActionCorrelation) -> None:
        """선택 제어의 순번만 진행하고 업무·컨텍스트 권한은 만들지 않는다."""
        with self._lock:
            self._require_open()
            self._validate_correlation(correlation)
            self._next_action_index += 1

    def _fail_locked(self) -> None:
        self._state = ContextInvocationState.FAILED
        self._clear_sensitive_locked()

    def _clear_sensitive_locked(self) -> None:
        if self._secret is not None:
            for index in range(len(self._secret)):
                self._secret[index] = 0
            self._secret = None
        self._handles.clear()
        self._wire_handle_by_full.clear()
        self._full_handle_by_wire.clear()
        self._safe_locator_by_full.clear()
        self._pending_receipt = None
        self._completed_receipts.clear()

    def set_remaining_provider_calls(
        self, remaining: int, *, minimum_completion_calls: int = 1,
    ) -> None:
        """실제 호출 원장에서 계산한 잔여량과 현재 국면의 완료 비용만 반영한다."""
        with self._lock:
            if (type(remaining) is not int or remaining < 0 or remaining > self._physical_call_limit
                    or type(minimum_completion_calls) is not int or minimum_completion_calls < 1
                    or (self._remaining_provider_calls is not None
                        and remaining > self._remaining_provider_calls)):
                raise action_error(ContextActionErrorCode.INVALID_VALUE)
            self._remaining_provider_calls = remaining
            self._minimum_completion_calls = minimum_completion_calls

    @property
    def remaining_provider_calls(self) -> int:
        with self._lock:
            return (max(0, self._physical_call_limit - self._next_action_index + 1)
                    if self._remaining_provider_calls is None else self._remaining_provider_calls)

    def allowed_actions(
        self,
        *,
        after: str | None,
        search_had_hits: bool = False,
    ) -> tuple[str, ...]:
        """Return the one authoritative action tuple for the next call."""

        with self._lock:
            if (
                after not in {None, "search", "read"}
                or type(search_had_hits) is not bool
            ):
                raise action_error(ContextActionErrorCode.INVALID_VALUE)
            nonterminal_capacity = (
                (self._physical_call_limit - self._next_action_index
                 if self._remaining_provider_calls is None
                 else self._remaining_provider_calls - self._minimum_completion_calls) > 0
                and self._nonterminal_actions < self._action_policy.max_nonterminal_actions
            )
            if not nonterminal_capacity:
                return ("answer",)
            can_search = bool(
                self._search_actions < self._action_policy.max_search_actions
                and self._queries < self._action_policy.max_cumulative_queries
                and self._allowed_search_source_kinds
            )
            can_read = bool(
                self._read_actions < self._action_policy.max_read_actions
                and self._read_handles < self._action_policy.max_cumulative_handles
                and self._handles
            )
            actions: list[str] = []
            if can_search:
                actions.append("search")
            if can_read and (
                (after == "search" and search_had_hits) or after == "read"
            ):
                actions.append("read")
            actions.append("answer")
            return tuple(actions)

    def action_limits(self) -> list[int]:
        """Return exact remaining dynamic limits for the next wire call."""

        with self._lock:
            return [
                max(
                    0,
                    min(
                        self._action_policy.max_nonterminal_actions
                        - self._nonterminal_actions,
                        (self._physical_call_limit - self._next_action_index
                     if self._remaining_provider_calls is None else max(
                         0, self._remaining_provider_calls - self._minimum_completion_calls)),
                    ),
                ),
                max(
                    0,
                    self._action_policy.max_search_actions - self._search_actions,
                ),
                max(
                    0,
                    self._action_policy.max_read_actions - self._read_actions,
                ),
                self._action_policy.max_queries_per_search,
                max(
                    0,
                    self._action_policy.max_cumulative_queries - self._queries,
                ),
                self._action_policy.max_handles_per_read,
                max(
                    0,
                    self._action_policy.max_cumulative_handles - self._read_handles,
                ),
                self._action_policy.max_query_characters,
            ]

    @property
    def allowed_search_source_kinds(self) -> tuple[ContextSourceKind, ...]:
        """Return the immutable route-specific search authority."""

        return self._allowed_search_source_kinds

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

    @staticmethod
    def _search_safe_locator(
        entry: ContextManifestEntry,
        record_content: str,
    ) -> str:
        """Retain own-state debate identity in an otherwise canonical locator."""

        locator = entry.provenance.locator
        if (
            entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
            and locator.startswith("manuscript/")
        ):
            return locator.removeprefix("manuscript/")
        if entry.source_kind is not ContextSourceKind.OWN_AGENT_STATE:
            return locator
        try:
            value = json.loads(record_content)
            debate_id = safe_identity(value.get("debate_id"))
        except (
            AttributeError,
            ContextActionError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            raise action_error(ContextActionErrorCode.HANDLE_INVALID) from None
        return f"{locator}/{debate_id}"

    def _search_manifest(
        self,
        artifact_path_prefix: str | None,
    ) -> ContextManifest:
        """Filter one shared artifact-prefix scope before any source scan."""

        if artifact_path_prefix is None:
            return self.binding.manifest
        return ContextManifest(
            self.binding.scope,
            tuple(
                entry
                for entry in self.binding.manifest.entries
                if entry.source_kind is ContextSourceKind.WRITING_ARTIFACT
                and entry.provenance.locator.startswith(artifact_path_prefix)
            ),
        )

    def search(self, action: ContextSearchAction) -> ContextSearchObservation:
        """Execute one bounded search and atomically retain its emitted handles."""

        with self._lock:
            executed = False
            try:
                self._require_open()
                if type(action) is not ContextSearchAction:
                    raise action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._search_actions >= self._action_policy.max_search_actions:
                    raise action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if len(action.queries) > self._action_policy.max_queries_per_search:
                    raise action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if any(
                    len(query.text) > self._action_policy.max_query_characters
                    or len(query.normalized_text)
                    > self._action_policy.max_query_characters
                    or any(
                        kind not in self._allowed_search_source_kinds
                        for kind in query.source_kinds
                    )
                    for query in action.queries
                ):
                    raise action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                if self._queries + len(action.queries) > (
                    self._action_policy.max_cumulative_queries
                ):
                    raise action_error(ContextActionErrorCode.SEARCH_LIMIT_EXCEEDED)
                executed = True
                self._state = ContextInvocationState.SEARCHING
                remaining_scan = (
                    self._source_policy.max_cumulative_scan_utf8_bytes
                    - self._scan_utf8_bytes
                )
                matches, truncated, scanned = self._sources.search(
                    self._search_manifest(action.queries[0].artifact_path_prefix),
                    action.queries,
                    source_policy=self._source_policy,
                    action_policy=self._action_policy,
                    remaining_scan_utf8_bytes=remaining_scan,
                )
                if self._state is not ContextInvocationState.SEARCHING:
                    raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
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
                        raise action_error(ContextActionErrorCode.HANDLE_INVALID)
                    handle_binding = self._build_handle_binding(
                        entry,
                        match.record.boundary,
                        origin_action_index=action.correlation.action_index,
                        safe_locator=self._search_safe_locator(
                            entry,
                            match.record.content,
                        ),
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
                    search_manifest = self._search_manifest(
                        action.queries[0].artifact_path_prefix
                    )
                    requested_kinds = tuple(
                        kind
                        for kind in SEARCH_SOURCE_KIND_ORDER
                        if any(kind in query.source_kinds for query in action.queries)
                    )
                    authorized_entries = tuple(
                        entry
                        for entry in search_manifest.entries
                        if entry.searchable and entry.source_kind in requested_kinds
                    )
                    zero_hit_receipt = (
                        ContextZeroHitSearchReceipt(
                            evidence_id=(f"search_{action.correlation.action_index}"),
                            correlation=action.correlation,
                            manifest_digest=self._manifest_fingerprint,
                            authorized_source_ids=tuple(
                                entry.source_id for entry in authorized_entries
                            ),
                            authorized_source_kinds=requested_kinds,
                            authorized_source_fingerprint=(
                                authorized_source_fingerprint(authorized_entries)
                            ),
                            artifact_path_prefix=(
                                action.queries[0].artifact_path_prefix
                            ),
                            query_digest=digest(
                                canonical_bytes(
                                    [
                                        "context-zero-hit-query-v1",
                                        [
                                            [
                                                query.normalized_text,
                                                [
                                                    kind.value
                                                    for kind in query.source_kinds
                                                ],
                                                query.artifact_path_prefix,
                                            ]
                                            for query in action.queries
                                        ],
                                    ]
                                )
                            ),
                            query_count=len(action.queries),
                        )
                        if not hits and not truncated
                        else None
                    )
                    observation = ContextSearchObservation(
                        action.correlation,
                        tuple(hits),
                        truncated,
                        zero_hit_receipt,
                    )
                    framed_bytes = len(
                        serialize_context_action_observation(
                            observation,
                            source_aliases=self._source_aliases,
                            handle_aliases=self._preview_handle_aliases(
                                tuple(pending_bindings)
                            ),
                            safe_locators=self._preview_safe_locators(
                                tuple(pending_bindings)
                            ),
                        ).encode("utf-8")
                        if self._source_aliases is not None
                        else serialize_context_action_observation(observation).encode(
                            "utf-8"
                        )
                    )
                    if framed_bytes <= available:
                        break
                    if not hits:
                        raise action_error(
                            ContextActionErrorCode.OBSERVATION_LIMIT_EXCEEDED
                        )
                    hits.pop()
                    pending_bindings.pop()
                    truncated = True
                if self._state is not ContextInvocationState.SEARCHING:
                    raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
                for handle_binding in pending_bindings:
                    self._register_handle(handle_binding)
                    self._register_wire_handle(handle_binding.handle)
                self._nonterminal_actions += 1
                self._search_actions += 1
                self._queries += len(action.queries)
                self._hits += len(hits)
                self._scan_utf8_bytes += scanned
                self._observation_utf8_bytes += framed_bytes
                if observation.zero_hit_receipt is not None:
                    self._zero_hit_receipts.append(observation.zero_hit_receipt)
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
            if len(selected) != boundary.end - boundary.start + 1 or tuple(
                record.boundary.start for record in selected
            ) != tuple(range(boundary.start, boundary.end + 1)):
                raise action_error(ContextActionErrorCode.HANDLE_INVALID)
            if boundary.kind is ContextSourceBoundaryKind.ARTIFACT_LINES:
                return b"".join(record.content.encode("utf-8") for record in selected)
            if len(selected) == 1:
                return selected[0].content.encode("utf-8")
            return canonical_bytes([json.loads(record.content) for record in selected])
        matches = tuple(
            record for record in snapshot.records if record.boundary == boundary
        )
        if len(matches) != 1:
            raise action_error(ContextActionErrorCode.HANDLE_INVALID)
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
                    raise action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                self._require_nonterminal_capacity()
                if self._read_actions >= self._action_policy.max_read_actions:
                    raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if len(action.handles) > self._action_policy.max_handles_per_read:
                    raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                if self._read_handles + len(action.handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                bindings = tuple(
                    self._lookup_handle(handle) for handle in action.handles
                )
                executed = True
                self._state = ContextInvocationState.READING
                requested_source_ids = {binding.entry.source_id for binding in bindings}
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
                        raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
                source_additions: dict[str, int] = {}
                evidence: list[ContextSourceEvidence] = []
                batch_bytes = 0
                for evidence_ordinal, handle_binding in enumerate(bindings, start=1):
                    entry = handle_binding.entry
                    snapshot = snapshots[entry.source_id]
                    excerpt = self._boundary_content(
                        snapshot,
                        handle_binding.boundary,
                    )
                    if len(excerpt) > self._action_policy.max_excerpt_utf8_bytes:
                        raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    source_additions[entry.source_id] = source_additions.get(
                        entry.source_id, 0
                    ) + len(excerpt)
                    prior_source_bytes = self._source_read_utf8_bytes.get(
                        entry.source_id,
                        0,
                    )
                    if prior_source_bytes + source_additions[entry.source_id] > (
                        self._action_policy.max_source_read_utf8_bytes
                    ):
                        raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    batch_bytes += len(excerpt)
                    if self._read_utf8_bytes + batch_bytes > (
                        self._action_policy.max_read_utf8_bytes
                    ):
                        raise action_error(ContextActionErrorCode.READ_LIMIT_EXCEEDED)
                    receipt = ContextEvidenceReceipt(
                        evidence_id=(
                            f"read_{action.correlation.action_index}_{evidence_ordinal}"
                        ),
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
                        originating_search_action_index=(
                            handle_binding.origin_action_index
                        ),
                        completing_read_action_index=(action.correlation.action_index),
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
                        raise action_error(ContextActionErrorCode.HANDLE_INVALID)
                    if self._state is not ContextInvocationState.READING:
                        raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
                observation = ContextReadObservation(
                    action.correlation,
                    tuple(evidence),
                )
                framed = self.serialize_observation(observation).encode("utf-8")
                if self._observation_utf8_bytes + len(framed) > (
                    self._action_policy.max_observation_utf8_bytes
                ):
                    raise action_error(
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
                    raise action_error(ContextActionErrorCode.OUTSTANDING_READ)
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
                    raise action_error(ContextActionErrorCode.INVALID_VALUE)
                self._validate_correlation(action.correlation)
                completed_evidence = tuple(
                    evidence
                    for receipt in self._completed_receipts
                    for evidence in receipt.evidence
                )
                completed = {
                    evidence.handle.value: evidence for evidence in completed_evidence
                }
                if len(completed) != len(completed_evidence):
                    raise action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if any(handle.value not in completed for handle in action.used_handles):
                    raise action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
                if len(action.used_handles) > (
                    self._action_policy.max_cumulative_handles
                ):
                    raise action_error(ContextActionErrorCode.ANSWER_NOT_AUTHORIZED)
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
                        raise action_error(
                            ContextActionErrorCode.ANSWER_NOT_AUTHORIZED
                        )
                    self._sources.capture(
                        self.binding.manifest,
                        entry,
                        self._source_policy,
                    )
                    if self._state is not ContextInvocationState.SEARCHING:
                        raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
                completed_read_digest = completed_evidence_digest(completed_evidence)
                answer_digest = digest(action.answer.encode("utf-8"))
                if self._secret is None:
                    raise action_error(ContextActionErrorCode.INVALID_TRANSITION)
                authorization_message = canonical_bytes(
                    [
                        "context-answer-authorization-v1",
                        scope_value(self.binding.scope),
                        self.binding.operation_id,
                        self.binding.result_correlation_id,
                        action.correlation.action_index,
                        self._manifest_fingerprint,
                        answer_digest,
                        completed_read_digest,
                        [evidence_value(item) for item in completed_evidence],
                        [handle.value for handle in action.used_handles],
                        [status_value(item) for item in self._core_statuses],
                        [zero_hit_value(item) for item in self._zero_hit_receipts],
                        self.binding.reference_operation_id,
                        [
                            reference_value(reference)
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
                    core_statuses=self._core_statuses,
                    zero_hit_searches=tuple(self._zero_hit_receipts),
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


__all__ = ["ContextInvocationSession"]
