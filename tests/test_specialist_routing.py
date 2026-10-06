"""Pure routing, versioned wire compatibility, and draft/review acceptance."""
from dataclasses import FrozenInstanceError, fields, replace
import json
from pathlib import Path
import unittest
from story_writing_agents import lead_premise_decisions as premise

from story_writing_agents import conversations as c
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain as Domain, AuthoringActivity as Activity, AuthoringScope,
    ReviewBasis, SpecialistAssignmentMode as Mode, artifact_domain,
    required_authoring_domains, required_authoring_specialists,
    validate_specialist_assignment, validate_specialist_routing,
    AuthoringContribution as Contribution, AuthoringContributionRole as Role,
    AuthoringDependencySlot as Slot, assignment_mode_for_specialist, required_drafting_specialists,
)
from tests import collaboration_contract_fixture as fixture


class SpecialistRoutingTests(unittest.TestCase):
    def scope(self, **changes):
        values = dict(activity=Activity.DRAFT, target_paths=(), domains=(Domain.PLOT_STRUCTURE,),
                      interpretation="Draft a synthetic causally ordered outline.", task_id="task_a", task_revision=1)
        values.update(changes)
        return AuthoringScope(**values)
    def mixed_scope(self):
        return self.scope(schema_version=3, domains=tuple(Domain),
                          contributions=tuple(Contribution(domain, Role.DRAFT_OWNER if domain is Domain.TONE_EXECUTION else Role.CONSTRAINT_ASSESSOR) for domain in Domain),
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
        outcome = c.LeadProviderOutcome(c.LeadOutcomeKind.RECOMMENDATION, "Finished candidate",
            c.LeadOutcomeScope.STORY_SPECIFIC, c.LeadEvidenceStatus.INCOMPLETE, (),
            None, None, None, action, authoring_scope=scope)
        self.assertIs(outcome.recommended_action, action)
        self.assertIs(outcome.authoring_scope, scope)
        parameters = inspect.signature(c.LeadProviderOutcome).parameters
        self.assertEqual(tuple(name for name, p in parameters.items()
            if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ("kind", "message", "scope", "evidence_status", "evidence_ids", "proposal",
             "artifact_intent", "objective_intent", "recommended_action"))
        self.assertIs(parameters["authoring_scope"].kind, inspect.Parameter.KEYWORD_ONLY)
        with self.assertRaises(TypeError):
            c.LeadProviderOutcome(c.LeadOutcomeKind.RECOMMENDATION, "Finished candidate",
                c.LeadOutcomeScope.STORY_SPECIFIC, c.LeadEvidenceStatus.INCOMPLETE, (),
                None, None, None, action, scope)
        basis_parameters = inspect.signature(c.CollaborationProposalBasis).parameters
        self.assertEqual(tuple((basis_parameters[key].default, basis_parameters[key].kind) for key in ("schema_version", "shared_premises")), ((1, inspect.Parameter.KEYWORD_ONLY), ((), inspect.Parameter.KEYWORD_ONLY)))

    def test_canonical_artifact_and_semantic_domain_ownership(self):
        cases = (("core_documents/global_outline.md", Domain.PLOT_STRUCTURE, "plot_architect"),
                 ("core_documents/part_outlines.md", Domain.PLOT_STRUCTURE, "plot_architect"),
                 ("core_documents/character_sheets.md", Domain.CHARACTER_WORLD, "character_custodian"),
                 ("core_documents/narrative_rules.md", Domain.TONE_EXECUTION, "execution_specialist"),
                 ("manuscript/part-01/opening.md", Domain.TONE_EXECUTION, "execution_specialist"))
        for path, domain, owner in cases:
            with self.subTest(path=path):
                self.assertIs(artifact_domain(path), domain)
                scope = self.scope(target_paths=(path,), domains=())
                self.assertEqual(required_authoring_domains(scope), (domain,))
                self.assertEqual(required_authoring_specialists(scope), (owner,))
        self.assertEqual(required_authoring_specialists(self.scope(domains=tuple(Domain))),
                         ("plot_architect", "character_custodian", "execution_specialist"))

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
        with self.assertRaises(FrozenInstanceError):
            spec.question = "Changed"
        with self.assertRaises(ValueError):
            replace(spec, dependent_domains=())

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

    def test_legacy_proposal_bytes_stay_omitted_and_new_scope_changes_digest(self):
        frozen = json.loads((Path(__file__).parent / "fixtures/authoring_scope_compatibility/scope_v2_basis_v1_76a80f7.json").read_bytes())
        self.assertEqual(frozen["source_commit"], "76a80f72be711d927fff7eee4ee455d3b37802c4")
        for case in frozen["cases"].values():
            historical = c.decode_collaboration_proposal(case["proposal_utf8"].encode())
            self.assertEqual(c.encode_collaboration_proposal(historical), case["proposal_utf8"].encode())
            self.assertEqual(historical.proposal_digest, case["proposal_digest"])
            for key in ("contributions", "dependency_slots"):
                with self.assertRaises(ValueError): c.authoring_scope_from_dict(dict(c.authoring_scope_to_dict(historical.authoring_scope), **{key: []}))
            poisoned = json.loads(case["proposal_utf8"])
            poisoned["proposal"]["basis"]["shared_premises"] = []
            with self.assertRaises(ValueError): c.decode_collaboration_proposal(json.dumps(poisoned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        legacy = fixture.make_proposal()
        before = c.encode_collaboration_proposal(legacy)
        self.assertNotIn(b'authoring_scope', before)
        self.assertNotIn(b'review_basis', before)
        self.assertEqual(c.encode_collaboration_proposal(c.decode_collaboration_proposal(before)), before)
        assignments = tuple(replace(a, mode=Mode.DRAFT) for a in legacy.assignments)
        scope = self.scope(domains=(Domain.PLOT_STRUCTURE, Domain.CHARACTER_WORLD))
        values = {f.name:getattr(legacy,f.name) for f in fields(legacy) if f.name != "proposal_digest"}
        values.update(authoring_scope=scope, assignments=assignments)
        scoped = c.CollaborationProposal.create(**values)
        wire = c.encode_collaboration_proposal(scoped)
        self.assertEqual(c.decode_collaboration_proposal(wire), scoped)
        self.assertNotEqual(scoped.proposal_digest, legacy.proposal_digest)
        for change in ({"task_revision": 2}, {"interpretation": "A different visible interpretation."},
                       {"target_paths": ("core_documents/global_outline.md",)}):
            changed = c.CollaborationProposal.create(**dict(values, authoring_scope=replace(scope, **change)))
            self.assertNotEqual(changed.proposal_digest, scoped.proposal_digest)
        self.assertEqual(c.encode_collaboration_proposal(legacy), before)
        from story_writing_agents.conversations import events as owner
        scope3 = replace(scope, schema_version=3, contributions=tuple(Contribution(domain, Role.DRAFT_OWNER) for domain in scope.domains), dependency_slots=(Slot("setting", (Domain.PLOT_STRUCTURE,)),))
        source = owner.CollaborationPremiseSource(scoped.basis.inputs[0], None)
        premise = owner.CollaborationSharedPremise("setting", "Exact café 한글\r\n  ", (source,))
        basis2 = replace(scoped.basis, schema_version=2, shared_premises=(premise,))
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
        with self.assertRaises(ValueError):
            replace(analysis, assessment="drafted")
        from story_writing_agents.lead_provider_contracts import PremiseDecisionConstraints
        constraints = PremiseDecisionConstraints("grounded-question", input_ids=("user_input",))
        value = premise.premise_value_to_dict(analysis)
        self.assertEqual(constraints.validate_analysis(value), analysis)
        self.assertEqual(constraints.analysis_schema()["properties"]["alternatives"]["items"]["properties"]["basis_input_ids"]["minItems"], 1)
        value["alternatives"][0]["basis_input_ids"] = []
        with self.assertRaises(ValueError):
            constraints.validate_analysis(value)
