"""Lazy required JSONL-viewer adapter for immutable agent exchanges."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from ..agent_exchanges import AgentExchangeScopeKind, AgentExchangeSelection
from ..conversations.events import CollaborationEvent
from ._exchange_projection import ExchangeProjection, project_agent_exchanges
from ..interactive_terminal._external_view import (
    ExternalOwnedViewOperation,
    ExternalTerminalIO,
)


_VIEWER_PUBLIC_NAMES = ("ViewerHost", "ViewerSpec", "ViewerTerminal", "view_jsonl")
_REQUIRED_PARAMETER = object()
_VIEWER_SPEC_PARAMETERS = (
    ("session_id", None),
    ("conversation_id", None),
    ("agent_id", None),
    ("date_time_field", "timestamp"),
    ("request_type_field", "request_type"),
    ("content_field", "content"),
    ("title", "JSONL Viewer"),
    ("conversation_label", "Conversation"),
    ("conversation_subject", None),
    ("input_protocol", "semantic"),
    ("context", ()),
    ("inspection", None),
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
        elif type(observed) is not type(expected_value) or observed != expected_value:
            return False
    try:
        primary_fields = getattr(spec, "primary_fields")
    except Exception:
        return False
    return type(primary_fields) is tuple and primary_fields == ()


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


def _preview(value: str, maximum: int = 2048) -> str:
    return value if len(value) <= maximum else value[:maximum - 1] + "…"


def _load_inspection_api() -> object:
    """Load the pinned inert-data facade only at the optional boundary."""
    try:
        module = importlib.import_module("jsonl_viewer.inspection")
        names = ("Context", "DecodeJson", "DECODE", "Facet", "Group", "Inspection", "MISSING",
                 "MissingValue", "Option", "SearchScope", "Section", "Target")
        if tuple(getattr(module, "__all__", ())) != names:
            raise AgentExchangeViewerError("incompatible")
        if any(not isinstance(getattr(module, name, None), type) for name in names if name not in {"DECODE", "MISSING"}):
            raise AgentExchangeViewerError("incompatible")
        if type(module.DECODE) is not module.DecodeJson or type(module.MISSING) is not module.MissingValue:
            raise AgentExchangeViewerError("incompatible")
        return module
    except AgentExchangeViewerError:
        raise
    except Exception as failure:
        raise AgentExchangeViewerError("incompatible") from failure


def _inspection(api: object, projection: ExchangeProjection) -> tuple[object | None, str | None]:
    """Translate Story facts into bounded data, never into viewer predicates."""
    notice = projection.notice
    if notice is not None:
        return None, notice
    if (sum(len(call.records) for call in projection.calls) > 10_000
            or len(projection.targets) + len(projection.unavailable) > 10_000):
        return None, "Story inspection metadata exceeds display limits; showing all unchanged original records."
    try:
        def options(rows: tuple) -> tuple:
            return tuple(api.Option(value, label) for value, label in rows)

        missing = api.MISSING
        facets = (
            api.Facet("outcome", "Call outcome", options(tuple((value, value) for value in (
                "accepted", "rejected", "provider_failure", "cancelled", "post_response_runtime_failure"))
                + ((missing, "Outcome not recorded"),))),
            api.Facet("provider", "Provider", options((("codex", "Codex"), ("antigravity", "Antigravity"),
                                                       (missing, "Provider not recorded")))),
            api.Facet("phase", "Phase", options((("procedure_status", "Procedure / status explanation"),
                ("lead_preliminary_review", "Lead preliminary review"), ("specialist_work", "Specialist work"),
                ("final_candidate_review", "Final candidate review"), (missing, "Phase not recorded")))),
            api.Facet("completeness", "Record completeness", options((("complete", "All three kinds present"),
                ("incomplete", "Some kinds absent"))), multiple=False),
        ) + tuple(api.Facet(f"{kind}_present", f"{label} record", options(((True, "Present"), (False, "Absent"))),
                            multiple=False) for kind, label in (("request", "Request"), ("response", "Response"), ("outcome", "Outcome")))
        groups = []
        for call in projection.calls:
            values = (("outcome", call.outcome if call.outcome is not None else missing),
                      ("provider", call.provider if call.provider is not None else missing),
                      ("phase", call.phase if call.phase is not None else missing),
                      ("completeness", call.completeness)) + tuple(
                          (f"{section.id}_present", call.response_present if section.id == "response" else bool(section.records))
                          for section in call.sections)
            label = " · ".join((call.timestamp or "Time not recorded", _preview(call.exchange_id, 24),
                                call.outcome or "Outcome not recorded", call.provider or "Provider not recorded",
                                call.phase or "Phase not recorded"))
            groups.append(api.Group(call.id, _preview(label, 512), call.records,
                sections=tuple(api.Section(section.id, section.label, section.records,
                    f"No {section.label} record was retained.") for section in call.sections),
                values=values, annotations=tuple(api.Context(_preview(label, 512), _preview(value))
                                               for label, value in call.annotations[:64])))
        scopes = tuple(api.SearchScope(scope_id, label,
            tuple(api.Target(target.record, (("content", api.DECODE) if target.decode_content else ("content",)) + target.path)
                  for target in projection.targets if target.scope_id == scope_id),
            tuple((item.record, _preview(item.reason, 512)) for item in projection.unavailable if item.scope_id == scope_id))
            for scope_id, label in (("request", "Request payload"), ("response", "Response content"), ("outcome", "Outcome")))
        return api.Inspection(tuple(groups), facets, scopes), None
    except (TypeError, ValueError, OverflowError, RecursionError):
        # A valid source must remain openable even when optional host metadata
        # cannot fit the generic descriptor budget. Never retain a partial set.
        return None, "Story inspection metadata exceeds display limits; showing all unchanged original records."


def prepare_agent_exchange_view(
    source: bytes,
    *,
    session_id: str,
    scope_kind: AgentExchangeScopeKind,
    scope_id: str,
    scope_subject: str,
    agent_id: str,
    typed_history: tuple[CollaborationEvent, ...] = (),
    history_notice: str | None = None,
) -> ExternalOwnedViewOperation:
    """Prepare one immutable view before the terminal lifecycle is acquired."""

    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    if type(scope_kind) is not AgentExchangeScopeKind:
        raise TypeError("scope_kind must be an AgentExchangeScopeKind")
    viewer_spec_type, viewer_terminal_type, view_jsonl = _load_viewer_api()
    api = _load_inspection_api()
    projection = project_agent_exchanges(source, AgentExchangeSelection(session_id, scope_kind, scope_id, agent_id), typed_history)
    inspection, notice = _inspection(api, projection)
    context = tuple(api.Context(label, _preview(value)) for label, value in (
        ("Session", session_id), (scope_kind.value.title(), f"{scope_id} — {scope_subject}"), ("Agent", agent_id)))
    if history_notice is not None:
        context += (api.Context("History", _preview(history_notice)),)
    if notice is not None:
        context += (api.Context("Inspection", _preview(notice)),)
    expected_projection = (
        ("session_id", None),
        ("conversation_id", None),
        ("agent_id", None),
        ("date_time_field", "timestamp"),
        ("request_type_field", "request_type"),
        ("content_field", "content"),
        ("title", "Agent Exchanges"),
        ("input_protocol", "keys"),
        ("context", context),
        ("inspection", inspection),
    )
    try:
        spec = viewer_spec_type(
            date_time_field="timestamp",
            request_type_field="request_type",
            content_field="content",
            title="Agent Exchanges",
            input_protocol="keys",
            context=context,
            inspection=inspection,
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
