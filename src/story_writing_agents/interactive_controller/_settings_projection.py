"""Project current and pending settings rows for the interactive controller."""

from __future__ import annotations

from collections.abc import Mapping

from ..agents import AGENT_SPECS, ROSTER
from ..interactive_settings import SettingsState


def effective_settings_provider(
    state: SettingsState,
    agent_id: str,
    *,
    pending: bool = True,
    provider_overrides: Mapping[str, str] | None = None,
) -> str:
    """Resolve one settings route through agent, workspace, then Codex."""

    configuration = state.pending if pending else state.baseline.configuration
    agent = configuration.agent_configs[agent_id]
    return (
        (provider_overrides or {}).get(agent_id)
        or agent.backend
        or configuration.system_defaults.default_backend
        or "codex"
    )


def settings_projection(
    state: SettingsState,
    *,
    provider_overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str | None, str, str, str], ...]:
    """Project ordered root and agent identities with current/pending values."""

    current = state.baseline.configuration
    pending = state.pending
    rows: list[tuple[str, str | None, str, str, str]] = [
        (
            "language",
            None,
            "Language",
            current.system_defaults.language,
            pending.system_defaults.language,
        ),
        (
            "persona",
            None,
            "Persona",
            current.system_defaults.persona,
            pending.system_defaults.persona,
        ),
        (
            "max_debate_rounds",
            None,
            "Maximum debate rounds",
            str(current.system_defaults.max_debate_rounds),
            str(pending.system_defaults.max_debate_rounds),
        ),
        (
            "conversation_storage",
            None,
            "Conversation storage",
            current.system_defaults.conversation_storage_backend or "filesystem",
            pending.system_defaults.conversation_storage_backend or "filesystem",
        ),
        (
            "conversation_rag_provider",
            None,
            "Conversation RAG Provider",
            (
                "Local"
                if current.system_defaults.conversation_rag_provider == "local"
                else "Disabled"
            ),
            (
                "Local"
                if pending.system_defaults.conversation_rag_provider == "local"
                else "Disabled"
            ),
        ),
        (
            "conversation_rag_automatic",
            None,
            "Conversation RAG Automatic",
            "On" if current.system_defaults.conversation_rag_automatic else "Off",
            "On" if pending.system_defaults.conversation_rag_automatic else "Off",
        ),
        (
            "show_message_details",
            None,
            "Message details",
            "On" if current.system_defaults.show_message_details else "Off",
            "On" if pending.system_defaults.show_message_details else "Off",
        ),
    ]
    for agent_id in ROSTER:
        display_name = AGENT_SPECS[agent_id].display_name
        current_agent = current.agent_configs[agent_id]
        pending_agent = pending.agent_configs[agent_id]
        current_provider = effective_settings_provider(
            state,
            agent_id,
            pending=False,
            provider_overrides=provider_overrides,
        )
        pending_provider = effective_settings_provider(
            state,
            agent_id,
            provider_overrides=provider_overrides,
        )
        rows.extend(
            (
                (
                    "providers",
                    agent_id,
                    f"{display_name} Provider",
                    (
                        current_provider
                        if current_agent.backend is not None
                        else f"{current_provider} (default)"
                    ),
                    (
                        pending_provider
                        if pending_agent.backend is not None
                        else f"{pending_provider} (default)"
                    ),
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Codex model",
                    current_agent.codex_model or "inherit",
                    pending_agent.codex_model or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Codex reasoning",
                    current_agent.codex_reasoning_effort or "inherit",
                    pending_agent.codex_reasoning_effort or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Antigravity model",
                    current_agent.antigravity_model or "inherit",
                    pending_agent.antigravity_model or "inherit",
                ),
                (
                    "providers",
                    agent_id,
                    f"{display_name} Antigravity effort",
                    current_agent.antigravity_effort or "inherit",
                    pending_agent.antigravity_effort or "inherit",
                ),
            )
        )
    return tuple(rows)
