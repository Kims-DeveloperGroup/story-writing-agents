"""Compact, content-free construction facts for visible agent replies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,127}")
_MAX_ITEMS = 64


class ReplyTraceOrigin(StrEnum):
    """How the visible reply body and semantic action were obtained."""

    PROVIDER = "provider"
    RECOVERED = "recovered"
    RUNTIME_FALLBACK = "runtime_fallback"


def _label(value: object, *, name: str) -> str:
    if type(value) is not str or _SAFE_LABEL.fullmatch(value) is None:
        raise ValueError(f"reply trace {name} is invalid")
    return value


def _labels(value: object, *, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"reply trace {name} must be a tuple")
    if len(value) > _MAX_ITEMS:
        raise ValueError(f"reply trace {name} exceeds its item limit")
    for item in value:
        _label(item, name=name)
    if len(set(value)) != len(value):
        raise ValueError(f"reply trace {name} must be unique")
    return value


@dataclass(frozen=True, slots=True)
class ReplyTrace:
    """Explain one reply without retaining prompts, prose, or raw output."""

    role: str
    action: str
    context_sources: tuple[str, ...] = ()
    recovered_fields: tuple[str, ...] = ()
    origin: ReplyTraceOrigin = ReplyTraceOrigin.PROVIDER

    def __post_init__(self) -> None:
        _label(self.role, name="role")
        _label(self.action, name="action")
        _labels(self.context_sources, name="context sources")
        _labels(self.recovered_fields, name="recovered fields")
        if type(self.origin) is not ReplyTraceOrigin:
            raise TypeError("reply trace origin is invalid")
        if self.origin is ReplyTraceOrigin.PROVIDER and self.recovered_fields:
            raise ValueError("provider trace cannot claim recovered fields")
        if self.origin is not ReplyTraceOrigin.PROVIDER and not self.recovered_fields:
            raise ValueError("non-provider trace must identify affected fields")
        if (
            self.origin is ReplyTraceOrigin.RUNTIME_FALLBACK
            and self.action != "none"
        ):
            raise ValueError("runtime fallback trace cannot claim an action")

    def to_dict(self) -> dict[str, object]:
        """Return the exact public JSON-compatible shape."""

        return {
            "role": self.role,
            "action": self.action,
            "context_sources": list(self.context_sources),
            "recovered_fields": list(self.recovered_fields),
            "origin": self.origin.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> ReplyTrace:
        """Decode one exact compact trace object."""

        fields = {
            "role",
            "action",
            "context_sources",
            "recovered_fields",
            "origin",
        }
        if type(value) is not dict or set(value) != fields:
            raise ValueError("reply trace has an invalid schema")
        context_sources = value["context_sources"]
        recovered_fields = value["recovered_fields"]
        if type(context_sources) is not list or type(recovered_fields) is not list:
            raise TypeError("reply trace lists are invalid")
        return cls(
            role=value["role"],
            action=value["action"],
            context_sources=tuple(context_sources),
            recovered_fields=tuple(recovered_fields),
            origin=ReplyTraceOrigin(value["origin"]),
        )


__all__ = ["ReplyTrace", "ReplyTraceOrigin"]
