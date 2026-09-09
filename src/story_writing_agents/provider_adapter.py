"""Private contracts between the backend coordinator and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .agent_context import (
    ContextComponentBytes,
    ContextComponentKind,
    JsonPath,
    ProviderCallAccounting,
    ProviderCallKind,
    ProviderCallState,
    ProviderJsonEncodingError,
    ProviderPayloadAccounting,
    ProviderUsage,
    account_provider_payload,
)
from .errors import ProviderAuthenticationRequiredError
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
    binding_components: tuple[ContextComponentBytes, ...] = ()

    def __post_init__(self) -> None:
        if type(self.binding_components) is not tuple or any(
            type(component) is not ContextComponentBytes
            for component in self.binding_components
        ):
            raise TypeError(
                "provider binding components must be ContextComponentBytes values"
            )


@dataclass(frozen=True)
class ProviderInvocation:
    """One structured provider turn independent of vendor transport details."""

    session_id: str
    schema: Mapping[str, Any] = field(repr=False)
    payload: Mapping[str, Any] = field(repr=False)
    thread_id: str | None
    call_kind: ProviderCallKind = ProviderCallKind.UNCLASSIFIED
    component_paths: Mapping[JsonPath, ContextComponentKind] = field(
        default_factory=dict,
        repr=False,
    )
    mandatory_reference_paths: tuple[JsonPath, ...] = field(
        default=(),
        repr=False,
    )
    binding_components: tuple[ContextComponentBytes, ...] = field(
        default=(),
        repr=False,
    )
    payload_json: bytes | None = field(init=False, repr=False)
    payload_accounting: ProviderPayloadAccounting | None = field(init=False)
    call_accounting: ProviderCallAccounting | None = field(init=False)

    def __post_init__(self) -> None:
        schema = MappingProxyType(dict(self.schema))
        payload = MappingProxyType(dict(self.payload))
        component_paths = MappingProxyType(dict(self.component_paths))
        if type(self.binding_components) is not tuple or any(
            type(component) is not ContextComponentBytes
            for component in self.binding_components
        ):
            raise TypeError(
                "provider invocation binding components must be "
                "ContextComponentBytes values"
            )
        try:
            payload_json, accounting = account_provider_payload(
                schema,
                payload,
                call_kind=self.call_kind,
                component_paths=component_paths,
                mandatory_reference_paths=self.mandatory_reference_paths,
            )
        except ProviderJsonEncodingError:
            payload_json = None
            accounting = None
        object.__setattr__(self, "schema", schema)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "component_paths", component_paths)
        object.__setattr__(self, "payload_json", payload_json)
        object.__setattr__(self, "payload_accounting", accounting)
        object.__setattr__(
            self,
            "call_accounting",
            (
                None
                if accounting is None
                else ProviderCallAccounting(
                    payload=accounting,
                    binding_components=self.binding_components,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ProviderResponseDiagnostic:
    """Passive completed-process bytes for private exchange diagnostics only."""

    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)
    exit_code: int

    def __post_init__(self) -> None:
        if type(self.stdout) is not bytes or type(self.stderr) is not bytes:
            raise TypeError("provider response diagnostic streams must be bytes")
        if type(self.exit_code) is not int:
            raise TypeError("provider response diagnostic exit code must be an integer")


@dataclass(frozen=True)
class ProviderResult:
    """Validated provider transport output awaiting domain-level decoding."""

    thread_id: str
    response_text: str
    usage: ProviderUsage | None = field(default=None, compare=False)
    actual_transport_utf8_bytes: int | None = field(default=None, compare=False)
    response_diagnostic: ProviderResponseDiagnostic | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            self.response_diagnostic is not None
            and type(self.response_diagnostic) is not ProviderResponseDiagnostic
        ):
            raise TypeError("provider response diagnostic has an invalid type")
        if self.usage is not None and type(self.usage) is not ProviderUsage:
            raise TypeError("provider result usage must be ProviderUsage or None")
        if self.actual_transport_utf8_bytes is not None:
            if type(self.actual_transport_utf8_bytes) is not int:
                raise TypeError("provider transport UTF-8 bytes must be an integer")
            if self.actual_transport_utf8_bytes < 0:
                raise ValueError(
                    "provider transport UTF-8 bytes must be nonnegative"
                )


@dataclass(frozen=True, slots=True)
class ProviderFailureAccounting:
    """Validated content-free transport facts carried across one failure."""

    state: ProviderCallState = ProviderCallState.PREPARED
    actual_transport_utf8_bytes: int | None = None
    usage: ProviderUsage | None = None

    def __post_init__(self) -> None:
        if type(self.state) is not ProviderCallState:
            raise TypeError("provider failure state must be a ProviderCallState")
        if self.actual_transport_utf8_bytes is not None:
            if type(self.actual_transport_utf8_bytes) is not int:
                raise TypeError(
                    "provider failure transport UTF-8 bytes must be an integer"
                )
            if self.actual_transport_utf8_bytes < 0:
                raise ValueError(
                    "provider failure transport UTF-8 bytes must be nonnegative"
                )
            if self.state is not ProviderCallState.SUBMITTED:
                raise ValueError(
                    "provider failure transport bytes require a submitted call"
                )
        if self.usage is not None:
            if type(self.usage) is not ProviderUsage:
                raise TypeError("provider failure usage must be ProviderUsage or None")
            if self.state is not ProviderCallState.SUBMITTED:
                raise ValueError("provider failure usage requires a submitted call")


_PREPARED_PROVIDER_FAILURE = ProviderFailureAccounting()


class ProviderContinuationRejection(StrEnum):
    """A deterministic provider rejection proving no turn was executed.

    Adapters may set this only from a supported machine-readable rejection
    envelope. Exit status, diagnostic prose, and missing output are insufficient.
    """

    UNAVAILABLE = "continuation_unavailable"
    CAPACITY = "context_capacity"


class ProviderInvocationError(Exception):
    """Carry one safe adapter diagnostic and thread-taint decision."""

    def __init__(
        self,
        diagnostic: str,
        *,
        taint_existing_thread: bool,
        continuation_rejection: ProviderContinuationRejection | None = None,
        authentication_failure: ProviderAuthenticationRequiredError | None = None,
    ) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        if authentication_failure is not None and (
            type(authentication_failure) is not ProviderAuthenticationRequiredError
        ):
            raise TypeError("authentication failure must be a typed provider failure")
        self.authentication_failure = authentication_failure
        self.taint_existing_thread = taint_existing_thread
        if continuation_rejection is not None and type(continuation_rejection) is not ProviderContinuationRejection:
            raise TypeError("continuation rejection must be a closed provider code")
        self.continuation_rejection = continuation_rejection
        self._call_accounting = _PREPARED_PROVIDER_FAILURE
        self._response_diagnostic: ProviderResponseDiagnostic | None = None

    def record_response_diagnostic(
        self,
        diagnostic: ProviderResponseDiagnostic,
    ) -> None:
        """Attach exact completed output without changing the safe error text."""

        if type(diagnostic) is not ProviderResponseDiagnostic:
            raise TypeError("provider response diagnostic has an invalid type")
        self._response_diagnostic = diagnostic

    @property
    def response_diagnostic(self) -> ProviderResponseDiagnostic | None:
        """Expose only a validated optional value to the exchange coordinator."""

        value = self._response_diagnostic
        return value if type(value) is ProviderResponseDiagnostic else None


def record_provider_failure_accounting(
    failure: BaseException,
    *,
    state: ProviderCallState,
    actual_transport_utf8_bytes: int | None = None,
    usage: ProviderUsage | None = None,
) -> None:
    """Attach validated, content-free call facts without replacing a failure."""

    accounting = ProviderFailureAccounting(
        state=state,
        actual_transport_utf8_bytes=actual_transport_utf8_bytes,
        usage=usage,
    )
    if isinstance(failure, ProviderInvocationError):
        failure._call_accounting = accounting
        return
    try:
        setattr(failure, "_provider_call_accounting", accounting)
    except (AttributeError, TypeError):
        pass


def provider_failure_accounting(
    failure: BaseException,
) -> ProviderFailureAccounting:
    """Return only validated private accounting attached to one failure."""

    if isinstance(failure, ProviderInvocationError):
        accounting = failure._call_accounting
    else:
        accounting = getattr(failure, "_provider_call_accounting", None)
    if type(accounting) is not ProviderFailureAccounting:
        return _PREPARED_PROVIDER_FAILURE
    return accounting


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
