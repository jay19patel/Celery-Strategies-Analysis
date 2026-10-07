"""The daily report: what a day's analytics hold, the email built from them, SMTP safety, and the 9 PM schedule."""

from __future__ import annotations

import smtplib
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradebuddy import email_report
from tradebuddy.analytics import collect_daily_analytics, get_day_bounds
from tradebuddy.api import Api, ApiError
from tradebuddy.email_report import MAX_ATTEMPTS, RETRY_AFTER, DailyReporter, EmailError, deliver, render
from tradebuddy.events import EmailReport, TradeSkipped
from tradebuddy.runner import PAUSED
from tradebuddy.settings import Settings, SettingsError, apply_changes

from .test_pipeline import close_bar, make

MAIL = {"email_enabled": True, "email_to": "me@example.com", "email_smtp_host": "smtp.example.com", "email_smtp_user": "me@example.com", "email_smtp_pass": "pw"}


def at(hour: int, minute: int = 0, tz: str = "Asia/Kolkata") -> float:
    """Today at hh:mm in tz."""
    now = datetime.now(ZoneInfo(tz))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


# -- settings ----------------------------------------------------------------------------


def test_report_goes_out_at_9_pm_by_default_and_the_hour_is_checked():
    assert Settings().email_report_hour == 21
    assert apply_changes(Settings(), {"email_report_hour": 7}).email_report_hour == 7
    with pytest.raises(SettingsError):
        apply_changes(Settings(), {"email_report_hour": 24})


def test_recipients_are_checked_and_several_are_allowed():
    s = apply_changes(Settings(), {"email_to": "a@x.com, b@y.org"})
    assert s.email_recipients == ["a@x.com", "b@y.org"]
    for bad in ("not-an-email", "a@x.com, oops"):
        with pytest.raises(SettingsError):
            apply_changes(Settings(), {"email_to": bad})
    with pytest.raises(SettingsError):
        apply_changes(Settings(), {"email_smtp_host": "smtp.gmail.com/evil path"})


def test_smtp_password_never_leaves_the_engine():
    s = apply_changes(Settings(), MAIL)
    assert s.public()["email_smtp_pass"] == "set" and "pw" not in str(s.public())


# -- analytics ---------------------------------------------------------------------------


async def test_a_day_holds_its_trades_orders_timeline_and_skips(cfg, exchange):
    system = await make(cfg, exchange)
    await close_bar(system)  # always_buy opens a paper long; broken raises
    await system.paper.close_position("BTCUSD", reason="take profit")
    system.bus.publish(TradeSkipped(strategy="always_buy", symbol="ETHUSD", side="", broker="", reason=PAUSED))
    system.bus.publish(TradeSkipped(strategy="needs_history", symbol="ETHUSD", side="", broker="", reason=PAUSED))
    await system.drain()

    d = collect_daily_analytics(system.store, None, "UTC")
    s = d["summary"]
    assert s["total_trades"] == 1 and s["total_orders"] == 1 and s["signals_count"] >= 1
    assert d["trades"][0]["reason"] == "take profit" and d["trades"][0]["time"] != "-"
    assert any(p["strategy"] == "always_buy" and p["orders"] == 1 for p in d["strategies_performance"])
    texts = [x["text"] for x in d["timeline"]]
    assert any(t.startswith("always_buy signalled BUY BTCUSD") for t in texts)
    assert any(t.startswith("Order placed: BUY") and "paper" in t for t in texts)
    assert any(t.startswith("Closed long BTCUSD on paper") for t in texts)
    assert d["outages"] == 1  # two PAUSED skips at the same moment are one outage
    assert d["skipped"][0]["reason"] == PAUSED and d["skipped"][0]["count"] == 2
    assert d["system_health"]["system_errors_count"] >= 1  # the broken strategy
    assert "AI" not in d["narrative"]["full_story"]


async def test_past_days_have_no_made_up_load_numbers(cfg, exchange):
    system = await make(cfg, exchange)
    d = collect_daily_analytics(system.store, "2020-01-01", "UTC")
    assert d["summary"]["total_trades"] == 0 and d["timeline"] == []
    sh = d["system_health"]
    assert sh["measured"] is False and sh["peak_cpu_pct"] is None and sh["peak_rss_mb"] is None


def test_day_bounds_follow_the_trading_timezone():
    start, end, day = get_day_bounds("2026-03-10", "Asia/Kolkata")
    assert day == "2026-03-10" and end - start == 86_400
    assert datetime.fromtimestamp(start, ZoneInfo("UTC")).strftime("%H:%M") == "18:30"  # IST midnight


# -- the email ---------------------------------------------------------------------------


async def test_email_escapes_what_it_shows_and_names_the_day(cfg, exchange):
    system = await make(cfg, exchange)
    system.bus.publish(TradeSkipped(strategy="s", symbol="BTCUSD", side="buy", broker="paper", reason="<script>x</script>"))
    await system.drain()
    d = collect_daily_analytics(system.store, None, "UTC")
    subject, _, html = render(d)
    assert d["date"] in subject and "<script>" not in html and "&lt;script&gt;" in html
    assert "Why signals were skipped" in html and "Timeline" in html


class FakeSMTP:
    instances: list[FakeSMTP] = []
    starttls_offered = True
    login_error: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        self.calls: list[str] = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def has_extn(self, name):
        return self.starttls_offered and name == "starttls"

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        if self.login_error:
            raise self.login_error
        self.calls.append("login")

    def send_message(self, msg):
        self.calls.append(f"send:{msg['To']}")


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.starttls_offered, FakeSMTP.login_error = [], True, None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_login_happens_only_after_starttls(smtp):
    deliver(apply_changes(Settings(), MAIL), "s", "t", "<p>h</p>")
    assert smtp.instances[0].calls == ["ehlo", "starttls", "ehlo", "login", "send:me@example.com"]


def test_no_password_in_the_clear(smtp):
    smtp.starttls_offered = False
    with pytest.raises(EmailError, match="unencrypted"):
        deliver(apply_changes(Settings(), MAIL), "s", "t", "h")
    assert "login" not in smtp.instances[0].calls


def test_a_refused_login_says_what_to_do_without_the_password(smtp):
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials pw")
    with pytest.raises(EmailError, match="App Password") as err:
        deliver(apply_changes(Settings(), MAIL), "s", "t", "h")
    assert "pw" not in str(err.value).replace("Password", "")


# -- the schedule ------------------------------------------------------------------------


@pytest.fixture
async def mailing(cfg, exchange, monkeypatch):
    system = await make(cfg, exchange)
    await system.update_settings(MAIL)
    sent: list[str] = []
    outcome: dict[str, Exception | None] = {"error": None}

    def fake_deliver(s, subject, text, html):
        if outcome["error"]:
            raise outcome["error"]
        sent.append(subject)

    monkeypatch.setattr(email_report, "deliver", fake_deliver)
    return system, sent, outcome


async def test_sends_once_after_9_pm_and_not_before(mailing):
    system, sent, _ = mailing
    r = system.reporter
    assert (await r.check(at(20, 59))).startswith("next at 21:00") and sent == []
    assert await r.check(at(21, 0)) == f"sent {r.sent_day}" and len(sent) == 1
    await r.check(at(22, 0))
    assert len(sent) == 1
    await system.drain()
    assert system.store.recent_events(5, types=["EmailReport"])[0]["ok"] is True


async def test_a_restart_does_not_send_the_day_twice(mailing):
    system, sent, _ = mailing
    await system.reporter.check(at(21, 5))
    await system.drain()
    again = DailyReporter(system)  # what a restart builds
    await again.check(at(21, 30))
    assert len(sent) == 1


async def test_a_failed_send_is_retried_a_few_times_then_left(mailing):
    system, sent, outcome = mailing
    outcome["error"] = EmailError("smtp down")
    r, t = system.reporter, at(21, 0)
    assert "failed: smtp down" in await r.check(t)
    assert (await r.check(t + 60)).startswith("retrying")
    for i in range(1, MAX_ATTEMPTS):
        await r.check(t + i * RETRY_AFTER)
    assert (await r.check(t + MAX_ATTEMPTS * RETRY_AFTER)).startswith("gave up")
    await system.drain()
    assert [e["ok"] for e in system.store.recent_events(10, types=["EmailReport"])] == [False] * MAX_ATTEMPTS
    outcome["error"] = None
    assert sent == []


async def test_nothing_is_sent_when_switched_off(mailing):
    system, sent, _ = mailing
    await system.update_settings({"email_enabled": False})
    assert await system.reporter.check(at(23)) == "off" and sent == []


async def test_journal_button_sends_any_past_day_but_not_the_future(mailing):
    system, sent, _ = mailing
    api = Api(system)
    res = await api.send_email_report("2020-01-01")
    assert res["status"] == "queued" and res["day"] == "2020-01-01"
    await next(iter(system.reporter._tasks))
    assert len(sent) == 1 and "2020-01-01" in sent[0]
    assert system.reporter.sent_day == ""  # a manual send does not replace the 9 PM one
    for bad in ("2999-01-01", "01/01/2020"):
        with pytest.raises(ApiError):
            await api.send_email_report(bad)


async def test_journal_button_needs_email_set_up(cfg, exchange):
    system = await make(cfg, exchange)
    with pytest.raises(ApiError, match="Daily Email Report"):
        await Api(system).send_email_report("")


def test_email_report_event_carries_no_address():
    fields = EmailReport(day="2026-01-01", trigger="schedule", ok=True).to_dict()
    assert not any("@" in str(v) for v in fields.values())
    assert replace(Settings(), **MAIL).email_ready
