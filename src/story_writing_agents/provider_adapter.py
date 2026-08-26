"""Private contracts between the backend coordinator and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .models import AgentDefinition


class ExecutionValueSource(StrEnum):
    """Authoritative origin of one effective provider execution value."""

    LAUNCH_OVERRIDE = "launch_override"
    AGENT_CONFIG = "agent_config"
    SYSTEM_DEFAULT = "system_default"
    BUILT_IN_DEFAULT = "built_in_default"
    PROVIDER_DEFAULT = "provider_default"


@dataclass(frozen=True, slots=True)
class AgentExecutionProfile:
    """One immutable set of values used for an exact agent invocation."""

    agent_id: str
    provider: str
    provider_source: ExecutionValueSource
    model: str | None
    model_source: ExecutionValueSource
    effort: str | None
    effort_source: ExecutionValueSource

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or not self.agent_id:
            raise ValueError("agent_id must be non-empty text")
        if self.provider not in {"codex", "antigravity"}:
            raise ValueError("provider must be codex or antigravity")
        for field_name in (
            "provider_source",
            "model_source",
            "effort_source",
        ):
            if not isinstance(getattr(self, field_name), ExecutionValueSource):
                raise TypeError(f"{field_name} must be an ExecutionValueSource")
        for field_name in ("model", "effort"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{field_name} must be non-empty text or None")


@dataclass(frozen=True)
class ProviderBinding:
    """One immutable provider-owned binding for an agent definition."""

    vendor: str
    definition: AgentDefinition
    staging_directory: Path


@dataclass(frozen=True)
class ProviderInvocation:
    """One structured provider turn independent of vendor transport details."""

    session_id: str
    schema: Mapping[str, Any]
    payload: Mapping[str, Any]
    thread_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema", MappingProxyType(dict(self.schema)))
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class ProviderResult:
    """Validated provider transport output awaiting domain-level decoding."""

    thread_id: str
    response_text: str


class ProviderInvocationError(Exception):
    """Carry one safe adapter diagnostic and thread-taint decision."""

    def __init__(
        self,
        diagnostic: str,
        *,
        taint_existing_thread: bool,
    ) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        self.taint_existing_thread = taint_existing_thread


class ProviderAdapter(Protocol):
    """Private lifecycle and invocation surface implemented by each provider."""

    vendor: str

    def bind(self, definition: AgentDefinition) -> ProviderBinding:
        """Create one provider-owned binding after validating its references."""

    async def invoke(
        self,
        binding: ProviderBinding,
        invocation: ProviderInvocation,
    ) -> ProviderResult:
        """Run one structured provider turn."""

    def release(self, binding: ProviderBinding) -> None:
        """Release one exact provider binding idempotently."""

    def executable_available(self) -> bool:
        """Return whether this adapter's executable is ready to launch."""

    def close(self) -> None:
        """Release provider-owned ephemeral resources idempotently."""
