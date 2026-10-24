"""The daily report: the trading day in a few lines, sent once at `daily_report_hour`."""

from __future__ import annotations

import time
from typing import Any

from sqlmodel import col, select

from app.core.db import session
from app.core.models import AiBrief, Decision, Structure
from app.settings import Settings
from app.trading.risk import day_start, trading_day


def _money(v: float | None) -> str:
    return "-" if v is None else f"{v:+,.2f}"


def build(settings: Settings, accounts: dict[str, Any], open_views: list[dict[str, Any]], now: float | None = None) -> str:
    tz = settings.day_timezone
    day = trading_day(tz, now)
    start = day_start(tz, day)
    with session() as s:
        closed = list(s.exec(select(Structure).where(Structure.status == "closed", col(Structure.closed_at) >= start)).all())
        decisions = list(s.exec(select(Decision).where(Decision.ts >= start)).all())
        brief = s.exec(select(AiBrief).where(AiBrief.error == "").order_by(col(AiBrief.ts).desc())).first()

    lines = [f"Daily report {day} ({tz})", ""]
    for broker, account in accounts.items():
        mine = [t for t in closed if t.broker == broker]
        pnl = sum(t.pnl or 0.0 for t in mine)
        wins = sum((t.pnl or 0) > 0 for t in mine)
        equity = account.get("equity") if isinstance(account, dict) else None
        lines.append(f"{broker.upper()}: equity {equity:,.2f}  ·  realised today {_money(pnl)}  ·  {len(mine)} closed, {wins} won"
                     if equity is not None else f"{broker.upper()}: account unavailable ({account})")
    lines.append("")
    if closed:
        lines.append("Closed today:")
        lines += [f"  {t.broker} {t.strategy} {t.underlying} ×{t.qty}: {_money(t.pnl)} — {t.close_reason}" for t in closed[:20]]
    else:
        lines.append("Nothing closed today.")
    if open_views:
        lines.append("Open now:")
        lines += [f"  {v['broker']} {v['label']} {v['underlying']} {v['expiry_label']} ×{v['qty']}: {_money((v.get('live') or {}).get('pnl'))}" for v in open_views[:20]]
    opens = sum(d.action == "open" for d in decisions)
    lines += ["", f"Auto-trader: {len(decisions)} decisions, {opens} opened, {sum(d.action == 'skip' for d in decisions)} skipped, {sum(d.action == 'error' for d in decisions)} errors"]
    if brief and brief.content and brief.ts > (now or time.time()) - 86_400:
        lines += ["", f"AI brief ({brief.model}): {brief.content.get('headline', '')}"]
    return "\n".join(lines)
