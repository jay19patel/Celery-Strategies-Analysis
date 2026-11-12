"""Settings fail closed, and each strategy's config lives with the strategy."""

from __future__ import annotations

import pytest

from app.errors import SettingsError
from app.settings import LIVE_CONFIRM_PHRASE, Settings, StrategyConfig, apply_changes, from_stored
from app.strategies import registry


def test_defaults_are_paper_only_and_delta_on_demo() -> None:
    s = Settings()
    assert s.active_brokers == ["paper"]
    assert s.delta_env == "demo" and not s.auto_delta and not s.is_real_money
    assert s.max_spread_pct <= 15  # a wing quoted wider than this gives the credit back to the book
    assert s.risk_per_trade_pct <= 1 and s.max_drawdown_pct > 0
    assert len(Settings.model_fields) <= 40  # fewer settings: a strategy's numbers are its own


def test_a_strategy_is_on_only_if_it_says_so_and_never_live_by_default() -> None:
    s = Settings()
    for name, cls in registry.classes().items():
        assert s.config(name).enabled == cls.default_on
        assert not s.config(name).live
    assert "delta" not in s.strategy_brokers("iron_condor")


def test_real_money_needs_the_phrase() -> None:
    change = {"delta_active": True, "delta_env": "live"}
    with pytest.raises(SettingsError, match="confirmation phrase"):
        apply_changes(Settings(), change)
    assert apply_changes(Settings(), change, LIVE_CONFIRM_PHRASE).is_real_money


def test_a_delta_routing_change_switches_delta_auto_trading_off() -> None:
    on = Settings(delta_active=True, auto_delta=True)
    assert not apply_changes(on, {"delta_api_key": "new-key-123456"}).auto_delta
    assert apply_changes(on, {"max_spread_pct": 10}).auto_delta  # unrelated changes leave it alone


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
    with pytest.raises(SettingsError, match="unknown"):
        apply_changes(Settings(), {"nope": 1})
    with pytest.raises(SettingsError, match="LiteLLM model id"):
        apply_changes(Settings(), {"ai_model": "not a model!"})


def test_a_strategys_params_are_validated_by_the_strategy_and_only_changes_are_kept() -> None:
    s = apply_changes(Settings(), {"strategies": {"iron_condor": {"enabled": True, "params": {"short_delta": 0.25, "wing_width_pct": 2.0}}}})
    assert s.config("iron_condor").params == {"short_delta": 0.25}  # the default is not stored: a new default reaches it
    with pytest.raises(SettingsError, match="short_delta"):
        apply_changes(Settings(), {"strategies": {"iron_condor": {"params": {"short_delta": 2}}}})
    with pytest.raises(SettingsError, match="expiry_min_hours"):
        apply_changes(Settings(), {"strategies": {"iron_condor": {"params": {"expiry_min_hours": 80}}}})
    with pytest.raises(SettingsError, match="no strategy"):
        apply_changes(Settings(), {"strategies": {"no_such_strategy": {"enabled": True}}})


def test_a_strategy_runs_on_an_underlying_only_when_both_switches_are_on() -> None:
    s = Settings(underlyings=["BTC", "ETH"], strategies={"flow_breakout": StrategyConfig(enabled=True, underlyings=["ETH"])})
    assert s.strategy_on("flow_breakout", "ETH") and not s.strategy_on("flow_breakout", "BTC")
    off = s.model_copy(update={"strategies": {"flow_breakout": StrategyConfig(enabled=False, underlyings=["ETH"])}})
    assert not off.strategy_on("flow_breakout", "ETH")  # the strategy switch wins over its symbols
    with pytest.raises(SettingsError, match="strategies"):
        apply_changes(s, {"strategies": {"flow_breakout": {"underlyings": ["DOGE"]}}})


def test_delta_takes_a_strategy_only_when_the_strategy_is_switched_to_live() -> None:
    s = Settings(delta_active=True, auto_delta=True)
    assert s.strategy_brokers("iron_condor") == ["paper"]
    live = s.model_copy(update={"strategies": {"iron_condor": StrategyConfig(enabled=True, live=True)}})
    assert live.strategy_brokers("iron_condor") == ["paper", "delta"]
    assert live.model_copy(update={"auto_delta": False}).strategy_brokers("iron_condor") == ["paper"]


def test_stored_values_that_no_longer_validate_fall_back() -> None:
    s = from_stored({"max_spread_pct": 10, "risk_per_trade_pct": 999, "retired_field": 1})
    assert s.max_spread_pct == 10
    assert s.risk_per_trade_pct == Settings().risk_per_trade_pct
    one_bad = from_stored({"strategies": {"iron_condor": {"enabled": True}, "bull_put_spread": {"params": {"short_delta": 9}}, "gone": {}}})
    assert one_bad.config("iron_condor").enabled and "bull_put_spread" not in one_bad.strategies and "gone" not in one_bad.strategies


def test_the_old_global_strategy_settings_become_per_strategy_configs() -> None:
    """What the previous version stored: on/off lists, symbols, and the option numbers kept globally."""
    old = {
        "directional_strategies": ["trend_4h_short", "supertrend"], "auto_strategies": ["iron_condor", "short_strangle"],
        "strategy_symbols": {"trend_4h_short": ["ETH"]}, "min_credit_width_pct": 10.0, "short_delta": 0.2,
        "zh_enabled": True, "risk_per_trade_pct": 2.0, "min_score": 70,
    }
    s = from_stored(old)
    assert s.config("trend_4h_short").enabled and s.config("trend_4h_short").underlyings == ["ETH"]
    assert s.config("iron_condor").enabled and s.config("iron_condor").params == {"min_credit_width_pct": 10.0, "short_delta": 0.2}
    assert s.config("iron_butterfly").params == {"min_credit_width_pct": 10.0}  # it has no short_delta to take
    assert not s.config("flow_breakout").enabled  # it was off before
    assert s.config("expiry_decay").enabled  # it rode on the zero-hero switch
    assert s.risk_per_trade_pct == 2.0  # the owner's own number is kept


def test_event_times_are_utc_minutes_and_a_window_finds_them() -> None:
    with pytest.raises(SettingsError, match="event_times"):
        apply_changes(Settings(), {"event_times": ["9 Dec 2026"]})
    s = apply_changes(Settings(), {"event_times": [" 2026-12-09 19:00 ", "2026-12-09 19:00"]})
    assert s.event_times == ["2026-12-09 19:00"]
    at = 1_796_842_800  # 2026-12-09 19:00 UTC
    assert s.event_between(at - 3600, at + 3600) == "2026-12-09 19:00"
    assert s.event_between(at + 3600, at + 86_400) == "2026-12-09 19:00"  # inside the buffer after it
    assert s.event_between(at + 3 * 3600, at + 86_400) == ""
    assert s.event_between(at - 86_400, at - 3600) == ""
