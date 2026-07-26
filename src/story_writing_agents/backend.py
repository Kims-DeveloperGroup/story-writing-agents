"""Public backend protocol and plugin loading."""

from __future__ import annotations

import importlib
import inspect
import re
from typing import Protocol, runtime_checkable

from .errors import BackendProtocolError
from .models import AgentReply, AgentRequest


@runtime_checkable
class AgentBackend(Protocol):
    """Minimal asynchronous adapter implemented by an LLM provider integration."""

    async def invoke(self, request: AgentRequest) -> AgentReply:
        """Return one structured response for ``request``."""


_PLUGIN_SPEC = re.compile(
    r"(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r":(?P<attribute>[A-Za-z_][A-Za-z0-9_]*)"
)


def load_backend(spec: str) -> AgentBackend:
    """Load a backend class, instance, or zero-argument factory.

    The strict ``module:attribute`` syntax avoids shell execution and nested
    attribute traversal. Importing a plugin still executes that trusted Python
    module, as normal for a Python plugin system.
    """

    match = _PLUGIN_SPEC.fullmatch(spec)
    if match is None:
        raise BackendProtocolError(
            "backend must use the safe 'module:attribute' plugin syntax"
        )

    module = importlib.import_module(match.group("module"))
    value = getattr(module, match.group("attribute"), None)
    if value is None:
        raise BackendProtocolError(f"backend attribute not found: {spec}")

    if inspect.isclass(value):
        value = value()
    elif not callable(getattr(value, "invoke", None)) and callable(value):
        value = value()

    invoke = getattr(value, "invoke", None)
    if invoke is None or not callable(invoke):
        raise BackendProtocolError(f"backend does not provide invoke(): {spec}")
    if not inspect.iscoroutinefunction(invoke):
        raise BackendProtocolError("backend invoke() must be async")
    return value
