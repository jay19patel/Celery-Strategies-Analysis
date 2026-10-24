"""Settings fail closed."""

from __future__ import annotations

import pytest

from app.errors import SettingsError
from app.settings import LIVE_CONFIRM_PHRASE, Settings, apply_changes, from_stored


def test_defaults_are_paper_only_and_delta_on_demo() -> None:
    s = Settings()
    assert s.active_brokers == ["paper"]
    assert s.delta_env == "demo" and not s.auto_delta and not s.is_real_money
    assert all(k in ("iron_condor", "iron_butterfly", "bull_put_spread", "bear_call_spread") for k in s.auto_strategies)


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


def test_stored_values_that_no_longer_validate_fall_back() -> None:
    s = from_stored({"min_score": 70, "risk_per_trade_pct": 999, "retired_field": 1})
    assert s.min_score == 70
    assert s.risk_per_trade_pct == Settings().risk_per_trade_pct
