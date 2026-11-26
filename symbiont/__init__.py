"""symbiont — the research layer's outward arm.

Route (fixed, source-agnostic): question -> source adapter -> fetch(since) -> verify
-> snapshot(content-addressed raw + sha256 + release id) -> xref(stable IDs, conflict log)
-> publish(one view) -> cost.

A *source* is a plug-in file under adapters/. The loop (loop.py) never learns a source's name.
"""
__all__ = ["http", "contract", "xref", "view", "loop"]
