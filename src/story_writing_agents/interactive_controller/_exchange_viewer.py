"""Lazy required JSONL-viewer adapter for immutable agent exchanges."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from ..agent_exchanges import AgentExchangeScopeKind
from ..interactive_terminal._external_view import (
    ExternalOwnedViewOperation,
    ExternalTerminalIO,
)


_VIEWER_PUBLIC_NAMES = ("ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl")
_REQUIRED_PARAMETER = object()
_VIEWER_SPEC_PARAMETERS = (
    ("session_id", _REQUIRED_PARAMETER),
    ("conversation_id", _REQUIRED_PARAMETER),
    ("agent_id", _REQUIRED_PARAMETER),
    ("date_time_field", "timestamp"),
    ("request_type_field", "request_type"),
    ("content_field", "content"),
    ("title", "JSONL Viewer"),
    ("conversation_label", "Conversation"),
    ("conversation_subject", None),
    ("input_protocol", "semantic"),
)
_VIEWER_HOST_METHODS = {
    "terminal_size": ("self",),
    "color_enabled": ("self",),
    "present": ("self", "frame"),
    "read_event": ("self",),
    "close_view": ("self",),
}
_VIEWER_TERMINAL_PARAMETERS = (
    ("input_stream", _REQUIRED_PARAMETER),
    ("output_stream", _REQUIRED_PARAMETER),
    ("read_character", None),
    ("no_color", False),
)
_VIEW_JSONL_PARAMETERS = (
    ("source", _REQUIRED_PARAMETER),
    ("spec", _REQUIRED_PARAMETER),
    ("host", None),
    ("terminal", None),
    ("transient_only", False),
)
_FAILURE_MESSAGES = {
    "installation_required": (
        "Required agent exchange viewer is unavailable. Repair or reinstall "
        "Story and retry /exchanges."
    ),
    "incompatible": (
        "Required agent exchange viewer is incompatible with Story. Repair or "
        "reinstall Story and retry /exchanges."
    ),
    "view_failed": "Agent exchange viewer failed.",
}


class AgentExchangeViewerError(RuntimeError):
    """Carry one fixed required-viewer failure without external diagnostics."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _FAILURE_MESSAGES:
            raise ValueError("unsupported agent exchange viewer error code")
        self.code = code
        super().__init__(_FAILURE_MESSAGES[code])


def _has_exact_signature(
    value: object,
    parameters: tuple[tuple[str, object], ...],
    *,
    keyword_only: tuple[str, ...] = (),
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
            or parameter.kind is not (
                inspect.Parameter.KEYWORD_ONLY if name in keyword_only
                else inspect.Parameter.POSITIONAL_OR_KEYWORD
            )
        ):
            return False
        if default is _REQUIRED_PARAMETER:
            if parameter.default is not inspect.Parameter.empty:
                return False
        elif type(parameter.default) is not type(default) or parameter.default != default:
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
        if expected_value is None:
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


def _load_viewer_api() -> tuple[
    Callable[..., object], Callable[..., object], Callable[..., object]
]:
    """Load and validate only the pinned viewer's public four-name facade."""

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
    viewer_terminal = getattr(module, "ViewerTerminal", None)
    view_jsonl = getattr(module, "view_jsonl", None)
    if (
        not isinstance(viewer_spec, type)
        or not _has_exact_signature(viewer_spec, _VIEWER_SPEC_PARAMETERS)
        or not _viewer_host_shape_is_supported(viewer_host)
        or not isinstance(viewer_terminal, type)
        or not _has_exact_signature(viewer_terminal, _VIEWER_TERMINAL_PARAMETERS)
        or not callable(view_jsonl)
        or not _has_exact_signature(
            view_jsonl,
            _VIEW_JSONL_PARAMETERS,
            keyword_only=("terminal", "transient_only"),
        )
    ):
        raise AgentExchangeViewerError("incompatible")
    return viewer_spec, viewer_terminal, view_jsonl


def prepare_agent_exchange_view(
    source: bytes,
    *,
    session_id: str,
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    scope_subject: str,
    agent_id: str,
) -> ExternalOwnedViewOperation:
    """Prepare one immutable view before the terminal lifecycle is acquired."""

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if type(scope_kind) is not AgentExchangeScopeKind:
        raise TypeError("scope_kind must be an AgentExchangeScopeKind")
    viewer_spec_type, viewer_terminal_type, view_jsonl = _load_viewer_api()
    expected_projection = (
        ("session_id", session_id),
        ("conversation_id", scope_id),
        ("agent_id", agent_id),
        ("date_time_field", "timestamp"),
        ("request_type_field", "request_type"),
        ("content_field", "content"),
        ("title", "Agent Exchanges"),
        ("conversation_label", scope_kind.value.title()),
        ("conversation_subject", scope_subject),
        ("input_protocol", "keys"),
    )
    try:
        spec = viewer_spec_type(
            session_id=session_id,
            conversation_id=scope_id,
            agent_id=agent_id,
            date_time_field="timestamp",
            request_type_field="request_type",
            content_field="content",
            title="Agent Exchanges",
            conversation_label=scope_kind.value.title(),
            conversation_subject=scope_subject,
            input_protocol="keys",
        )
    except Exception as exc:
        raise AgentExchangeViewerError("incompatible") from exc
    if not _viewer_spec_projection_is_supported(spec, expected_projection):
        raise AgentExchangeViewerError("incompatible")

    def view(transport: ExternalTerminalIO) -> None:
        if type(transport) is not ExternalTerminalIO:
            raise TypeError("transport must be ExternalTerminalIO")
        try:
            terminal = viewer_terminal_type(
                transport.input_stream, transport.output_stream,
                read_character=transport.read_character,
                no_color=transport.no_color,
            )
            if (getattr(terminal, "input_stream", None) is not transport.input_stream
                    or getattr(terminal, "output_stream", None) is not transport.output_stream
                    or getattr(terminal, "read_character", None) is not transport.read_character
                    or getattr(terminal, "no_color", None) is not transport.no_color):
                raise AgentExchangeViewerError("incompatible")
        except AgentExchangeViewerError:
            raise
        except Exception as exc:
            raise AgentExchangeViewerError("incompatible") from exc
        try:
            view_jsonl(source, spec, terminal=terminal, transient_only=True)
        except AgentExchangeViewerError:
            raise
        except Exception as exc:
            raise AgentExchangeViewerError("view_failed") from exc

    return view


__all__ = ["AgentExchangeViewerError", "prepare_agent_exchange_view"]
