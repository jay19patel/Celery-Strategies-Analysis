"""The strategies, found by looking: every module in this package (except `base`, `registry` and
those starting with "_") is imported, and every concrete `Strategy` or `OptionStrategy` subclass
defined in it is registered under its `name`. Adding a file adds a strategy; deleting it removes one.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from functools import cache
from typing import Any

from app import strategies as package
from app.strategies.base import OptionStrategy, Strategy

Plugin = Strategy | OptionStrategy
SKIP = {"base", "registry"}


@cache
def classes() -> dict[str, type[Plugin]]:
    """Every strategy class by name, perpetual ones first, each family in name order."""
    found: dict[str, type[Plugin]] = {}
    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name in SKIP or mod.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package.__name__}.{mod.name}")
        for obj in vars(module).values():
            if (
                inspect.isclass(obj) and issubclass(obj, Strategy | OptionStrategy) and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__ and "name" in vars(obj)
            ):
                if obj.name in found:
                    raise RuntimeError(f"two strategies are named {obj.name!r}: {found[obj.name].__module__} and {module.__name__}")
                found[obj.name] = obj
    return dict(sorted(found.items(), key=lambda kv: (kv[1].family != "perp", kv[0])))


def get(name: str) -> type[Plugin] | None:
    return classes().get(name)


def option(name: str) -> type[OptionStrategy] | None:
    cls = get(name)
    return cls if cls is not None and issubclass(cls, OptionStrategy) else None


def names(family: str = "") -> list[str]:
    return [n for n, c in classes().items() if not family or c.family == family]


def label(name: str) -> str | None:
    cls = get(name)
    return cls.label if cls else None


def make(name: str, params: dict[str, Any] | None = None) -> Plugin:
    cls = get(name)
    if cls is None:
        raise KeyError(f"no strategy {name!r}")
    return cls(params)


def validate_params(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """The params as the strategy's model reads them (ValueError naming the field otherwise). Only
    values that differ from the default are kept, so a changed default reaches every install."""
    cls = get(name)
    if cls is None:
        raise ValueError(f"no strategy {name!r}")
    model = cls.Params.model_validate(params)
    default = cls.Params()
    return {k: v for k, v in model.model_dump().items() if getattr(default, k) != v}


def enabled(settings: Any, family: str) -> dict[str, Plugin]:
    """The strategies of `family` switched on in `settings`, each built with its configured params."""
    out: dict[str, Plugin] = {}
    for name in names(family):
        cfg = settings.config(name)
        if cfg.enabled:
            out[name] = make(name, cfg.params)
    return out


def describe(settings: Any) -> list[dict[str, Any]]:
    """Every strategy for the dashboard: what it is, how it is configured, and its form."""
    out = []
    for name, cls in classes().items():
        cfg = settings.config(name)
        plugin = cls(cfg.params)
        out.append(plugin.describe() | {
            "config": cfg.model_dump(),
            "params": plugin.p.model_dump(),
            "schema": cls.Params.model_json_schema(),
        })
    return out
