"""Deterministic prompt assembly with least-context hydration."""

from __future__ import annotations

from .agents import AGENT_SPECS
from .config import ConfigManager
from .models import AgentRequest, DebateMessage, Phase
from .workspace import Workspace


class PromptAssembler:
    """Build exact ordered system blocks and separate content payload fields."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.config = ConfigManager(workspace)

    def build_request(
        self,
        *,
        session_id: str,
        debate_id: str,
        agent_id: str,
        phase: Phase,
        round_number: int,
        revision_id: str,
        task: str,
        user_payload: str,
        candidate_payload: str | None = None,
        messages: tuple[DebateMessage, ...] = (),
    ) -> AgentRequest:
        """Re-read config, persona, and targeted context for every invocation."""

        runtime_overrides = self.workspace.load_session_overrides(session_id)
        resolved = self.config.resolve(agent_id, runtime_overrides)
        persona = self.workspace.read_persona(resolved.persona)
        context = self.workspace.read_core_documents(
            AGENT_SPECS[agent_id].context_documents
        )
        context_block = "\n\n".join(
            f"## {filename}\n{content}" for filename, content in context.items()
        )
        agent_state = self.workspace.read_agent_state(
            agent_id,
            session_id,
            debate_id,
        )
        system_prompt = "\n\n".join(
            (
                f"[Persona]\n{persona}",
                f"[Domain Rules]\n{resolved.domain_rules}",
                (
                    "[Language Directive]\n"
                    f"Use {resolved.language} for all visible responses."
                ),
                f"[Context Documents]\n{context_block}",
            )
        )
        return AgentRequest(
            session_id=session_id,
            debate_id=debate_id,
            agent_id=agent_id,
            phase=phase,
            round_number=round_number,
            revision_id=revision_id,
            system_prompt=system_prompt,
            task=(
                f"{task} Return only the requested structured result. "
                "Do not reveal or provide hidden chain-of-thought."
            ),
            user_payload=user_payload,
            candidate_payload=candidate_payload,
            messages=messages,
            agent_state=agent_state,
        )
