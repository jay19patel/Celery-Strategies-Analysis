"""Data provider factory — settings-driven provider selection.

Reads the ``DATA_PROVIDER`` environment variable (defaults to ``"delta"``)
and returns the appropriate BaseDataProvider subclass. This is the single
entry point the rest of the codebase uses to get data; strategies and tasks
never import a specific provider directly.

Inspired by OpenAlgo's broker factory pattern where ``broker/__init__.py``
selects the right broker module at runtime.
"""

import logging
from functools import lru_cache

from app.data_providers.base_provider import BaseDataProvider

logger = logging.getLogger(__name__)

# Registry of provider name → lazy import path
_PROVIDER_REGISTRY: dict[str, str] = {
    "delta": "app.data_providers.delta_exchange.DeltaExchangeProvider",
}


@lru_cache(maxsize=1)
def get_data_provider(provider_name: str | None = None) -> BaseDataProvider:
    """Return a singleton data provider instance based on configuration.

    The provider name is resolved in this order:
        1. ``provider_name`` argument (if passed)
        2. ``DATA_PROVIDER`` environment variable
        3. Falls back to ``"delta"``

    Args:
        provider_name: Explicit provider key. Overrides env var.

    Returns:
        An initialised BaseDataProvider subclass.

    Raises:
        ValueError: If the provider name is not in the registry.
        ImportError: If the provider module cannot be loaded.
    """
    import importlib
    import os

    name = provider_name or os.getenv("DATA_PROVIDER", "delta")
    name = name.strip().lower()

    dotted_path = _PROVIDER_REGISTRY.get(name)
    if dotted_path is None:
        available = ", ".join(sorted(_PROVIDER_REGISTRY))
        raise ValueError(
            f"Unknown data provider '{name}'. Available: {available}"
        )

    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)
    instance = provider_class()

    logger.info(f"✅ Data provider initialized: {instance.name} (key='{name}')")
    return instance


def register_provider(key: str, dotted_class_path: str) -> None:
    """Register a new data provider at runtime.

    Use this to plug in custom providers without editing this module:

        register_provider("angel", "app.data_providers.angel_one.AngelOneProvider")

    Args:
        key: Short name used in the ``DATA_PROVIDER`` env var.
        dotted_class_path: Full dotted path to the provider class.
    """
    _PROVIDER_REGISTRY[key.strip().lower()] = dotted_class_path
    # Clear cached singleton so next call picks up the new provider
    get_data_provider.cache_clear()
    logger.info(f"📦 Registered data provider: '{key}' → {dotted_class_path}")
