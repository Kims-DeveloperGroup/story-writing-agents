"""Deterministic budgeted agent-context projection contracts."""

from __future__ import annotations

import ast
import hashlib
import inspect
import itertools
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from unittest import mock

import story_writing_agents
from story_writing_agents import agent_context, writing_artifacts
from story_writing_agents.agent_context import manifest as manifest_module
from story_writing_agents.agent_context import planning as planning_module
from story_writing_agents.agent_context.manifest import (
    ContextAuthorityKind,
    ContextManifest,
    ContextManifestEntry,
    ContextPriority,
    ContextProvenance,
    ContextScope,
    ContextSemanticChannel,
    ContextSourceKind,
    ContextSubjectKind,
)
from story_writing_agents.agent_context.planning import (
    CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    DEFAULT_CONTEXT_PLANNING_POLICY,
    MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
    ContextBootstrapReason,
    ContextBudgetAccounting,
    ContextCheckpointProposal,
    ContextCatalogCursor,
    ContextCheckpointRecord,
    ContextCheckpointSource,
    ContextCheckpointState,
    ContextCheckpointView,
    ContextDecisionAccounting,
    ContextDecisionKind,
    ContextDeliveryMode,
    ContextMaterial,
    ContextPlanningConditions,
    ContextPlanningError,
    ContextPlanningErrorCode,
    ContextPlanningPolicy,
    ContextProjectedUnit,
    ContextProjection,
    ContextProjectionAccounting,
    ContextRecordCursor,
    ContextRecordMaterial,
    ContextReferenceBudgetUsage,
    ContextSearchableAccounting,
    ContextSourceBudgetUsage,
    ContextSourceDecision,
    MandatoryReferenceReservation,
    plan_context_projection,
)


_UNSET = object()
_AUTHORITY_BY_KIND = {
    ContextSourceKind.RUNTIME_INPUT: ContextAuthorityKind.REQUEST,
    ContextSourceKind.AGENT_DEFINITION: ContextAuthorityKind.AGENT_DEFINITION,
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY
    ),
    ContextSourceKind.CONVERSATION: ContextAuthorityKind.CONVERSATION_STORAGE,
    ContextSourceKind.ACTIVE_DEBATE: ContextAuthorityKind.ACTIVE_DEBATE,
    ContextSourceKind.OWN_AGENT_STATE: ContextAuthorityKind.OWN_AGENT_STATE,
}
_DEFAULT_CLASSIFICATION = {
    ContextSourceKind.RUNTIME_INPUT: (
        ContextSemanticChannel.CURRENT_INPUT,
        ContextPriority.P0_REQUIRED,
    ),
    ContextSourceKind.AGENT_DEFINITION: (
        ContextSemanticChannel.STABLE_BEHAVIOR,
        ContextPriority.P1_STABLE,
    ),
    ContextSourceKind.WRITING_ARTIFACT: (
        ContextSemanticChannel.FOCUSED_EVIDENCE,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.CONVERSATION: (
        ContextSemanticChannel.HISTORY,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.ACTIVE_DEBATE: (
        ContextSemanticChannel.HARD_CONSTRAINT,
        ContextPriority.P2_WORKING,
    ),
    ContextSourceKind.OWN_AGENT_STATE: (
        ContextSemanticChannel.OPTIONAL_BACKGROUND,
        ContextPriority.P3_OPTIONAL,
    ),
}


def _digest(body_utf8: bytes) -> str:
    return f"sha256:{hashlib.sha256(body_utf8).hexdigest()}"


def _scope(
    workspace_id: str = "workspace",
    session_id: str = "session",
    subject_kind: ContextSubjectKind = ContextSubjectKind.CONVERSATION,
    subject_id: str = "c_main",
    agent_id: str = "lead_orchestrator",
) -> ContextScope:
    return ContextScope(
        workspace_id,
        session_id,
        subject_kind,
        subject_id,
        agent_id,
    )


def _entry(
    source_id: str,
    body_utf8: bytes,
    *,
    source_kind: ContextSourceKind = ContextSourceKind.WRITING_ARTIFACT,
    semantic_channel: ContextSemanticChannel | None = None,
    priority: ContextPriority | None = None,
    ordinal: int = 0,
    revision: str = "revision-1",
    provenance: ContextProvenance | None = None,
    content_digest: object = _UNSET,
    utf8_bytes: object = _UNSET,
    searchable: object = _UNSET,
) -> ContextManifestEntry:
    default_channel, default_priority = _DEFAULT_CLASSIFICATION[source_kind]
    selected_priority = priority or default_priority
    return ContextManifestEntry(
        source_id,
        source_kind,
        semantic_channel or default_channel,
        selected_priority,
        ordinal,
        revision,
        _digest(body_utf8) if content_digest is _UNSET else content_digest,
        provenance
        or ContextProvenance(
            _AUTHORITY_BY_KIND[source_kind],
            f"sources/{source_id}",
        ),
        len(body_utf8) if utf8_bytes is _UNSET else utf8_bytes,
        searchable=(
            selected_priority
            in {ContextPriority.P2_WORKING, ContextPriority.P3_OPTIONAL}
            if searchable is _UNSET
            else searchable
        ),
    )  # type: ignore[arg-type]


def _source(
    source_id: str,
    body_utf8: bytes = b"x",
    **entry_overrides: object,
) -> tuple[ContextManifestEntry, ContextMaterial]:
    entry = _entry(source_id, body_utf8, **entry_overrides)  # type: ignore[arg-type]
    return entry, ContextMaterial(entry, body_utf8)


def _record(
    record_id: str,
    body_utf8: bytes,
    ordinal: int,
) -> ContextRecordMaterial:
    return ContextRecordMaterial(
        ContextRecordCursor(
            record_id,
            "records",
            ordinal,
            ordinal,
            None,
            None,
            _digest(body_utf8),
            len(body_utf8),
        ),
        body_utf8,
    )


def _checkpoint_from_projection(
    projection: ContextProjection,
    *,
    provider_thread_id: str = "thread",
) -> ContextCheckpointView:
    proposal = projection.checkpoint_proposal
    return ContextCheckpointView(
        ContextCheckpointState.READY,
        proposal.conditions,
        provider_thread_id,
        proposal.sources,
        proposal.delta_depth,
        proposal.catalog_sources,
        proposal.source_alias_high_watermark,
    )


def _policy(**overrides: int) -> ContextPlanningPolicy:
    values = {
        "total_source_utf8_bytes": 64,
        "p0_source_utf8_bytes": 16,
        "p1_source_utf8_bytes": 8,
        "p2_source_utf8_bytes": 24,
        "p3_source_utf8_bytes": 16,
        "mandatory_reference_utf8_bytes": 64,
        "max_delta_depth": 16,
    }
    values.update(overrides)
    return ContextPlanningPolicy(**values)


def _conditions(
    policy: ContextPlanningPolicy,
    *,
    scope: ContextScope | None = None,
    provider_id: str = "provider",
    definition_fingerprint: str | None = None,
    policy_fingerprint: str | None = None,
    schema_version: int = CONTEXT_CHECKPOINT_SCHEMA_VERSION,
) -> ContextPlanningConditions:
    return ContextPlanningConditions(
        scope or _scope(),
        provider_id,
        definition_fingerprint or _digest(b"definition"),
        policy_fingerprint or policy.fingerprint,
        schema_version,
    )


def _ready_checkpoint(
    conditions: ContextPlanningConditions,
    entries: tuple[ContextManifestEntry, ...] = (),
    *,
    provider_thread_id: str | None = "thread",
    delta_depth: int = 0,
) -> ContextCheckpointView:
    catalog_entries = tuple(
        entry
        for entry in sorted(entries, key=lambda value: value.ordinal)
        if entry.source_kind
        in {
            ContextSourceKind.WRITING_ARTIFACT,
            ContextSourceKind.CONVERSATION,
            ContextSourceKind.ACTIVE_DEBATE,
            ContextSourceKind.OWN_AGENT_STATE,
        }
    )
    catalog = tuple(
        ContextCatalogCursor(entry, f"s{ordinal}")
        for ordinal, entry in enumerate(catalog_entries, start=1)
    )
    return ContextCheckpointView(
        ContextCheckpointState.READY,
        conditions,
        provider_thread_id,
        tuple(ContextCheckpointSource(entry) for entry in entries),
        delta_depth,
        catalog,
        len(catalog),
    )


def _project(
    entries: tuple[ContextManifestEntry, ...],
    materials: tuple[ContextMaterial, ...],
    *,
    policy: ContextPlanningPolicy | None = None,
    conditions: ContextPlanningConditions | None = None,
    checkpoint: ContextCheckpointView | None = None,
    active_provider_thread_id: str | None = None,
    mandatory_references: tuple[MandatoryReferenceReservation, ...] = (),
    fresh: bool = False,
) -> ContextProjection:
    selected_policy = policy or _policy()
    selected_conditions = conditions or _conditions(selected_policy)
    return plan_context_projection(
        ContextManifest(selected_conditions.scope, entries),
        materials,
        policy=selected_policy,
        conditions=selected_conditions,
        checkpoint=checkpoint
        or ContextCheckpointView(ContextCheckpointState.MISSING),
        active_provider_thread_id=active_provider_thread_id,
        mandatory_references=mandatory_references,
        fresh=fresh,
    )


def _reference(
    ordinal: int,
    *,
    reference_id: str | None = None,
    source_id: str | None = None,
    locator: str | None = None,
    utf8_bytes: int = 1,
    content_digest: str | None = None,
) -> MandatoryReferenceReservation:
    return MandatoryReferenceReservation(
        ordinal,
        reference_id or f"reference-{ordinal}",
        source_id or f"artifact-{ordinal}",
        "revision-1",
        ContextProvenance(
            ContextAuthorityKind.REFERENCE_AUTHORIZATION,
            locator or f"references/item-{ordinal}.md",
        ),
        content_digest or _digest(f"reference-{ordinal}".encode("ascii")),
        utf8_bytes,
    )


def _decision_map(projection: ContextProjection) -> dict[str, ContextSourceDecision]:
    return {decision.entry.source_id: decision for decision in projection.decisions}


def _decision_allowed(
    priority: ContextPriority,
    channel: ContextSemanticChannel,
    decision: ContextDecisionKind,
) -> bool:
    if priority is ContextPriority.P0_REQUIRED:
        return (
            channel
            in {
                ContextSemanticChannel.CURRENT_INPUT,
                ContextSemanticChannel.CANDIDATE,
            }
            and decision is ContextDecisionKind.SELECTED
        )
    if priority is ContextPriority.P1_STABLE:
        return (
            channel is ContextSemanticChannel.STABLE_BEHAVIOR
            and decision
            in {
                ContextDecisionKind.SELECTED,
                ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            }
        )
    if priority is ContextPriority.P2_WORKING:
        if channel not in {
            ContextSemanticChannel.HARD_CONSTRAINT,
            ContextSemanticChannel.FOCUSED_EVIDENCE,
            ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,
        }:
            return False
        return not (
            channel is ContextSemanticChannel.HARD_CONSTRAINT
            and decision
            in {
                ContextDecisionKind.OMITTED_CLASS_BUDGET,
                ContextDecisionKind.OMITTED_MATERIAL,
            }
        )
    return (
        priority is ContextPriority.P3_OPTIONAL
        and channel
        in {
            ContextSemanticChannel.OPTIONAL_BACKGROUND,
            ContextSemanticChannel.RETRIEVED_EVIDENCE,
            ContextSemanticChannel.HISTORY,
        }
    )


class PlanningTestCase(unittest.TestCase):
    """Common fixed-error assertion for planning contract tests."""

    def assert_planning_error(
        self,
        callable_: object,
        code: ContextPlanningErrorCode,
    ) -> ContextPlanningError:
        with self.assertRaises(ContextPlanningError) as caught:
            callable_()  # type: ignore[operator]
        self.assertIs(caught.exception.code, code)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        return caught.exception


class PlanningSurfaceTests(PlanningTestCase):
    """Verify the direct-module surface and stable planning vocabularies."""

    def test_exact_surface_enums_and_absent_total_budget_category(self) -> None:
        self.assertEqual(
            tuple(planning_module.__all__),
            (
                "CONTEXT_CHECKPOINT_SCHEMA_VERSION",
                "DEFAULT_CONTEXT_PLANNING_POLICY",
                "MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES",
                "ContextBootstrapReason",
                "ContextBudgetAccounting",
                "ContextCheckpointProposal",
                "ContextCheckpointSource",
                "ContextCheckpointState",
                "ContextCheckpointView",
                "ContextDecisionAccounting",
                "ContextDecisionKind",
                "ContextDeliveryMode",
                "ContextMaterial",
                "ContextPlanningConditions",
                "ContextPlanningError",
                "ContextPlanningErrorCode",
                "ContextPlanningPolicy",
                "ContextProjectedUnit",
                "ContextProjection",
                "ContextProjectionAccounting",
                "ContextReferenceBudgetUsage",
                "ContextSearchableAccounting",
                "ContextSourceBudgetUsage",
                "ContextSourceDecision",
                "MandatoryReferenceReservation",
                "plan_context_projection",
            ),
        )
        self.assertEqual(CONTEXT_CHECKPOINT_SCHEMA_VERSION, 1)
        self.assertEqual(
            MAX_MANDATORY_REFERENCE_RESERVATION_UTF8_BYTES,
            262_144,
        )
        self.assertEqual(
            tuple(item.value for item in ContextDeliveryMode),
            ("bootstrap", "delta"),
        )
        self.assertEqual(
            tuple(item.value for item in ContextCheckpointState),
            ("ready", "missing", "corrupt", "stale", "tainted"),
        )
        self.assertEqual(
            tuple(item.value for item in ContextDecisionKind),
            (
                "selected",
                "deduplicated",
                "checkpoint_suppressed",
                "omitted_class_budget",
                "omitted_material",
            ),
        )
        self.assertFalse(hasattr(ContextDecisionKind, "OMITTED_TOTAL_BUDGET"))
        self.assertFalse(
            any("total_budget" in item.value for item in ContextDecisionKind)
        )
        self.assertEqual(
            tuple(item.value for item in ContextBootstrapReason),
            (
                "explicit_fresh",
                "checkpoint_missing",
                "checkpoint_corrupt",
                "checkpoint_stale",
                "checkpoint_tainted",
                "scope_mismatch",
                "provider_mismatch",
                "provider_context_unavailable",
                "provider_thread_mismatch",
                "definition_fingerprint_mismatch",
                "stable_context_changed",
                "policy_fingerprint_mismatch",
                "schema_mismatch",
                "max_delta_depth",
            ),
        )

    def test_fixed_error_codes_messages_and_privacy(self) -> None:
        expected = {
            ContextPlanningErrorCode.INVALID_VALUE: (
                "context planning contains an invalid value"
            ),
            ContextPlanningErrorCode.INVALID_POLICY: (
                "context planning policy is invalid"
            ),
            ContextPlanningErrorCode.INVALID_CHECKPOINT: (
                "context checkpoint is invalid"
            ),
            ContextPlanningErrorCode.INVALID_MATERIAL: "context material is invalid",
            ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH: (
                "context material length does not match its manifest entry"
            ),
            ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH: (
                "context material digest does not match its manifest entry"
            ),
            ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH: (
                "a source revision conflicts with checkpoint content identity"
            ),
            ContextPlanningErrorCode.MANIFEST_SCOPE_MISMATCH: (
                "context manifest and planning scope do not match"
            ),
            ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING: (
                "required context material is unavailable"
            ),
            ContextPlanningErrorCode.STABLE_MATERIAL_MISSING: (
                "stable context material is unavailable"
            ),
            ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING: (
                "working hard-constraint material is unavailable"
            ),
            ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED: (
                "required context exceeds its planning budget"
            ),
            ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED: (
                "stable context exceeds its planning budget"
            ),
            ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED: (
                "working hard constraints exceed the configured budget"
            ),
            ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED: (
                "mandatory references exceed their reservation budget"
            ),
        }
        self.assertEqual(set(expected), set(ContextPlanningErrorCode))
        for code, message in expected.items():
            with self.subTest(code=code):
                error = ContextPlanningError(code)
                self.assertEqual(str(error), message)
                self.assertNotIn("PRIVATE", str(error))
                self.assertNotIn("PRIVATE", repr(error))
                self.assertIsNone(error.__cause__)
                self.assertIsNone(error.__context__)

    def test_exact_dataclass_fields_and_immutability(self) -> None:
        self.assertEqual(
            tuple(field.name for field in fields(ContextBudgetAccounting)),
            ("total", "p0", "p1", "p2", "p3", "mandatory_references"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(ContextProjection)),
            (
                "mode",
                "bootstrap_reason",
                "units",
                "mandatory_references",
                "decisions",
                "removed_checkpoint_sources",
                "accounting",
                "checkpoint_proposal",
                "projected_records",
                "removed_checkpoint_records",
                "catalog_updates",
                "removed_catalog_sources",
            ),
        )
        value = ContextSourceBudgetUsage(3, 1, 2)
        self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            value.selected_unique_utf8_bytes = 2  # type: ignore[misc]


class PlanningPolicyTests(PlanningTestCase):
    """Verify finite default and override policy boundaries."""

    def test_default_values_and_fingerprint_are_exact(self) -> None:
        policy = DEFAULT_CONTEXT_PLANNING_POLICY
        self.assertEqual(
            (
                policy.total_source_utf8_bytes,
                policy.p0_source_utf8_bytes,
                policy.p1_source_utf8_bytes,
                policy.p2_source_utf8_bytes,
                policy.p3_source_utf8_bytes,
                policy.mandatory_reference_utf8_bytes,
                policy.max_delta_depth,
            ),
            (131_072, 73_728, 16_384, 32_768, 0, 262_144, 16),
        )
        serialized = (
            b"total=131072;p0=73728;p1=16384;p2=32768;p3=0;"
            b"references=262144;depth=16"
        )
        self.assertEqual(policy.fingerprint, _digest(serialized))
        self.assertRegex(policy.fingerprint, r"\Asha256:[0-9a-f]{64}\Z")

    def test_every_field_rejects_non_exact_integer_and_negative(self) -> None:
        defaults = {
            field.name: getattr(DEFAULT_CONTEXT_PLANNING_POLICY, field.name)
            for field in fields(ContextPlanningPolicy)
        }
        for field_name in defaults:
            for value in (True, 1.0, "1", None, -1):
                with self.subTest(field=field_name, value=value):
                    values = dict(defaults)
                    values[field_name] = value
                    self.assert_planning_error(
                        lambda values=values: ContextPlanningPolicy(**values),
                        ContextPlanningErrorCode.INVALID_POLICY,
                    )

    def test_zero_class_aggregate_and_reference_boundaries(self) -> None:
        defaults = {
            field.name: getattr(DEFAULT_CONTEXT_PLANNING_POLICY, field.name)
            for field in fields(ContextPlanningPolicy)
        }
        for field_name in (
            "total_source_utf8_bytes",
            "p0_source_utf8_bytes",
            "p1_source_utf8_bytes",
            "max_delta_depth",
        ):
            values = dict(defaults)
            values[field_name] = 0
            with self.subTest(field=field_name):
                self.assert_planning_error(
                    lambda values=values: ContextPlanningPolicy(**values),
                    ContextPlanningErrorCode.INVALID_POLICY,
                )
        self.assertEqual(
            ContextPlanningPolicy(2, 1, 1, 0, 0, 0, 1),
            ContextPlanningPolicy(2, 1, 1, 0, 0, 0, 1),
        )
        exact = ContextPlanningPolicy(10, 1, 1, 4, 4, 262_144, 1)
        self.assertEqual(
            exact.p0_source_utf8_bytes
            + exact.p1_source_utf8_bytes
            + exact.p2_source_utf8_bytes
            + exact.p3_source_utf8_bytes,
            exact.total_source_utf8_bytes,
        )
        self.assert_planning_error(
            lambda: ContextPlanningPolicy(10, 1, 1, 4, 5, 0, 1),
            ContextPlanningErrorCode.INVALID_POLICY,
        )
        for class_values in (
            (10, 11, 1, 0, 0),
            (10, 1, 11, 0, 0),
            (10, 1, 1, 11, 0),
            (10, 1, 1, 0, 11),
        ):
            total, p0, p1, p2, p3 = class_values
            with self.subTest(class_values=class_values):
                self.assert_planning_error(
                    lambda total=total, p0=p0, p1=p1, p2=p2, p3=p3: (
                        ContextPlanningPolicy(total, p0, p1, p2, p3, 0, 1)
                    ),
                    ContextPlanningErrorCode.INVALID_POLICY,
                )
        self.assert_planning_error(
            lambda: ContextPlanningPolicy(2, 1, 1, 0, 0, 262_145, 1),
            ContextPlanningErrorCode.INVALID_POLICY,
        )


class PlanningValueTests(PlanningTestCase):
    """Verify material, checkpoint, decision, and accounting value invariants."""

    def test_material_is_strict_utf8_exact_and_hidden_from_repr(self) -> None:
        body = "문맥🙂".encode("utf-8")
        entry = _entry("unicode", body)
        material = ContextMaterial(entry, body)
        self.assertEqual(material.body_utf8, body)
        self.assertNotIn("문맥", repr(material))
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            material.body_utf8 = b"changed"  # type: ignore[misc]

        invalid_body = b"PRIVATE\xffBODY"
        invalid_entry = _entry("invalid", invalid_body)
        error = self.assert_planning_error(
            lambda: ContextMaterial(invalid_entry, invalid_body),
            ContextPlanningErrorCode.INVALID_MATERIAL,
        )
        self.assertNotIn("PRIVATE", str(error))
        self.assertNotIn("PRIVATE", repr(error))

        valid = _entry("valid", b"abc")
        self.assert_planning_error(
            lambda: ContextMaterial(replace(valid, utf8_bytes=4), b"abc"),
            ContextPlanningErrorCode.MATERIAL_LENGTH_MISMATCH,
        )
        self.assert_planning_error(
            lambda: ContextMaterial(
                replace(valid, content_digest=_digest(b"xyz")),
                b"abc",
            ),
            ContextPlanningErrorCode.MATERIAL_DIGEST_MISMATCH,
        )
        for wrong in ("abc", bytearray(b"abc"), memoryview(b"abc"), None):
            with self.subTest(wrong_type=type(wrong).__name__):
                self.assert_planning_error(
                    lambda wrong=wrong: ContextMaterial(valid, wrong),  # type: ignore[arg-type]
                    ContextPlanningErrorCode.INVALID_MATERIAL,
                )

    def test_projected_unit_validates_exact_body_channel_and_priority(self) -> None:
        first = _entry("first", b"same")
        second = _entry("second", b"same", ordinal=1)
        unit = ContextProjectedUnit((second, first), b"same")
        self.assertEqual(tuple(entry.source_id for entry in unit.entries), ("first", "second"))
        self.assertNotIn("same", repr(unit))
        different_channel = _entry(
            "history",
            b"same",
            semantic_channel=ContextSemanticChannel.HISTORY,
        )
        self.assert_planning_error(
            lambda: ContextProjectedUnit((first, different_channel), b"same"),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        p0_first = _entry(
            "p0-first",
            b"same",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        p0_second = _entry(
            "p0-second",
            b"same",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
            semantic_channel=ContextSemanticChannel.CANDIDATE,
        )
        self.assert_planning_error(
            lambda: ContextProjectedUnit((p0_first, p0_second), b"same"),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_checkpoint_shapes_and_source_constraints_are_strict(self) -> None:
        policy = _policy()
        conditions = _conditions(policy)
        p1, _ = _source(
            "definition",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        p0, _ = _source(
            "input",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        for state in (
            ContextCheckpointState.MISSING,
            ContextCheckpointState.CORRUPT,
            ContextCheckpointState.STALE,
            ContextCheckpointState.TAINTED,
        ):
            self.assertEqual(ContextCheckpointView(state).sources, ())
            for kwargs in (
                {"conditions": conditions},
                {"provider_thread_id": "thread"},
                {"sources": (ContextCheckpointSource(p1),)},
                {"delta_depth": 0},
            ):
                with self.subTest(state=state, kwargs=kwargs):
                    self.assert_planning_error(
                        lambda state=state, kwargs=kwargs: ContextCheckpointView(
                            state,
                            **kwargs,
                        ),
                        ContextPlanningErrorCode.INVALID_CHECKPOINT,
                    )
        self.assert_planning_error(
            lambda: ContextCheckpointSource(p0),
            ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        malformed_ready = (
            lambda: ContextCheckpointView(ContextCheckpointState.READY),
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                [],  # type: ignore[arg-type]
                0,
            ),
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                (),
                True,
            ),
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                (),
                -1,
            ),
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "unsafe\nthread",
                (),
                0,
            ),
        )
        for case in malformed_ready:
            self.assert_planning_error(
                case,
                ContextPlanningErrorCode.INVALID_CHECKPOINT,
            )
        self.assert_planning_error(
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                (ContextCheckpointSource(p1), ContextCheckpointSource(p1)),
                0,
            ),
            ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        no_thread = _ready_checkpoint(
            conditions,
            (p1,),
            provider_thread_id=None,
        )
        self.assertIsNone(no_thread.provider_thread_id)

    def test_catalog_aliases_are_unique_exact_integers_and_high_water_bounded(
        self,
    ) -> None:
        policy = _policy()
        conditions = _conditions(policy)
        entry, _material = _source("catalog-source")
        cursor = _record("record-one", b"record", 1).cursor

        for alias in (
            True,
            0,
            -1,
            planning_module._MAX_RECORD_ALIAS + 1,
        ):
            with self.subTest(record_alias=alias):
                self.assert_planning_error(
                    lambda alias=alias: ContextCheckpointRecord(cursor, alias),
                    ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )
        for high_water in (
            True,
            -1,
            planning_module._MAX_RECORD_ALIAS + 1,
        ):
            with self.subTest(record_high_water=high_water):
                self.assert_planning_error(
                    lambda high_water=high_water: ContextCatalogCursor(
                        entry,
                        "s1",
                        high_water,
                    ),
                    ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )
        for source_alias in (
            "",
            "s0",
            "s01",
            "h1",
        ):
            with self.subTest(source_alias=source_alias):
                self.assert_planning_error(
                    lambda source_alias=source_alias: ContextCatalogCursor(
                        entry,
                        source_alias,
                    ),
                    ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )

        checkpoint_record = ContextCheckpointRecord(cursor, 2)
        source = ContextCheckpointSource(entry, (checkpoint_record,))
        too_low = ContextCatalogCursor(entry, "s1", 1)
        self.assert_planning_error(
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                (source,),
                0,
                (too_low,),
                1,
            ),
            ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        valid_catalog = ContextCatalogCursor(entry, "s1", 2)
        for high_water in (True, 0):
            with self.subTest(source_high_water=high_water):
                self.assert_planning_error(
                    lambda high_water=high_water: ContextCheckpointView(
                        ContextCheckpointState.READY,
                        conditions,
                        "thread",
                        (source,),
                        0,
                        (valid_catalog,),
                        high_water,
                    ),
                    ContextPlanningErrorCode.INVALID_CHECKPOINT,
                )

        sibling = replace(entry, source_id="catalog-sibling", ordinal=1)
        duplicate_alias = ContextCatalogCursor(sibling, "s1")
        self.assert_planning_error(
            lambda: ContextCheckpointView(
                ContextCheckpointState.READY,
                conditions,
                "thread",
                (source,),
                0,
                (valid_catalog, duplicate_alias),
                1,
            ),
            ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )
        duplicate_record_alias = ContextCheckpointRecord(
            replace(cursor, record_id="record-two"),
            2,
        )
        self.assert_planning_error(
            lambda: ContextCheckpointSource(
                entry,
                (checkpoint_record, duplicate_record_alias),
            ),
            ContextPlanningErrorCode.INVALID_CHECKPOINT,
        )

    def test_decision_priority_channel_matrix_is_exhaustive(self) -> None:
        valid_entries = (
            _entry("current", b"x", source_kind=ContextSourceKind.RUNTIME_INPUT),
            _entry(
                "candidate",
                b"x",
                source_kind=ContextSourceKind.RUNTIME_INPUT,
                semantic_channel=ContextSemanticChannel.CANDIDATE,
            ),
            _entry("stable", b"x", source_kind=ContextSourceKind.AGENT_DEFINITION),
            *(
                _entry(
                    f"p2-{channel.value}",
                    b"x",
                    semantic_channel=channel,
                )
                for channel in (
                    ContextSemanticChannel.HARD_CONSTRAINT,
                    ContextSemanticChannel.FOCUSED_EVIDENCE,
                    ContextSemanticChannel.RETRIEVED_EVIDENCE,
                    ContextSemanticChannel.HISTORY,
                )
            ),
            _entry("optional", b"x", source_kind=ContextSourceKind.OWN_AGENT_STATE),
        )
        for entry, decision in itertools.product(valid_entries, ContextDecisionKind):
            represented = "representative" if decision is ContextDecisionKind.DEDUPLICATED else None
            with self.subTest(entry=entry.source_id, decision=decision):
                if _decision_allowed(entry.priority, entry.semantic_channel, decision):
                    value = ContextSourceDecision(entry, decision, represented)
                    self.assertIs(value.searchable, entry.searchable)
                else:
                    self.assert_planning_error(
                        lambda entry=entry, decision=decision, represented=represented: (
                            ContextSourceDecision(entry, decision, represented)
                        ),
                        ContextPlanningErrorCode.INVALID_VALUE,
                    )
        for priority, channel, decision in itertools.product(
            ContextPriority,
            ContextSemanticChannel,
            ContextDecisionKind,
        ):
            with self.subTest(priority=priority, channel=channel, decision=decision):
                if _decision_allowed(priority, channel, decision):
                    ContextDecisionAccounting(priority, channel, decision, 1, 1)
                else:
                    self.assert_planning_error(
                        lambda priority=priority, channel=channel, decision=decision: (
                            ContextDecisionAccounting(
                                priority,
                                channel,
                                decision,
                                1,
                                1,
                            )
                        ),
                        ContextPlanningErrorCode.INVALID_VALUE,
                    )
        p2 = valid_entries[3]
        self.assert_planning_error(
            lambda: ContextSourceDecision(
                p2,
                ContextDecisionKind.DEDUPLICATED,
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: ContextSourceDecision(
                p2,
                ContextDecisionKind.DEDUPLICATED,
                p2.source_id,
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_budget_and_searchable_accounting_are_self_validating(self) -> None:
        valid_usage = ContextSourceBudgetUsage(4, 1, 3)
        for args in ((4, 5, 0), (4, 1, 2), (-1, 0, 0), (4, True, 3)):
            with self.subTest(args=args):
                self.assert_planning_error(
                    lambda args=args: ContextSourceBudgetUsage(*args),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )
        valid_reference = ContextReferenceBudgetUsage(4, 1, 3)
        self.assertEqual(valid_reference.remaining_utf8_bytes, 3)
        zero = ContextSourceBudgetUsage(0, 0, 0)
        one = ContextSourceBudgetUsage(1, 0, 1)
        total = ContextSourceBudgetUsage(2, 0, 2)
        ContextBudgetAccounting(
            total,
            one,
            one,
            zero,
            zero,
            ContextReferenceBudgetUsage(0, 0, 0),
        )
        for values in (
            (zero, one, one, zero, zero, ContextReferenceBudgetUsage(0, 0, 0)),
            (total, zero, one, zero, zero, ContextReferenceBudgetUsage(0, 0, 0)),
            (total, one, zero, zero, zero, ContextReferenceBudgetUsage(0, 0, 0)),
            (
                total,
                one,
                one,
                zero,
                zero,
                ContextReferenceBudgetUsage(262_145, 0, 262_145),
            ),
        ):
            with self.subTest(values=values):
                self.assert_planning_error(
                    lambda values=values: ContextBudgetAccounting(*values),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )
        searchable = ContextSearchableAccounting(
            5,
            15,
            1,
            1,
            1,
            2,
            1,
            3,
            1,
            4,
            1,
            5,
            2,
            3,
            3,
            12,
        )
        for field_name in (
            "total_source_count",
            "total_source_utf8_bytes",
            "selected_source_count",
            "selected_source_utf8_bytes",
            "unprojected_source_count",
            "unprojected_source_utf8_bytes",
        ):
            with self.subTest(field=field_name):
                self.assert_planning_error(
                    lambda field_name=field_name: replace(
                        searchable,
                        **{field_name: getattr(searchable, field_name) + 1},
                    ),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )


class BootstrapPlanningTests(PlanningTestCase):
    """Verify checkpoint compatibility, precedence, and depth semantics."""

    def _stable_fixture(
        self,
    ) -> tuple[
        ContextPlanningPolicy,
        ContextPlanningConditions,
        ContextManifestEntry,
        ContextMaterial,
    ]:
        policy = _policy()
        conditions = _conditions(policy)
        stable, material = _source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        return policy, conditions, stable, material

    def test_checkpoint_state_and_explicit_fresh_reasons(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        state_reasons = {
            ContextCheckpointState.MISSING: ContextBootstrapReason.CHECKPOINT_MISSING,
            ContextCheckpointState.CORRUPT: ContextBootstrapReason.CHECKPOINT_CORRUPT,
            ContextCheckpointState.STALE: ContextBootstrapReason.CHECKPOINT_STALE,
            ContextCheckpointState.TAINTED: ContextBootstrapReason.CHECKPOINT_TAINTED,
        }
        for state, reason in state_reasons.items():
            projection = _project(
                (stable,),
                (material,),
                policy=policy,
                conditions=conditions,
                checkpoint=ContextCheckpointView(state),
            )
            self.assertIs(projection.mode, ContextDeliveryMode.BOOTSTRAP)
            self.assertIs(projection.bootstrap_reason, reason)
            self.assertEqual(projection.checkpoint_proposal.delta_depth, 0)
        fresh = _project(
            (stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=ContextCheckpointView(ContextCheckpointState.CORRUPT),
            fresh=True,
        )
        self.assertIs(
            fresh.bootstrap_reason,
            ContextBootstrapReason.EXPLICIT_FRESH,
        )

    def test_ready_mismatch_matrix_and_thread_none_semantics(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        other_scope = _scope(workspace_id="other")
        cases = (
            (
                _ready_checkpoint(
                    _conditions(policy, scope=other_scope),
                    (stable,),
                ),
                "thread",
                ContextBootstrapReason.SCOPE_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(policy, provider_id="other-provider"),
                    (stable,),
                ),
                "thread",
                ContextBootstrapReason.PROVIDER_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    conditions,
                    (stable,),
                    provider_thread_id=None,
                ),
                None,
                ContextBootstrapReason.PROVIDER_CONTEXT_UNAVAILABLE,
            ),
            (
                _ready_checkpoint(
                    conditions,
                    (stable,),
                    provider_thread_id=None,
                ),
                "thread",
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _ready_checkpoint(conditions, (stable,)),
                None,
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _ready_checkpoint(conditions, (stable,)),
                "different-thread",
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        definition_fingerprint=_digest(b"old-definition"),
                    ),
                    (stable,),
                ),
                "thread",
                ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        policy_fingerprint=_digest(b"old-policy"),
                    ),
                    (stable,),
                ),
                "thread",
                ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(policy, schema_version=2),
                    (stable,),
                ),
                "thread",
                ContextBootstrapReason.SCHEMA_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    conditions,
                    (stable,),
                    delta_depth=16,
                ),
                "thread",
                ContextBootstrapReason.MAX_DELTA_DEPTH,
            ),
        )
        for checkpoint, active_thread, expected in cases:
            with self.subTest(expected=expected):
                projection = _project(
                    (stable,),
                    (material,),
                    policy=policy,
                    conditions=conditions,
                    checkpoint=checkpoint,
                    active_provider_thread_id=active_thread,
                )
                self.assertIs(projection.bootstrap_reason, expected)
                self.assertEqual(projection.checkpoint_proposal.delta_depth, 0)

    def test_stable_change_reason_and_precedence_are_exact(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        old_stable = replace(stable, revision="old-revision")
        changed = _project(
            (stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (old_stable,)),
            active_provider_thread_id="thread",
        )
        self.assertIs(
            changed.bootstrap_reason,
            ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(
            tuple(source.entry for source in changed.checkpoint_proposal.sources),
            (stable,),
        )

        definition_precedes = _project(
            (stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(
                _conditions(
                    policy,
                    definition_fingerprint=_digest(b"old-definition"),
                ),
                (old_stable,),
            ),
            active_provider_thread_id="thread",
        )
        self.assertIs(
            definition_precedes.bootstrap_reason,
            ContextBootstrapReason.DEFINITION_FINGERPRINT_MISMATCH,
        )
        stable_precedes_policy = _project(
            (stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(
                _conditions(
                    policy,
                    policy_fingerprint=_digest(b"old-policy"),
                    schema_version=2,
                ),
                (old_stable,),
                delta_depth=16,
            ),
            active_provider_thread_id="thread",
        )
        self.assertIs(
            stable_precedes_policy.bootstrap_reason,
            ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )

    def test_compatibility_reason_precedence_is_pairwise_complete(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        lower_mismatches = {
            "provider_id": "other-provider",
            "definition_fingerprint": _digest(b"old-definition"),
            "policy_fingerprint": _digest(b"old-policy"),
            "schema_version": 2,
        }
        cases = (
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        scope=_scope(workspace_id="other"),
                        **lower_mismatches,
                    ),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ContextBootstrapReason.SCOPE_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(policy, **lower_mismatches),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ContextBootstrapReason.PROVIDER_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        definition_fingerprint=_digest(b"old-definition"),
                        policy_fingerprint=_digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,),
                    provider_thread_id=None,
                    delta_depth=16,
                ),
                None,
                ContextBootstrapReason.PROVIDER_CONTEXT_UNAVAILABLE,
            ),
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        definition_fingerprint=_digest(b"old-definition"),
                        policy_fingerprint=_digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,),
                    provider_thread_id="old-thread",
                    delta_depth=16,
                ),
                "new-thread",
                ContextBootstrapReason.PROVIDER_THREAD_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(
                        policy,
                        policy_fingerprint=_digest(b"old-policy"),
                        schema_version=2,
                    ),
                    (stable,),
                    delta_depth=16,
                ),
                "thread",
                ContextBootstrapReason.POLICY_FINGERPRINT_MISMATCH,
            ),
            (
                _ready_checkpoint(
                    _conditions(policy, schema_version=2),
                    (stable,),
                    delta_depth=16,
                ),
                "thread",
                ContextBootstrapReason.SCHEMA_MISMATCH,
            ),
        )
        for checkpoint, active_thread, expected in cases:
            with self.subTest(expected=expected):
                projection = _project(
                    (stable,),
                    (material,),
                    policy=policy,
                    conditions=conditions,
                    checkpoint=checkpoint,
                    active_provider_thread_id=active_thread,
                )
                self.assertIs(projection.bootstrap_reason, expected)

    def test_compatible_depth_15_delta_advances_to_16(self) -> None:
        policy, conditions, stable, material = self._stable_fixture()
        projection = _project(
            (stable,),
            (material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(
                conditions,
                (stable,),
                delta_depth=15,
            ),
            active_provider_thread_id="thread",
        )
        self.assertIs(projection.mode, ContextDeliveryMode.DELTA)
        self.assertIsNone(projection.bootstrap_reason)
        self.assertEqual(projection.checkpoint_proposal.delta_depth, 16)
        self.assertEqual(projection.units, ())
        self.assertIs(
            projection.decisions[0].decision,
            ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )


class RequiredAndStableSelectionTests(PlanningTestCase):
    """Verify exact-or-fail P0/P1 treatment and compatible suppression."""

    def test_p0_exact_fit_plus_one_missing_and_no_deduplication(self) -> None:
        policy = ContextPlanningPolicy(5, 4, 1, 0, 0, 0, 16)
        exact, exact_material = _source(
            "required",
            b"abcd",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        projection = _project(
            (exact,),
            (exact_material,),
            policy=policy,
        )
        self.assertEqual(projection.units[0].body_utf8, b"abcd")
        self.assertIs(
            projection.decisions[0].decision,
            ContextDecisionKind.SELECTED,
        )
        too_large, too_large_material = _source(
            "required-large",
            b"abcde",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        self.assert_planning_error(
            lambda: _project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ),
            ContextPlanningErrorCode.REQUIRED_BUDGET_EXCEEDED,
        )
        self.assert_planning_error(
            lambda: _project((exact,), (), policy=policy),
            ContextPlanningErrorCode.REQUIRED_MATERIAL_MISSING,
        )

        two_policy = ContextPlanningPolicy(3, 2, 1, 0, 0, 0, 16)
        first, first_material = _source(
            "current",
            b"x",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        second, second_material = _source(
            "candidate",
            b"x",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
            semantic_channel=ContextSemanticChannel.CANDIDATE,
        )
        two = _project(
            (second, first),
            (second_material, first_material),
            policy=two_policy,
        )
        self.assertEqual(len(two.units), 2)
        self.assertEqual(
            tuple(decision.decision for decision in two.decisions),
            (ContextDecisionKind.SELECTED, ContextDecisionKind.SELECTED),
        )
        self.assertEqual(two.accounting.saved_duplicate_utf8_bytes, 0)

    def test_p1_exact_fit_plus_one_missing_and_no_deduplication(self) -> None:
        policy = ContextPlanningPolicy(5, 1, 4, 0, 0, 0, 16)
        first, first_material = _source(
            "definition-a",
            b"aa",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        second, second_material = _source(
            "definition-b",
            b"aa",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
            ordinal=1,
        )
        projection = _project(
            (second, first),
            (second_material, first_material),
            policy=policy,
        )
        self.assertEqual(len(projection.units), 2)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)
        too_large, too_large_material = _source(
            "definition-large",
            b"abcde",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        self.assert_planning_error(
            lambda: _project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ),
            ContextPlanningErrorCode.STABLE_BUDGET_EXCEEDED,
        )
        self.assert_planning_error(
            lambda: _project((first,), (), policy=policy),
            ContextPlanningErrorCode.STABLE_MATERIAL_MISSING,
        )

    def test_compatible_delta_always_selects_p0_and_suppresses_p1(self) -> None:
        policy = ContextPlanningPolicy(4, 1, 3, 0, 0, 0, 16)
        conditions = _conditions(policy)
        required, required_material = _source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        stable, stable_material = _source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        projection = _project(
            (stable, required),
            (stable_material, required_material),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (stable,)),
            active_provider_thread_id="thread",
        )
        decisions = _decision_map(projection)
        self.assertIs(
            decisions[required.source_id].decision,
            ContextDecisionKind.SELECTED,
        )
        self.assertIs(
            decisions[stable.source_id].decision,
            ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertEqual(tuple(unit.entries for unit in projection.units), ((required,),))

    def test_every_valid_p1_set_or_metadata_change_bootstraps(self) -> None:
        policy = _policy()
        conditions = _conditions(policy)
        current, material = _source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        previous_variants = (
            (),
            (replace(current, revision="old-revision"),),
            (
                replace(
                    current,
                    provenance=ContextProvenance(
                        ContextAuthorityKind.AGENT_DEFINITION,
                        "agents/old-definition",
                    ),
                ),
            ),
            (replace(current, ordinal=7),),
        )
        for previous in previous_variants:
            with self.subTest(previous=previous):
                projection = _project(
                    (current,),
                    (material,),
                    policy=policy,
                    conditions=conditions,
                    checkpoint=_ready_checkpoint(conditions, previous),
                    active_provider_thread_id="thread",
                )
                self.assertIs(
                    projection.bootstrap_reason,
                    ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
                )
                self.assertEqual(projection.units[0].entries, (current,))
        removed = _project(
            (),
            (),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (current,)),
            active_provider_thread_id="thread",
        )
        self.assertIs(
            removed.bootstrap_reason,
            ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(removed.units, ())
        self.assertEqual(
            tuple(source.entry for source in removed.removed_checkpoint_sources),
            (),
        )

        sibling, sibling_material = _source(
            "definition-sibling",
            b"law",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
            ordinal=1,
        )
        changed_set = _project(
            (sibling, current),
            (sibling_material, material),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(
                conditions,
                (replace(current, revision="old-revision"), sibling),
            ),
            active_provider_thread_id="thread",
        )
        self.assertIs(
            changed_set.bootstrap_reason,
            ContextBootstrapReason.STABLE_CONTEXT_CHANGED,
        )
        self.assertEqual(
            tuple(decision.decision for decision in changed_set.decisions),
            (ContextDecisionKind.SELECTED, ContextDecisionKind.SELECTED),
        )
        self.assertEqual(
            tuple(unit.entries[0] for unit in changed_set.units),
            (current, sibling),
        )


class WorkingAndOptionalSelectionTests(PlanningTestCase):
    """Verify hard constraints and deterministic first-fit-with-skip selection."""

    def test_hard_constraints_exact_fit_plus_one_and_missing_fail_atomically(self) -> None:
        policy = ContextPlanningPolicy(6, 1, 1, 4, 0, 0, 16)
        exact, material = _source(
            "hard",
            b"hard",
            source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        projection = _project((exact,), (material,), policy=policy)
        self.assertEqual(projection.units[0].entries, (exact,))
        too_large, too_large_material = _source(
            "hard-large",
            b"large",
            source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        error = self.assert_planning_error(
            lambda: _project(
                (too_large,),
                (too_large_material,),
                policy=policy,
            ),
            ContextPlanningErrorCode.WORKING_CONSTRAINT_BUDGET_EXCEEDED,
        )
        self.assertEqual(
            str(error),
            "working hard constraints exceed the configured budget",
        )
        self.assert_planning_error(
            lambda: _project((exact,), (), policy=policy),
            ContextPlanningErrorCode.WORKING_CONSTRAINT_MATERIAL_MISSING,
        )

    def test_p2_and_p3_first_fit_skip_preserves_later_fitting_groups(self) -> None:
        policy = ContextPlanningPolicy(9, 1, 1, 5, 2, 0, 16)
        hard, hard_material = _source(
            "hard",
            b"hh",
            source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        focused, focused_material = _source(
            "focused-too-large",
            b"ffff",
            semantic_channel=ContextSemanticChannel.FOCUSED_EVIDENCE,
        )
        retrieved, retrieved_material = _source(
            "retrieved-fits",
            b"rrr",
            semantic_channel=ContextSemanticChannel.RETRIEVED_EVIDENCE,
        )
        missing = _entry(
            "history-missing",
            b"mmm",
            semantic_channel=ContextSemanticChannel.HISTORY,
        )
        optional_large, optional_large_material = _source(
            "optional-large",
            b"ooo",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
            ordinal=0,
        )
        optional_fit, optional_fit_material = _source(
            "optional-fit",
            b"zz",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
            ordinal=1,
        )
        projection = _project(
            (
                optional_fit,
                missing,
                retrieved,
                focused,
                hard,
                optional_large,
            ),
            (
                optional_fit_material,
                retrieved_material,
                focused_material,
                hard_material,
                optional_large_material,
            ),
            policy=policy,
        )
        decisions = _decision_map(projection)
        self.assertIs(decisions[hard.source_id].decision, ContextDecisionKind.SELECTED)
        self.assertIs(
            decisions[focused.source_id].decision,
            ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertIs(
            decisions[retrieved.source_id].decision,
            ContextDecisionKind.SELECTED,
        )
        self.assertIs(
            decisions[missing.source_id].decision,
            ContextDecisionKind.OMITTED_MATERIAL,
        )
        self.assertIs(
            decisions[optional_large.source_id].decision,
            ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertIs(
            decisions[optional_fit.source_id].decision,
            ContextDecisionKind.SELECTED,
        )
        self.assertEqual(
            tuple(unit.entries[0].source_id for unit in projection.units),
            (hard.source_id, retrieved.source_id, optional_fit.source_id),
        )
        self.assertEqual(projection.accounting.budgets.p2.selected_unique_utf8_bytes, 5)
        self.assertEqual(projection.accounting.budgets.p3.selected_unique_utf8_bytes, 2)

    def test_unused_other_class_and_total_headroom_is_not_borrowed(self) -> None:
        policy = ContextPlanningPolicy(12, 1, 1, 3, 7, 0, 16)
        focused, material = _source("focused", b"four")
        projection = _project((focused,), (material,), policy=policy)
        self.assertIs(
            projection.decisions[0].decision,
            ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertEqual(projection.accounting.budgets.total.selected_unique_utf8_bytes, 0)
        self.assertEqual(projection.accounting.budgets.p3.remaining_utf8_bytes, 7)


class DeduplicationPlanningTests(PlanningTestCase):
    """Verify byte-equality deduplication without losing semantic pointers."""

    def test_same_channel_exact_bodies_serialize_once_with_all_pointers(self) -> None:
        first, first_material = _source("first", b"same", ordinal=0)
        second, second_material = _source("second", b"same", ordinal=1)
        projection = _project(
            (second, first),
            (second_material, first_material),
        )
        self.assertEqual(len(projection.units), 1)
        self.assertEqual(projection.units[0].entries, (first, second))
        decisions = _decision_map(projection)
        self.assertIs(decisions[first.source_id].decision, ContextDecisionKind.SELECTED)
        self.assertIs(
            decisions[second.source_id].decision,
            ContextDecisionKind.DEDUPLICATED,
        )
        self.assertEqual(
            decisions[second.source_id].represented_by_source_id,
            first.source_id,
        )
        self.assertEqual(projection.accounting.selected_logical_source_count, 2)
        self.assertEqual(projection.accounting.selected_logical_utf8_bytes, 8)
        self.assertEqual(projection.accounting.projected_unique_utf8_bytes, 4)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 4)

    def test_cross_channel_bodies_never_deduplicate(self) -> None:
        focused, focused_material = _source("focused", b"same")
        history, history_material = _source(
            "history",
            b"same",
            semantic_channel=ContextSemanticChannel.HISTORY,
        )
        projection = _project(
            (history, focused),
            (history_material, focused_material),
        )
        self.assertEqual(len(projection.units), 2)
        self.assertTrue(
            all(
                decision.decision is ContextDecisionKind.SELECTED
                for decision in projection.decisions
            )
        )
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)

    def test_forced_digest_collision_still_requires_exact_body_equality(self) -> None:
        forced = "sha256:" + ("f" * 64)
        with mock.patch.object(planning_module, "_body_digest", return_value=forced):
            first = _entry("first", b"alpha", content_digest=forced)
            second = _entry("second", b"bravo", content_digest=forced, ordinal=1)
            first_material = ContextMaterial(first, b"alpha")
            second_material = ContextMaterial(second, b"bravo")
            projection = _project(
                (second, first),
                (second_material, first_material),
                policy=ContextPlanningPolicy(12, 1, 1, 10, 0, 0, 16),
            )
        self.assertEqual(len(projection.units), 2)
        self.assertEqual(projection.accounting.projected_unique_utf8_bytes, 10)
        self.assertEqual(projection.accounting.saved_duplicate_utf8_bytes, 0)


class DeltaAndReferencePlanningTests(PlanningTestCase):
    """Verify delta metadata, removed sources, and separate references."""

    def test_unchanged_sources_suppress_while_p2_p3_changes_select(self) -> None:
        policy = _policy()
        conditions = _conditions(policy)
        stable, stable_material = _source(
            "definition",
            b"role",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        unchanged, unchanged_material = _source("unchanged", b"old")
        changed, changed_material = _source(
            "changed",
            b"new",
            revision="revision-2",
        )
        previous_changed = _entry("changed", b"old", revision="revision-1")
        optional, optional_material = _source(
            "optional",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        projection = _project(
            (optional, changed, stable, unchanged),
            (optional_material, changed_material, stable_material, unchanged_material),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(
                conditions,
                (stable, unchanged, previous_changed),
                delta_depth=3,
            ),
            active_provider_thread_id="thread",
        )
        decisions = _decision_map(projection)
        self.assertIs(projection.mode, ContextDeliveryMode.DELTA)
        self.assertIs(
            decisions[stable.source_id].decision,
            ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertIs(
            decisions[unchanged.source_id].decision,
            ContextDecisionKind.CHECKPOINT_SUPPRESSED,
        )
        self.assertIs(decisions[changed.source_id].decision, ContextDecisionKind.SELECTED)
        self.assertIs(decisions[optional.source_id].decision, ContextDecisionKind.SELECTED)
        self.assertEqual(projection.checkpoint_proposal.delta_depth, 4)
        self.assertEqual(
            tuple(source.entry for source in projection.removed_checkpoint_sources),
            (),
        )
        self.assertEqual(
            tuple(source.entry for source in projection.catalog_updates),
            (changed, optional),
        )

    def test_catalog_alias_and_record_epochs_cover_add_change_remove_reappear(
        self,
    ) -> None:
        policy = _policy(p3_source_utf8_bytes=0)
        conditions = _conditions(policy)

        first_record = _record("record-one", b"one", 1)
        first_entry = _entry("artifact", b"[one]", revision="revision-1")
        first_material = ContextMaterial(
            first_entry,
            b"[one]",
            (first_record,),
        )
        first = _project(
            (first_entry,),
            (first_material,),
            policy=policy,
            conditions=conditions,
        )
        self.assertIs(first.mode, ContextDeliveryMode.BOOTSTRAP)
        self.assertEqual(
            tuple(
                (
                    item.source_alias,
                    item.record_alias_high_watermark,
                )
                for item in first.checkpoint_proposal.catalog_sources
            ),
            (("s1", 1),),
        )
        self.assertEqual(
            tuple(
                (item.record.cursor.record_id, item.record.record_alias)
                for item in first.projected_records
            ),
            (("record-one", 1),),
        )
        self.assertEqual(first.catalog_updates, first.checkpoint_proposal.catalog_sources)

        changed_record = _record("record-one", b"one-v2", 1)
        new_record = _record("record-two", b"two", 2)
        changed_entry = _entry(
            "artifact",
            b"[one-v2,two]",
            revision="revision-2",
        )
        changed_material = ContextMaterial(
            changed_entry,
            b"[one-v2,two]",
            (changed_record, new_record),
        )
        changed = _project(
            (changed_entry,),
            (changed_material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_checkpoint_from_projection(first),
            active_provider_thread_id="thread",
        )
        changed_source = changed.checkpoint_proposal.sources[0]
        self.assertEqual(
            tuple(
                (item.cursor.record_id, item.record_alias)
                for item in changed_source.records
            ),
            (("record-one", 1), ("record-two", 2)),
        )
        self.assertEqual(
            tuple(
                (
                    item.source_alias,
                    item.record.record_alias,
                    item.record.cursor.record_id,
                )
                for item in changed.projected_records
            ),
            (("s1", 1, "record-one"), ("s1", 2, "record-two")),
        )
        self.assertEqual(
            changed.checkpoint_proposal.catalog_sources[0].record_alias_high_watermark,
            2,
        )

        retained_entry = _entry(
            "artifact",
            b"[two]",
            revision="revision-3",
        )
        retained_material = ContextMaterial(
            retained_entry,
            b"[two]",
            (new_record,),
        )
        retained = _project(
            (retained_entry,),
            (retained_material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_checkpoint_from_projection(changed),
            active_provider_thread_id="thread",
        )
        self.assertEqual(retained.projected_records, ())
        self.assertEqual(
            tuple(
                (item.source_alias, item.record_alias, item.record_id)
                for item in retained.removed_checkpoint_records
            ),
            (("s1", 1, "record-one"),),
        )
        self.assertEqual(
            retained.checkpoint_proposal.catalog_sources[0].record_alias_high_watermark,
            2,
        )

        removed = _project(
            (),
            (),
            policy=policy,
            conditions=conditions,
            checkpoint=_checkpoint_from_projection(retained),
            active_provider_thread_id="thread",
        )
        self.assertEqual(removed.checkpoint_proposal.catalog_sources, ())
        self.assertEqual(
            tuple(item.source_alias for item in removed.removed_catalog_sources),
            ("s1",),
        )
        self.assertEqual(
            removed.checkpoint_proposal.source_alias_high_watermark,
            1,
        )

        reappeared_record = _record("record-three", b"three", 1)
        reappeared_entry = _entry(
            "artifact",
            b"[three]",
            revision="revision-4",
        )
        reappeared_material = ContextMaterial(
            reappeared_entry,
            b"[three]",
            (reappeared_record,),
        )
        reappeared = _project(
            (reappeared_entry,),
            (reappeared_material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_checkpoint_from_projection(removed),
            active_provider_thread_id="thread",
        )
        self.assertEqual(
            tuple(
                (item.source_alias, item.record_alias_high_watermark)
                for item in reappeared.checkpoint_proposal.catalog_sources
            ),
            (("s2", 1),),
        )
        self.assertEqual(
            reappeared.checkpoint_proposal.source_alias_high_watermark,
            2,
        )
        self.assertEqual(
            tuple(item.source_alias for item in reappeared.catalog_updates),
            ("s2",),
        )

    def test_accepted_source_omission_retains_epoch_and_emits_delivery_removal(
        self,
    ) -> None:
        policy = _policy(p2_source_utf8_bytes=4, p3_source_utf8_bytes=0)
        conditions = _conditions(policy)
        accepted_entry = _entry("bounded", b"four", revision="revision-1")
        accepted = _project(
            (accepted_entry,),
            (ContextMaterial(accepted_entry, b"four"),),
            policy=policy,
            conditions=conditions,
        )
        omitted_entry = _entry("bounded", b"fives", revision="revision-2")
        omitted = _project(
            (omitted_entry,),
            (ContextMaterial(omitted_entry, b"fives"),),
            policy=policy,
            conditions=conditions,
            checkpoint=_checkpoint_from_projection(accepted),
            active_provider_thread_id="thread",
        )
        decision = _decision_map(omitted)["bounded"]
        self.assertIs(
            decision.decision,
            ContextDecisionKind.OMITTED_CLASS_BUDGET,
        )
        self.assertEqual(omitted.checkpoint_proposal.sources, ())
        self.assertEqual(
            tuple(item.entry.source_id for item in omitted.removed_checkpoint_sources),
            ("bounded",),
        )
        self.assertEqual(omitted.removed_catalog_sources, ())
        self.assertEqual(
            tuple(item.source_alias for item in omitted.checkpoint_proposal.catalog_sources),
            ("s1",),
        )
        self.assertEqual(
            omitted.checkpoint_proposal.source_alias_high_watermark,
            1,
        )

    def test_same_scope_revision_content_conflict_fails_even_when_thread_changes(
        self,
    ) -> None:
        policy = _policy()
        conditions = _conditions(policy)
        previous = _entry("same", b"old", revision="shared-revision")
        variants = (
            _entry("same", b"new", revision="shared-revision"),
            _entry("same", b"longer", revision="shared-revision"),
        )
        for current in variants:
            with self.subTest(bytes=current.utf8_bytes):
                material = ContextMaterial(
                    current,
                    b"new" if current.utf8_bytes == 3 else b"longer",
                )
                error = self.assert_planning_error(
                    lambda current=current, material=material: _project(
                        (current,),
                        (material,),
                        policy=policy,
                        conditions=conditions,
                        checkpoint=_ready_checkpoint(
                            conditions,
                            (previous,),
                            provider_thread_id="old-thread",
                        ),
                        active_provider_thread_id="new-thread",
                    ),
                    ContextPlanningErrorCode.REVISION_CONTENT_MISMATCH,
                )
                self.assertNotIn("old", str(error))
                self.assertNotIn("new", str(error))

    def test_reference_whole_range_multiple_order_and_separate_accounting(self) -> None:
        references = (
            _reference(
                1,
                source_id="outline",
                locator="core_documents/outline.md",
                utf8_bytes=3,
            ),
            _reference(
                2,
                source_id="chapter",
                locator="manuscript/part_01/chapter.md#L4-L8",
                utf8_bytes=4,
            ),
            _reference(
                3,
                source_id="chapter",
                locator="manuscript/part_01/chapter.md#L12-L16",
                utf8_bytes=2,
            ),
        )
        policy = ContextPlanningPolicy(2, 1, 1, 0, 0, 9, 16)
        projection = _project(
            (),
            (),
            policy=policy,
            mandatory_references=references,
        )
        self.assertEqual(projection.mandatory_references, references)
        self.assertEqual(projection.units, ())
        self.assertEqual(projection.decisions, ())
        self.assertEqual(projection.checkpoint_proposal.sources, ())
        self.assertEqual(projection.accounting.mandatory_reference_count, 3)
        self.assertEqual(projection.accounting.mandatory_reference_utf8_bytes, 9)
        self.assertEqual(projection.accounting.budgets.total.selected_unique_utf8_bytes, 0)
        self.assertEqual(projection.accounting.budgets.mandatory_references.used_utf8_bytes, 9)

    def test_reference_constructor_sequence_identity_and_budget_failures(self) -> None:
        self.assert_planning_error(
            lambda: _reference(0),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        wrong_authority = ContextProvenance(
            ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
            "core_documents/outline.md",
        )
        self.assert_planning_error(
            lambda: MandatoryReferenceReservation(
                1,
                "reference",
                "source",
                "revision",
                wrong_authority,
                _digest(b"x"),
                1,
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        policy = ContextPlanningPolicy(2, 1, 1, 0, 0, 4, 16)
        invalid_sequences = (
            (_reference(2),),
            (_reference(1), _reference(3)),
            (
                _reference(1, reference_id="duplicate"),
                _reference(2, reference_id="duplicate"),
            ),
        )
        for references in invalid_sequences:
            with self.subTest(references=references):
                self.assert_planning_error(
                    lambda references=references: _project(
                        (),
                        (),
                        policy=policy,
                        mandatory_references=references,
                    ),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )
        self.assert_planning_error(
            lambda: _project(
                (),
                (),
                policy=policy,
                mandatory_references=(
                    _reference(1, utf8_bytes=3),
                    _reference(2, utf8_bytes=2),
                ),
            ),
            ContextPlanningErrorCode.REFERENCE_BUDGET_EXCEEDED,
        )


class ProjectionAccountingTests(PlanningTestCase):
    """Verify complete explainability, deterministic replay, and privacy."""

    @staticmethod
    def _mixed_projection() -> ContextProjection:
        policy = ContextPlanningPolicy(6, 1, 1, 3, 1, 5, 16)
        conditions = _conditions(policy)
        required, required_material = _source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        stable, stable_material = _source(
            "definition",
            b"r",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        hard = _entry(
            "hard-suppressed",
            b"h",
            source_kind=ContextSourceKind.ACTIVE_DEBATE,
        )
        focused, focused_material = _source("focused", b"aa", ordinal=0)
        alias, alias_material = _source("focused-alias", b"aa", ordinal=1)
        retrieved, retrieved_material = _source(
            "retrieved-omitted",
            b"bbbb",
            semantic_channel=ContextSemanticChannel.RETRIEVED_EVIDENCE,
        )
        history = _entry(
            "history-missing",
            b"ccc",
            semantic_channel=ContextSemanticChannel.HISTORY,
        )
        optional, optional_material = _source(
            "optional",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        references = (
            _reference(1, utf8_bytes=1),
            _reference(2, utf8_bytes=2),
        )
        return _project(
            (
                optional,
                history,
                retrieved,
                alias,
                hard,
                stable,
                focused,
                required,
            ),
            (
                optional_material,
                retrieved_material,
                alias_material,
                stable_material,
                focused_material,
                required_material,
            ),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (stable, hard)),
            active_provider_thread_id="thread",
            mandatory_references=references,
        )

    def test_every_decision_searchable_and_budget_partition_is_exact(self) -> None:
        projection = self._mixed_projection()
        decisions = _decision_map(projection)
        self.assertEqual(len(decisions), len(projection.decisions))
        expected = {
            "input": ContextDecisionKind.SELECTED,
            "definition": ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            "hard-suppressed": ContextDecisionKind.CHECKPOINT_SUPPRESSED,
            "focused": ContextDecisionKind.SELECTED,
            "focused-alias": ContextDecisionKind.DEDUPLICATED,
            "retrieved-omitted": ContextDecisionKind.OMITTED_CLASS_BUDGET,
            "history-missing": ContextDecisionKind.OMITTED_MATERIAL,
            "optional": ContextDecisionKind.SELECTED,
        }
        self.assertEqual(
            {source_id: decision.decision for source_id, decision in decisions.items()},
            expected,
        )
        self.assertTrue(
            all(decision.searchable is decision.entry.searchable for decision in projection.decisions)
        )
        searchable = projection.accounting.searchable
        self.assertEqual(
            (
                searchable.total_source_count,
                searchable.total_source_utf8_bytes,
                searchable.selected_primary_count,
                searchable.selected_primary_utf8_bytes,
                searchable.deduplicated_pointer_count,
                searchable.deduplicated_pointer_utf8_bytes,
                searchable.checkpoint_suppressed_count,
                searchable.checkpoint_suppressed_utf8_bytes,
                searchable.class_budget_omitted_count,
                searchable.class_budget_omitted_utf8_bytes,
                searchable.material_omitted_count,
                searchable.material_omitted_utf8_bytes,
                searchable.selected_source_count,
                searchable.selected_source_utf8_bytes,
                searchable.unprojected_source_count,
                searchable.unprojected_source_utf8_bytes,
            ),
            (6, 13, 2, 3, 1, 2, 1, 1, 1, 4, 1, 3, 3, 5, 3, 8),
        )
        accounting = projection.accounting
        self.assertEqual(accounting.logical_source_count, 8)
        self.assertEqual(accounting.logical_source_utf8_bytes, 15)
        self.assertEqual(accounting.selected_logical_source_count, 4)
        self.assertEqual(accounting.selected_logical_utf8_bytes, 6)
        self.assertEqual(accounting.projected_unit_count, 3)
        self.assertEqual(accounting.projected_unique_utf8_bytes, 4)
        self.assertEqual(accounting.saved_duplicate_utf8_bytes, 2)
        self.assertEqual(
            (
                accounting.budgets.total.selected_unique_utf8_bytes,
                accounting.budgets.p0.selected_unique_utf8_bytes,
                accounting.budgets.p1.selected_unique_utf8_bytes,
                accounting.budgets.p2.selected_unique_utf8_bytes,
                accounting.budgets.p3.selected_unique_utf8_bytes,
                accounting.budgets.mandatory_references.used_utf8_bytes,
            ),
            (4, 1, 0, 2, 1, 3),
        )

    def test_replay_is_identical_and_all_retained_values_are_content_free(self) -> None:
        first = self._mixed_projection()
        second = self._mixed_projection()
        self.assertEqual(first, second)
        self.assertEqual(first.accounting, second.accounting)

        secret = "PRIVATE-PROJECTION-BODY-한글🙂"
        entry, material = _source("private-source", secret.encode("utf-8"))
        private_projection = _project((entry,), (material,))
        self.assertNotIn(secret, repr(private_projection))
        self.assertNotIn(secret, repr(private_projection.accounting))
        self.assertNotIn(secret, repr(private_projection.checkpoint_proposal))


class ProjectionInvariantTests(PlanningTestCase):
    """Adversarially verify every exported cross-object projection invariant."""

    def _deduplicated_projection(self) -> ContextProjection:
        first, first_material = _source("first", b"same", ordinal=0)
        second, second_material = _source("second", b"same", ordinal=1)
        return _project(
            (second, first),
            (second_material, first_material),
        )

    def test_accounting_depth_bucket_order_uniqueness_and_selected_relations(self) -> None:
        projection = self._deduplicated_projection()
        accounting = projection.accounting
        mutations = (
            {"proposed_delta_depth": 1},
            {"projected_unit_count": accounting.projected_unit_count + 1},
            {"projected_unique_utf8_bytes": accounting.projected_unique_utf8_bytes + 1},
            {"saved_duplicate_utf8_bytes": accounting.saved_duplicate_utf8_bytes + 1},
            {"selected_logical_source_count": accounting.selected_logical_source_count + 1},
            {"selected_logical_utf8_bytes": accounting.selected_logical_utf8_bytes + 1},
            {"decision_buckets": tuple(reversed(accounting.decision_buckets))},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_planning_error(
                    lambda mutation=mutation: replace(accounting, **mutation),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )

        first, first_material = _source("one", b"x", ordinal=0)
        second, second_material = _source("two", b"y", ordinal=1)
        two_selected = _project(
            (second, first),
            (second_material, first_material),
        ).accounting
        bucket = next(
            item
            for item in two_selected.decision_buckets
            if item.decision is ContextDecisionKind.SELECTED
        )
        duplicate_buckets = tuple(
            item
            for item in two_selected.decision_buckets
            if item is not bucket
        ) + (
            replace(bucket, source_count=1, raw_source_utf8_bytes=1),
            replace(bucket, source_count=1, raw_source_utf8_bytes=1),
        )
        self.assert_planning_error(
            lambda: replace(two_selected, decision_buckets=duplicate_buckets),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

        policy = _policy()
        conditions = _conditions(policy)
        stable, stable_material = _source(
            "definition",
            b"r",
            source_kind=ContextSourceKind.AGENT_DEFINITION,
        )
        delta = _project(
            (stable,),
            (stable_material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (stable,)),
            active_provider_thread_id="thread",
        )
        self.assert_planning_error(
            lambda: replace(delta.accounting, proposed_delta_depth=0),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_unit_decision_mapping_and_same_id_metadata_substitutions_fail(self) -> None:
        projection = self._deduplicated_projection()
        decisions = list(projection.decisions)
        alias_index = next(
            index
            for index, decision in enumerate(decisions)
            if decision.decision is ContextDecisionKind.DEDUPLICATED
        )
        decisions[alias_index] = replace(
            decisions[alias_index],
            represented_by_source_id="wrong-representative",
        )
        self.assert_planning_error(
            lambda: replace(projection, decisions=tuple(decisions)),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

        selected = next(
            decision.entry
            for decision in projection.decisions
            if decision.decision is ContextDecisionKind.SELECTED
        )
        body = projection.units[0].body_utf8
        substitutions = (
            replace(selected, revision="other-revision"),
            replace(
                selected,
                provenance=ContextProvenance(
                    ContextAuthorityKind.WRITING_ARTIFACT_REPOSITORY,
                    "sources/other-path",
                ),
            ),
            replace(selected, ordinal=99),
            replace(
                selected,
                source_kind=ContextSourceKind.CONVERSATION,
                provenance=ContextProvenance(
                    ContextAuthorityKind.CONVERSATION_STORAGE,
                    "conversations/c_main",
                ),
            ),
        )
        for substitute in substitutions:
            with self.subTest(substitute=substitute):
                unit = ContextProjectedUnit((substitute,), body)
                self.assert_planning_error(
                    lambda unit=unit: replace(projection, units=(unit,)),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )
        first, first_material = _source("ordered-first", b"a", ordinal=0)
        second, second_material = _source("ordered-second", b"b", ordinal=1)
        ordered = _project(
            (second, first),
            (second_material, first_material),
        )
        self.assertEqual(len(ordered.units), 2)
        self.assert_planning_error(
            lambda: replace(ordered, units=tuple(reversed(ordered.units))),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

    def test_proposal_removed_reference_and_budget_cross_checks(self) -> None:
        mixed = ProjectionAccountingTests._mixed_projection()
        proposal = mixed.checkpoint_proposal
        self.assert_planning_error(
            lambda: replace(
                mixed,
                checkpoint_proposal=ContextCheckpointProposal(
                    proposal.conditions,
                    proposal.sources[:-1],
                    proposal.delta_depth,
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        omitted = next(
            decision.entry
            for decision in mixed.decisions
            if decision.decision is ContextDecisionKind.OMITTED_MATERIAL
        )
        self.assert_planning_error(
            lambda: replace(
                mixed,
                checkpoint_proposal=ContextCheckpointProposal(
                    proposal.conditions,
                    (*proposal.sources, ContextCheckpointSource(omitted)),
                    proposal.delta_depth,
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                mixed,
                checkpoint_proposal=replace(
                    proposal,
                    delta_depth=proposal.delta_depth + 1,
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

        policy = _policy()
        conditions = _conditions(policy)
        removed = _entry("removed", b"old")
        required, required_material = _source(
            "input",
            b"q",
            source_kind=ContextSourceKind.RUNTIME_INPUT,
        )
        projection = _project(
            (required,),
            (required_material,),
            policy=policy,
            conditions=conditions,
            checkpoint=_ready_checkpoint(conditions, (removed,)),
            active_provider_thread_id="thread",
        )
        self.assertEqual(
            tuple(source.entry for source in projection.removed_checkpoint_sources),
            (removed,),
        )
        self.assertEqual(
            (
                projection.accounting.removed_checkpoint_source_count,
                projection.accounting.removed_checkpoint_source_utf8_bytes,
            ),
            (1, removed.utf8_bytes),
        )
        bad_removed_accounting = replace(
            projection.accounting,
            removed_checkpoint_source_count=0,
        )
        self.assert_planning_error(
            lambda: replace(projection, accounting=bad_removed_accounting),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                projection,
                removed_checkpoint_sources=(
                    projection.removed_checkpoint_sources[0],
                    projection.removed_checkpoint_sources[0],
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                projection,
                accounting=replace(
                    projection.accounting,
                    removed_checkpoint_source_utf8_bytes=0,
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )

        duplicate_reference = replace(
            mixed.mandatory_references[1],
            reference_id=mixed.mandatory_references[0].reference_id,
        )
        self.assert_planning_error(
            lambda: replace(
                mixed,
                mandatory_references=(
                    mixed.mandatory_references[0],
                    duplicate_reference,
                ),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        self.assert_planning_error(
            lambda: replace(
                mixed,
                mandatory_references=tuple(reversed(mixed.mandatory_references)),
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )
        for field_name in (
            "mandatory_reference_count",
            "mandatory_reference_utf8_bytes",
        ):
            with self.subTest(field=field_name):
                self.assert_planning_error(
                    lambda field_name=field_name: replace(
                        mixed,
                        accounting=replace(
                            mixed.accounting,
                            **{
                                field_name: getattr(mixed.accounting, field_name) + 1
                            },
                        ),
                    ),
                    ContextPlanningErrorCode.INVALID_VALUE,
                )

        p2, p2_material = _source("p2", b"aa")
        p3, p3_material = _source(
            "p3",
            b"z",
            source_kind=ContextSourceKind.OWN_AGENT_STATE,
        )
        budget_projection = _project(
            (p3, p2),
            (p3_material, p2_material),
        )
        budgets = budget_projection.accounting.budgets
        swapped = ContextBudgetAccounting(
            budgets.total,
            budgets.p0,
            budgets.p1,
            ContextSourceBudgetUsage(
                budgets.p2.configured_utf8_bytes,
                1,
                budgets.p2.configured_utf8_bytes - 1,
            ),
            ContextSourceBudgetUsage(
                budgets.p3.configured_utf8_bytes,
                2,
                budgets.p3.configured_utf8_bytes - 2,
            ),
            budgets.mandatory_references,
        )
        bad_budget_accounting = replace(
            budget_projection.accounting,
            budgets=swapped,
        )
        self.assert_planning_error(
            lambda: replace(
                budget_projection,
                accounting=bad_budget_accounting,
            ),
            ContextPlanningErrorCode.INVALID_VALUE,
        )


class PlanningBoundaryAndPurityTests(PlanningTestCase):
    """Verify leaf imports, unchanged facades, clean import, and no I/O."""

    def test_planning_imports_only_stdlib_and_supported_manifest_names(self) -> None:
        root = Path(__file__).resolve().parents[1]
        planning_path = (
            root / "src" / "story_writing_agents" / "agent_context" / "planning.py"
        )
        manifest_path = (
            root / "src" / "story_writing_agents" / "agent_context" / "manifest.py"
        )
        source = planning_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(planning_path))
        compile(source, str(planning_path), "exec")
        relative_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level
        ]
        self.assertEqual(len(relative_imports), 1)
        self.assertEqual(relative_imports[0].module, "manifest")
        self.assertTrue(
            all(
                alias.name in manifest_module.__all__
                for alias in relative_imports[0].names
            )
        )
        absolute_imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and not node.level
            and node.module != "__future__"
        }
        absolute_imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertEqual(absolute_imports, {"hashlib", "dataclasses", "enum"})
        compile(manifest_path.read_text(encoding="utf-8"), str(manifest_path), "exec")

    def test_facades_remain_exact_and_clean_direct_import_works(self) -> None:
        self.assertEqual(len(story_writing_agents.__all__), 120)
        self.assertEqual(len(agent_context.__all__), 11)
        self.assertEqual(len(writing_artifacts.__all__), 62)
        self.assertFalse(hasattr(agent_context, "ContextManifest"))
        self.assertFalse(hasattr(agent_context, "ContextPlanningPolicy"))
        self.assertFalse(hasattr(story_writing_agents, "ContextManifest"))
        self.assertEqual(
            tuple(inspect.signature(plan_context_projection).parameters),
            (
                "manifest",
                "materials",
                "policy",
                "conditions",
                "checkpoint",
                "active_provider_thread_id",
                "mandatory_references",
                "fresh",
            ),
        )
        source_root = Path(__file__).resolve().parents[1] / "src"
        environment = dict(
            os.environ,
            PYTHONPATH=str(source_root),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONWARNINGS="error",
        )
        script = (
            "import story_writing_agents.agent_context.manifest as manifest; "
            "import story_writing_agents.agent_context.planning as planning; "
            "assert len(manifest.__all__) == 14; "
            "assert len(planning.__all__) == 26"
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-B", "-c", script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_planning_performs_no_filesystem_network_process_clock_or_state_io(self) -> None:
        entry, material = _source("focused", "문맥🙂".encode("utf-8"))
        forbidden = AssertionError("pure planner attempted external access")
        with (
            mock.patch("builtins.open", side_effect=forbidden),
            mock.patch("subprocess.Popen", side_effect=forbidden),
            mock.patch("socket.socket", side_effect=forbidden),
            mock.patch("time.time", side_effect=forbidden),
            mock.patch("time.monotonic", side_effect=forbidden),
            mock.patch("sqlite3.connect", side_effect=forbidden),
        ):
            projection = _project((entry,), (material,))
        self.assertEqual(projection.units[0].body_utf8, material.body_utf8)


if __name__ == "__main__":
    unittest.main()
