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
    domain_rules: str
    context_documents: tuple[str, ...]
    votes: bool


AGENT_SPECS: dict[str, AgentSpec] = {
    LEAD_ORCHESTRATOR: AgentSpec(
        agent_id=LEAD_ORCHESTRATOR,
        display_name="Lead Orchestrator",
        domain_rules=(
            "Moderate the shared debate, preserve every specialist critique, "
            "synthesize revisions without discarding conflicts, and return only "
            "structured updates for the allowlisted core documents."
        ),
        context_documents=CORE_DOCUMENTS,
        votes=False,
    ),
    PLOT_ARCHITECT: AgentSpec(
        agent_id=PLOT_ARCHITECT,
        display_name="Plot & Structure Architect",
        domain_rules=(
            "Audit narrative flow, pacing, chapter progression, logical leaps, "
            "plot holes, and compliance with the global and part outlines."
        ),
        context_documents=("global_outline.md", "part_outlines.md"),
        votes=True,
    ),
    CHARACTER_CUSTODIAN: AgentSpec(
        agent_id=CHARACTER_CUSTODIAN,
        display_name="Character & World Custodian",
        domain_rules=(
            "Audit character behavior, relationships, world continuity, and "
            "secondary-character consistency against the character sheets."
        ),
        context_documents=("character_sheets.md",),
        votes=True,
    ),
    EXECUTION_SPECIALIST: AgentSpec(
        agent_id=EXECUTION_SPECIALIST,
        display_name="Tone & Execution Specialist",
        domain_rules=(
            "Draft and audit prose, point of view, tone, grammar, formatting, "
            "and compliance with the narrative rules."
        ),
        context_documents=("narrative_rules.md",),
        votes=True,
    ),
}
