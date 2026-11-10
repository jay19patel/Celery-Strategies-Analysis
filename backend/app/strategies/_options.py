"""Shared by the option-structure strategies: the strike-rule params. Not a strategy (leading "_")."""

from __future__ import annotations

from pydantic import Field

from app.strategies.base import OptionParams


class ShortDelta(OptionParams):
    short_delta: float = Field(0.18, ge=0.05, le=0.45, description="|delta| of the sold strikes")


class Wings(OptionParams):
    wing_width_pct: float = Field(2.0, ge=0.25, le=15, description="Protective wings this % of spot beyond the short strike")


class ShortDeltaWings(ShortDelta, Wings):
    pass
