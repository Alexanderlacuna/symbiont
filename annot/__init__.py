"""annot — the ONE core of the ship's surface (see annot/core.py).

Two functions are the whole public API:

    dispatch(tool, args) -> bytes        canonical JSON, exactly what every door returns
    answer(tool, args)   -> dict         the same response as an object (page rendering only)

`SPECS` declares the surface: the six tools and their parameters. Doors are generated from it.
"""
from __future__ import annotations

from .core import (CORE, CORE_VERSION, DEFAULT_STORE, SPECS, STORE_SOURCES, TOOLS, ToolError,
                   answer, dispatch, dumps, input_schema, normalize, provenance, store)

__all__ = ["CORE", "CORE_VERSION", "DEFAULT_STORE", "SPECS", "STORE_SOURCES", "TOOLS", "ToolError",
           "answer", "dispatch", "dumps", "input_schema", "normalize", "provenance", "store"]
