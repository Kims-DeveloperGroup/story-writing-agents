"""Pure routing, versioned wire compatibility, and draft/review acceptance."""
from dataclasses import FrozenInstanceError, fields, replace
import json
import unittest

from story_writing_agents import conversations as c
from story_writing_agents.specialist_routing import (
    ArtifactReviewDomain as Domain, AuthoringActivity as Activity, AuthoringScope,
    ReviewBasis, SpecialistAssignmentMode as Mode, artifact_domain,
    required_authoring_domains, required_authoring_specialists,
    validate_specialist_assignment, validate_specialist_routing,
)
from tests import collaboration_contract_fixture as fixture


class SpecialistRoutingTests(unittest.TestCase):
    def scope(self, **changes):
        values = dict(activity=Activity.DRAFT, target_paths=(), domains=(Domain.PLOT_STRUCTURE,),
                      interpretation="Draft a synthetic causally ordered outline.", task_id="task_a", task_revision=1)
        values.update(changes)
        return AuthoringScope(**values)

    def test_published_recommended_action_position_and_keyword_only_scope_are_preserved(self):
        import inspect
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

    def test_review_requires_disclosed_concrete_basis_while_draft_forbids_it(self):
        basis = ReviewBasis(("candidate",), "Compare motivation and chronology.")
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

    def test_legacy_proposal_bytes_stay_omitted_and_new_scope_changes_digest(self):
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

    def test_drafted_is_neither_supports_nor_legacy_review_assessment(self):
        events, _, _ = fixture.executed_history()
        reply = next(e.payload for e in events if type(e.payload) is c.CollaborationSpecialistReplyPayload)
        drafted = replace(reply, mode=Mode.DRAFT, assessment=c.CollaborationSpecialistAssessment.DRAFTED)
        self.assertIsNot(drafted.assessment, c.CollaborationSpecialistAssessment.SUPPORTS)
        for mode, assessment in ((Mode.DRAFT, c.CollaborationSpecialistAssessment.SUPPORTS),
                                 (Mode.REVIEW, c.CollaborationSpecialistAssessment.DRAFTED),
                                 (None, c.CollaborationSpecialistAssessment.DRAFTED)):
            with self.subTest(mode=mode), self.assertRaises((TypeError, ValueError)):
                replace(reply, mode=mode, assessment=assessment)
