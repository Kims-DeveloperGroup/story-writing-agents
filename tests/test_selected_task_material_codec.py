"""Pure wire-shape regression for actual selected-material input rows."""
import unittest

from story_writing_agents.lead_task_transitions import (
    LeadMaterialCapture, LeadMaterialKind, LeadMaterialRef, selected_task_materials_to_dict,
)
from story_writing_agents.provider_request_mapping import project_runtime_object


class SelectedTaskMaterialCodecTests(unittest.TestCase):
    def test_two_captures_keep_closed_wire_shape_and_order(self):
        rejected = LeadMaterialCapture(
            origin_event_id="q_materials", target_task_id="new_task", target_task_revision=1,
            source=LeadMaterialRef(session_id="material_session", conversation_id="c_rejected",
                event_id="e_rejected", kind=LeadMaterialKind.PUBLIC_RESPONSE,
                material_digest="c5fdc44d56f982a9bf9733bd035b1c846a2822e543aee024b1d1d71fff072fc3"),
            selected_text="Selected rejected whole explanation.",
            material_digest="c5fdc44d56f982a9bf9733bd035b1c846a2822e543aee024b1d1d71fff072fc3",
        )
        drafted = LeadMaterialCapture(
            origin_event_id="q_materials", target_task_id="new_task", target_task_revision=1,
            source=LeadMaterialRef(session_id="material_session", conversation_id="c_drafted",
                event_id="e_drafted", kind=LeadMaterialKind.DRAFT,
                material_digest="01642e593613379511bc45ffe533a08675699735b9db1f0543152fbd8a2e27b2"),
            selected_text="The draft material is available for integration; no artifact changed.",
            material_digest="01642e593613379511bc45ffe533a08675699735b9db1f0543152fbd8a2e27b2",
        )
        actual = project_runtime_object(selected_task_materials_to_dict(
            (("e_capture_rejected", rejected), ("e_capture_drafted", drafted))), "selected_task_materials")
        expected = [
            {"schema_version": 1, "input_source_id": "selected_material_1", "capture_event_id": "e_capture_rejected",
             "origin_event_id": "q_materials", "selected_text": "Selected rejected whole explanation.",
             "material_digest": "c5fdc44d56f982a9bf9733bd035b1c846a2822e543aee024b1d1d71fff072fc3",
             "source": {"session_id": "material_session", "conversation_id": "c_rejected", "event_id": "e_rejected",
                        "record_type": "public_response", "material_digest": "c5fdc44d56f982a9bf9733bd035b1c846a2822e543aee024b1d1d71fff072fc3"}},
            {"schema_version": 1, "input_source_id": "selected_material_2", "capture_event_id": "e_capture_drafted",
             "origin_event_id": "q_materials", "selected_text": "The draft material is available for integration; no artifact changed.",
             "material_digest": "01642e593613379511bc45ffe533a08675699735b9db1f0543152fbd8a2e27b2",
             "source": {"session_id": "material_session", "conversation_id": "c_drafted", "event_id": "e_drafted",
                        "record_type": "draft", "material_digest": "01642e593613379511bc45ffe533a08675699735b9db1f0543152fbd8a2e27b2"}},
        ]
        self.assertEqual(actual, expected)
