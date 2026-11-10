"""Registry."""

from __future__ import annotations

import json
import unicodedata
from ..actions import (
    CONTEXT_ACTION_POLICY,
    ContextActionPolicy,
    ContextSearchQuery,
    ContextSourceBoundary,
    ContextSourceStatusReceipt,
)
from ..manifest import (
    ContextManifest,
    ContextManifestEntry,
    ContextSourceKind,
    context_manifest_entry_key,
)
from .active_debate import ActiveDebateSourceAdapter
from .capture_values import (
    CONTEXT_SOURCE_POLICY,
    ContextSourceDescriptor,
    ContextSourceErrorCode,
    ContextSourcePolicy,
    ContextSourceRecord,
    ContextSourceSearchMatch,
    ContextSourceSnapshot,
    source_snapshot_order_key,
    require_source_policy,
    source_error,
    source_nonnegative,
    source_record,
)
from .focused_conversation import FocusedConversationSourceAdapter
from .own_agent_state import OwnAgentStateSourceAdapter
from .writing_artifact import WritingArtifactSourceAdapter


def _descriptor_matches_entry(
    descriptor: ContextSourceDescriptor,
    entry: ContextManifestEntry,
) -> bool:
    return (
        descriptor.source_id == entry.source_id
        and descriptor.source_kind is entry.source_kind
        and descriptor.revision == entry.revision
        and descriptor.provenance == entry.provenance
        and descriptor.content_digest == entry.content_digest
        and descriptor.utf8_bytes == entry.utf8_bytes
    )

def _normalize_source_text(value: str) -> str:
    replaced = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    return " ".join(unicodedata.normalize("NFKC", replaced).casefold().split())

def _snippet(
    normalized: str,
    occurrence: int,
    literal: str,
    policy: ContextActionPolicy,
) -> str:
    literal_end = occurrence + len(literal)
    if literal_end > len(normalized):
        raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    start = max(0, occurrence - policy.max_snippet_characters // 3)
    end = min(len(normalized), start + policy.max_snippet_characters)
    if end < literal_end:
        end = literal_end
        start = max(0, end - policy.max_snippet_characters)
    while len(normalized[start:end].encode("utf-8")) > (policy.max_snippet_utf8_bytes):
        if start < occurrence:
            start += 1
        elif end > literal_end:
            end -= 1
        else:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
    candidate = normalized[start:end]
    if literal not in candidate:
        raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
    return candidate

def _expanded_artifact_record(
    snapshot: ContextSourceSnapshot,
    record: ContextSourceRecord,
    max_utf8_bytes: int,
) -> ContextSourceRecord | None:
    assert record.boundary.start is not None
    center = record.boundary.start
    if record.utf8_bytes > max_utf8_bytes:
        return None
    selected_start = center
    selected_end = center
    upper = len(snapshot.records)
    for distance in range(1, 13):
        candidate_start = center - distance
        if candidate_start >= 1:
            content = "".join(
                item.content
                for item in snapshot.records[candidate_start - 1 : selected_end]
            )
            if len(content.encode("utf-8")) <= max_utf8_bytes:
                selected_start = candidate_start
        candidate_end = center + distance
        if candidate_end <= upper:
            content = "".join(
                item.content
                for item in snapshot.records[selected_start - 1 : candidate_end]
            )
            if len(content.encode("utf-8")) <= max_utf8_bytes:
                selected_end = candidate_end
    content = "".join(
        item.content for item in snapshot.records[selected_start - 1 : selected_end]
    )
    return source_record(
        f"lines_{selected_start}_{selected_end}",
        ContextSourceBoundary.artifact_lines(selected_start, selected_end),
        content,
        content,
    )

class ContextSourceRegistry:
    """Bind exactly four canonical authority adapters for one invocation scope."""

    def __init__(
        self,
        writing_artifacts: WritingArtifactSourceAdapter,
        focused_conversation: FocusedConversationSourceAdapter,
        active_debate: ActiveDebateSourceAdapter,
        own_agent_state: OwnAgentStateSourceAdapter,
    ) -> None:
        adapters = (
            writing_artifacts,
            focused_conversation,
            active_debate,
            own_agent_state,
        )
        expected_types = (
            WritingArtifactSourceAdapter,
            FocusedConversationSourceAdapter,
            ActiveDebateSourceAdapter,
            OwnAgentStateSourceAdapter,
        )
        if any(
            type(adapter) is not expected
            for adapter, expected in zip(adapters, expected_types)
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        scopes = {adapter.scope for adapter in adapters}
        if len(scopes) != 1:
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        self.scope = adapters[0].scope
        self._adapters = adapters
        self._by_kind = {adapter.source_kind: adapter for adapter in adapters}

    def canonical_core_statuses(
        self,
        manifest_digest: str,
        policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> tuple[ContextSourceStatusReceipt, ...]:
        """Return exact core statuses from the writing-artifact authority."""

        return self._adapters[0].canonical_core_statuses(manifest_digest, policy)

    def inventory(
        self,
        policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> tuple[ContextSourceDescriptor, ...]:
        if type(policy) is not ContextSourcePolicy:
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        descriptors: list[ContextSourceDescriptor] = []
        for adapter in self._adapters:
            descriptors.extend(adapter.inventory(policy))
            if len(descriptors) > policy.max_inventory_sources:
                raise source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if sum(item.utf8_bytes for item in descriptors) > (
            policy.max_inventory_body_utf8_bytes
        ):
            raise source_error(ContextSourceErrorCode.INVENTORY_LIMIT_EXCEEDED)
        if len({item.source_id for item in descriptors}) != len(descriptors):
            raise source_error(ContextSourceErrorCode.ADAPTER_CONTRACT_VIOLATION)
        return tuple(
            sorted(
                descriptors,
                key=source_snapshot_order_key,
            )
        )

    def capture(
        self,
        manifest: ContextManifest,
        entry: ContextManifestEntry,
        policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
    ) -> ContextSourceSnapshot:
        policy = require_source_policy(policy)
        if (
            type(manifest) is not ContextManifest
            or manifest.scope != self.scope
            or type(entry) is not ContextManifestEntry
            or entry not in manifest.entries
        ):
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if entry.utf8_bytes > policy.max_source_utf8_bytes:
            raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
        adapter = self._by_kind.get(entry.source_kind)
        if adapter is None:
            raise source_error(ContextSourceErrorCode.AUTHORITY_MISMATCH)
        snapshot = adapter.capture(entry.provenance.locator, policy)
        if snapshot.descriptor.scope != manifest.scope:
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if not _descriptor_matches_entry(snapshot.descriptor, entry):
            raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return snapshot

    def search(
        self,
        manifest: ContextManifest,
        queries: tuple[ContextSearchQuery, ...],
        *,
        source_policy: ContextSourcePolicy = CONTEXT_SOURCE_POLICY,
        action_policy: ContextActionPolicy = CONTEXT_ACTION_POLICY,
        remaining_scan_utf8_bytes: int | None = None,
    ) -> tuple[tuple[ContextSourceSearchMatch, ...], bool, int]:
        if type(manifest) is not ContextManifest or manifest.scope != self.scope:
            raise source_error(ContextSourceErrorCode.SCOPE_MISMATCH)
        if (
            type(source_policy) is not ContextSourcePolicy
            or type(action_policy) is not ContextActionPolicy
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if (
            type(queries) is not tuple
            or not queries
            or len(queries) > action_policy.max_queries_per_search
            or any(
                type(query) is not ContextSearchQuery
                or len(query.text) > action_policy.max_query_characters
                or len(query.normalized_text) > action_policy.max_query_characters
                for query in queries
            )
        ):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        if remaining_scan_utf8_bytes is None:
            remaining_scan_utf8_bytes = source_policy.max_cumulative_scan_utf8_bytes
        source_nonnegative(remaining_scan_utf8_bytes)
        remaining_scan_utf8_bytes = min(
            remaining_scan_utf8_bytes,
            source_policy.max_cumulative_scan_utf8_bytes,
        )
        normalized_pairs = tuple(
            (query.normalized_text, query.source_kinds) for query in queries
        )
        if len(set(normalized_pairs)) != len(normalized_pairs):
            raise source_error(ContextSourceErrorCode.INVALID_VALUE)
        matches: list[ContextSourceSearchMatch] = []
        scanned = 0
        truncated = False
        requested_kinds = {
            source_kind for query in queries for source_kind in query.source_kinds
        }
        entries = tuple(
            entry
            for entry in sorted(manifest.entries, key=context_manifest_entry_key)
            if entry.searchable and entry.source_kind in requested_kinds
        )
        captured: list[tuple[ContextManifestEntry, ContextSourceSnapshot]] = []
        for entry in entries:
            if entry.utf8_bytes > source_policy.max_source_scan_utf8_bytes:
                truncated = True
                continue
            if scanned + entry.utf8_bytes > remaining_scan_utf8_bytes:
                truncated = True
                break
            snapshot = self.capture(manifest, entry, source_policy)
            if snapshot.descriptor.utf8_bytes > (
                source_policy.max_source_scan_utf8_bytes
            ):
                raise source_error(ContextSourceErrorCode.SOURCE_LIMIT_EXCEEDED)
            scanned += snapshot.descriptor.utf8_bytes
            if (
                entry.source_kind is ContextSourceKind.CONVERSATION
                and json.loads(snapshot.content)["bootstrap"]["truncated"]
            ):
                truncated = True
            captured.append((entry, snapshot))
        emitted: set[tuple[str, str, ContextSourceBoundary]] = set()
        for query_index, query in enumerate(queries, start=1):
            query_hits = 0
            for entry, snapshot in captured:
                if entry.source_kind not in query.source_kinds:
                    continue
                source_hits = 0
                records = (
                    reversed(snapshot.records)
                    if entry.source_kind is ContextSourceKind.CONVERSATION
                    else iter(snapshot.records)
                )
                for record in records:
                    normalized = _normalize_source_text(record.search_text)
                    occurrence = normalized.find(query.normalized_text)
                    if occurrence < 0:
                        continue
                    selected = record
                    if entry.source_kind is ContextSourceKind.WRITING_ARTIFACT:
                        selected = _expanded_artifact_record(
                            snapshot,
                            record,
                            action_policy.max_excerpt_utf8_bytes,
                        )
                    if (
                        selected is None
                        or selected.utf8_bytes > action_policy.max_excerpt_utf8_bytes
                    ):
                        truncated = True
                        continue
                    identity = (
                        entry.source_id,
                        entry.revision,
                        selected.boundary,
                    )
                    if identity in emitted:
                        continue
                    if (
                        source_hits >= action_policy.max_hits_per_source_query
                        or query_hits >= action_policy.max_hits_per_query
                        or len(matches) >= action_policy.max_hits_per_search
                    ):
                        truncated = True
                        continue
                    snippet = _snippet(
                        normalized,
                        occurrence,
                        query.normalized_text,
                        action_policy,
                    )
                    matches.append(
                        ContextSourceSearchMatch(
                            query_index=query_index,
                            descriptor=snapshot.descriptor,
                            record=selected,
                            snippet=snippet,
                            snippet_utf8_bytes=len(snippet.encode("utf-8")),
                        )
                    )
                    emitted.add(identity)
                    source_hits += 1
                    query_hits += 1
        for entry, snapshot in captured:
            if self.capture(manifest, entry, source_policy) != snapshot:
                raise source_error(ContextSourceErrorCode.SOURCE_DRIFT)
        return tuple(matches), truncated, scanned
