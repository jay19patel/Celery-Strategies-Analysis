"""The daily email: what happened today, sent by the engine once a day at `email_report_hour`.

`DailyReporter` is an engine job (System page → Jobs). Each minute it checks the clock in
`day_timezone`; after the hour it sends today's report once. A failed send is retried at most
MAX_ATTEMPTS times a day, RETRY_AFTER apart, and every attempt is an `EmailReport` event. A restart
does not send twice: the last scheduled success is read back from the event log.

The report only reads: the store, and for today each active broker's account and positions.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from tradebuddy.analytics import collect_daily_analytics, gather_live, get_day_bounds
from tradebuddy.events import EmailReport
from tradebuddy.jobs import NULL_JOB
from tradebuddy.settings import Settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_AFTER = 15 * 60
SMTP_TIMEOUT = 20
ROWS = 60  # per table in the email; the Journal page has the rest

GREEN, RED, AMBER, MUTED, INK, LINE, SOFT = "#15803d", "#b91c1c", "#b45309", "#64748b", "#0f172a", "#e2e8f0", "#f8fafc"


class EmailError(Exception):
    """The report could not be sent. The message is safe to show: it never holds the password."""


# -- render -------------------------------------------------------------------


def _e(v: Any) -> str:
    return escape("" if v is None else str(v))


def _money(v: float | None) -> str:
    return "-" if v is None else f"{'+' if v > 0 else ''}{v:,.2f}"


def _num(v: float | None) -> str:
    return "-" if v is None else f"{v:,.6g}"


def _pnl_color(v: float | None) -> str:
    return MUTED if not v else (GREEN if v > 0 else RED)


def _section(title: str, body: str, note: str = "") -> str:
    note_html = f'<div style="font-size:12px;color:{MUTED};margin:-4px 0 8px">{_e(note)}</div>' if note else ""
    return (
        f'<h2 style="font-size:14px;margin:28px 0 8px;color:{INK};text-transform:uppercase;letter-spacing:.04em">{_e(title)}</h2>'
        f"{note_html}{body}"
    )


def _table(headers: list[str], rows: list[list[str]], empty: str, right: set[int] = frozenset()) -> str:
    """rows hold ready HTML cells (already escaped)."""
    if not rows:
        return f'<p style="font-size:13px;color:{MUTED};margin:4px 0">{_e(empty)}</p>'
    th = "".join(
        f'<th style="padding:7px 8px;text-align:{"right" if i in right else "left"};font-size:11px;color:{MUTED};border-bottom:1px solid {LINE};white-space:nowrap">{_e(h)}</th>'
        for i, h in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(
            f'<td style="padding:7px 8px;text-align:{"right" if i in right else "left"};font-size:12px;border-bottom:1px solid {LINE};vertical-align:top">{c}</td>'
            for i, c in enumerate(r)
        ) + "</tr>"
        for r in rows
    )
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid {LINE}"><tr style="background:{SOFT}">{th}</tr>{body}</table>'


def _more(total: int, shown: int) -> str:
    return f'<p style="font-size:12px;color:{MUTED};margin:6px 0 0">+ {total - shown} more on the Journal page.</p>' if total > shown else ""


def _kpi(label: str, value: str, sub: str, color: str = INK) -> str:
    return (
        f'<td width="25%" style="padding:12px;border:1px solid {LINE};background:{SOFT};vertical-align:top">'
        f'<div style="font-size:11px;color:{MUTED};text-transform:uppercase">{_e(label)}</div>'
        f'<div style="font-size:20px;font-weight:700;color:{color};margin-top:4px">{_e(value)}</div>'
        f'<div style="font-size:11px;color:{MUTED};margin-top:2px">{_e(sub)}</div></td>'
    )


def _colored(v: float | None, text: str | None = None) -> str:
    return f'<b style="color:{_pnl_color(v)}">{_e(text if text is not None else _money(v))}</b>'


def render(d: dict[str, Any]) -> tuple[str, str, str]:
    """(subject, plain text, html) for one day's analytics."""
    s, n, sh = d["summary"], d["narrative"], d["system_health"]
    pnl = s["total_pnl"]
    subject = f"TradeBuddy daily report · {d['date']} · {_money(pnl)} USD · {s['total_trades']} trades, {s['total_orders']} orders"

    kpis = (
        "<tr>"
        + _kpi("Net PnL (paper)", f"{_money(pnl)}", f"fees {s['total_fees']:,.2f} · PF {s['profit_factor'] if s['profit_factor'] is not None else '-'}", _pnl_color(pnl))
        + _kpi("Win rate", f"{s['win_rate']}%", f"{s['wins_count']}W / {s['losses_count']}L of {s['total_trades']}")
        + _kpi("Orders", str(s["total_orders"]), ", ".join(f"{v} {k}" for k, v in sorted(s["order_status_counts"].items())) or "none")
        + _kpi("Signals", str(s["signals_count"]), f"{s['skipped_count']} skipped · {s['total_trailing_steps']} trails")
        + "</tr>"
    )

    brief = "".join(
        f'<li style="margin:0 0 6px">{_e(x)}</li>' for x in (n["execution"], n["trailing"], n["system"])
    )
    summary_block = (
        f'<div style="border:1px solid {LINE};border-left:4px solid #4f46e5;padding:14px 16px;background:{SOFT}">'
        f'<div style="font-size:15px;font-weight:700;color:{INK}">{_e(n["headline"])}</div>'
        f'<ul style="font-size:13px;color:#334155;margin:10px 0 0;padding-left:18px;line-height:1.5">{brief}</ul>'
        f'<div style="font-size:13px;color:{INK};margin-top:8px"><b>Next:</b> {_e(n["coach_tip"])}</div>'
        f'<div style="font-size:11px;color:{MUTED};margin-top:8px">Written by rules from the numbers below, not by AI.</div></div>'
    )

    parts = [summary_block, f'<table role="presentation" width="100%" cellpadding="0" cellspacing="6" style="margin-top:16px">{kpis}</table>']

    # Accounts and open positions: today only, read from the brokers at send time.
    if d["is_today"]:
        acc_rows = []
        for a in d["risk"]["accounts"]:
            if a.get("error"):
                acc_rows.append([_e(a["broker"]), f'<span style="color:{RED}">{_e(a["error"])}</span>', "", "", "", ""])
            else:
                acc_rows.append([
                    f"<b>{_e(a['broker'])}</b>", _e(f"{a['balance']:,.2f} {a.get('currency', '')}"), _e(f"{a['equity']:,.2f}"),
                    _colored(a.get("unrealized_pnl")), _e(f"{a.get('margin_used', 0):,.2f}"), _e(f"{a.get('available', 0):,.2f}"),
                ])
        parts.append(_section("Accounts now", _table(["Broker", "Balance", "Equity", "Unrealised", "Margin", "Available"], acc_rows, "No active broker.", {1, 2, 3, 4, 5})))
        pos_rows = [
            [
                f"<b>{_e(p['symbol'])}</b>", _e(p["broker"]), _e(p.get("strategy") or "-"), _e(p["side"]), _e(_num(p["size"])),
                _e(_num(p["entry_price"])), _e(_num(p["mark_price"])), _e(f"{_num(p.get('stop_loss'))} / {_num(p.get('take_profit'))}"), _colored(p["unrealized_pnl"]),
            ]
            for p in d["risk"]["positions"]
        ]
        parts.append(_section("Open positions now", _table(["Symbol", "Broker", "Strategy", "Side", "Size", "Entry", "Mark", "SL / TP", "Unrealised"], pos_rows, "No open positions: flat going into the night.", {4, 5, 6, 7, 8})))

    # The day, in order.
    tl = d["timeline"][-ROWS:]
    tl_rows = [
        [_e(x["time"]), f'<span style="color:{RED if x["level"] == "error" else (AMBER if x["level"] == "warning" else MUTED)}">{_e(x["type"])}</span>', _e(x["text"])]
        for x in tl
    ]
    parts.append(_section("Timeline", _table(["Time", "Event", "What happened"], tl_rows, "Nothing happened today: no signals, orders or alerts.") + _more(d["timeline_total"], len(tl)), f"All times {d['timezone']}."))

    trade_rows = [
        [
            _e(f"{t['opened']} → {t['time']}"), f"<b>{_e(t['symbol'])}</b>", _e(t["strategy"]), _e(t["side"]), _e(_num(t["size"])),
            _e(f"{_num(t['entry_price'])} → {_num(t['exit_price'])}"), _e(t["reason"]), _e(f"{t['fees']:,.2f}"), _colored(t["pnl"]),
        ]
        for t in d["trades"][:ROWS]
    ]
    parts.append(_section("Closed trades (paper)", _table(["Open → close", "Symbol", "Strategy", "Side", "Size", "Entry → exit", "Exit reason", "Fees", "PnL"], trade_rows, "No trade closed today.", {4, 7, 8}) + _more(len(d["trades"]), len(trade_rows))))

    order_rows = [
        [
            _e(o["time"]), _e(o["broker"]), _e(o["strategy"]), f"<b>{_e(o['symbol'])}</b>", _e(o["side"].upper()), _e(_num(o["size"])), _e(_num(o["price"])),
            _e(f"{_num(o.get('stop_loss'))} / {_num(o.get('take_profit'))}"),
            f'<span style="color:{GREEN if o["status"] in ("filled", "closed", "placed") else (RED if o["status"] in ("failed", "rejected") else AMBER)}">{_e(o["status"])}</span>'
            + (f'<div style="color:{RED};font-size:11px">{_e(str(o["error"])[:160])}</div>' if o.get("error") else ""),
        ]
        for o in d["orders"][:ROWS]
    ]
    parts.append(_section("Orders", _table(["Time", "Broker", "Strategy", "Symbol", "Side", "Size", "Price", "SL / TP", "Status"], order_rows, "No orders today.", {5, 6, 7}) + _more(len(d["orders"]), len(order_rows))))

    strat_rows = [
        [f"<b>{_e(p['strategy'])}</b>", _e(p["signals"]), _e(p["skipped"]), _e(p["orders"]), _e(f"{p['trades_count']} ({p['wins_count']}W/{p['losses_count']}L)"), _e(f"{p['win_rate']}%"), _colored(p["total_pnl"])]
        for p in d["strategies_performance"]
    ]
    parts.append(_section("Strategies", _table(["Strategy", "Signals", "Skipped", "Orders", "Trades", "Win rate", "PnL"], strat_rows, "No strategy produced a signal today.", {1, 2, 3, 4, 5, 6})))

    sym_rows = [[f"<b>{_e(x['symbol'])}</b>", _e(x["orders"]), _e(x["trades"]), _colored(x["pnl"])] for x in d["symbols"]]
    if sym_rows:
        parts.append(_section("Symbols", _table(["Symbol", "Orders", "Trades", "PnL"], sym_rows, "", {1, 2, 3})))

    skip_rows = [
        [_e(g["count"]), _e(g["reason"]), _e(", ".join(g["strategies"])), _e(", ".join(g["symbols"])), _e(", ".join(g["brokers"]) or "before any broker"), _e(f"{g['first']} - {g['last']}")]
        for g in d["skipped"][:ROWS]
    ]
    parts.append(_section("Why signals were skipped", _table(["Count", "Reason", "Strategies", "Symbols", "Broker", "Between"], skip_rows, "No signal was refused today."), "Every refusal to trade, grouped by its reason."))

    trail_rows = [
        [f"<b>{_e(t['symbol'])}</b>", _e(t["broker"]), _e(f"{t['steps_count']} (step {t['latest_step']}/{t['max_steps']})"), _e(f"{_num(t['initial_stop_loss'])} → {_num(t['latest_stop_loss'])}"), _e(_num(t["latest_take_profit"])), _e(f"{t['first']} - {t['last']}")]
        for t in d["trailing_actions"]
    ]
    halts = "".join(
        f'<p style="font-size:13px;color:{RED};margin:6px 0">{_e(h["time"])} · {_e(h["broker"])} hit the daily loss limit ({h["loss_pct"]:.2f}% of {h["limit_pct"]:.2f}%); closed {_e(", ".join(h["closed"]) or "nothing")}.</p>'
        for h in d["risk"]["halts"]
    )
    parts.append(_section("Risk: trailing stops and loss limit", halts + _table(["Symbol", "Broker", "Trails", "Stop loss", "Take profit", "When"], trail_rows, "No stop was trailed today.", {4})))

    ai = d["ai"]
    if ai["attempts"]:
        badge = '<span style="background:#ede9fe;color:#5b21b6;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px">TB-AI</span>'
        latest = ai["latest"]
        body = (
            f'<p style="font-size:13px;margin:4px 0">{badge} {_e(latest["time"])} · {_e(latest["health"])} · {_e(latest["headline"])}</p>' if latest
            else f'<p style="font-size:13px;color:{MUTED};margin:4px 0">No TB-AI review succeeded today.</p>'
        )
        body += f'<p style="font-size:12px;color:{MUTED};margin:4px 0">{ai["good"]} review(s) written, {ai["failed"]} failed. Advisory only: TB-AI never places an order.</p>'
        parts.append(_section("Market review (TB-AI)", body))

    proc_rows = [[_e(p["role"]), _e(f"{p['cpu_pct']}%"), _e(f"{p['memory_mb']} MB"), _e(f"{p['uptime_hours']} h")] for p in sh["processes"]]
    job_rows = [[_e(j["process"]), _e(j["name"]), _e(j["errors"]), _e(j["last_error"])] for j in sh["failing_jobs"]]
    err_rows = [[_e(x["time"]), _e(x["type"]), _e(x["text"])] for x in sh["error_samples"]]
    health = (
        f'<p style="font-size:13px;margin:4px 0">Status: <b>{_e(sh["status_label"])}</b> · {sh["system_errors_count"]} error(s), {sh["warnings_count"]} warning(s)'
        + (f", {d['outages']} live-price outage(s)" if d["outages"] else "")
        + "</p>"
        + (_table(["Process", "CPU now", "Memory", "Up"], proc_rows, "", {1, 2, 3}) if proc_rows else "")
        + (_section("Jobs with errors", _table(["Process", "Job", "Errors", "Last error"], job_rows, "")) if job_rows else "")
        + _section("Errors", _table(["Time", "Type", "What"], err_rows, "No errors today."))
    )
    parts.append(_section("System", health))

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:16px;background:#f1f5f9;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;background:#ffffff;border:1px solid {LINE}">
<tr><td style="background:#1e1b4b;color:#ffffff;padding:22px 24px">
<div style="font-size:12px;opacity:.8;text-transform:uppercase;letter-spacing:.08em">TradeBuddy · daily report</div>
<div style="font-size:22px;font-weight:700;margin-top:6px">{_e(d['date_human'])}</div>
<div style="font-size:13px;opacity:.8;margin-top:4px">Generated {_e(d['generated_at'])} {_e(d['timezone'])}</div>
</td></tr>
<tr><td style="padding:20px 24px">{''.join(parts)}</td></tr>
<tr><td style="padding:14px 24px;background:{SOFT};border-top:1px solid {LINE};font-size:11px;color:{MUTED}">
Sent by the TradeBuddy engine. PnL is realised paper PnL; Delta fills show under Orders. Change the time or recipients in Settings → Daily Email Report.
</td></tr></table></td></tr></table></body></html>"""

    text = "\n".join([
        f"TradeBuddy daily report — {d['date_human']} ({d['timezone']})",
        "",
        n["headline"], n["execution"], n["trailing"], n["system"], f"Next: {n['coach_tip']}",
        "",
        "Timeline:",
        *([f"  {x['time']}  {x['text']}" for x in tl] or ["  nothing happened"]),
        "",
        "Skipped:",
        *([f"  {g['count']}x {g['reason']}" for g in d["skipped"]] or ["  none"]),
        "",
        "Open an HTML-capable mail client for the full tables.",
    ])
    return subject, text, html


# -- deliver ------------------------------------------------------------------


def deliver(s: Settings, subject: str, text: str, html: str) -> None:
    """Send over SMTP: implicit TLS on 465, STARTTLS elsewhere. Credentials never travel unencrypted."""
    recipients = s.email_recipients
    if not s.email_smtp_host or not recipients:
        raise EmailError("set the SMTP host and at least one recipient in Settings")
    sender = s.email_smtp_user or "tradebuddy@localhost"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain="tradebuddy.local")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    host, port, user, password = s.email_smtp_host, s.email_smtp_port, s.email_smtp_user, s.email_smtp_pass
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=SMTP_TIMEOUT) as server:
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
            return
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=context)
                server.ehlo()
            elif user and password:
                raise EmailError(f"{host}:{port} does not offer STARTTLS, so the password would go unencrypted; use port 465")
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    except EmailError:
        raise
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(f"SMTP login refused ({exc.smtp_code}). For Gmail use an App Password, not the account password.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailError(f"recipient refused: {', '.join(exc.recipients)}") from exc
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        raise EmailError(f"{type(exc).__name__}: {str(exc)[:200]} ({host}:{port})") from exc


# -- schedule -----------------------------------------------------------------


class DailyReporter:
    """Engine job: today's report by email once a day, at email_report_hour in day_timezone."""

    def __init__(self, system: Any) -> None:
        self.system = system
        self.job = NULL_JOB
        self.every = 60.0
        self._lock = asyncio.Lock()  # one report at a time: schedule and the Journal button
        self._attempts: dict[str, list[float]] = {}  # day -> scheduled attempt times
        self._tasks: set[asyncio.Task] = set()
        self.sent_day = self._last_scheduled_success()

    def _last_scheduled_success(self) -> str:
        for e in self.system.store.recent_events(50, types=["EmailReport"]):
            if e.get("ok") and e.get("trigger") == "schedule":
                return str(e.get("day") or "")
        return ""

    async def run(self) -> None:
        while True:
            try:
                with self.job.tick() as job:
                    job.note = await self.check()
            except Exception:
                log.exception("email_report_check_failed")
            await asyncio.sleep(self.every)

    async def check(self, now: float | None = None) -> str:
        """Send today's report if it is due. Returns what it did, for the Jobs table."""
        s = self.system.settings
        if not s.email_enabled:
            return "off"
        if not s.email_ready:
            return "not configured: recipient or SMTP host missing"
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, ZoneInfo(s.day_timezone))
        day = local.date().isoformat()
        if self.sent_day == day:
            return f"sent {day}"
        if local.hour < s.email_report_hour:
            return f"next at {s.email_report_hour:02d}:00 {s.day_timezone}"
        attempts = self._attempts.setdefault(day, [])
        if len(attempts) >= MAX_ATTEMPTS:
            return f"gave up on {day} after {MAX_ATTEMPTS} attempts; send it from the Journal page"
        if attempts and now - attempts[-1] < RETRY_AFTER:
            return f"retrying {day} at {datetime.fromtimestamp(attempts[-1] + RETRY_AFTER, local.tzinfo):%H:%M}"
        attempts.append(now)
        self._attempts = {day: attempts}  # forget earlier days
        result = await self.send(day, "schedule")
        return f"sent {day}" if result.ok else f"attempt {len(attempts)}/{MAX_ATTEMPTS} failed: {result.error}"

    def send_soon(self, day: str) -> None:
        """The Journal button: send in the background (SMTP can outlast an RPC); the result is an EmailReport event."""
        task = asyncio.create_task(self.send(day, "manual"), name="email-report")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def send(self, day: str, trigger: str) -> EmailReport:
        async with self._lock:
            s = self.system.settings
            try:
                today = get_day_bounds(None, s.day_timezone)[2]
                live = await gather_live(self.system) if day == today else None
                data = await asyncio.to_thread(collect_daily_analytics, self.system.store, day, s.day_timezone, live)
                subject, text, html = render(data)
                await asyncio.to_thread(deliver, s, subject, text, html)
                event = EmailReport(day=day, trigger=trigger, ok=True)
                if trigger == "schedule":
                    self.sent_day = day
                log.info("email_report_sent day=%s trigger=%s recipients=%d", day, trigger, len(s.email_recipients))
            except EmailError as exc:
                event = EmailReport(day=day, trigger=trigger, ok=False, error=str(exc))
                log.warning("email_report_failed day=%s trigger=%s error=%s", day, trigger, exc)
            except Exception as exc:
                event = EmailReport(day=day, trigger=trigger, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                log.exception("email_report_failed day=%s trigger=%s", day, trigger)
            self.system.bus.publish(event)
            return event
