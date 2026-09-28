"""Strategies. Drop a module in this folder with a Strategy subclass and it is picked up."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from tradebuddy.strategies.base import Context, Signal, Strategy

__all__ = ["Context", "Signal", "Strategy", "discover"]


def discover() -> list[Strategy]:
    found: dict[str, Strategy] = {}
    for info in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{__name__}.{info.name}")
        for obj in vars(module).values():
            if inspect.isclass(obj) and issubclass(obj, Strategy) and obj.__module__ == module.__name__ and not inspect.isabstract(obj):
                if obj.name in found:
                    raise ValueError(f"two strategies are named {obj.name!r}")
                found[obj.name] = obj()
    return [found[name] for name in sorted(found)]
