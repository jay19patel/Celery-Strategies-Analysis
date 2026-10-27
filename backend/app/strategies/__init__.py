"""Directional strategies on the perpetuals. Each is a pure `decide()`; `REGISTRY` is the set the
runner evaluates and the Settings page switches on and off."""

from __future__ import annotations

from app.strategies.base import Strategy
from app.strategies.ema_9_15 import Ema915
from app.strategies.ema_vwap import EmaVwap
from app.strategies.pcr_options import PcrOptions
from app.strategies.supertrend import SupertrendTrend

REGISTRY: dict[str, Strategy] = {s.name: s for s in (EmaVwap(), Ema915(), PcrOptions(), SupertrendTrend())}
