"""Private contracts between the backend coordinator and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import inspect
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
    ProviderStreamSink,
    ProviderUsage,
    account_provider_payload,
    active_provider_stream_sink,
)
from .errors import ProviderAuthenticationRequiredError
from .models import AgentDefinition
from .provider_execution_workspace import ProviderExecutionWorkspace
from .agent_context.accounting import ProviderUsageObservation, ProviderUsageScope, provider_schema_statistics


DEFAULT_PROVIDER_TIMEOUT_SECONDS = 900.0
PROVIDER_TIMEOUT_GRACE_SECONDS = 30.0


class NativeContextCheckStage(StrEnum):
    PRE_SUBMIT = "pre_submit"
    POST_COMPLETE = "post_complete"
    UNKNOWN = "unknown"


class NativeContextSourceStage(StrEnum):
    INPUT = "input"
    WORKSPACE = "workspace"
    DATABASE = "database"
    ROLLOUT = "rollout"
    RECORDS = "records"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class NativeContextCheckReason(StrEnum):
    VERIFIED = "verified"
    INPUT_INVALID = "input_invalid"
    ANCHOR_MISSING = "anchor_missing"
    WORKSPACE_UNAVAILABLE = "workspace_unavailable"
    ROOT_INVALID = "root_invalid"
    SCOPE_MISMATCH = "scope_mismatch"
    DATABASE_LIMIT = "database_limit"
    DATABASE_HEADER_INVALID = "database_header_invalid"
    WAL_NOT_VISIBLE = "wal_not_visible"
    WAL_HEADER_UNSTABLE = "wal_header_unstable"
    WAL_CHECKSUM_UNSTABLE = "wal_checksum_unstable"
    DATABASE_MODULE_UNAVAILABLE = "database_module_unavailable"
    DATABASE_SCHEMA_UNSUPPORTED = "database_schema_unsupported"
    DATABASE_BUSY = "database_busy"
    DATABASE_QUERY_FAILED = "database_query_failed"
    DATABASE_CHANGED = "database_changed"
    DATABASE_IDENTITY_CHANGED = "database_identity_changed"
    THREAD_NOT_VISIBLE = "thread_not_visible"
    THREAD_RECORD_INVALID = "thread_record_invalid"
    ROLLOUT_PATH_CHANGED = "rollout_path_changed"
    ROLLOUT_NOT_VISIBLE = "rollout_not_visible"
    ROLLOUT_PATH_INVALID = "rollout_path_invalid"
    ROLLOUT_NAME_INVALID = "rollout_name_invalid"
    ROLLOUT_LIMIT = "rollout_limit"
    ROLLOUT_IDENTITY_CHANGED = "rollout_identity_changed"
    ROLLOUT_CHANGED = "rollout_changed"
    ROLLOUT_FLUSH_PENDING = "rollout_flush_pending"
    PREFIX_MISMATCH = "prefix_mismatch"
    ANCHOR_MISMATCH = "anchor_mismatch"
    PRE_SUBMIT_HISTORY_CHANGED = "pre_submit_history_changed"
    RECORD_LIMIT = "record_limit"
    CALL_MISMATCH = "call_mismatch"
    RECORD_INVALID = "record_invalid"
    RECORD_UNSUPPORTED = "record_unsupported"
    METADATA_MISMATCH = "metadata_mismatch"
    EVENT_UNSUPPORTED = "event_unsupported"
    ITEM_UNSUPPORTED = "item_unsupported"
    CONCURRENT_TURN = "concurrent_turn"
    SETTINGS_MISMATCH = "settings_mismatch"
    RESPONSE_ITEM_UNSUPPORTED = "response_item_unsupported"
    REQUEST_NOT_VISIBLE = "request_not_visible"
    REQUEST_MATCH_INVALID = "request_match_invalid"
    TURN_FLUSH_PENDING = "turn_flush_pending"
    TURN_MISMATCH = "turn_mismatch"
    SOURCE_IO_UNAVAILABLE = "source_io_unavailable"
    SOURCE_UNSAFE = "source_unsafe"
    INSPECTION_FAILED = "inspection_failed"
    THREAD_MISMATCH = "thread_mismatch"
    INPUT_CHANGED = "input_changed"
    LINEAGE_MISMATCH = "lineage_mismatch"
    LINEAGE_NOT_VISIBLE = "lineage_not_visible"
    TURN_ORDER_MISMATCH = "turn_order_mismatch"
    HISTORY_EFFECT_UNKNOWN = "history_effect_unknown"


@dataclass(frozen=True, slots=True)
class NativeContextObservation:
    """One content-free check; exception text and native paths stay private."""

    stage: NativeContextCheckStage
    source_stage: NativeContextSourceStage
    reason: NativeContextCheckReason
    exception_type: str | None = None
    error_code: int | None = None

    def __post_init__(self) -> None:
        if (type(self.stage) is not NativeContextCheckStage
                or type(self.source_stage) is not NativeContextSourceStage
                or type(self.reason) is not NativeContextCheckReason):
            raise TypeError("native context observation requires closed categories")
        if self.exception_type is not None and (
                type(self.exception_type) is not str or not self.exception_type
                or len(self.exception_type) > 80
                or not self.exception_type.isascii()
                or not self.exception_type.replace("_", "").isalnum()):
            raise ValueError("native context exception category is invalid")
        if self.error_code is not None and type(self.error_code) is not int:
            raise TypeError("native context error code must be an integer")

    def as_object(self) -> dict[str, object]:
        return {"stage": self.stage.value, "source_stage": self.source_stage.value,
                "reason": self.reason.value, "exception_type": self.exception_type,
                "error_code": self.error_code}

    @classmethod
    def from_object(cls, value: object) -> NativeContextObservation:
        if type(value) is not dict or set(value) != {
                "stage", "source_stage", "reason", "exception_type", "error_code"}:
            raise ValueError("invalid native context observation")
        return cls(NativeContextCheckStage(value["stage"]),
                   NativeContextSourceStage(value["source_stage"]),
                   NativeContextCheckReason(value["reason"]),
                   value["exception_type"], value["error_code"])


@dataclass(frozen=True, slots=True)
class NativeContextCheck:
    """Ordered observations of one physical call; never grants authority."""

    call_id: str | None
    prior_call_id: str | None
    observations: tuple[NativeContextObservation, ...]
    observed_thread_id: str | None = field(default=None, kw_only=True)
    normal_exit_code: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        for value in (self.call_id, self.prior_call_id, self.observed_thread_id):
            if value is not None and (type(value) is not str or not value
                                      or len(value) > 256
                                      or not value.isascii()
                                      or any(not (c.isalnum() or c in "_-.") for c in value)):
                raise ValueError("native context call identity is invalid")
        if type(self.observations) is not tuple or not 1 <= len(self.observations) <= 4 or any(
                type(item) is not NativeContextObservation for item in self.observations):
            raise ValueError("native context check requires actual ordered observations")
        if self.verified and (self.call_id is None or self.stage is NativeContextCheckStage.UNKNOWN):
            raise ValueError("verified native context requires a known call and stage")
        if self.normal_exit_code is not None and (type(self.normal_exit_code) is not int or self.normal_exit_code != 0):
            raise ValueError("normal native completion must have exit code zero")

    @property
    def stage(self) -> NativeContextCheckStage:
        return self.observations[-1].stage

    @property
    def reason(self) -> NativeContextCheckReason:
        return self.observations[-1].reason

    @property
    def verified(self) -> bool:
        return self.reason is NativeContextCheckReason.VERIFIED

    @property
    def outcome(self) -> str:
        if self.verified:
            return "verified"
        return "contradiction" if self.reason in {
            NativeContextCheckReason.SCOPE_MISMATCH, NativeContextCheckReason.CALL_MISMATCH,
            NativeContextCheckReason.THREAD_MISMATCH, NativeContextCheckReason.INPUT_CHANGED,
            NativeContextCheckReason.LINEAGE_MISMATCH, NativeContextCheckReason.TURN_ORDER_MISMATCH,
            NativeContextCheckReason.ANCHOR_MISMATCH, NativeContextCheckReason.CONCURRENT_TURN,
        } else "unavailable"

    @property
    def pre_submit_verified(self) -> bool:
        return any(item.stage is NativeContextCheckStage.PRE_SUBMIT
                   and item.reason is NativeContextCheckReason.VERIFIED for item in self.observations)

    def as_object(self) -> dict[str, object]:
        return {"call_id": self.call_id, "prior_call_id": self.prior_call_id,
                "outcome": self.outcome,
                "observed_thread_id": self.observed_thread_id, "normal_exit_code": self.normal_exit_code,
                "observations": [item.as_object() for item in self.observations]}

    @classmethod
    def from_object(cls, value: object) -> NativeContextCheck:
        if type(value) is not dict or set(value) != {
                "call_id", "prior_call_id", "outcome", "observations", "observed_thread_id", "normal_exit_code"} or type(value["observations"]) is not list:
            raise ValueError("invalid native context check")
        result = cls(value["call_id"], value["prior_call_id"],
                     tuple(NativeContextObservation.from_object(item) for item in value["observations"]),
                     observed_thread_id=value["observed_thread_id"], normal_exit_code=value["normal_exit_code"])
        if value["outcome"] != result.outcome:
            raise ValueError("native context outcome differs from final observation")
        return result


@dataclass(frozen=True, slots=True)
class ProviderContextWitness:
    """실제 전송과 한 native 이력의 연속성을 연결하는 불변 관측값이다."""

    thread_id: str
    rollout_id: str
    rollout_path: Path = field(repr=False)
    scope_digest: str
    file_identity: tuple[int, int]
    prefix_utf8_bytes: int
    prefix_sha256: str
    payload_sha256: str
    call_id: str
    turn_id: str
    settings_digest: str
    cli_version: str = "0.164.0"
    bootstrap_turn_id: str = field(kw_only=True)
    bootstrap_payload_sha256: str = field(kw_only=True)
    bootstrap_call_id: str = field(kw_only=True)
    input_lineage_sha256: str = field(kw_only=True)
    input_lineage_count: int = field(kw_only=True)

    def __post_init__(self) -> None:
        if (type(self.cli_version) is not str or len(self.cli_version) > 80 or type(self.rollout_path) is not Path
                and not isinstance(self.rollout_path, Path)):
            raise ValueError("native context witness has an invalid source")
        if not self.rollout_path.is_absolute():
            raise ValueError("native context witness needs an absolute owned path")
        for value in (self.thread_id, self.rollout_id, self.call_id, self.turn_id,
                      self.bootstrap_turn_id, self.bootstrap_call_id):
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError("native context witness has an invalid identity")
        for value in (self.scope_digest, self.prefix_sha256, self.payload_sha256, self.settings_digest,
                      self.bootstrap_payload_sha256, self.input_lineage_sha256):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("native context witness has an invalid digest")
        if (type(self.file_identity) is not tuple or len(self.file_identity) != 2
                or any(type(v) is not int or v < 0 for v in self.file_identity)
                or type(self.prefix_utf8_bytes) is not int or self.prefix_utf8_bytes < 1):
            raise ValueError("native context witness has invalid file facts")
        if type(self.input_lineage_count) is not int or self.input_lineage_count < 1:
            raise ValueError("native context witness has invalid logical lineage")

    def has_lineage_anchor(self, anchor: ProviderContextWitness) -> bool:
        """Compare logical lineage facts; file identity is observational only."""
        return (type(anchor) is ProviderContextWitness
                and (self.thread_id, self.scope_digest, self.bootstrap_turn_id,
                     self.bootstrap_payload_sha256, self.bootstrap_call_id)
                == (anchor.thread_id, anchor.scope_digest, anchor.bootstrap_turn_id,
                    anchor.bootstrap_payload_sha256, anchor.bootstrap_call_id)
                and self.input_lineage_count >= anchor.input_lineage_count
                and (self.input_lineage_count != anchor.input_lineage_count
                     or (self.input_lineage_sha256, self.turn_id, self.payload_sha256)
                     == (anchor.input_lineage_sha256, anchor.turn_id, anchor.payload_sha256)))


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
    stream_sink: ProviderStreamSink | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    execution_workspace: ProviderExecutionWorkspace | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    context_witness: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    context_anchor: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    context_call_id: str | None = field(default=None, kw_only=True)
    payload_json: bytes | None = field(init=False, repr=False)
    payload_accounting: ProviderPayloadAccounting | None = field(init=False)
    call_accounting: ProviderCallAccounting | None = field(init=False)

    def __post_init__(self) -> None:
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("provider context witness has an invalid type")
        if self.context_anchor is not None and type(self.context_anchor) is not ProviderContextWitness:
            raise TypeError("provider context anchor has an invalid type")
        if (self.context_witness is None) != (self.context_anchor is None):
            raise ValueError("delta needs both accepted and transport native witnesses")
        if self.context_witness is not None and (self.thread_id != self.context_witness.thread_id
                or not self.context_witness.has_lineage_anchor(self.context_anchor)):
            raise ValueError("delta witness logical lineage is inconsistent")
        if self.context_call_id is not None and (type(self.context_call_id) is not str or not self.context_call_id):
            raise ValueError("provider context call identity is invalid")
        if self.stream_sink is None:
            active_sink = active_provider_stream_sink()
            if active_sink is not None:
                object.__setattr__(self, "stream_sink", active_sink)
        if self.stream_sink is not None:
            call = getattr(self.stream_sink, "__call__", None)
            if (
                not callable(self.stream_sink)
                or inspect.iscoroutinefunction(self.stream_sink)
                or inspect.isasyncgenfunction(self.stream_sink)
                or inspect.iscoroutinefunction(call)
                or inspect.isasyncgenfunction(call)
            ):
                raise TypeError(
                    "provider invocation stream sink must be a synchronous callable or None"
                )
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
        if self.execution_workspace is not None and type(
            self.execution_workspace
        ) is not ProviderExecutionWorkspace:
            raise TypeError(
                "provider invocation execution workspace has an invalid type"
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
                schema_statistics=(provider_schema_statistics(schema) if self.context_call_id is not None else None),
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
    usage_observation: ProviderUsageObservation | None = field(default=None, kw_only=True, compare=False)
    elapsed_ns: int | None = field(default=None, kw_only=True, compare=False)
    actual_transport_utf8_bytes: int | None = field(default=None, compare=False)
    context_witness: ProviderContextWitness | None = field(default=None, kw_only=True, repr=False)
    native_context_check: NativeContextCheck | None = field(default=None, kw_only=True, repr=False)
    native_context_cause: BaseException | None = field(default=None, kw_only=True, repr=False, compare=False)
    response_diagnostic: ProviderResponseDiagnostic | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.native_context_check is not None and type(self.native_context_check) is not NativeContextCheck:
            raise TypeError("provider native context check is invalid")
        if self.native_context_cause is not None and not isinstance(self.native_context_cause, BaseException):
            raise TypeError("provider native context cause is invalid")
        if self.usage_observation is not None and (
            type(self.usage_observation) is not ProviderUsageObservation
            or self.usage_observation.raw != self.usage
        ):
            raise ValueError("provider observation must retain the exact raw counters")
        if self.elapsed_ns is not None and (type(self.elapsed_ns) is not int or self.elapsed_ns < 0):
            raise ValueError("provider elapsed nanoseconds must be nonnegative")
        if self.context_witness is not None and type(self.context_witness) is not ProviderContextWitness:
            raise TypeError("provider context witness has an invalid type")
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
    usage_observation: ProviderUsageObservation | None = field(default=None, kw_only=True, compare=False)
    elapsed_ns: int | None = field(default=None, kw_only=True, compare=False)

    def __post_init__(self) -> None:
        if self.usage_observation is not None and (
            type(self.usage_observation) is not ProviderUsageObservation
            or self.usage_observation.raw != self.usage
        ):
            raise ValueError("provider observation must retain the exact raw counters")
        if self.elapsed_ns is not None and (type(self.elapsed_ns) is not int or self.elapsed_ns < 0):
            raise ValueError("provider elapsed nanoseconds must be nonnegative")
        if (self.usage_observation is not None or self.elapsed_ns is not None) and self.state is not ProviderCallState.SUBMITTED:
            raise ValueError("provider observations require a submitted call")
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
        timed_out: bool = False,
        native_context_check: NativeContextCheck | None = None,
    ) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic
        if native_context_check is not None and type(native_context_check) is not NativeContextCheck:
            raise TypeError("provider native context check is invalid")
        self.native_context_check = native_context_check
        if authentication_failure is not None and (
            type(authentication_failure) is not ProviderAuthenticationRequiredError
        ):
            raise TypeError("authentication failure must be a typed provider failure")
        self.authentication_failure = authentication_failure
        self.taint_existing_thread = taint_existing_thread
        if type(timed_out) is not bool:
            raise TypeError("provider timeout fact must be a boolean")
        self.timed_out = timed_out
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
    usage_observation: ProviderUsageObservation | None = None,
    elapsed_ns: int | None = None,
) -> None:
    """Attach validated, content-free call facts without replacing a failure."""

    if usage is not None and usage_observation is None:
        usage_observation = ProviderUsageObservation(usage, ProviderUsageScope.UNKNOWN, "provider.failure.reported_usage")
    accounting = ProviderFailureAccounting(
        state=state,
        actual_transport_utf8_bytes=actual_transport_utf8_bytes,
        usage=usage,
        usage_observation=usage_observation,
        elapsed_ns=elapsed_ns,
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
