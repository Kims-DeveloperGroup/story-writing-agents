'Existing LedgerCodecPrivacyAndLimitTests scenarios, moved without new cases.'
from __future__ import annotations
import json
import os
from dataclasses import replace
from story_writing_agents.agent_context.accounting import ProviderCallKind, ProviderUsage
from story_writing_agents.agent_context.manifest import ContextAuthorityKind, ContextPriority, ContextSemanticChannel, ContextSourceKind
from story_writing_agents.agent_context.contracts import checkpoints as ctx_checkpoints, planning_inputs as ctx_planning_inputs, projection as ctx_projection
from story_writing_agents.agent_context.ledger import checkpoint_wire as owner_checkpoint_wire
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerCodecPrivacyAndLimitTests(fixture_ledger_fixture.LedgerTestCase):
    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(
            value, ensure_ascii=False,
            allow_nan=False, separators=(",", ":"),
            sort_keys=True,).encode("utf-8")

    def test_canonical_record_is_content_free_and_retains_exact_numeric_evidence(self,
    ) -> None:
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=self.plan,)
        reference = self.ledger.append_provider_call(self.key,
            expected_generation=reserved.generation,
            attempt_id=self.plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(
                1,
                call_kind=ProviderCallKind.WRITING_ARTIFACT_REFERENCE_READ,),
        )
        substantive = self.ledger.append_provider_call(self.key,
            expected_generation=reference.generation, attempt_id=self.plan.attempt_id,
            provider_call=fixture_ledger_fixture.make_provider_call(2,
                usage=ProviderUsage(input_tokens=19,
                    output_tokens=6,
                    cached_input_tokens=4,),
            ),)
        ready = self.ledger.commit_attempt(
            self.key, expected_generation=substantive.generation,
            authorization=fixture_ledger_fixture.make_authorization(self.plan,
                self.projection,
                terminal_call_id="call-2",),
        )
        raw = self.record_path().read_bytes()
        decoded = json.loads(raw)
        self.assertEqual(raw, self._canonical(decoded))
        self.assertEqual(set(decoded),
            {"attempt",
                "created_at", "generation",
                "key", "ready_checkpoint",
                "schema_version", "state",
                "updated_at",},
        )
        self.assertEqual(decoded["schema_version"], 2)
        persisted_accounting = decoded["attempt"]["plan"]["projection_accounting"]
        self.assertEqual(
            (persisted_accounting["logical_source_count"],
                persisted_accounting["logical_source_utf8_bytes"],
                persisted_accounting["projected_unique_utf8_bytes"],
                persisted_accounting["mandatory_reference_count"],
                persisted_accounting["mandatory_reference_utf8_bytes"],),
            (self.projection.accounting.logical_source_count,
                self.projection.accounting.logical_source_utf8_bytes,
                self.projection.accounting.projected_unique_utf8_bytes,
                self.projection.accounting.mandatory_reference_count,
                self.projection.accounting.mandatory_reference_utf8_bytes,),
        )
        self.assertEqual(decoded["attempt"]["provider_calls"][1]["accounting"]["usage"],
            {"cached_input_tokens": 4,
                "input_tokens": 19,
                "output_tokens": 6,},
        )
        self.assertEqual(
            tuple(item["entry"]["source_id"]
                for item in decoded["ready_checkpoint"]["sources"]),
            tuple(source.entry.source_id
                for source in self.projection.checkpoint_proposal.sources),
        )
        prohibited_keys = {"body",
            "body_utf8", "prompt",
            "excerpt", "candidate",
            "answer", "summary",
            "projected_units", "reference_path",
            "line_range",
            "exception",}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                self.assertTrue(prohibited_keys.isdisjoint(value))
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(decoded)
        text = raw.decode("utf-8")
        for canary in fixture_ledger_fixture.PRIVATE_BODY_CANARIES:
            self.assertNotIn(canary, text)
            self.assertNotIn(canary, repr(ready.record))
        self.assertNotIn("PRIVATE_REFERENCE_BODY", text)
        self.assertEqual(decoded["attempt"]["plan"]["mandatory_reference_count"],
            1,)
        self.assertGreater(decoded["attempt"]["plan"]["projection_accounting"][
                "mandatory_reference_utf8_bytes"],
            0,)
        self.assertEqual(set(
                decoded["attempt"]["plan"]["projection_accounting"]["budgets"][
                    "mandatory_references"]
            ), {
                "configured_utf8_bytes", "remaining_utf8_bytes",
                "used_utf8_bytes",},
        )

    def test_strict_corruption_matrix_is_nonmutating_and_maps_to_corrupt(self) -> None:
        self.ready(usage=ProviderUsage(input_tokens=0))
        path = self.record_path()
        baseline = path.read_bytes()
        value = json.loads(baseline)

        def changed(mutator: object) -> bytes:
            candidate = json.loads(baseline)
            mutator(candidate)  # type: ignore[operator]
            return self._canonical(candidate)

        cases: tuple[tuple[str, bytes], ...] = (("empty", b""),
            ("truncated", baseline[:-1]), ("non_utf8", b"\xff"),
            ("bom", b"\xef\xbb\xbf" + baseline),
            ("pretty", json.dumps(value, indent=2).encode("utf-8")), (
                "duplicate_key", baseline.replace(
                    b'"generation":3', b'"generation":3,"generation":3',
                    1,),
            ),
            ("unknown_top_level",
                changed(lambda item: item.__setitem__("unknown", 1)),),
            ("missing_top_level",
                changed(lambda item: item.pop("updated_at")),),
            ("unknown_nested",
                changed(lambda item: item["attempt"]["plan"].__setitem__("unknown", 1)),),
            ("missing_nested",
                changed(lambda item: item["attempt"]["plan"].pop("operation_id")),),
            ("unknown_schema",
                changed(lambda item: item.__setitem__("schema_version", 3)),),
            ("bool_integer",
                changed(lambda item: item.__setitem__("generation", True)),),
            ("invalid_state",
                changed(lambda item: item.__setitem__("state", "retired_ready")),),
            ("bad_scope_digest",
                changed(lambda item: item["key"].__setitem__(
                        "scope_key", fixture_ledger_fixture.digest_bytes(b"wrong-scope"))
                ),),
            ("bad_proposal_digest",
                changed(lambda item: item["attempt"]["plan"].__setitem__(
                        "proposal_digest", fixture_ledger_fixture.digest_bytes(b"wrong-proposal"))
                ),),
            ("noncanonical_sources",
                changed(lambda item: item["ready_checkpoint"].__setitem__(
                        "sources",
                        list(reversed(item["ready_checkpoint"]["sources"])),)
                ),),
            ("invalid_accounting",
                changed(lambda item: item["attempt"]["plan"][
                        "projection_accounting"
                    ].__setitem__("logical_source_count", True)),
            ),
            ("invalid_usage",
                changed(lambda item: item["attempt"]["provider_calls"][0][
                        "accounting"].__setitem__(
                        "usage", {
                            "cached_input_tokens": None, "input_tokens": None,
                            "output_tokens": None,},
                    )),
            ),
            ("negative_usage",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("input_tokens", -1)),
            ),
            ("boolean_usage",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("input_tokens", True)),
            ),
            ("unknown_usage_field",
                changed(
                    lambda item: item["attempt"]["provider_calls"][0]["accounting"][
                        "usage"
                    ].__setitem__("estimated", 1)),
            ),)
        for label, payload in cases:
            with self.subTest(label=label):
                path.write_bytes(payload)
                os.chmod(path, 0o600)
                before = path.read_bytes()
                inspection = self.ledger.inspect(self.key)
                self.assertEqual(
                    (
                        inspection.generation,
                        inspection.record,
                        inspection.checkpoint_view.state,),
                    (0, None, ctx_planning_inputs.ContextCheckpointState.CORRUPT),)
                self.assertEqual(path.read_bytes(), before)
        path.write_bytes(baseline)
        os.chmod(path, 0o600)
        self.assertIs(
            self.ledger.inspect(self.key).checkpoint_view.state,
            ctx_planning_inputs.ContextCheckpointState.READY,)

    def test_record_source_call_generation_and_size_limits_fail_closed(self) -> None:
        calls = tuple(fixture_ledger_fixture.make_provider_call(ordinal)
            for ordinal in range(1, owner_record_validation.MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 2))
        self.assert_ledger_error(lambda: owner_record_values.ContextLedgerAttempt(
                self.plan, calls,
                owner_record_values.ContextLedgerAttemptOutcome.IN_PROGRESS, "2026-08-28T00:00:00.000000Z",
                "2026-08-28T00:00:00.000000Z",),
            owner_record_validation.ContextLedgerErrorCode.LIMIT_EXCEEDED,)
        sources = []
        for ordinal in range(owner_record_validation.MAX_CONTEXT_LEDGER_SOURCES + 1):
            entry, _material = fixture_ledger_fixture.make_entry(f"artifact:{ordinal}",
                "x", ordinal=ordinal,
                source_kind=ContextSourceKind.AGENT_DEFINITION,
                channel=ContextSemanticChannel.STABLE_BEHAVIOR, priority=ContextPriority.P1_STABLE,
                authority=ContextAuthorityKind.AGENT_DEFINITION,
                searchable=False,)
            sources.append(ctx_checkpoints.ContextCheckpointSource(entry))
        oversized_proposal = ctx_projection.ContextCheckpointProposal(
            self.projection.checkpoint_proposal.conditions, tuple(sources),
            0,)
        self.assert_ledger_error(lambda: owner_checkpoint_wire.context_checkpoint_proposal_digest(oversized_proposal),
            owner_record_validation.ContextLedgerErrorCode.LIMIT_EXCEEDED,)
        ready = self.ready()
        path = self.record_path()
        with path.open("r+b") as handle:
            handle.truncate(owner_record_validation.MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1)
        inspection = self.ledger.inspect(self.key)
        self.assertIs(inspection.checkpoint_view.state, ctx_planning_inputs.ContextCheckpointState.CORRUPT)
        self.assertIsNone(inspection.record)
        self.assertEqual(path.stat().st_size, owner_record_validation.MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES + 1)
        self.assertIs(ready.record.state, owner_record_values.ContextLedgerRecordState.READY)

    def test_exact_provider_call_limit_and_max_generation_do_not_partially_advance(self,
    ) -> None:
        projection = fixture_ledger_fixture.make_projection(self.scope)
        plan = fixture_ledger_fixture.make_plan(projection)
        reserved = self.ledger.reserve_attempt(
            self.key,
            expected_generation=0,
            plan=plan,)
        current = reserved
        for ordinal in range(1, owner_record_validation.MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 1):
            current = self.ledger.append_provider_call(self.key,
                expected_generation=current.generation,
                attempt_id=plan.attempt_id,
                provider_call=fixture_ledger_fixture.make_provider_call(ordinal),)
        before = self.record_path().read_bytes()
        self.assert_ledger_error(
            lambda: self.ledger.append_provider_call(
                self.key, expected_generation=current.generation,
                attempt_id=plan.attempt_id,
                provider_call=fixture_ledger_fixture.make_provider_call(owner_record_validation.MAX_CONTEXT_LEDGER_PROVIDER_CALLS + 1),),
            owner_record_validation.ContextLedgerErrorCode.LIMIT_EXCEEDED,)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.ledger.taint_attempt(self.key,
            expected_generation=current.generation, attempt_id=plan.attempt_id,
            outcome=owner_record_values.ContextLedgerAttemptOutcome.INCOMPLETE,)
        self.ledger.inspect(self.key)
        maximum_generation = (1 << 63) - 1
        maximum_value = json.loads(self.record_path().read_bytes())
        maximum_value["generation"] = maximum_generation
        self.record_path().write_bytes(self._canonical(maximum_value))
        os.chmod(self.record_path(), 0o600)
        maximum_observed = self.ledger.inspect(self.key)
        self.assertEqual(maximum_observed.generation, maximum_generation)
        self.assert_ledger_error(
            lambda: self.ledger.reserve_attempt(
                self.key, expected_generation=maximum_generation,
                plan=replace(plan, attempt_id="attempt-after-maximum"),),
            owner_record_validation.ContextLedgerErrorCode.LIMIT_EXCEEDED,)
