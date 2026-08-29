"""Lazy optional JSONL-viewer adapter for immutable agent exchanges."""

from __future__ import annotations

import importlib
from collections.abc import Callable

from ..agent_exchanges import AgentExchangeScopeKind
from ..interactive_terminal._external_view import (
    ExternalViewerHost,
    ExternalViewOperation,
)


_VIEWER_PUBLIC_NAMES = ("ViewerHost", "ViewerSpec", "view_jsonl")
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
        or viewer_host is None
        or not callable(view_jsonl)
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
