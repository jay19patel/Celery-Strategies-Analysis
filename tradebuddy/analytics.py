"""One local day of trading, read back from the store: the Journal page and the daily email.

Everything here is read-only. Numbers come from the permanent tables (orders, paper_trades) and the
event log; nothing is estimated. Process load is not stored (heartbeats are never written), so it
is only known for today, from the live snapshot the engine passes in.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tradebuddy.codec import EVENT_TYPES
from tradebuddy.runner import PAUSED
from tradebuddy.store import Store

log = logging.getLogger(__name__)

# Event types that tell the day's story. TradeSkipped is summarised by reason, not listed one by one.
STORY_TYPES = (
    "SignalGenerated", "TradeSkipped", "StrategyError", "OrderPlaced", "OrderFailed", "OrderUnknown",
    "PositionClosed", "ProtectionTrailed", "DailyLossHalt", "GuardAlert", "ToggleChanged", "SettingsChanged",
    "EmailReport",
)
ERROR_TYPES = sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL == "error")
# Warnings other than skips, which have their own section.
WARNING_TYPES = sorted(n for n, cls in EVENT_TYPES.items() if cls.LEVEL == "warning" and n != "TradeSkipped")
MAX_STORY_EVENTS = 20_000  # one day; far above what strategies x symbols x bars produce
TIMELINE_LIMIT = 300


def get_day_bounds(date_str: str | None = None, tz_name: str = "Asia/Kolkata") -> tuple[float, float, str]:
    """(start_ts, end_ts, YYYY-MM-DD) of one local day. Midnight to midnight, so a DST day is 23 or 25 hours."""
    tz = ZoneInfo(tz_name)
    day = parse_day(date_str) or datetime.now(tz).date()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start.timestamp(), end.timestamp(), day.isoformat()


def parse_day(value: str | None) -> date | None:
    """A YYYY-MM-DD string as a date, or None when blank. Raises ValueError on anything else."""
    if not value or not value.strip():
        return None
    return date.fromisoformat(value.strip())


def _money(v: float | None) -> str:
    return "-" if v is None else f"{'+' if v > 0 else ''}{v:,.2f}"


def _num(v: float | None) -> str:
    return "-" if v is None else f"{v:,.6g}"


def _short(text: Any, n: int = 240) -> str:
    text = str(text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def describe(e: dict[str, Any], orders: dict[str, dict[str, Any]]) -> str:
    """One line for the timeline."""
    t = e["type"]
    if t == "SignalGenerated":
        return f"{e['strategy']} signalled {e['side'].upper()} {e['symbol']}: {_short(e.get('reason'))}"
    if t in ("OrderPlaced", "OrderFailed", "OrderUnknown"):
        o = orders.get(e.get("client_order_id", ""), {})
        what = f"{o.get('side', '?').upper()} {_num(o.get('size'))} {o.get('symbol', '?')} @ {_num(o.get('price'))} on {o.get('broker', '?')} ({o.get('strategy', '?')})"
        if t == "OrderPlaced":
            return f"Order placed: {what}, {e.get('status', '')}"
        if t == "OrderFailed":
            return f"Order failed: {what}: {_short(e.get('error'))}"
        return f"Order outcome unknown, being looked up: {what}: {_short(e.get('error'))}"
    if t == "PositionClosed":
        return (
            f"Closed {e['side']} {e['symbol']} on {e['broker']} ({e['strategy']}): {_num(e['entry_price'])} → {_num(e['exit_price'])}, "
            f"PnL {_money(e['pnl'])}, {_short(e.get('reason'), 80)}"
        )
    if t == "ProtectionTrailed":
        return (
            f"Trailed {e['symbol']} on {e['broker']}, step {e['step']}/{e['max_steps']} at {_num(e['price'])}: "
            f"SL {_num(e.get('old_stop_loss'))} → {_num(e['stop_loss'])}, TP {_num(e.get('old_take_profit'))} → {_num(e['take_profit'])}"
        )
    if t == "DailyLossHalt":
        return (
            f"{e['broker']} hit the daily loss limit ({e['loss_pct']:.2f}% of {e['limit_pct']:.2f}%): "
            f"closed {', '.join(e.get('closed') or []) or 'nothing'}, no new entries today"
        )
    if t == "GuardAlert":
        return f"Guard alert on {e['broker']} {e['symbol']}: {_short(e.get('message'))}"
    if t == "StrategyError":
        return f"{e['strategy']} failed on {e['symbol']}: {_short(e.get('error'))}"
    if t == "ToggleChanged":
        return f"{e['key']} switched {'on' if e['enabled'] else 'off'}"
    if t == "SettingsChanged":
        stopped = ", Delta trading stopped" if e.get("trading_stopped") else ""
        return f"Settings changed: {', '.join(e.get('changed') or [])}{stopped}"
    if t == "EmailReport":
        return f"Report for {e['day']} emailed ({e['trigger']})" if e.get("ok") else f"Report email for {e['day']} failed: {_short(e.get('error'))}"
    return t


def synthesize_narrative(d: dict[str, Any]) -> dict[str, Any]:
    """The day in plain sentences, from the numbers. Rule-based: no AI writes this."""
    s, sh = d["summary"], d["system_health"]
    pnl = s["total_pnl"]
    word = "profit" if pnl > 0 else ("loss" if pnl < 0 else "flat")
    headline = (
        f"{_money(pnl)} USD net {word} from {s['total_trades']} closed trade(s); "
        f"{s['signals_count']} signal(s), {s['total_orders']} order(s), {s['skipped_count']} skipped."
    )

    counts = s["order_status_counts"]
    if not s["total_orders"]:
        execution = "No orders were sent today." if not s["signals_count"] else "Signals came, but none became an order (see Skipped)."
    else:
        failed = sum(v for k, v in counts.items() if k in ("failed", "rejected"))
        execution = f"{s['total_orders']} order(s): " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + "."
        if failed:
            execution += f" {failed} did not go through; check the errors below."
    perf = [p for p in d["strategies_performance"] if p["trades_count"]]
    if perf:
        best = max(perf, key=lambda p: p["total_pnl"])
        worst = min(perf, key=lambda p: p["total_pnl"])
        execution += f" Best strategy: {best['strategy']} ({_money(best['total_pnl'])}, {best['wins_count']}/{best['trades_count']} won)."
        if worst["strategy"] != best["strategy"]:
            execution += f" Weakest: {worst['strategy']} ({_money(worst['total_pnl'])})."

    steps = s["total_trailing_steps"]
    if steps:
        parts = [f"{t['symbol']} on {t['broker']} {t['steps_count']}x (SL now {_num(t['latest_stop_loss'])})" for t in d["trailing_actions"]]
        trailing = f"Stops were trailed {steps} time(s): " + "; ".join(parts) + "."
    else:
        trailing = "No stop was trailed today."
    halts = d["risk"]["halts"]
    if halts:
        trailing += " Daily loss limit hit on " + ", ".join(h["broker"] for h in halts) + "."

    errors, warnings = sh["system_errors_count"], sh["warnings_count"]
    system = f"{errors} error(s) and {warnings} warning(s) in the event log."
    if d["outages"]:
        system += f" Live prices dropped {d['outages']} time(s), strategies paused until they came back."
    if sh["measured"]:
        system += f" Now: CPU {sh['peak_cpu_pct']}%, memory {sh['peak_rss_mb']} MB, worst loop lag {sh['max_loop_lag_ms']} ms."

    if s["total_trades"] and pnl > 0 and s["win_rate"] >= 50:
        tip = "A good day: keep the risk settings as they are and don't force extra trades."
    elif pnl < 0:
        tip = "A losing day: check whether the stop-outs fit each strategy's plan before changing anything."
    elif s["skipped_count"] and not s["total_orders"]:
        tip = "Signals were skipped: read the skip reasons, they show what blocked the trades."
    else:
        tip = "A quiet day. Nothing needs changing."
    if errors:
        tip += " Look at the errors first."

    return {
        "headline": headline, "execution": execution, "trailing": trailing, "system": system, "coach_tip": tip,
        "full_story": " ".join((headline, execution, trailing, system, tip)),
    }


def _system_health(errors: list[dict[str, Any]], warnings_count: int, by_type: dict[str, int], live: dict[str, Any] | None) -> dict[str, Any]:
    metrics = (live or {}).get("metrics") or {}
    procs = metrics.get("processes") or []
    cpu = [float(p["process_cpu_pct"]) for p in procs if p.get("process_cpu_pct") is not None]
    mem = [float(p["process_memory_mb"]) for p in procs if p.get("process_memory_mb") is not None]
    lag = [float(p["loop_lag_max_ms"]) for p in procs if p.get("loop_lag_max_ms") is not None]
    measured = bool(cpu)
    now = time.time()
    failing_jobs = [
        {"process": j.get("process"), "name": j.get("name"), "errors": j.get("errors"), "last_error": _short(j.get("last_error"), 160)}
        for j in metrics.get("jobs") or []
        if j.get("state") in ("failed", "silent") or (j.get("errors") and now - (j.get("last_error_at") or 0) < 86_400)
    ]
    peak_cpu = round(max(cpu), 1) if measured else None
    max_lag = round(max(lag), 1) if lag else None
    label = "Optimal"
    if len(errors) > 10 or (peak_cpu or 0) > 80 or (max_lag or 0) > 200:
        label = "Needs attention"
    elif errors or failing_jobs or (peak_cpu or 0) > 50 or (max_lag or 0) > 50:
        label = "Watch"
    return {
        "measured": measured,  # process load is known only for today, from the engine's live snapshot
        "peak_cpu_pct": peak_cpu,
        "avg_cpu_pct": round(sum(cpu) / len(cpu), 1) if measured else None,
        "peak_rss_mb": round(max(mem), 1) if mem else None,
        "max_loop_lag_ms": max_lag,
        "processes": [
            {"role": p.get("role"), "cpu_pct": p.get("process_cpu_pct"), "memory_mb": p.get("process_memory_mb"), "uptime_hours": round((p.get("uptime_seconds") or 0) / 3600, 1)}
            for p in procs
        ],
        "failing_jobs": failing_jobs,
        "system_errors_count": len(errors),
        "warnings_count": warnings_count,
        "events_by_type": by_type,
        "error_samples": errors[-20:][::-1],
        "status_label": label,
    }


def collect_daily_analytics(
    store: Store,
    date_str: str | None = None,
    tz_name: str = "Asia/Kolkata",
    live: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything that happened on one local day. `live` (accounts, positions, metrics) only for today."""
    start_ts, end_ts, day = get_day_bounds(date_str, tz_name)
    tz = ZoneInfo(tz_name)
    hhmm = lambda ts: datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S") if ts else "-"  # noqa: E731

    trades = [dict(r) for r in store.db.execute(
        "SELECT * FROM paper_trades WHERE closed_at >= ? AND closed_at < ? ORDER BY closed_at", (start_ts, end_ts),
    )]
    orders = [dict(r) for r in store.db.execute(
        "SELECT * FROM orders WHERE ts >= ? AND ts < ? ORDER BY ts", (start_ts, end_ts),
    )]
    rows = store.db.execute(
        f"SELECT ts, type, data FROM events WHERE ts >= ? AND ts < ? AND type IN ({','.join('?' * len(STORY_TYPES))}) ORDER BY ts LIMIT ?",  # noqa: S608 - placeholders only
        (start_ts, end_ts, *STORY_TYPES, MAX_STORY_EVENTS),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for r in rows:
        try:
            events.append(json.loads(r["data"]) | {"ts": r["ts"], "type": r["type"]})
        except (TypeError, ValueError):
            log.warning("unreadable %s event at %s", r["type"], r["ts"])
    by_type = {r["type"]: r["n"] for r in store.db.execute(
        "SELECT type, COUNT(*) AS n FROM events WHERE ts >= ? AND ts < ? GROUP BY type", (start_ts, end_ts),
    )}

    # Orders an event refers to may be from an earlier day (e.g. a fill confirmed after midnight).
    order_by_id = {o["client_order_id"]: o for o in orders}
    missing = {e["client_order_id"] for e in events if e.get("client_order_id") and e["client_order_id"] not in order_by_id}
    if missing:
        ids = list(missing)[:500]
        order_by_id |= {r["client_order_id"]: dict(r) for r in store.db.execute(
            f"SELECT * FROM orders WHERE client_order_id IN ({','.join('?' * len(ids))})", ids,  # noqa: S608 - placeholders only
        )}

    for t in trades:
        t["time"] = hhmm(t["closed_at"])
        t["opened"] = hhmm(t.get("opened_at"))
    for o in orders:
        o["time"] = hhmm(o["ts"])

    # -- trades and strategies -------------------------------------------------
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]  # same split as the Paper page
    gross_win, gross_loss = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    signals = [e for e in events if e["type"] == "SignalGenerated"]
    skipped = [e for e in events if e["type"] == "TradeSkipped"]

    names = sorted({t["strategy"] for t in trades} | {o["strategy"] for o in orders} | {e["strategy"] for e in signals})
    perf = []
    for name in names:
        st = [t for t in trades if t["strategy"] == name]
        sw = [t for t in st if t["pnl"] > 0]
        perf.append({
            "strategy": name,
            "signals": sum(1 for e in signals if e["strategy"] == name),
            "orders": sum(1 for o in orders if o["strategy"] == name),
            "skipped": sum(1 for e in skipped if e["strategy"] == name),
            "trades_count": len(st), "wins_count": len(sw), "losses_count": len(st) - len(sw),
            "win_rate": round(100 * len(sw) / len(st), 1) if st else 0.0,
            "total_pnl": round(sum(t["pnl"] for t in st), 4),
            "fees": round(sum(t["fees"] for t in st), 4),
        })
    perf.sort(key=lambda p: (-p["trades_count"], -p["orders"], -p["signals"], p["strategy"]))

    symbols: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "orders": 0})
    for t in trades:
        symbols[t["symbol"]]["trades"] += 1
        symbols[t["symbol"]]["pnl"] = round(symbols[t["symbol"]]["pnl"] + t["pnl"], 4)
    for o in orders:
        symbols[o["symbol"]]["orders"] += 1

    # -- skips, grouped by why --------------------------------------------------
    groups: dict[str, dict[str, Any]] = {}
    for e in skipped:
        g = groups.setdefault(e["reason"], {"reason": e["reason"], "count": 0, "first": e["ts"], "last": e["ts"], "strategies": set(), "symbols": set(), "brokers": set()})
        g["count"] += 1
        g["last"] = e["ts"]
        g["strategies"].add(e["strategy"])
        g["symbols"].add(e["symbol"])
        if e.get("broker"):
            g["brokers"].add(e["broker"])
    skip_groups = [
        g | {"first": hhmm(g["first"]), "last": hhmm(g["last"]), "strategies": sorted(g["strategies"]), "symbols": sorted(g["symbols"]), "brokers": sorted(g["brokers"])}
        for g in sorted(groups.values(), key=lambda g: -g["count"])
    ]
    # One PAUSED skip per strategy x symbol per outage, all within moments: a gap of a minute starts a new outage.
    paused = [e["ts"] for e in skipped if e["reason"] == PAUSED]
    outages = sum(1 for i, ts in enumerate(paused) if i == 0 or ts - paused[i - 1] > 60)

    # -- trailing and risk ------------------------------------------------------
    trails: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e["type"] == "ProtectionTrailed":
            trails[(e.get("broker", "?"), e["symbol"])].append(e)
    trailing_actions = [
        {
            "broker": broker, "symbol": sym, "steps_count": len(evs),
            "max_steps": evs[-1].get("max_steps"), "latest_step": evs[-1].get("step"),
            "initial_stop_loss": evs[0].get("old_stop_loss"), "latest_stop_loss": evs[-1].get("stop_loss"),
            "latest_take_profit": evs[-1].get("take_profit"), "price_at_last_trail": evs[-1].get("price"),
            "first": hhmm(evs[0]["ts"]), "last": hhmm(evs[-1]["ts"]),
        }
        for (broker, sym), evs in trails.items()
    ]
    halts = [
        {"broker": e["broker"], "time": hhmm(e["ts"]), "loss_pct": e["loss_pct"], "limit_pct": e["limit_pct"], "closed": e.get("closed") or []}
        for e in events if e["type"] == "DailyLossHalt"
    ]

    # -- timeline and problems --------------------------------------------------
    story = [e for e in events if e["type"] != "TradeSkipped"]
    timeline = [
        {"ts": e["ts"], "time": hhmm(e["ts"]), "type": e["type"], "level": e.get("level", "info"), "text": describe(e, order_by_id)}
        for e in story[-TIMELINE_LIMIT:]
    ]
    errors = [
        {"ts": e["ts"], "time": hhmm(e["ts"]), "type": e["type"], "text": describe(e, order_by_id)}
        for e in events if e["type"] in ERROR_TYPES
    ]
    warnings_count = sum(n for t, n in by_type.items() if t in WARNING_TYPES)

    # -- TB-AI reviews of the day (written by Mistral, so labelled TB-AI wherever shown) --
    ai_rows = store.ai_reports(start_ts, end_ts, limit=2000)
    good = [r for r in ai_rows if r["ok"]]
    ai = {
        "attempts": len(ai_rows), "good": len(good), "failed": len(ai_rows) - len(good),
        "latest": {"time": hhmm(good[0]["ts"]), "headline": good[0].get("headline") or "", "health": good[0].get("health") or "", "model": good[0].get("model") or ""} if good else None,
    }

    live = live or {}
    data: dict[str, Any] = {
        "date": day,
        "date_human": datetime.fromtimestamp(start_ts, tz).strftime("%A, %d %B %Y"),
        "timezone": tz_name,
        "generated_at": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S"),
        "is_today": bool(live),
        "summary": {
            "total_pnl": round(gross_win - gross_loss, 4),
            "gross_profit": round(gross_win, 4),
            "gross_loss": round(gross_loss, 4),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "total_trades": len(trades),
            "wins_count": len(wins),
            "losses_count": len(losses),
            "win_rate": round(100 * len(wins) / len(trades), 1) if trades else 0.0,
            "total_fees": round(sum(t["fees"] for t in trades), 4),
            "best_trade": round(max((t["pnl"] for t in trades), default=0.0), 4),
            "worst_trade": round(min((t["pnl"] for t in trades), default=0.0), 4),
            "total_orders": len(orders),
            "order_status_counts": dict(Counter(o["status"] for o in orders)),
            "orders_by_broker": dict(Counter(o["broker"] for o in orders)),
            "signals_count": len(signals),
            "skipped_count": len(skipped),
            "total_trailing_steps": sum(t["steps_count"] for t in trailing_actions),
        },
        "strategies_performance": perf,
        "symbols": [{"symbol": k, **v} for k, v in sorted(symbols.items())],
        "trailing_actions": trailing_actions,
        "skipped": skip_groups,
        "outages": outages,
        "risk": {"halts": halts, "accounts": live.get("accounts") or [], "positions": live.get("positions") or []},
        "ai": ai,
        "timeline": timeline,
        "timeline_total": len(story),
        "system_health": _system_health(errors, warnings_count, by_type, live),
        "trades": trades[::-1],  # newest first
        "orders": orders[::-1],
    }
    data["narrative"] = synthesize_narrative(data)
    return data


async def gather_live(system: Any) -> dict[str, Any]:
    """Engine side, for today's report: each active broker's account and open positions, and process load.
    A broker that does not answer is reported as such; it never stops the report."""
    accounts, positions = [], []
    for name, broker in system.active.items():
        try:
            accounts.append((await broker.account()).to_dict())
        except Exception as exc:
            accounts.append({"broker": name, "error": _short(exc, 160)})
        try:
            positions += [p.to_dict() for p in await broker.positions()]
        except Exception as exc:
            log.warning("report: %s positions unavailable: %s", name, exc)
    return {"accounts": accounts, "positions": positions, "metrics": system.metrics()}


def collect_monthly_calendar_data(store: Store, year: int, month: int, tz_name: str = "Asia/Kolkata") -> dict[str, dict[str, Any]]:
    """PnL, trades and orders for every day of a month that had any."""
    tz = ZoneInfo(tz_name)
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=tz)
    span = (start.timestamp(), end.timestamp())
    days: dict[str, dict[str, Any]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "orders": 0})
    for r in store.db.execute("SELECT closed_at, pnl FROM paper_trades WHERE closed_at >= ? AND closed_at < ?", span):
        d = days[datetime.fromtimestamp(r["closed_at"], tz).date().isoformat()]
        d["pnl"] = round(d["pnl"] + r["pnl"], 2)
        d["trades"] += 1
    for r in store.db.execute("SELECT ts FROM orders WHERE ts >= ? AND ts < ?", span):
        days[datetime.fromtimestamp(r["ts"], tz).date().isoformat()]["orders"] += 1
    return dict(days)
