"""Private contracts between the backend coordinator and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .models import AgentDefinition


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

    def executable_available(self) -> bool:
        """Return whether this adapter's executable is ready to launch."""

    def close(self) -> None:
        """Release provider-owned ephemeral resources idempotently."""
