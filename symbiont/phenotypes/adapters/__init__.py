"""The adapter registry: adapter name -> the adapter class whose `name` matches, BY NAME.
The class may live in ANY module of this package (the gene arm's discovery rule); a config
naming an adapter that does not exist is REFUSED — the config and the code may not disagree
about what exists."""
from __future__ import annotations

import importlib
import pkgutil

from ..contract import ConfigError


def load_adapter(name: str):
    import symbiont.phenotypes.adapters as pkg
    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if mod_info.name.startswith("_"):
            continue
        mod = importlib.import_module(f".{mod_info.name}", package="symbiont.phenotypes.adapters")
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if isinstance(obj, type) and getattr(obj, "name", None) == name:
                return obj()
    raise ConfigError(f"no adapter named {name!r} exists under symbiont/phenotypes/adapters/ — "
                      f"the config and the code disagree about what exists")
