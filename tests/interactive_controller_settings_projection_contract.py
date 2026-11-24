"""Existing Controller contract for the settings value projection."""

from story_writing_agents import interactive_settings as settings_module
from story_writing_agents import interactive_terminal as terminal_module
from story_writing_agents.agents import PLOT_ARCHITECT
from story_writing_agents.interactive_controller import InteractiveController
from story_writing_agents.interactive_terminal._render import render_plain


def verify_settings_panel_omits_empty_diff_sections(self) -> None:
    workspace = self.make_workspace()
    state = settings_module.load_settings_state(workspace)
    self.assertEqual(
        InteractiveController._settings_panel(state),
        terminal_module.Panel(
            "Settings",
            (
                terminal_module.Notice(
                    "No pending settings changes.",
                    label="no changes",
                    role=terminal_module.SemanticRole.MUTED,
                ),
            ),
            state=terminal_module.ViewState.OPEN,
        ),
    )

    changed = settings_module.update_agent_codex_model(
        state,
        PLOT_ARCHITECT,
        "codex-model",
    )
    changed = settings_module.update_agent_antigravity_model(
        changed,
        PLOT_ARCHITECT,
        "agy-model",
    )
    panel = InteractiveController._settings_panel(changed)
    self.assertEqual(panel, self.expected_panel(changed))
    rendered = render_plain(panel, width=120).text
    self.assertIn("Plot & Structure Architect Codex model", rendered)
    self.assertIn('Plot & Structure Architect Antigravity model', rendered)
