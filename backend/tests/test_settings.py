"""Settings fail closed."""

from __future__ import annotations

import pytest

from app.errors import SettingsError
from app.settings import LIVE_CONFIRM_PHRASE, STRATEGY_KEYS, Settings, apply_changes, from_stored
from app.strategies import REGISTRY


def test_defaults_are_paper_only_and_delta_on_demo() -> None:
    s = Settings()
    assert s.active_brokers == ["paper"]
    assert s.delta_env == "demo" and not s.auto_delta and not s.is_real_money
    # Every structure in the catalog is enabled, including the two whose loss is not bounded.
    assert sorted(s.auto_strategies) == sorted(STRATEGY_KEYS)
    assert sorted(s.directional_strategies) == sorted(REGISTRY)


def test_real_money_needs_the_phrase() -> None:
    change = {"delta_active": True, "delta_env": "live"}
    with pytest.raises(SettingsError, match="confirmation phrase"):
        apply_changes(Settings(), change)
    assert apply_changes(Settings(), change, LIVE_CONFIRM_PHRASE).is_real_money


def test_a_delta_routing_change_switches_delta_auto_trading_off() -> None:
    on = Settings(delta_active=True, auto_delta=True)
    assert not apply_changes(on, {"delta_api_key": "new-key-123456"}).auto_delta
    assert apply_changes(on, {"min_score": 70}).auto_delta  # unrelated changes leave it alone


def test_secrets_are_masked_and_blank_keeps_them() -> None:
    s = apply_changes(Settings(), {"delta_api_key": "abcdefghijkl", "delta_api_secret": "shh", "telegram_bot_token": "t0k"})
    public = s.public()
    assert public["delta_api_key"] == "••••ijkl"
    assert public["delta_api_secret"] == "set" and public["telegram_bot_token"] == "set"
    assert "shh" not in str(public) and "t0k" not in str(public)
    assert apply_changes(s, {"delta_api_secret": ""}).delta_api_secret == "shh"


def test_invalid_changes_are_refused_with_the_field_named() -> None:
    with pytest.raises(SettingsError, match="risk_per_trade_pct"):
        apply_changes(Settings(), {"risk_per_trade_pct": 50})
    with pytest.raises(SettingsError, match="at least one broker"):
        apply_changes(Settings(), {"paper_active": False})
    with pytest.raises(SettingsError, match="expiry_min_hours"):
        apply_changes(Settings(), {"expiry_min_hours": 80})
    with pytest.raises(SettingsError, match="unknown"):
        apply_changes(Settings(), {"nope": 1})
    with pytest.raises(SettingsError, match="LiteLLM model id"):
        apply_changes(Settings(), {"ai_model": "not a model!"})


def test_a_strategy_runs_on_an_underlying_only_when_both_switches_are_on() -> None:
    s = Settings(underlyings=["BTC", "ETH"])
    assert s.strategy_underlyings("supertrend") == ["BTC", "ETH"]  # unmapped: every enabled underlying

    s = apply_changes(s, {"strategy_symbols": {"supertrend": ["ETH"], "pcr_options": []}})
    assert s.strategy_on("supertrend", "ETH") and not s.strategy_on("supertrend", "BTC")
    assert s.strategy_underlyings("pcr_options") == ["BTC", "ETH"]  # empty list means no restriction

    off = apply_changes(s, {"directional_strategies": ["pcr_options"]})
    assert not off.strategy_on("supertrend", "ETH")  # the strategy switch still wins over its symbols

    with pytest.raises(SettingsError, match="strategy_symbols"):
        apply_changes(s, {"strategy_symbols": {"supertrend": ["DOGE"]}})
    with pytest.raises(SettingsError, match="strategy_symbols"):
        apply_changes(s, {"strategy_symbols": {"no_such_strategy": ["BTC"]}})


def test_stored_values_that_no_longer_validate_fall_back() -> None:
    s = from_stored({"min_score": 70, "risk_per_trade_pct": 999, "retired_field": 1})
    assert s.min_score == 70
    assert s.risk_per_trade_pct == Settings().risk_per_trade_pct
