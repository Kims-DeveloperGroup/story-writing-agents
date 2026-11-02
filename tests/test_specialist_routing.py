"""Pure routing, versioned wire compatibility, and draft/review acceptance."""
from dataclasses import FrozenInstanceError, fields, replace
import json
import hashlib
from pathlib import Path
import unittest
from story_writing_agents import specialist_routing as routing
from story_writing_agents import lead_premise_decisions as premise
from story_writing_agents import core_document_readiness as readiness
from story_writing_agents import review_contracts as review

from story_writing_agents import conversations as c
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain as Domain, AuthoringActivity as Activity, AuthoringIntent as Intent, AuthoringScope,
    ReviewBasis, SpecialistAssignmentMode as Mode, artifact_domain,
    required_authoring_domains, required_authoring_specialists,
    validate_specialist_assignment, validate_specialist_routing,
    AuthoringContribution as Contribution, AuthoringContributionRole as Role,
    AuthoringDependencySlot as Slot, assignment_mode_for_specialist, required_drafting_specialists,
)
from tests import collaboration_contract_fixture as fixture


class SpecialistRoutingTests(unittest.TestCase):
    def scope(self, **changes):
        values = dict(activity=Activity.DRAFT, target_paths=(), domains=(Domain.PLOT_STRUCTURE,), interpretation="Draft a synthetic causally ordered outline.", task_id="task_a", task_revision=1)
        values.update(changes)
        return AuthoringScope(**values)
    def mixed_scope(self):
        return self.scope(schema_version=3, domains=tuple(Domain), contributions=tuple(Contribution(domain, Role.DRAFT_OWNER if domain is Domain.TONE_EXECUTION else Role.CONSTRAINT_ASSESSOR) for domain in Domain),
                          dependency_slots=(Slot("setting", (Domain.TONE_EXECUTION,)),))

    def test_published_recommended_action_position_and_keyword_only_scope_are_preserved(self):
        import inspect
        scope_parameters = inspect.signature(AuthoringScope).parameters
        self.assertEqual(scope_parameters["schema_version"].default, 2)
        for name in ("intent", "contributions", "dependency_slots"):
            self.assertIs(scope_parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
        action = c.LeadRecommendedAction(c.LeadRecommendationActionKind.PREPARE_ARTIFACT,
            "Review a supplied candidate", "Prepare its separate review", "One candidate review",
            ("current_response",), ("core_documents/global_outline.md",))
        scope = self.scope(activity=Activity.REVIEW)
        outcome = c.LeadProviderOutcome(c.LeadOutcomeKind.RECOMMENDATION, "Finished candidate", c.LeadOutcomeScope.STORY_SPECIFIC, c.LeadEvidenceStatus.INCOMPLETE, (), None, None, None, action, authoring_scope=scope)
        self.assertIs(outcome.recommended_action, action)
        self.assertIs(outcome.authoring_scope, scope)
        parameters = inspect.signature(c.LeadProviderOutcome).parameters
        self.assertEqual(tuple(name for name, p in parameters.items()
            if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ("kind", "message", "scope", "evidence_status", "evidence_ids", "proposal",
             "artifact_intent", "objective_intent", "recommended_action"))
        self.assertIs(parameters["authoring_scope"].kind, inspect.Parameter.KEYWORD_ONLY)
        with self.assertRaises(TypeError):
            c.LeadProviderOutcome(c.LeadOutcomeKind.RECOMMENDATION, "Finished candidate", c.LeadOutcomeScope.STORY_SPECIFIC, c.LeadEvidenceStatus.INCOMPLETE, (), None, None, None, action, scope)
        basis_parameters = inspect.signature(c.CollaborationProposalBasis).parameters
        self.assertEqual(tuple((basis_parameters[key].default, basis_parameters[key].kind) for key in ("schema_version", "shared_premises")), ((1, inspect.Parameter.KEYWORD_ONLY), ((), inspect.Parameter.KEYWORD_ONLY)))

    def test_canonical_artifact_and_semantic_domain_ownership(self):
        cases = (("core_documents/global_outline.md", Domain.PLOT_STRUCTURE, "plot_architect"),
                 ("core_documents/part_outlines.md", Domain.PLOT_STRUCTURE, "plot_architect"),
                 ("core_documents/character_sheets.md", Domain.CHARACTER_WORLD, "character_custodian"),
                 ("core_documents/narrative_rules.md", Domain.TONE_EXECUTION, "execution_specialist"),
                 ("manuscript/part-01/opening.md", Domain.TONE_EXECUTION, "execution_specialist"))
        empty = readiness.project_readiness(None, ())
        target = readiness.AuthoringTarget
        prerequisites = {
            target.GLOBAL_OUTLINE: (),
            target.PART_OUTLINES: (target.GLOBAL_OUTLINE.value,),
            target.CHARACTER_SHEETS: (target.GLOBAL_OUTLINE.value, target.PART_OUTLINES.value),
            target.NARRATIVE_RULES: (target.GLOBAL_OUTLINE.value, target.PART_OUTLINES.value),
            target.MANUSCRIPT: (target.GLOBAL_OUTLINE.value, target.PART_OUTLINES.value,
                                target.CHARACTER_SHEETS.value, target.NARRATIVE_RULES.value), }
        for path, domain, owner in cases:
            with self.subTest(path=path):
                self.assertIs(artifact_domain(path), domain)
                scope = self.scope(target_paths=(path,), domains=())
                self.assertEqual(required_authoring_domains(scope), (domain,))
                self.assertEqual(required_authoring_specialists(scope), (owner,))
                actual_target = target.MANUSCRIPT if path.startswith("manuscript/") else target(path)
                needed = readiness.require_readiness(actual_target, empty)
                self.assertEqual(needed.required_documents if needed else (), prerequisites[actual_target])
        self.assertIsInstance(readiness.require_readiness(None, empty), readiness.CoreDocumentReadinessNeeded)
        self.assertEqual(required_authoring_specialists(self.scope(domains=tuple(Domain))),
                         ("plot_architect", "character_custodian", "execution_specialist"))

        from story_writing_agents.structured_output_validation import SchemaRules
        rules = tuple((domains, SchemaRules.declare(routing.artifact_path_rule(domains)))
            for domains in ((Domain.PLOT_STRUCTURE,), (Domain.CHARACTER_WORLD,),
                            (Domain.TONE_EXECUTION,), tuple(Domain)))
        for path, expected in (
            ("core_documents/global_outline.md", Domain.PLOT_STRUCTURE),
            ("core_documents/character_sheets.md", Domain.CHARACTER_WORLD),
            ("core_documents/narrative_rules.md", Domain.TONE_EXECUTION),
            ("manuscript/a.md", Domain.TONE_EXECUTION),
            ("manuscript/space dir/한글.md", Domain.TONE_EXECUTION),
            ("manuscript/private-\ue000.md", Domain.TONE_EXECUTION),
            ("manuscript/unassigned-\u0378.md", Domain.TONE_EXECUTION),
            ("manuscript/.../a..md", Domain.TONE_EXECUTION),
            ("core_documents/unknown.md", None), ("manuscript/.md", None),
            ("manuscript/./a.md", None), ("manuscript/../a.md", None),
            ("manuscript//a.md", None), ("manuscript/a.MD", None),
            ("manuscript/a.md.txt", None), ("manuscript/a.md\n", None),
            ("manuscript/a\x00.md", None), ("manuscript/a\u200d.md", None),
            ("manuscript/a\u2028.md", None), ("manuscript/}/a.md", None),
            ("manuscript/\ud800.md", None),
        ):
            try: actual = artifact_domain(path)
            except ValueError: actual = None
            self.assertIs(actual, expected, repr(path))
            for domains, declared in rules:
                self.assertEqual(not declared.findings(path), expected in domains, repr(path))

    def test_mixed_paths_and_semantics_union_without_inventory_inference(self):
        mixed = self.mixed_scope()
        self.assertEqual(required_authoring_domains(mixed), tuple(Domain))
        self.assertEqual(required_drafting_specialists(mixed), ("execution_specialist",))
        self.assertEqual(tuple(assignment_mode_for_specialist(mixed, agent) for agent in required_authoring_specialists(mixed)), (Mode.CONSTRAINT_ASSESSMENT, Mode.CONSTRAINT_ASSESSMENT, Mode.DRAFT))
        self.assertEqual(tuple(assignment_mode_for_specialist(replace(mixed, activity=Activity.REVIEW), agent) for agent in required_authoring_specialists(mixed)), (Mode.REVIEW,) * 3)
        for changes in ({"contributions": mixed.contributions[1:]}, {"contributions": tuple(Contribution(domain, Role.CONSTRAINT_ASSESSOR) for domain in Domain)}, {"dependency_slots": (Slot("setting", (Domain.PLOT_STRUCTURE,)),)}):
            with self.assertRaises(ValueError): replace(mixed, **changes)
        scope = self.scope(target_paths=("manuscript/part-01/opening.md",))
        self.assertEqual(required_authoring_domains(scope), (Domain.PLOT_STRUCTURE, Domain.TONE_EXECUTION))
        self.assertEqual(required_authoring_domains(scope, affected_paths=("core_documents/character_sheets.md",)), tuple(Domain))
        self.assertEqual(required_authoring_specialists(self.scope()), ("plot_architect",))
        with self.assertRaisesRegex(ValueError, "omits"):
            validate_specialist_routing(scope, ("plot_architect",))
        with self.assertRaisesRegex(ValueError, "order"):
            validate_specialist_routing(scope, ("execution_specialist", "plot_architect"))
        current = self.scope(schema_version=3, activity=Activity.REVIEW,
            domains=(Domain.PLOT_STRUCTURE, Domain.CHARACTER_WORLD),
            contributions=(Contribution(Domain.PLOT_STRUCTURE, Role.DRAFT_OWNER),
                           Contribution(Domain.CHARACTER_WORLD, Role.DRAFT_OWNER)))
        rule = routing.authoring_scope_rules(current.activity, current.intent, current.domains,
            current.contributions, schema_version=current.schema_version)
        self.assertEqual(tuple(field.name for field in fields(type(rule)))[-1], 'schema_version')
        self.assertEqual(rule.schema_version, 3)
        self.assertEqual(routing.legal_specialist_rosters(rule), (('plot_architect', 'character_custodian'),))
        self.assertEqual(tuple(routing.assignment_rule(rule, agent)
                               for agent in ('plot_architect', 'character_custodian')), (Mode.REVIEW, Mode.REVIEW))
        with self.assertRaises(ValueError):
            routing.assignment_rule(rule, 'execution_specialist')
        self.assertTrue(all(item.schema_version == 3 for item in routing.finite_authoring_scope_rules()))

        questions = (
            routing.SpecialistWorkQuestion(
                "motive", mixed.task_id, 1, "Why does Mara protect the letter?",
                ("outline", "characters"), ("preserve_motive",)),
            routing.SpecialistWorkQuestion(
                "betrayal", mixed.task_id, 1, "Does the betrayal preserve her motive?",
                ("scene",), ("keep_secret",)),
        )
        plan = routing.SpecialistStagePlan(
            routing.ReviewStage.SPECIALIST_WORK, routing.SpecialistWorkKind.DEEP_REVIEW,
            mixed.task_id, 1, (Domain.CHARACTER_WORLD,), ("character_custodian",),
            questions, ("preserve_motive", "keep_secret"), "Keep the secret unresolved.",
            ("Whether the betrayal follows from her motive",), "A bounded motivation review.")
        sources = ("outline", "characters", "scene")
        review_scope = replace(mixed, activity=Activity.REVIEW)
        routing.validate_stage_routing(
            review_scope, ("character_custodian",), plan, source_input_ids=sources,
            required_condition_ids=("preserve_motive", "keep_secret"))
        self.assertEqual(required_authoring_domains(mixed), tuple(Domain))
        self.assertEqual(required_authoring_specialists(mixed),
                         ("plot_architect", "character_custodian", "execution_specialist"))
        self.assertEqual(routing.stage_required_specialists(review_scope, plan),
                         ("character_custodian",))
        encoded = routing.stage_plan_to_dict(plan)
        self.assertEqual(encoded["specialist_ids"], ["character_custodian"])
        self.assertEqual(encoded["required_condition_ids"], ["preserve_motive", "keep_secret"])
        self.assertEqual(encoded["agreed_direction"], "Keep the secret unresolved.")
        self.assertEqual(routing.stage_plan_from_dict(encoded), plan)
        with self.assertRaises(FrozenInstanceError): plan.specialist_ids = ()
        for field in encoded:
            incomplete = dict(encoded)
            incomplete.pop(field)
            with self.assertRaises(ValueError): routing.stage_plan_from_dict(incomplete)
        with self.assertRaises(ValueError):
            routing.stage_plan_from_dict(dict(encoded, apply_authority=True))
        with self.assertRaises(ValueError):
            routing.stage_plan_from_dict(dict(encoded, stage="workflow"))
        for changes in ({"questions": questions[:1]},
                        {"questions": (replace(questions[0], task_id="foreign"), questions[1])}):
            with self.assertRaises(ValueError): replace(plan, **changes)
        with self.assertRaises(ValueError):
            routing.stage_required_specialists(replace(review_scope, task_revision=2), plan)
        with self.assertRaises(ValueError):
            routing.stage_required_specialists(self.scope(activity=Activity.REVIEW), plan)
        with self.assertRaises(ValueError):
            routing.validate_stage_routing(review_scope, ("character_custodian",), plan,
                source_input_ids=("outline", "characters"))
        with self.assertRaises(ValueError):
            routing.validate_stage_routing(review_scope, ("character_custodian",), plan,
                source_input_ids=sources, required_condition_ids=("unapproved_condition",))
        roster = ("plot_architect", "character_custodian", "execution_specialist")
        final = replace(plan, stage=routing.ReviewStage.FINAL_CANDIDATE_REVIEW,
                        focus_domains=tuple(Domain), specialist_ids=roster)
        self.assertEqual(routing.stage_required_specialists(review_scope, final), roster)
        with self.assertRaises(ValueError):
            routing.stage_required_specialists(
                review_scope, replace(final, specialist_ids=("character_custodian",)))
        draft = replace(final, stage=routing.ReviewStage.SPECIALIST_WORK,
                        work_kind=routing.SpecialistWorkKind.DRAFT)
        self.assertEqual(routing.stage_required_specialists(mixed, draft), roster)
        for focus, participants in (((Domain.TONE_EXECUTION,), ("execution_specialist",)),
                                    ((Domain.CHARACTER_WORLD,), ("character_custodian",))):
            with self.assertRaises(ValueError):
                routing.stage_required_specialists(
                    mixed, replace(draft, focus_domains=focus, specialist_ids=participants))
        for stage in (routing.ReviewStage.PROCEDURE_STATUS,
                      routing.ReviewStage.LEAD_PRELIMINARY_REVIEW):
            self.assertEqual(routing.stage_required_specialists(
                mixed, replace(plan, stage=stage, focus_domains=(), specialist_ids=())), ())

    def test_scope_is_strict_versioned_immutable_and_content_safe(self):
        scope = self.scope()
        with self.assertRaises(FrozenInstanceError): scope.task_revision = 2
        self.assertNotIn(scope.interpretation, repr(scope))
        bad = ({"schema_version": True}, {"schema_version": 3}, {"task_revision": True},
               {"task_revision": 0}, {"task_id": "../task"}, {"activity": "draft"},
               {"target_paths": []}, {"domains": (Domain.PLOT_STRUCTURE, Domain.PLOT_STRUCTURE)},
               {"domains": ()}, {"interpretation": " "},
               {"target_paths": ("../manuscript/a.md",)}, {"target_paths": ("notes.md",)},
               {"target_paths": ("/manuscript/a.md",)}, {"activity": Activity.WORKFLOW})
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                self.scope(**changes)
        workflow = self.scope(activity=Activity.WORKFLOW, domains=())
        self.assertEqual(required_authoring_specialists(workflow), ())
        with self.assertRaises(ValueError):
            required_authoring_domains(workflow, affected_paths=("core_documents/global_outline.md",))


        literal = "Éither the narrator knows.\r\nOr the reader learns first.  "
        spec = premise.PremiseDecisionSpec("question-1", "knowledge", literal, (Domain.PLOT_STRUCTURE,), "user-1")
        self.assertEqual(spec.question, literal)
        self.assertNotEqual(spec.question_digest, replace(spec, question=literal + "?").question_digest)
        with self.assertRaises(FrozenInstanceError): spec.question = "Changed"
        with self.assertRaises(ValueError): replace(spec, dependent_domains=())

    def test_review_requires_disclosed_concrete_basis_while_draft_forbids_it(self):
        basis = ReviewBasis(("candidate",), "Compare motivation and chronology.")
        validate_specialist_assignment(Mode.CONSTRAINT_ASSESSMENT, ("brief",), None)
        with self.assertRaises(ValueError): validate_specialist_assignment(Mode.CONSTRAINT_ASSESSMENT, ("candidate",), basis)
        validate_specialist_assignment(Mode.REVIEW, ("candidate", "brief"), basis)
        validate_specialist_assignment(Mode.DRAFT, ("brief",), None)
        for mode, ids, candidate in ((Mode.REVIEW, ("brief",), basis),
                                     (Mode.REVIEW, ("candidate",), None),
                                     (Mode.DRAFT, ("candidate",), basis)):
            with self.subTest(mode=mode), self.assertRaises((TypeError, ValueError)):
                validate_specialist_assignment(mode, ids, candidate)
        with self.assertRaises(ValueError): ReviewBasis((), "No candidate.")

    def test_scope_and_review_basis_codecs_are_closed_and_round_trip(self):
        for value, encode, decode in ((self.scope(schema_version=1), c.authoring_scope_to_dict, c.authoring_scope_from_dict),
                                     (ReviewBasis(("candidate",), "Assess supplied candidate."), c.review_basis_to_dict, c.review_basis_from_dict)):
            wire = encode(value)
            self.assertEqual(decode(wire), value)
            for key in tuple(wire):
                wrong = dict(wire); wrong.pop(key)
                with self.subTest(missing=key), self.assertRaises((TypeError, ValueError)): decode(wrong)
            with self.assertRaises((TypeError, ValueError)): decode(dict(wire, authority=True))


        spec = premise.PremiseDecisionSpec("question-1", "knowledge", "Keep the knowledge choice unresolved.", (Domain.PLOT_STRUCTURE,), "user-1")
        encoded = premise.premise_value_to_dict(spec)
        self.assertEqual(premise.premise_spec_from_dict(encoded), spec)
        with self.assertRaises(ValueError):
            premise.premise_spec_from_dict({**encoded, "candidate_input_ids": ["user_input"]})

        # Pure records are not READ proofs or host-granted final-review authority.
        whole_digest = "sha256:" + hashlib.sha256(b"Mara protects the letter.\n").hexdigest()
        range_digest = "sha256:" + hashlib.sha256(b"protects the letter").hexdigest()
        user = review.ReviewTarget(
            "user_input", review.ReviewTargetKind.USER_PROVIDED, "turn/user", "1",
            whole_digest, whole_digest, whole_digest, True)
        artifact = replace(user, input_id="reference_1",
            kind=review.ReviewTargetKind.WRITING_ARTIFACT,
            target_id="core_documents/character_sheets.md", revision="characters-1")
        ranged = replace(user, input_id="read_1", kind=review.ReviewTargetKind.READ_SOURCE,
            target_id="manuscript/part-01/scene.md", content_digest=range_digest,
            boundary_digest=range_digest, whole_source=False, boundary_kind="artifact_lines",
            boundary_start=2, boundary_end=3)
        candidate = replace(user, input_id="candidate_1", kind=review.ReviewTargetKind.CANDIDATE,
                            target_id="candidate-1", revision="3")
        excluded = replace(artifact, input_id="unread_1",
            target_id="core_documents/part_outlines.md", revision=None, content_digest=None,
            source_digest=None, boundary_digest=None, whole_source=False, boundary_kind="unread",
            availability=review.ReviewTargetAvailability.UNREAD, source_status="present",
            source_manifest_digest="sha256:" + hashlib.sha256(b"known core status").hexdigest())
        expected_user = {"input_id": "user_input", "kind": "user_provided",
            "target_id": "turn/user", "revision": "1", "content_digest": whole_digest,
            "source_digest": whole_digest, "boundary_digest": whole_digest,
            "whole_source": True, "boundary_kind": "whole",
            "boundary_start": None, "boundary_end": None, "availability": "supplied",
            "source_status": None, "source_manifest_digest": None}
        self.assertEqual(review.review_target_to_dict(user), expected_user)
        self.assertEqual(review.review_target_from_dict(review.review_target_to_dict(excluded)), excluded)
        self.assertIsNone(excluded.content_digest)
        self.assertFalse(excluded.whole_source)
        for changes in ({"content_digest": whole_digest}, {"whole_source": True},
                        {"source_status": None}, {"source_manifest_digest": None},
                        {"availability": review.ReviewTargetAvailability.SUPPLIED}):
            with self.assertRaises((TypeError, ValueError)):
                replace(excluded, **changes)
        for field in expected_user:
            incomplete = dict(expected_user)
            incomplete.pop(field)
            with self.assertRaises(ValueError): review.review_target_from_dict(incomplete)
        with self.assertRaises(ValueError):
            review.review_target_from_dict(dict(expected_user, apply_authority=True))
        for target in (user, artifact, ranged, candidate):
            self.assertEqual(review.review_target_from_dict(
                review.review_target_to_dict(target)), target)
        report = review.LeadPreliminaryReview(
            "review-task", 2, (user, artifact, ranged, candidate, excluded),
            ("user_input", "reference_1", "read_1", "candidate_1"), ("unread_1",),
            (review.ReviewFinding("The inspected scene preserves her motive.", ("read_1",)),),
            (review.ReviewFinding("The supplied directions disagree.",
                                  ("user_input", "reference_1")),),
            ("The part outline was not inspected.",), True,
            review.PreliminaryNextAction.REQUEST_INFORMATION)
        self.assertFalse(report.whole_target_coverage)
        self.assertEqual(report.stage, "lead_preliminary_review")
        self.assertEqual(report.inspected_input_ids,
                         ("user_input", "reference_1", "read_1", "candidate_1"))
        self.assertEqual(report.excluded_input_ids, ("unread_1",))
        wire = review.preliminary_review_to_dict(report)
        self.assertEqual(review.preliminary_review_from_dict(wire), report)
        with self.assertRaises(ValueError):
            replace(report, inspected_input_ids=tuple(item.input_id for item in report.targets), excluded_input_ids=())
        self.assertEqual(wire["targets"][0], expected_user)
        self.assertEqual(wire["next_action"], "request_information")
        self.assertEqual(wire["conflicts"], [{"message": "The supplied directions disagree.",
            "evidence_input_ids": ["user_input", "reference_1"]}])
        self.assertTrue({"apply_authority", "commit_authority", "final_review_evidence",
                         "objective_satisfied"}.isdisjoint(wire))
        for field in wire:
            incomplete = dict(wire)
            incomplete.pop(field)
            with self.assertRaises(ValueError): review.preliminary_review_from_dict(incomplete)
        with self.assertRaises(ValueError):
            review.preliminary_review_from_dict(dict(wire, apply_authority=True))
        wire["targets"][0]["revision"] = "changed-export"
        self.assertEqual(report.targets[0].revision, "1")
        with self.assertRaises(FrozenInstanceError): report.task_revision = 3
        for changes in ({"task_revision": True}, {"task_revision": 0},
                        {"stage": "final_candidate_review"}, {"targets": ()},
                        {"targets": (user, user)}, {"inspected_input_ids": ("foreign",)},
                        {"excluded_input_ids": ("unread_1", "read_1")},
                        {"findings": (review.ReviewFinding("Unread claim.", ("unread_1",)),)},
                        {"conflicts": (review.ReviewFinding("Only one source.", ("read_1",)),)},
                        {"missing_information": (), "further_review_required": False},
                        {"next_action": "finish_direct"}):
            with self.assertRaises((TypeError, ValueError)): replace(report, **changes)
        for changes in ({"whole_source": 1}, {"boundary_start": 1},
                        {"boundary_digest": range_digest}, {"kind": "user_provided"}):
            with self.assertRaises(ValueError): replace(user, **changes)
        with self.assertRaises(ValueError): replace(ranged, boundary_start=True)
        complete = replace(report, targets=(user, artifact, candidate),
            inspected_input_ids=("user_input", "reference_1", "candidate_1"),
            excluded_input_ids=(), findings=(), conflicts=(), missing_information=(),
            further_review_required=False, next_action=review.PreliminaryNextAction.FINISH_DIRECT)
        self.assertTrue(complete.whole_target_coverage)

    def test_legacy_proposal_bytes_stay_omitted_and_new_scope_changes_digest(self):
        from tests.test_specialist_routing_runtime import _ADMISSION_BRIEF, _fixture_assessment
        legacy = fixture.make_proposal()
        before = c.encode_collaboration_proposal(legacy)
        self.assertNotIn(b'authoring_scope', before)
        self.assertNotIn(b'review_basis', before)
        self.assertEqual(c.encode_collaboration_proposal(c.decode_collaboration_proposal(before)), before)
        from tests.test_lead_orchestration import LeadOrchestrationTestCase
        readiness_case = LeadOrchestrationTestCase()
        readiness_case._setupAsyncioRunner()
        self.addCleanup(readiness_case._tearDownAsyncioRunner)
        self.addCleanup(readiness_case.doCleanups)
        readiness_case._callSetUp()
        readiness_basis = readiness_case._callMaybeAsync(readiness_case.record_book_readiness).basis
        current_basis = replace(legacy.basis, core_document_readiness=readiness_basis)
        assignments = tuple(replace(a, input_ids=(*a.input_ids, 'user_input'), mode=Mode.DRAFT) for a in legacy.assignments)
        scope = self.scope(domains=(Domain.PLOT_STRUCTURE, Domain.CHARACTER_WORLD), target_paths=("core_documents/global_outline.md",))
        values = {f.name:getattr(legacy,f.name) for f in fields(legacy) if f.name != "proposal_digest"}
        values.update(authoring_scope=scope, assignments=assignments, basis=fixture.qualify_fixture_basis(current_basis, scope, user_text=_ADMISSION_BRIEF, assessment=_fixture_assessment()))
        scoped = c.CollaborationProposal.create(**values)
        wire = c.encode_collaboration_proposal(scoped)
        self.assertEqual(c.decode_collaboration_proposal(wire), scoped)
        self.assertNotEqual(scoped.proposal_digest, legacy.proposal_digest)
        frozen = json.loads((Path(__file__).parent / "fixtures/authoring_scope_compatibility/scope_v2_basis_v1_76a80f7.json").read_bytes())
        self.assertEqual(frozen["source_commit"], "76a80f72be711d927fff7eee4ee455d3b37802c4")
        for case in frozen["cases"].values():
            raw = case["proposal_utf8"].encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), case["proposal_sha256"])
            self.assertEqual(json.loads(raw)["proposal"]["proposal_digest"], case["proposal_digest"])
            historical_scope = c.authoring_scope_from_dict(json.loads(case["scope_utf8"]))
            if case["intent"] == "authoring":
                with self.assertRaises(ValueError): c.decode_collaboration_proposal(raw)
            else:
                with self.assertRaisesRegex(ValueError, "proposal basis has missing or extra fields"):
                    c.decode_collaboration_proposal(raw)
            for key in ("contributions", "dependency_slots"):
                with self.assertRaises(ValueError): c.authoring_scope_from_dict(dict(c.authoring_scope_to_dict(historical_scope), **{key: []}))
            poisoned = json.loads(c.encode_collaboration_proposal(scoped))
            poisoned["proposal"]["basis"]["shared_premises"] = []
            with self.assertRaises(ValueError): c.decode_collaboration_proposal(json.dumps(poisoned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        review_scope = replace(scope, activity=Activity.REVIEW, intent=Intent.REVIEW_ONLY, interpretation="Review the supplied fixture candidate only.")
        reviewed = fixture.replace_proposal(legacy, authoring_scope=review_scope,
            assignments=tuple(replace(a, mode=Mode.REVIEW, review_basis=ReviewBasis(a.input_ids, "Review the supplied fixture candidate only."))
                for a in legacy.assignments))
        self.assertEqual(c.decode_collaboration_proposal(c.encode_collaboration_proposal(reviewed)), reviewed)
        for change in ({'task_revision': 2}, {'interpretation': 'A different visible interpretation.'}, {'target_paths': ('core_documents/part_outlines.md',)}):
            changed_scope = replace(scope, **change)
            changed = c.CollaborationProposal.create(**dict(values, authoring_scope=changed_scope,
                basis=fixture.qualify_fixture_basis(current_basis, changed_scope, user_text=_ADMISSION_BRIEF, assessment=_fixture_assessment())))
            self.assertNotEqual(changed.proposal_digest, scoped.proposal_digest)
        self.assertEqual(c.encode_collaboration_proposal(legacy), before)
        from story_writing_agents.conversations import events as owner
        scope3 = replace(scope, schema_version=3, contributions=tuple(Contribution(domain, Role.DRAFT_OWNER) for domain in scope.domains), dependency_slots=(Slot("setting", (Domain.PLOT_STRUCTURE,)),))
        source = owner.CollaborationPremiseSource(scoped.basis.inputs[0], None)
        premise = owner.CollaborationSharedPremise("setting", "Exact café 한글\r\n  ", (source,))
        basis2 = fixture.qualify_fixture_basis(replace(current_basis, schema_version=2, shared_premises=(premise,)), scope3, user_text=_ADMISSION_BRIEF, assessment=_fixture_assessment())
        current = c.CollaborationProposal.create(**dict(values, authoring_scope=scope3, basis=basis2))
        self.assertEqual(c.decode_collaboration_proposal(c.encode_collaboration_proposal(current)), current)
        self.assertNotEqual(current.proposal_digest, scoped.proposal_digest)
        alias = replace(source.input_revision, input_id="renamed_alias")
        aliased_basis = replace(basis2, inputs=(alias, *basis2.inputs[1:]), shared_premises=(replace(premise, sources=(replace(source, input_revision=alias),)),))
        self.assertEqual(owner.authoring_capture_identity(scope3, basis2), owner.authoring_capture_identity(scope3, aliased_basis))
        local = owner.CollaborationPremiseSource(replace(source.input_revision, kind=c.CollaborationInputKind.USER_INPUT, locator="message:shared", start_line=None, end_line=None), "conversation_a")
        self.assertNotEqual(owner.premise_source_identity(local), owner.premise_source_identity(replace(local, source_conversation_id="conversation_b")))
        self.assertNotEqual(owner.premise_source_identity(source), owner.premise_source_identity(replace(source, input_revision=replace(source.input_revision, revision_digest="sha256:" + "b" * 64))))
        self.assertNotEqual(owner.authoring_capture_identity(scope3, basis2), owner.authoring_capture_identity(scope3, replace(basis2, shared_premises=(replace(premise, selected_premise="Changed choice"),))))
        with self.assertRaises(ValueError): owner.CollaborationPremiseSource(source.input_revision, "foreign_owner")
        with self.assertRaises(ValueError): owner.CollaborationPremiseSource(replace(source.input_revision, state=c.CollaborationInputState.ABSENT), None)
        for field in ("shared_premises", "schema_version"):
            poisoned = json.loads(c.encode_collaboration_proposal(current)); del poisoned["proposal"]["basis"][field]
            with self.assertRaises(ValueError): c.decode_collaboration_proposal(json.dumps(poisoned, ensure_ascii=False, separators=(",", ":")).encode())
        for field in ("contributions", "dependency_slots"):
            poisoned = c.authoring_scope_to_dict(scope3); del poisoned[field]
            with self.assertRaises(ValueError): c.authoring_scope_from_dict(poisoned)
        poisoned = json.loads(c.encode_collaboration_proposal(current)); del poisoned["proposal"]["basis"]["shared_premises"][0]["sources"][0]["source_conversation_id"]
        with self.assertRaises(ValueError): c.decode_collaboration_proposal(json.dumps(poisoned, ensure_ascii=False, separators=(",", ":")).encode())
        with self.assertRaises(ValueError): replace(premise, selected_premise="")
        self.assertNotEqual(owner.authoring_capture_identity(scope3, basis2), owner.authoring_capture_identity(replace(scope3, interpretation="Changed literal brief."), basis2))
        self.assertNotEqual(owner.authoring_capture_identity(scope3, basis2), owner.authoring_capture_identity(replace(scope3, contributions=(Contribution(Domain.PLOT_STRUCTURE, Role.DRAFT_OWNER), Contribution(Domain.CHARACTER_WORLD, Role.CONSTRAINT_ASSESSOR))), basis2))

    def test_drafted_is_neither_supports_nor_legacy_review_assessment(self):
        events, _, _ = fixture.executed_history()
        reply = next(e.payload for e in events if type(e.payload) is c.CollaborationSpecialistReplyPayload)
        constraint = replace(reply, mode=Mode.CONSTRAINT_ASSESSMENT, assessment=c.CollaborationSpecialistAssessment.CONSTRAINTS_ASSESSED)
        self.assertIsNot(constraint.assessment, c.CollaborationSpecialistAssessment.SUPPORTS)
        for status in (c.CollaborationSpecialistAssessment.DRAFTED, c.CollaborationSpecialistAssessment.SUPPORTS):
            with self.assertRaises(ValueError): replace(constraint, assessment=status)
        drafted = replace(reply, mode=Mode.DRAFT, assessment=c.CollaborationSpecialistAssessment.DRAFTED)
        self.assertIsNot(drafted.assessment, c.CollaborationSpecialistAssessment.SUPPORTS)
        for mode, assessment in ((Mode.DRAFT, c.CollaborationSpecialistAssessment.SUPPORTS),
                                 (Mode.REVIEW, c.CollaborationSpecialistAssessment.DRAFTED),
                                 (None, c.CollaborationSpecialistAssessment.DRAFTED)):
            with self.subTest(mode=mode), self.assertRaises((TypeError, ValueError)):
                replace(reply, mode=mode, assessment=assessment)


        analysis = premise.PremiseAnalysis("alternatives_assessed", "No candidate was reviewed.", (premise.PremiseAlternative("knows", "The narrator knows.", "Preserve intentional ambiguity.", ("The reader may remain uncertain.",), ("user_input",)),))
        self.assertEqual(premise.premise_analysis_from_dict(premise.premise_value_to_dict(analysis)), analysis)
        with self.assertRaises(ValueError): replace(analysis, assessment="drafted")
        from story_writing_agents.lead_provider_contracts import PremiseDecisionConstraints
        constraints = PremiseDecisionConstraints("grounded-question", input_ids=("user_input",))
        value = premise.premise_value_to_dict(analysis)
        self.assertEqual(constraints.validate_analysis(value), analysis)
        from tests.provider_wire_fixture import fixture_schema_node
        schema = constraints.analysis_schema()
        branches = fixture_schema_node(schema, schema)['anyOf']
        assessed = next(fixture_schema_node(schema, member) for member in branches
            if fixture_schema_node(schema, fixture_schema_node(schema, member)[
                'properties']['assessment'])['enum'] == ['alternatives_assessed'])
        basis = assessed
        for field in ('alternatives', '*', 'basis_input_ids'):
            basis = fixture_schema_node(schema, basis)
            basis = basis['items'] if field == '*' else basis['properties'][field]
        self.assertEqual(fixture_schema_node(schema, basis)['minItems'], 1)
        value["alternatives"][0]["basis_input_ids"] = []
        with self.assertRaises(ValueError): constraints.validate_analysis(value)
