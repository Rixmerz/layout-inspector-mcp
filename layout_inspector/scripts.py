"""Loads the browser-side scripts and splices the shared helpers into them."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

_MARKER = "/* @inject helpers */"


@cache
def _helpers() -> str:
    return (files("layout_inspector") / "js" / "_helpers.js").read_text(encoding="utf-8")


@cache
def load(name: str) -> str:
    """Return the named script with shared helper declarations inlined.

    Each script is a standalone arrow function, so the helpers cannot be loaded
    as a separate module — they are spliced into the function body instead.
    """
    source = (files("layout_inspector") / "js" / name).read_text(encoding="utf-8")
    if _MARKER not in source:
        raise ValueError(f"{name} is missing the {_MARKER!r} injection point")
    return source.replace(_MARKER, _helpers())


EXTRACT_LAYOUT = "extract_layout.js"
ELEMENT_CONTEXT = "element_context.js"
ACCESSIBILITY = "accessibility.js"
