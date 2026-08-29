"""Lazy optional JSONL-viewer adapter for immutable agent exchanges."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from ..agent_exchanges import AgentExchangeScopeKind
from ..interactive_terminal._external_view import (
    ExternalViewerHost,
    ExternalViewOperation,
)


_VIEWER_PUBLIC_NAMES = ("ViewerHost", "ViewerSpec", "view_jsonl")
_REQUIRED_PARAMETER = object()
_VIEWER_SPEC_PARAMETERS = (
    ("session_id", _REQUIRED_PARAMETER),
    ("conversation_id", _REQUIRED_PARAMETER),
    ("agent_id", _REQUIRED_PARAMETER),
    ("searchable_fields", _REQUIRED_PARAMETER),
    ("date_time_field", "timestamp"),
    ("request_type_field", "request_type"),
    ("content_field", "content"),
    ("title", "JSONL Viewer"),
    ("conversation_label", "Conversation"),
    ("conversation_subject", None),
)
_VIEWER_HOST_METHODS = {
    "terminal_size": ("self",),
    "color_enabled": ("self",),
    "present": ("self", "frame"),
    "read_event": ("self",),
    "close_view": ("self",),
}
_VIEW_JSONL_PARAMETERS = ("source", "spec", "host")
_SEARCHABLE_FIELDS = (
    "schema_version",
    "workspace_sequence",
    "sequence",
    "timestamp",
    "request_type",
    "session_id",
    "scope_kind",
    "scope_id",
    "agent_id",
    "provider",
    "exchange_id",
    "content",
    "content_full_utf8_bytes",
    "content_retained_utf8_bytes",
    "content_full_characters",
    "content_retained_characters",
    "content_truncated",
    "content_sha256",
)
_FAILURE_MESSAGES = {
    "installation_required": (
        "Optional agent exchange viewer is unavailable. Install Story with "
        "the 'viewer' extra and retry /exchanges."
    ),
    "incompatible": "Optional agent exchange viewer is incompatible with Story.",
    "view_failed": "Agent exchange viewer failed.",
}


class AgentExchangeViewerError(RuntimeError):
    """Carry one fixed optional-viewer failure without external diagnostics."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _FAILURE_MESSAGES:
            raise ValueError("unsupported agent exchange viewer error code")
        self.code = code
        super().__init__(_FAILURE_MESSAGES[code])


def _has_exact_signature(
    value: object,
    parameters: tuple[tuple[str, object], ...],
) -> bool:
    """Return whether one callable exposes the pinned named call shape."""

    if not callable(value):
        return False
    try:
        signature = inspect.signature(value, follow_wrapped=False)
    except (TypeError, ValueError):
        return False
    observed = tuple(signature.parameters.values())
    if len(observed) != len(parameters):
        return False
    for parameter, (name, default) in zip(observed, parameters, strict=True):
        if (
            parameter.name != name
            or parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
        ):
            return False
        if default is _REQUIRED_PARAMETER:
            if parameter.default is not inspect.Parameter.empty:
                return False
        elif parameter.default != default:
            return False
    return True


def _viewer_host_shape_is_supported(viewer_host: object) -> bool:
    """Return whether the external protocol declares the exact borrowed host API."""

    if not isinstance(viewer_host, type):
        return False
    for method_name, parameter_names in _VIEWER_HOST_METHODS.items():
        method = inspect.getattr_static(viewer_host, method_name, None)
        parameters = tuple(
            (parameter_name, _REQUIRED_PARAMETER) for parameter_name in parameter_names
        )
        if not callable(method) or not _has_exact_signature(method, parameters):
            return False
    return True


def _viewer_spec_projection_is_supported(
    spec: object,
    expected: tuple[tuple[str, object], ...],
) -> bool:
    """Confirm the constructed immutable header and field projection before open."""

    for attribute, expected_value in expected:
        try:
            observed = getattr(spec, attribute)
        except Exception:
            return False
        if type(expected_value) is tuple:
            if type(observed) is not tuple or observed != expected_value:
                return False
        elif expected_value is None:
            if observed is not None:
                return False
        elif type(observed) is not str or observed != expected_value:
            return False
    try:
        primary_fields = getattr(spec, "primary_fields")
    except Exception:
        return False
    return type(primary_fields) is tuple and primary_fields == (
        "timestamp",
        "request_type",
        "content",
    )


def _load_viewer_api() -> tuple[Callable[..., object], Callable[..., object]]:
    """Load and validate only the pinned viewer's public three-name facade."""

    try:
        module = importlib.import_module("jsonl_viewer")
    except ModuleNotFoundError as exc:
        code = "installation_required" if exc.name == "jsonl_viewer" else "incompatible"
        raise AgentExchangeViewerError(code) from exc
    except ImportError as exc:
        raise AgentExchangeViewerError("incompatible") from exc

    exported = getattr(module, "__all__", None)
    if type(exported) is not list or tuple(exported) != _VIEWER_PUBLIC_NAMES:
        raise AgentExchangeViewerError("incompatible")
    viewer_spec = getattr(module, "ViewerSpec", None)
    viewer_host = getattr(module, "ViewerHost", None)
    view_jsonl = getattr(module, "view_jsonl", None)
    if (
        not isinstance(viewer_spec, type)
        or not _has_exact_signature(viewer_spec, _VIEWER_SPEC_PARAMETERS)
        or not _viewer_host_shape_is_supported(viewer_host)
        or not callable(view_jsonl)
        or not _has_exact_signature(
            view_jsonl,
            tuple(
                (parameter_name, _REQUIRED_PARAMETER)
                for parameter_name in _VIEW_JSONL_PARAMETERS
            ),
        )
    ):
        raise AgentExchangeViewerError("incompatible")
    return viewer_spec, view_jsonl


def prepare_agent_exchange_view(
    source: bytes,
    *,
    session_id: str,
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    scope_subject: str,
    agent_id: str,
) -> ExternalViewOperation:
    """Prepare one immutable view before the terminal lifecycle is acquired."""

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if type(scope_kind) is not AgentExchangeScopeKind:
        raise TypeError("scope_kind must be an AgentExchangeScopeKind")
    viewer_spec_type, view_jsonl = _load_viewer_api()
    expected_projection = (
        ("session_id", session_id),
        ("conversation_id", scope_id),
        ("agent_id", agent_id),
        ("searchable_fields", _SEARCHABLE_FIELDS),
        ("date_time_field", "timestamp"),
        ("request_type_field", "request_type"),
        ("content_field", "content"),
        ("title", "Agent Exchanges"),
        ("conversation_label", scope_kind.value.title()),
        ("conversation_subject", scope_subject),
    )
    try:
        spec = viewer_spec_type(
            session_id=session_id,
            conversation_id=scope_id,
            agent_id=agent_id,
            searchable_fields=_SEARCHABLE_FIELDS,
            date_time_field="timestamp",
            request_type_field="request_type",
            content_field="content",
            title="Agent Exchanges",
            conversation_label=scope_kind.value.title(),
            conversation_subject=scope_subject,
        )
    except Exception as exc:
        raise AgentExchangeViewerError("incompatible") from exc
    if not _viewer_spec_projection_is_supported(spec, expected_projection):
        raise AgentExchangeViewerError("incompatible")

    def view(host: ExternalViewerHost) -> None:
        if not isinstance(host, ExternalViewerHost):
            raise TypeError("host must implement ExternalViewerHost")
        try:
            view_jsonl(source, spec, host)
        except AgentExchangeViewerError:
            raise
        except Exception as exc:
            raise AgentExchangeViewerError("view_failed") from exc

    return view


__all__ = ["AgentExchangeViewerError", "prepare_agent_exchange_view"]
