import pytest

from tradebuddy.settings import LIVE_CONFIRM_PHRASE, Settings, SettingsError, apply_changes, from_stored
from tradebuddy.system import System
from tradebuddy.trading import TRADING_KEY

from .conftest import IdleStream


def test_defaults_are_paper_on_demo_prices():
    s = Settings()
    assert (s.broker, s.data_env, s.is_real_money) == ("paper", "demo", False)


@pytest.mark.parametrize(
    "changes",
    [{"nope": 1}, {"broker": "binance"}, {"delta_env": "prod"}, {"stop_loss_pct": 0}, {"paper_leverage": 500}, {"take_profit_pct": "abc"}],
)
def test_invalid_changes_are_refused(changes):
    with pytest.raises(SettingsError):
        apply_changes(Settings(), changes)


def test_real_money_needs_the_phrase_at_the_moment_it_becomes_real():
    live_account = apply_changes(Settings(), {"delta_env": "live"})  # still paper: no money at risk yet
    with pytest.raises(SettingsError):
        apply_changes(live_account, {"broker": "delta"})
    real = apply_changes(live_account, {"broker": "delta"}, confirm=LIVE_CONFIRM_PHRASE)
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
    s = from_stored({"broker": "delta", "delta_env": "live", "stop_loss_pct": 999, "gone": 1})
    assert s.is_real_money and s.stop_loss_pct == Settings().stop_loss_pct


def build(cfg, exchange):
    IdleStream.instances.clear()
    return System(cfg, strategies=[], stream_factory=IdleStream, client_factory=exchange.client)


async def test_routing_change_stops_trading_and_rebuilds(cfg, exchange):
    system = build(cfg, exchange)
    system.bus.start()
    system.set_toggle(TRADING_KEY, True)

    await system.update_settings({"broker": "delta", "delta_api_key": "k" * 12, "delta_api_secret": "s"})
    await system.bus.drain()

    assert system.store.enabled(TRADING_KEY, default=False) is False
    assert system.broker.name == "delta" and system.delta.client.has_credentials
    assert IdleStream.instances[-1].api_key == "k" * 12  # private channels only now that Delta is the broker
    event = system.store.recent_events(5, types=["SettingsChanged"])[0]
    assert event["trading_stopped"] and "delta_api_key" in event["changed"]
    assert "kkkkkkkkkkkk" not in str(event)  # field names only, never values
    await system.bus.stop()


async def test_risk_change_keeps_trading_on(cfg, exchange):
    system = build(cfg, exchange)
    system.set_toggle(TRADING_KEY, True)
    await system.update_settings({"stop_loss_pct": 1.5})
    assert system.store.enabled(TRADING_KEY, default=False) is True


async def test_settings_survive_restart_but_trading_does_not(cfg, exchange):
    first = build(cfg, exchange)
    await first.update_settings({"broker": "delta", "take_profit_pct": 3})
    first.set_toggle(TRADING_KEY, True)
    second = build(cfg, exchange)
    assert second.settings.broker == "delta" and second.settings.take_profit_pct == 3
    assert second.store.enabled(TRADING_KEY, default=False) is False
