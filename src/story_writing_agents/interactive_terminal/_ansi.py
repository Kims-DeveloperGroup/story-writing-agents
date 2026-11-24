"""Share the terminal reset and the recognized decorative SGR vocabulary."""

from __future__ import annotations

import re


RESET = "\x1b[0m"
_DECORATIVE_SGR = re.compile(r"\x1b\[(?:0|1|2|3|4|7|3[1-7]|1;3[1-7])m")


def strip_decorative_sgr(text: str) -> str:
    """Remove exactly the fixed decorative SGR vocabulary."""

    if type(text) is not str:
        raise TypeError("text must be a string")
    return _DECORATIVE_SGR.sub("", text)
