import pytest

from tradebuddy.settings import LIVE_CONFIRM_PHRASE, Settings, SettingsError, apply_changes, from_stored
from tradebuddy.system import System
from tradebuddy.trading import trading_key

from .conftest import IdleStream


def test_defaults_are_paper_only_on_demo_prices():
    s = Settings()
    assert (s.active_brokers, s.data_env, s.is_real_money) == (["paper"], "demo", False)


def test_at_least_one_broker_stays_active():
    with pytest.raises(SettingsError):
        apply_changes(Settings(), {"paper_active": False})
    assert apply_changes(Settings(), {"paper_active": False, "delta_active": True}).active_brokers == ["delta"]


def test_delta_prices_every_broker_while_active():
    s = apply_changes(Settings(), {"market_data": "live", "delta_active": True})
    assert s.data_env == "demo"  # Delta demo account -> demo prices for paper too


@pytest.mark.parametrize(
    "changes",
    [{"nope": 1}, {"delta_active": "yes"}, {"delta_env": "prod"}, {"stop_loss_pct": 0}, {"paper_leverage": 500}, {"take_profit_pct": "abc"}],
)
def test_invalid_changes_are_refused(changes):
    with pytest.raises(SettingsError):
        apply_changes(Settings(), changes)


def test_real_money_needs_the_phrase_at_the_moment_it_becomes_real():
    live_account = apply_changes(Settings(), {"delta_env": "live"})  # Delta not active: no money at risk yet
    with pytest.raises(SettingsError):
        apply_changes(live_account, {"delta_active": True})
    real = apply_changes(live_account, {"delta_active": True}, confirm=LIVE_CONFIRM_PHRASE)
    assert real.is_real_money
    assert apply_changes(real, {"stop_loss_pct": 2}).stop_loss_pct == 2  # already confirmed


def test_blank_secret_keeps_the_stored_one():
    s = apply_changes(Settings(), {"delta_api_key": "abcdefghij1234", "delta_api_secret": "shh"})
    assert apply_changes(s, {"delta_api_key": "", "delta_api_secret": "  "}) == s


def test_public_view_never_contains_secrets():
    s = apply_changes(Settings(), {"delta_api_key": "abcdefghij1234", "delta_api_secret": "supersecret"})
    public = str(s.public())
    assert "supersecret" not in public and "abcdefghij" not in public and "1234" in public


def test_stored_junk_falls_back_to_defaults():
    s = from_stored({"paper_active": False, "delta_active": True, "delta_env": "live", "stop_loss_pct": 999, "gone": 1})
    assert s.active_brokers == ["delta"] and s.is_real_money and s.stop_loss_pct == Settings().stop_loss_pct
    assert from_stored({"paper_active": False, "delta_active": False}).active_brokers == ["paper"]


def build(cfg, exchange):
    IdleStream.instances.clear()
    return System(cfg, strategies=[], stream_factory=IdleStream, client_factory=exchange.client)


async def test_delta_change_stops_delta_trading_only(cfg, exchange):
    system = build(cfg, exchange)
    system.bus.start()
    await system.update_settings({"delta_active": True})
    system.set_toggle(trading_key("delta"), True)

    await system.update_settings({"delta_api_key": "k" * 12, "delta_api_secret": "s"})
    await system.bus.drain()

    assert system.trading_on("delta") is False
    assert system.trading_on("paper") is True
    assert list(system.active) == ["paper", "delta"] and system.delta.client.has_credentials
    assert IdleStream.instances[-1].api_key == "k" * 12  # private channels only now that Delta is the broker
    event = system.store.recent_events(5, types=["SettingsChanged"])[0]
    assert event["trading_stopped"] and "delta_api_key" in event["changed"]
    assert "kkkkkkkkkkkk" not in str(event)  # field names only, never values
    await system.bus.stop()


async def test_risk_change_keeps_trading_on(cfg, exchange):
    system = build(cfg, exchange)
    await system.update_settings({"delta_active": True})
    system.set_toggle(trading_key("delta"), True)
    await system.update_settings({"stop_loss_pct": 1.5})
    assert system.trading_on("delta") is True


async def test_deactivating_paper_stops_it(cfg, exchange):
    system = build(cfg, exchange)
    await system.update_settings({"delta_active": True, "paper_active": False})
    assert system.trading_on("paper") is False and list(system.active) == ["delta"]


async def test_settings_survive_restart_but_delta_trading_does_not(cfg, exchange):
    first = build(cfg, exchange)
    await first.update_settings({"delta_active": True, "take_profit_pct": 3})
    first.set_toggle(trading_key("delta"), True)
    first.set_toggle(trading_key("paper"), False)
    second = build(cfg, exchange)
    assert second.settings.active_brokers == ["paper", "delta"] and second.settings.take_profit_pct == 3
    assert second.trading_on("delta") is False
    assert second.trading_on("paper") is False  # paper's switch is the user's choice and persists
