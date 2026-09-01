"""Exact, content-free accounting for provider-neutral model input."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ProviderCallAccounting:
    """One content-free physical model-input measurement."""

    payload: ProviderPayloadAccounting
    binding_components: tuple[ContextComponentBytes, ...] = ()
    state: ProviderCallState = ProviderCallState.PREPARED
    actual_transport_utf8_bytes: int | None = None
    usage: ProviderUsage | None = None

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
