"""Actual provider-free advisory admission and separate confirmation boundaries."""

import hashlib
from dataclasses import replace

from story_writing_agents import authoring_admission as admission
from story_writing_agents import conversations as c
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.specialist_routing import (
    AuthoringActivity,
    AuthoringScope,
    SpecialistAssignmentMode,
)
from tests.lead_fixtures.preparation_data import (
    OUTLINE,
    fixture_assessment as _fixture_assessment,
)
from tests.lead_fixtures.specialist_routing import (
    SpecialistRoutingRuntimeCase,
    ADMISSION_BRIEF as _ADMISSION_BRIEF,
)


class SpecialistAdvisoryScopeTests(SpecialistRoutingRuntimeCase):
    async def test_provider_free_advisory_requires_scope_and_separate_confirmation(self):
        conversation = self.conversation()
        with self.assertRaisesRegex(ValueError, "explicit authoring scope"):
            await self.runtime.propose_specialist_advisory(self.session_id, conversation, PLOT_ARCHITECT, "Draft a synthetic outline.")
        scope = AuthoringScope(AuthoringActivity.DRAFT, (OUTLINE,), (), "Draft a synthetic outline.", "provided_task", 1)
        needed = await self.runtime.propose_specialist_advisory(
            self.session_id, conversation, PLOT_ARCHITECT, _ADMISSION_BRIEF, authoring_scope=scope,)
        self.assertIsInstance(needed, admission.AuthoringAdmissionNeeded)
        user = next(v for v in needed.offered_sources if v.source_id == "user_input")
        self.assertIs(user.kind, admission.AdmissionSourceKind.USER)
        self.assertEqual((user.original_authority, user.utf8_bytes), ("literal_user", len(_ADMISSION_BRIEF.encode())))
        assessment = _fixture_assessment()
        for structural_only in (replace(assessment, creative_scope=None), replace(assessment, allowed_creative_actions=())):
            self.assertEqual(admission.assessment_from_dict(admission.assessment_to_dict(structural_only)), structural_only)
        pins = dict( sources=(user,), request_digest=user.revision_digest, task_id=scope.task_id,
            task_revision=scope.task_revision,
            scope_digest=admission.admission_digest(c.authoring_scope_to_dict(scope)), )
        for structural_only in (replace(assessment, creative_scope=None), replace(assessment, allowed_creative_actions=())):
            self.assertIsInstance(admission.qualify_authoring_assessment(structural_only, **pins), admission.AuthoringAdmissionNeeded)
        for container, field, maximum in (('foundations', 'requirement', 256), ('decisions', 'decision', 256),
                ('direction', 'justification', 4096), ('decisions', 'explanation', 4096)):
            for text, valid in (('x' * maximum, True), ('x' * (maximum + 1), False),
                    ('한' * (maximum // 3), True), ('한' * (maximum // 3 + 1), False)):
                encoded_assessment = admission.assessment_to_dict(assessment); row = encoded_assessment[container]
                row = row[0] if isinstance(row, list) else row; row[field] = text
                if valid: self.assertIsInstance(admission.assessment_from_dict(encoded_assessment), admission.AuthoringAssessment)
                else:
                    with self.assertRaises(ValueError): admission.assessment_from_dict(encoded_assessment)
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, foundations=()), **pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, creative_scope=None), **pins),
            admission.AuthoringAdmissionNeeded, )
        missing = admission.AdmissionDecision("Ending", admission.DecisionCoverage.MISSING, (), "Choose the ending.")
        self.assertIsInstance(
            admission.qualify_authoring_assessment(replace(assessment, decisions=(missing,)), **pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=0),)},
            ), admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(
                assessment, **{**pins, "sources": (replace(user, utf8_bytes=None),)},
            ), admission.AuthoringAdmissionNeeded, )
        with self.assertRaisesRegex(ValueError, "unqualified source"):
            admission.qualify_authoring_assessment(_fixture_assessment(foundation_ids=("unoffered",)), **pins)
        material = admission.AdmissionSource( "input_material", admission.AdmissionSourceKind.MATERIAL,
            "fixture:nonempty-material", user.revision_digest, "input_only", len(_ADMISSION_BRIEF.encode()), )
        material_assessment = _fixture_assessment(foundation_ids=(material.source_id,))
        material_pins = {**pins, "sources": (material, user)}
        self.assertIsInstance( admission.qualify_authoring_assessment(material_assessment, **material_pins),
            admission.AuthoringAdmissionBasis, )
        empty_user = admission.AdmissionSource(
            "empty_user", admission.AdmissionSourceKind.USER, "fixture:empty-user",
            "sha256:" + hashlib.sha256(b"").hexdigest(), "literal_user", 0, )
        unknown_user = replace(empty_user, utf8_bytes=None)
        empty_direction = replace( material_assessment, direction=replace(assessment.direction, source_ids=(empty_user.source_id,)), )
        empty_delegation = replace( material_assessment, decisions=(replace(assessment.decisions[0], source_ids=(empty_user.source_id,)),), )
        empty_pins = {**pins, "sources": (material, user, empty_user)}
        unknown_pins = {**pins, "sources": (material, user, unknown_user)}
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_direction, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_direction, **unknown_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_delegation, **empty_pins),
            admission.AuthoringAdmissionNeeded, )
        self.assertIsInstance( admission.qualify_authoring_assessment(empty_delegation, **unknown_pins),
            admission.AuthoringAdmissionNeeded, )
        result = await self.runtime.propose_specialist_advisory(
            self.session_id, conversation, PLOT_ARCHITECT, _ADMISSION_BRIEF,
            authoring_scope=scope, authoring_assessment=assessment, )
        self.assertEqual(self.backend.calls, [])
        self.assertIs(result.state, c.CollaborationStepState.AWAITING_CONFIRMATION)
        self.assertIs(result.proposal.assignments[0].mode, SpecialistAssignmentMode.DRAFT)
        basis = result.proposal.basis.authoring_admission
        source = next(v for v in result.proposal.basis.inputs if v.input_id == "user_input")
        expected_user = replace(user, source_identity=source.locator, revision_digest=source.revision_digest)
        self.assertEqual((basis.request_digest, basis.sources), (source.revision_digest, (expected_user,)))
        encoded = admission.admission_basis_to_dict(basis)
        self.assertEqual(admission.admission_basis_from_dict(encoded), basis)
        with self.assertRaises(ValueError):
            admission.admission_basis_from_dict({**encoded, "caller_authorized": True})
        with self.assertRaises(ValueError):
            admission.admission_basis_from_dict({k: v for k, v in encoded.items() if k != "sources"})
