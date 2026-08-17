"""Stable import boundary for synchronous interactive coordination."""

from ._core import InteractiveController as InteractiveController


InteractiveController.__module__ = __name__

__all__ = ["InteractiveController"]
