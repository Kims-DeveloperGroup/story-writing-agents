"""Canonical agent roster and narrowly targeted context ownership."""

from __future__ import annotations

from dataclasses import dataclass


LEAD_ORCHESTRATOR = "lead_orchestrator"
PLOT_ARCHITECT = "plot_architect"
CHARACTER_CUSTODIAN = "character_custodian"
EXECUTION_SPECIALIST = "execution_specialist"

ROSTER: tuple[str, ...] = (
    LEAD_ORCHESTRATOR,
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
)

SPECIALIST_IDS: tuple[str, ...] = (
    PLOT_ARCHITECT,
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
)

AGENT_STATE_DIRECTORIES: dict[str, str] = {
    LEAD_ORCHESTRATOR: "orchestrator_state",
    PLOT_ARCHITECT: "plot_architect_state",
    CHARACTER_CUSTODIAN: "character_custodian_state",
    EXECUTION_SPECIALIST: "execution_specialist_state",
}

CORE_DOCUMENTS: tuple[str, ...] = (
    "global_outline.md",
    "part_outlines.md",
    "character_sheets.md",
    "narrative_rules.md",
)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Static, provider-neutral agent behavior."""

    agent_id: str
    display_name: str
    system_prompt_file: str
    context_documents: tuple[str, ...]
    votes: bool


AGENT_SPECS: dict[str, AgentSpec] = {
    LEAD_ORCHESTRATOR: AgentSpec(
        agent_id=LEAD_ORCHESTRATOR,
        display_name="Lead Orchestrator",
        system_prompt_file="config/agents/lead_orchestrator/AGENTS.md",
        context_documents=CORE_DOCUMENTS,
        votes=False,
    ),
    PLOT_ARCHITECT: AgentSpec(
        agent_id=PLOT_ARCHITECT,
        display_name="Plot & Structure Architect",
        system_prompt_file="config/agents/plot_architect/AGENTS.md",
        context_documents=("global_outline.md", "part_outlines.md"),
        votes=True,
    ),
    CHARACTER_CUSTODIAN: AgentSpec(
        agent_id=CHARACTER_CUSTODIAN,
        display_name="Character & World Custodian",
        system_prompt_file="config/agents/character_custodian/AGENTS.md",
        context_documents=("character_sheets.md",),
        votes=True,
    ),
    EXECUTION_SPECIALIST: AgentSpec(
        agent_id=EXECUTION_SPECIALIST,
        display_name="Tone & Execution Specialist",
        system_prompt_file="config/agents/execution_specialist/AGENTS.md",
        context_documents=("narrative_rules.md",),
        votes=True,
    ),
}
