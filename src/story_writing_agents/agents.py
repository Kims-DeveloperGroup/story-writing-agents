"""Canonical agent roster and narrowly targeted context ownership."""

from __future__ import annotations

from dataclasses import dataclass


LEAD_ORCHESTRATOR = "lead_orchestrator"
PLOT_ARCHITECT = "plot_architect"
CHARACTER_CUSTODIAN = "character_custodian"
EXECUTION_SPECIALIST = "execution_specialist"

COMMON_BEHAVIOR_POLICY_FILE = "config/agents/common/AGENTS.md"
SPECIALIST_COLLABORATION_POLICY_FILE = (
    "config/agents/specialist_collaboration/AGENTS.md"
)

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
    behavior_policy_files: tuple[str, ...]
    context_documents: tuple[str, ...]
    votes: bool


AGENT_SPECS: dict[str, AgentSpec] = {
    LEAD_ORCHESTRATOR: AgentSpec(
        agent_id=LEAD_ORCHESTRATOR,
        display_name="Lead Orchestrator",
        system_prompt_file="config/agents/lead_orchestrator/AGENTS.md",
        behavior_policy_files=(
            COMMON_BEHAVIOR_POLICY_FILE,
            "config/agents/lead_orchestrator/AGENTS.md",
        ),
        context_documents=CORE_DOCUMENTS,
        votes=False,
    ),
    PLOT_ARCHITECT: AgentSpec(
        agent_id=PLOT_ARCHITECT,
        display_name="Plot & Structure Architect",
        system_prompt_file="config/agents/plot_architect/AGENTS.md",
        behavior_policy_files=(
            COMMON_BEHAVIOR_POLICY_FILE,
            SPECIALIST_COLLABORATION_POLICY_FILE,
            "config/agents/plot_architect/AGENTS.md",
        ),
        context_documents=("global_outline.md", "part_outlines.md"),
        votes=True,
    ),
    CHARACTER_CUSTODIAN: AgentSpec(
        agent_id=CHARACTER_CUSTODIAN,
        display_name="Character & World Custodian",
        system_prompt_file="config/agents/character_custodian/AGENTS.md",
        behavior_policy_files=(
            COMMON_BEHAVIOR_POLICY_FILE,
            SPECIALIST_COLLABORATION_POLICY_FILE,
            "config/agents/character_custodian/AGENTS.md",
        ),
        context_documents=("character_sheets.md",),
        votes=True,
    ),
    EXECUTION_SPECIALIST: AgentSpec(
        agent_id=EXECUTION_SPECIALIST,
        display_name="Tone & Execution Specialist",
        system_prompt_file="config/agents/execution_specialist/AGENTS.md",
        behavior_policy_files=(
            COMMON_BEHAVIOR_POLICY_FILE,
            SPECIALIST_COLLABORATION_POLICY_FILE,
            "config/agents/execution_specialist/AGENTS.md",
        ),
        context_documents=("narrative_rules.md",),
        votes=True,
    ),
}


def behavior_policy_files(agent_id: str) -> tuple[str, ...]:
    """Return the one canonical ordered behavior-policy composition."""

    if type(agent_id) is not str or agent_id not in ROSTER:
        raise ValueError("behavior policy agent is not canonical")
    spec = AGENT_SPECS.get(agent_id)
    if type(spec) is not AgentSpec or spec.agent_id != agent_id:
        raise ValueError("behavior policy metadata is incomplete")
    expected = (
        (COMMON_BEHAVIOR_POLICY_FILE, spec.system_prompt_file)
        if agent_id == LEAD_ORCHESTRATOR
        else (
            COMMON_BEHAVIOR_POLICY_FILE,
            SPECIALIST_COLLABORATION_POLICY_FILE,
            spec.system_prompt_file,
        )
    )
    if spec.behavior_policy_files != expected:
        raise ValueError("behavior policy metadata crosses role boundaries")
    if len(set(expected)) != len(expected) or any(
        type(path) is not str
        or not path.startswith("config/agents/")
        or not path.endswith("/AGENTS.md")
        or "\x00" in path
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        for path in expected
    ):
        raise ValueError("behavior policy component path is invalid")
    return expected
