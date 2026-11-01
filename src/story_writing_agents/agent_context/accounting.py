"""Exact, content-free accounting for provider-neutral model input."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import TypeAlias

JsonPathSegment: TypeAlias = str | int
JsonPath: TypeAlias = tuple[JsonPathSegment, ...]


class ContextComponentKind(StrEnum):
    """Stable attribution categories for serialized provider model input."""

    BINDING_STRUCTURE = "binding_structure"
    ROLE_SYSTEM_PROMPT = "role_system_prompt"
    PERSONA = "persona"
    RESPONSE_LANGUAGE = "response_language"
    CORE_DOCUMENT = "core_document"
    PROVIDER_CONSTRAINTS = "provider_constraints"
    OUTPUT_SCHEMA = "output_schema"
    CONTROL = "control"
    CURRENT_INPUT = "current_input"
    CANDIDATE = "candidate"
    DEBATE_MESSAGES = "debate_messages"
    CONVERSATION_CONTEXT = "conversation_context"
    AGENT_STATE = "agent_state"
    RETRIEVED_OBSERVATION = "retrieved_observation"
    RAG_CONTEXT = "rag_context"
    MANDATORY_REFERENCE_DESCRIPTOR = "mandatory_reference_descriptor"
    MANDATORY_REFERENCE_CONTENT = "mandatory_reference_content"


class ProviderCallKind(StrEnum):
    """Physical provider-call roles used by context accounting."""

    UNCLASSIFIED = "unclassified"
    DEBATE = "debate"
    CONVERSATION = "conversation"
    CONVERSATION_RETRIEVAL_INITIAL = "conversation_retrieval_initial"
    CONVERSATION_RETRIEVAL_CONTINUATION = "conversation_retrieval_continuation"
    COLLABORATION_INITIAL = "collaboration_initial"
    COLLABORATION_CONTINUATION = "collaboration_continuation"
    WRITING_ARTIFACT_REFERENCE_READ = "writing_artifact_reference_read"


class ProviderCallState(StrEnum):
    """Whether exact model input was only prepared or actually submitted."""

    PREPARED = "prepared"
    SUBMITTED = "submitted"


class ProviderJsonEncodingError(ValueError):
    """Report content-free canonical provider-input encoding failure."""


def _nonnegative_integer(name: str, value: object) -> int:
    """Return one exact nonnegative integer or fail without exposing content."""

    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    assert isinstance(value, int)
    if value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _optional_nonnegative_integer(name: str, value: object) -> int | None:
    """Validate an optional exact provider-reported counter."""

    if value is None:
        return None
    return _nonnegative_integer(name, value)


@dataclass(frozen=True, slots=True)
class ContextComponentBytes:
    """Exact serialized bytes for one content-free component category."""

    kind: ContextComponentKind
    serialized_utf8_bytes: int
    raw_mandatory_reference_utf8_bytes: int = 0
    identity: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ContextComponentKind:
            raise TypeError("context component kind must be a ContextComponentKind")
        serialized = _nonnegative_integer(
            "serialized component UTF-8 bytes",
            self.serialized_utf8_bytes,
        )
        raw_reference = _nonnegative_integer(
            "raw mandatory-reference UTF-8 bytes",
            self.raw_mandatory_reference_utf8_bytes,
        )
        if (
            self.kind is not ContextComponentKind.MANDATORY_REFERENCE_CONTENT
            and raw_reference != 0
        ):
            raise ValueError(
                "raw mandatory-reference bytes require the mandatory-reference "
                "component"
            )
        if raw_reference > serialized:
            raise ValueError(
                "raw mandatory-reference bytes cannot exceed serialized component bytes"
            )
        if self.identity is not None:
            if type(self.identity) is not str or not self.identity:
                raise TypeError("context component identity must be nonempty text")
            try:
                encoded_identity = self.identity.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "context component identity must be valid UTF-8"
                ) from exc
            if len(encoded_identity) > 1024:
                raise ValueError(
                    "context component identity must not exceed 1024 UTF-8 bytes"
                )
            if any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                or character in {"\u2028", "\u2029"}
                for character in self.identity
            ):
                raise ValueError(
                    "context component identity must not contain control characters"
                )
        if self.kind is ContextComponentKind.CORE_DOCUMENT and self.identity is None:
            raise ValueError("core-document components require a source identity")


@dataclass(frozen=True, slots=True)
class ProviderPayloadAccounting:
    """Content-free exact byte accounting for one schema and payload pair."""

    call_kind: ProviderCallKind
    schema_utf8_bytes: int
    payload_utf8_bytes: int
    components: tuple[ContextComponentBytes, ...]

    def __post_init__(self) -> None:
        if type(self.call_kind) is not ProviderCallKind:
            raise TypeError("provider call kind must be a ProviderCallKind")
        schema_bytes = _nonnegative_integer(
            "provider schema UTF-8 bytes",
            self.schema_utf8_bytes,
        )
        payload_bytes = _nonnegative_integer(
            "provider payload UTF-8 bytes",
            self.payload_utf8_bytes,
        )
        if type(self.components) is not tuple or any(
            type(component) is not ContextComponentBytes
            for component in self.components
        ):
            raise TypeError(
                "provider payload components must be ContextComponentBytes values"
            )
        keys = tuple(
            (component.kind, component.identity) for component in self.components
        )
        if len(set(keys)) != len(keys):
            raise ValueError("provider payload component identities must be unique")
        ranks = tuple(list(ContextComponentKind).index(kind) for kind, _ in keys)
        if ranks != tuple(sorted(ranks)):
            raise ValueError("provider payload components must use canonical order")
        if sum(component.serialized_utf8_bytes for component in self.components) != (
            schema_bytes + payload_bytes
        ):
            raise ValueError(
                "provider payload component bytes must partition serialized input"
            )

    @property
    def serialized_input_utf8_bytes(self) -> int:
        """Return exact canonical schema-plus-payload UTF-8 bytes."""

        return self.schema_utf8_bytes + self.payload_utf8_bytes

    @property
    def raw_mandatory_reference_utf8_bytes(self) -> int:
        """Return the non-additive raw mandatory-reference byte subset."""

        return sum(
            component.raw_mandatory_reference_utf8_bytes
            for component in self.components
        )


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Validated exact token counters reported by one provider call."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None

    def __post_init__(self) -> None:
        values = (
            _optional_nonnegative_integer("provider input tokens", self.input_tokens),
            _optional_nonnegative_integer(
                "provider output tokens",
                self.output_tokens,
            ),
            _optional_nonnegative_integer(
                "provider cached input tokens",
                self.cached_input_tokens,
            ),
        )
        if all(value is None for value in values):
            raise ValueError("provider usage must contain at least one exact counter")


class ProviderUsageScope(StrEnum):
    """The observed counter scope, independent of model or call-kind guesses."""

    CALL = "call"
    SESSION_CUMULATIVE = "session_cumulative"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderUsageObservation:
    """Raw counters and content-free provenance; unknown never means zero."""

    raw: ProviderUsage
    scope: ProviderUsageScope
    source: str
    unit: str = "tokens"
    session_ref: str | None = None
    prefix_ref: str | None = None
    previous_prefix_ref: str | None = None
    session_start: bool = False

    def __post_init__(self) -> None:
        if type(self.raw) is not ProviderUsage or type(self.scope) is not ProviderUsageScope:
            raise TypeError("usage observation requires typed counters and scope")
        if type(self.session_start) is not bool or (self.session_start and self.previous_prefix_ref is not None):
            raise ValueError("usage session start conflicts with a predecessor")
        for value in (self.source, self.unit):
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError("usage observation provenance is invalid")
        for value in (self.session_ref, self.prefix_ref, self.previous_prefix_ref):
            if value is not None and (type(value) is not str or not value or len(value) > 256):
                raise ValueError("usage observation reference is invalid")
        if self.scope is ProviderUsageScope.SESSION_CUMULATIVE and (
            self.session_ref is None or self.prefix_ref is None
        ):
            raise ValueError("cumulative usage requires an observed session and prefix")


def normalized_provider_usage(
    observation: ProviderUsageObservation,
    previous: ProviderUsageObservation | None = None,
) -> ProviderUsage | None:
    """Attribute only explicit call counters or a proven cumulative prefix delta.

    A new, observed cumulative session has no predecessor. A resume without its
    exact predecessor remains unavailable, including resets and source changes.
    """

    if type(observation) is not ProviderUsageObservation:
        raise TypeError("usage normalization requires an observation")
    if observation.unit != "tokens":
        return None
    if observation.scope is ProviderUsageScope.CALL:
        return observation.raw
    if observation.scope is not ProviderUsageScope.SESSION_CUMULATIVE:
        return None
    if observation.previous_prefix_ref is None:
        return observation.raw if observation.session_start else None
    if previous is None or (
        previous.scope is not ProviderUsageScope.SESSION_CUMULATIVE
        or previous.session_ref != observation.session_ref
        or previous.prefix_ref != observation.previous_prefix_ref
        or previous.source != observation.source
        or previous.unit != observation.unit
    ):
        return None
    values: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens", "cached_input_tokens"):
        current, prior = getattr(observation.raw, name), getattr(previous.raw, name)
        if current is not None and prior is not None and current < prior:
            return None
        values[name] = None if current is None or prior is None else current - prior
    return None if all(value is None for value in values.values()) else ProviderUsage(**values)


@dataclass(frozen=True, slots=True)
class ProviderSchemaStatistics:
    """Descriptive native-schema shape, never a schema validation gate."""

    outer_alternatives: int
    any_of_paths: tuple[str, ...]
    definition_count: int
    enum_value_count: int

    def __post_init__(self) -> None:
        for name in ("outer_alternatives", "definition_count", "enum_value_count"):
            _nonnegative_integer(name, getattr(self, name))
        if type(self.any_of_paths) is not tuple or any(type(path) is not str for path in self.any_of_paths):
            raise TypeError("schema locations must be an immutable tuple")


def provider_schema_statistics(schema: Mapping[str, object]) -> ProviderSchemaStatistics:
    """Count final schema-node locations, without a validation or syntax gate."""

    def pointer(path, key):
        return path + "/" + str(key).replace("~", "~0").replace("/", "~1")

    paths: list[str] = []
    definitions = enums = 0
    pending: list[tuple[object, str]] = [(schema, "$")]
    while pending:
        node, path = pending.pop()
        if not isinstance(node, Mapping):
            continue
        enum = node.get("enum")
        if type(enum) in {list, tuple}:
            enums += len(enum)
        for key in ("$defs", "definitions", "properties", "patternProperties"):
            children = node.get(key)
            if isinstance(children, Mapping):
                if key in {"$defs", "definitions"}:
                    definitions += len(children)
                pending.extend((value, pointer(pointer(path, key), name))
                               for name, value in children.items())
        for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            children = node.get(key)
            if type(children) in {list, tuple}:
                if key == "anyOf":
                    paths.append(pointer(path, key))
                pending.extend((value, pointer(pointer(path, key), index))
                               for index, value in enumerate(children))
        for key in ("items", "additionalProperties", "unevaluatedProperties", "contains", "not", "if", "then", "else", "propertyNames"):
            child = node.get(key)
            if isinstance(child, Mapping):
                pending.append((child, pointer(path, key)))
    def resolved(node):
        seen = set()
        while isinstance(node, Mapping) and "$ref" in node:
            reference = node["$ref"]
            if type(reference) is not str or not reference.startswith("#/") or reference in seen:
                return {}
            seen.add(reference)
            node = schema
            for part in reference[2:].split("/"):
                node = node.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(node, Mapping) else {}
        return node
    root = resolved(schema)
    properties = root.get("properties", {}) if isinstance(root, Mapping) else {}
    action = resolved(properties.get("agent_action", {})) if isinstance(properties, Mapping) else {}
    alternatives = action.get("anyOf") if isinstance(action, Mapping) else None
    return ProviderSchemaStatistics(
        len(alternatives) if type(alternatives) in {list, tuple} else 1,
        tuple(sorted(paths)), definitions, enums,
    )


@dataclass(frozen=True, slots=True)
class ProviderCallAccounting:
    """One content-free physical model-input measurement."""

    payload: ProviderPayloadAccounting
    binding_components: tuple[ContextComponentBytes, ...] = ()
    state: ProviderCallState = ProviderCallState.PREPARED
    actual_transport_utf8_bytes: int | None = None
    usage: ProviderUsage | None = None
    usage_observation: ProviderUsageObservation | None = field(default=None, kw_only=True, compare=False)
    elapsed_ns: int | None = field(default=None, kw_only=True, compare=False)
    schema_statistics: ProviderSchemaStatistics | None = field(default=None, kw_only=True, compare=False)

    def __post_init__(self) -> None:
        if type(self.payload) is not ProviderPayloadAccounting:
            raise TypeError("provider call payload must be ProviderPayloadAccounting")
        if type(self.binding_components) is not tuple or any(
            type(component) is not ContextComponentBytes
            for component in self.binding_components
        ):
            raise TypeError(
                "provider binding components must be ContextComponentBytes values"
            )
        binding_kinds = {
            ContextComponentKind.BINDING_STRUCTURE,
            ContextComponentKind.ROLE_SYSTEM_PROMPT,
            ContextComponentKind.PERSONA,
            ContextComponentKind.RESPONSE_LANGUAGE,
            ContextComponentKind.CORE_DOCUMENT,
            ContextComponentKind.PROVIDER_CONSTRAINTS,
        }
        if any(
            component.kind not in binding_kinds for component in self.binding_components
        ):
            raise ValueError(
                "provider binding components must use binding component kinds"
            )
        binding_keys = tuple(
            (component.kind, component.identity)
            for component in self.binding_components
        )
        if len(set(binding_keys)) != len(binding_keys):
            raise ValueError("provider binding component identities must be unique")
        if type(self.state) is not ProviderCallState:
            raise TypeError("provider call state must be a ProviderCallState")
        if self.actual_transport_utf8_bytes is not None:
            _nonnegative_integer(
                "actual provider transport UTF-8 bytes",
                self.actual_transport_utf8_bytes,
            )
            if self.state is not ProviderCallState.SUBMITTED:
                raise ValueError(
                    "actual provider transport bytes require a submitted call"
                )
        if self.usage is not None and type(self.usage) is not ProviderUsage:
            raise TypeError("provider call usage must be ProviderUsage or None")
        if self.usage is not None and self.state is not ProviderCallState.SUBMITTED:
            raise ValueError("provider usage requires a submitted call")
        if self.usage_observation is not None:
            if type(self.usage_observation) is not ProviderUsageObservation:
                raise TypeError("provider usage observation has an invalid type")
            if self.state is not ProviderCallState.SUBMITTED or self.usage_observation.raw != self.usage:
                raise ValueError("usage observation must describe the submitted raw counters")
        if self.elapsed_ns is not None:
            _nonnegative_integer("provider elapsed nanoseconds", self.elapsed_ns)
            if self.state is not ProviderCallState.SUBMITTED:
                raise ValueError("provider elapsed time requires submitted call")
        if self.schema_statistics is not None and type(self.schema_statistics) is not ProviderSchemaStatistics:
            raise TypeError("provider schema statistics have an invalid type")

    @property
    def binding_instruction_utf8_bytes(self) -> int:
        """Return exact staged binding-instruction bytes."""

        return sum(
            component.serialized_utf8_bytes for component in self.binding_components
        )

    @property
    def gross_model_input_utf8_bytes(self) -> int:
        """Return binding, schema, and prompt bytes prepared for the model."""

        return (
            self.binding_instruction_utf8_bytes
            + self.payload.serialized_input_utf8_bytes
        )

    @property
    def prepared_model_input_utf8_bytes(self) -> int:
        """Return exact gross bytes regardless of later submission outcome."""

        return self.gross_model_input_utf8_bytes

    @property
    def non_reference_model_input_utf8_bytes(self) -> int:
        """Return gross bytes minus only the raw mandatory-reference subset."""

        return (
            self.gross_model_input_utf8_bytes
            - self.payload.raw_mandatory_reference_utf8_bytes
        )

    @property
    def submitted_model_input_utf8_bytes(self) -> int | None:
        """Return gross bytes only when provider submission occurred."""

        if self.state is ProviderCallState.PREPARED:
            return None
        return self.gross_model_input_utf8_bytes


def provider_call_accounting_to_object(value: ProviderCallAccounting) -> dict[str, object]:
    """Encode exact typed accounting at ledger and conversation boundaries."""

    if type(value) is not ProviderCallAccounting:
        raise TypeError("accounting codec requires ProviderCallAccounting")
    result = asdict(value)
    for name in ("usage_observation", "elapsed_ns", "schema_statistics"):
        if result[name] is None:
            del result[name]
    return result


def provider_call_accounting_from_object(value: object) -> ProviderCallAccounting:
    """Decode the closed content-free accounting record without runtime authority."""

    def record(raw: object, required: set[str], optional: set[str] = frozenset()) -> dict:
        if type(raw) is not dict or not required <= set(raw) or set(raw) - required - optional:
            raise ValueError("invalid provider accounting fields")
        return raw

    def component(raw: object) -> ContextComponentBytes:
        item = record(raw, {"identity", "kind", "serialized_utf8_bytes", "raw_mandatory_reference_utf8_bytes"})
        return ContextComponentBytes(**{**item, "kind": ContextComponentKind(item["kind"])})

    def usage(raw: object) -> ProviderUsage | None:
        if raw is None:
            return None
        item = record(raw, {"input_tokens", "output_tokens", "cached_input_tokens"})
        return ProviderUsage(**item)

    item = record(value, {"payload", "binding_components", "state", "actual_transport_utf8_bytes", "usage"},
                  {"usage_observation", "elapsed_ns", "schema_statistics"})
    payload = record(item["payload"], {"call_kind", "schema_utf8_bytes", "payload_utf8_bytes", "components"})
    if type(payload["components"]) not in {list, tuple} or type(item["binding_components"]) not in {list, tuple}:
        raise ValueError("invalid provider accounting components")
    observation = None
    if "usage_observation" in item:
        raw = record(item["usage_observation"], {"raw", "scope", "source", "unit", "session_ref", "prefix_ref", "previous_prefix_ref", "session_start"})
        observation = ProviderUsageObservation(**{**raw, "raw": usage(raw["raw"]), "scope": ProviderUsageScope(raw["scope"])})
    shape = None
    if "schema_statistics" in item:
        raw = record(item["schema_statistics"], {"outer_alternatives", "any_of_paths", "definition_count", "enum_value_count"})
        if type(raw["any_of_paths"]) not in {list, tuple}:
            raise ValueError("invalid schema statistics paths")
        shape = ProviderSchemaStatistics(**{**raw, "any_of_paths": tuple(raw["any_of_paths"])})
    return ProviderCallAccounting(
        ProviderPayloadAccounting(ProviderCallKind(payload["call_kind"]), payload["schema_utf8_bytes"],
                                  payload["payload_utf8_bytes"], tuple(component(raw) for raw in payload["components"])),
        tuple(component(raw) for raw in item["binding_components"]), ProviderCallState(item["state"]),
        item["actual_transport_utf8_bytes"], usage(item["usage"]),
        usage_observation=observation, elapsed_ns=item.get("elapsed_ns"), schema_statistics=shape,
    )


@dataclass(slots=True)
class _EncodingObservation:
    """Mutable call-local counters hidden behind the pure accounting API."""

    component_bytes: dict[ContextComponentKind, int]
    raw_reference_bytes: int = 0

    def add(self, kind: ContextComponentKind, content: bytes) -> bytes:
        """Attribute one encoded fragment and return it for composition."""

        self.component_bytes[kind] = self.component_bytes.get(kind, 0) + len(content)
        return content


def _validated_path(path: object) -> JsonPath:
    """Return one unambiguous payload path without accepting booleans."""

    if type(path) is not tuple or not path:
        raise TypeError("provider component paths must be nonempty tuples")
    assert isinstance(path, tuple)
    for segment in path:
        if type(segment) is str:
            if not segment:
                raise ValueError("provider component path keys must be nonempty")
            continue
        if type(segment) is int:
            if segment < 0:
                raise ValueError("provider component path indexes must be nonnegative")
            continue
        raise TypeError("provider component path segments must be strings or integers")
    return path


def _validated_component_paths(
    component_paths: Mapping[JsonPath, ContextComponentKind],
) -> dict[JsonPath, ContextComponentKind]:
    """Copy and validate exact component attribution paths."""

    if not isinstance(component_paths, Mapping):
        raise TypeError("provider component paths must be a mapping")
    result: dict[JsonPath, ContextComponentKind] = {}
    for raw_path, kind in component_paths.items():
        path = _validated_path(raw_path)
        if type(kind) is not ContextComponentKind:
            raise TypeError("provider component path values must be component kinds")
        result[path] = kind
    return result


def _validated_reference_paths(
    reference_paths: tuple[JsonPath, ...],
    component_paths: Mapping[JsonPath, ContextComponentKind],
) -> frozenset[JsonPath]:
    """Validate paths whose raw UTF-8 source bytes form the excluded subset."""

    if type(reference_paths) is not tuple:
        raise TypeError("mandatory-reference paths must be a tuple")
    paths = tuple(_validated_path(path) for path in reference_paths)
    if len(set(paths)) != len(paths):
        raise ValueError("mandatory-reference paths must be unique")
    for path in paths:
        if (
            component_paths.get(path)
            is not ContextComponentKind.MANDATORY_REFERENCE_CONTENT
        ):
            raise ValueError(
                "mandatory-reference paths must select mandatory-reference components"
            )
    return frozenset(paths)


def _scalar_json(value: object) -> bytes:
    """Encode one scalar through the canonical strict JSON configuration."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return text.encode("utf-8", errors="strict")
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProviderJsonEncodingError(
            "provider input is not canonical strict UTF-8 JSON"
        ) from exc


def _encode_json(
    value: object,
    *,
    path: JsonPath,
    inherited_kind: ContextComponentKind,
    component_paths: Mapping[JsonPath, ContextComponentKind],
    reference_paths: frozenset[JsonPath],
    observed_paths: set[JsonPath],
    active_containers: set[int],
    observation: _EncodingObservation | None,
) -> bytes:
    """Encode one JSON value while attributing every emitted byte exactly once."""

    kind = component_paths.get(path, inherited_kind)
    if path in component_paths:
        observed_paths.add(path)
    if path in reference_paths:
        if type(value) is not str:
            raise TypeError("mandatory-reference content must be text")
        try:
            raw_reference = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("mandatory-reference content must be valid UTF-8") from exc
        assert observation is not None
        observation.raw_reference_bytes += len(raw_reference)

    def attributed(content: bytes, owner: ContextComponentKind = kind) -> bytes:
        if observation is None:
            return content
        return observation.add(owner, content)

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active_containers:
            raise ProviderJsonEncodingError(
                "provider input must not contain recursive containers"
            )
        active_containers.add(identity)
        try:
            parts = [attributed(b"{")]
            for index, (key, item) in enumerate(value.items()):
                if type(key) is not str:
                    raise ProviderJsonEncodingError(
                        "provider JSON object keys must be strings"
                    )
                if index:
                    parts.append(attributed(b","))
                parts.append(attributed(_scalar_json(key)))
                parts.append(attributed(b":"))
                parts.append(
                    _encode_json(
                        item,
                        path=path + (key,),
                        inherited_kind=kind,
                        component_paths=component_paths,
                        reference_paths=reference_paths,
                        observed_paths=observed_paths,
                        active_containers=active_containers,
                        observation=observation,
                    )
                )
            parts.append(attributed(b"}"))
            return b"".join(parts)
        finally:
            active_containers.remove(identity)

    if type(value) in {list, tuple}:
        identity = id(value)
        if identity in active_containers:
            raise ProviderJsonEncodingError(
                "provider input must not contain recursive containers"
            )
        active_containers.add(identity)
        try:
            parts = [attributed(b"[")]
            assert isinstance(value, (list, tuple))
            for index, item in enumerate(value):
                if index:
                    parts.append(attributed(b","))
                parts.append(
                    _encode_json(
                        item,
                        path=path + (index,),
                        inherited_kind=kind,
                        component_paths=component_paths,
                        reference_paths=reference_paths,
                        observed_paths=observed_paths,
                        active_containers=active_containers,
                        observation=observation,
                    )
                )
            parts.append(attributed(b"]"))
            return b"".join(parts)
        finally:
            active_containers.remove(identity)

    return attributed(_scalar_json(value))


def canonical_provider_json(value: object) -> bytes:
    """Return compact insertion-ordered strict UTF-8 JSON bytes.

    The function performs no I/O and never includes the rejected value in its
    error. Native provider envelopes remain adapter-owned and are not part of
    this provider-neutral model-input boundary.
    """

    return _encode_json(
        value,
        path=(),
        inherited_kind=ContextComponentKind.CONTROL,
        component_paths={},
        reference_paths=frozenset(),
        observed_paths=set(),
        active_containers=set(),
        observation=None,
    )


def account_provider_payload(
    schema: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    call_kind: ProviderCallKind = ProviderCallKind.UNCLASSIFIED,
    component_paths: Mapping[JsonPath, ContextComponentKind] | None = None,
    mandatory_reference_paths: tuple[JsonPath, ...] = (),
) -> tuple[bytes, ProviderPayloadAccounting]:
    """Encode and account for one exact provider schema/payload pair.

    Component paths are relative to the payload. A path changes attribution
    for that value and its descendants; object keys and structural separators
    stay with the containing component. Schema bytes always belong to
    ``OUTPUT_SCHEMA``. Raw mandatory-reference bytes are measured only for
    explicitly selected text paths and remain a non-additive subset.
    """

    if not isinstance(schema, Mapping) or not isinstance(payload, Mapping):
        raise TypeError("provider schema and payload must be mappings")
    if type(call_kind) is not ProviderCallKind:
        raise TypeError("provider call kind must be a ProviderCallKind")
    paths = _validated_component_paths(
        {} if component_paths is None else component_paths
    )
    references = _validated_reference_paths(mandatory_reference_paths, paths)
    observation = _EncodingObservation({})
    schema_json = _encode_json(
        schema,
        path=(),
        inherited_kind=ContextComponentKind.OUTPUT_SCHEMA,
        component_paths={},
        reference_paths=frozenset(),
        observed_paths=set(),
        active_containers=set(),
        observation=observation,
    )
    observed_paths: set[JsonPath] = set()
    payload_json = _encode_json(
        payload,
        path=(),
        inherited_kind=ContextComponentKind.CONTROL,
        component_paths=paths,
        reference_paths=references,
        observed_paths=observed_paths,
        active_containers=set(),
        observation=observation,
    )
    if observed_paths != set(paths):
        raise ValueError("provider component paths must identify payload values")
    components = tuple(
        ContextComponentBytes(
            kind=kind,
            serialized_utf8_bytes=observation.component_bytes[kind],
            raw_mandatory_reference_utf8_bytes=(
                observation.raw_reference_bytes
                if kind is ContextComponentKind.MANDATORY_REFERENCE_CONTENT
                else 0
            ),
        )
        for kind in ContextComponentKind
        if observation.component_bytes.get(kind, 0) != 0
    )
    return payload_json, ProviderPayloadAccounting(
        call_kind=call_kind,
        schema_utf8_bytes=len(schema_json),
        payload_utf8_bytes=len(payload_json),
        components=components,
    )
