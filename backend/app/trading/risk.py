"""The trading day, each broker's daily loss halt, and the rules every entry answers to beyond its
own broker's gate:

  kill switch     "Stop all" (or the drawdown limit) refuses every entry on every broker, manual
                  included, until it is lifted by hand.
  reconciliation  a Delta account whose positions do not match the database takes no new entry.
  one per symbol  automatic entries hold at most one position per underlying per broker, across
                  every family: an option structure and a perpetual on BTC are one bet, not two.
  heat            the worst cases of a broker's open automatic positions, plus the new one, stay
                  within `MAX_HEAT_PCT` of its equity.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.db import kv_get, kv_set, session
from app.core.models import DayRisk

MAX_HEAT_PCT = 4.0
KILL_KEY = "risk:kill"
AUTO_SOURCES = ("auto",)


def trading_day(tz: str, now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo(tz)).strftime("%Y-%m-%d")


def day_start(tz: str, day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz)).timestamp()


def day_record(broker: str, tz: str) -> DayRisk | None:
    with session() as s:
        return s.get(DayRisk, (broker, trading_day(tz)))


def halt_reason(broker: str, tz: str) -> str:
    row = day_record(broker, tz)
    return row.halted if row else ""


def start_day(broker: str, tz: str, equity: float) -> DayRisk:
    with session() as s:
        row = s.get(DayRisk, (broker, trading_day(tz)))
        if row is None:
            row = DayRisk(broker=broker, day=trading_day(tz), start_equity=equity)
            s.add(row)
            s.commit()
        return row


def set_halt(broker: str, tz: str, reason: str, equity: float | None = None) -> None:
    """Halt (reason) or resume (""): a resume restarts the day from `equity`."""
    with session() as s:
        row = s.get(DayRisk, (broker, trading_day(tz)))
        if row is None:
            return
        row.halted = reason
        if equity is not None:
            row.start_equity = equity
        s.add(row)
        s.commit()


# -- kill switch ----------------------------------------------------------------------------------


def kill_state() -> dict[str, Any] | None:
    """{reason, at} while every entry is stopped, else None. Kept in the database: a restart does
    not lift it."""
    state = kv_get(KILL_KEY)
    return state if isinstance(state, dict) and state.get("reason") else None


def set_kill(reason: str) -> None:
    kv_set(KILL_KEY, {"reason": reason, "at": time.time()} if reason else None)


# -- reconciliation -------------------------------------------------------------------------------


def reconcile_reason(broker: str) -> str:
    return str(kv_get(f"risk:reconcile:{broker}") or "")


def set_reconcile(broker: str, reason: str) -> None:
    kv_set(f"risk:reconcile:{broker}", reason or None)


# -- portfolio ------------------------------------------------------------------------------------


def worst_case(st: Any) -> float:
    """What an open position can lose, as it was sized: its max loss, else the margin it holds."""
    return float(st.max_loss if st.max_loss is not None else st.margin or 0.0)


def portfolio_refusal(held: list[Any], underlying: str, new_risk: float, equity: float) -> str:
    """Why an automatic entry on `underlying` risking `new_risk` breaks the broker's portfolio rules
    (one position per underlying, the heat cap), or "". `held` is the broker's open structures."""
    auto = [st for st in held if st.source in AUTO_SOURCES]
    if same := next((st for st in auto if st.underlying == underlying), None):
        return f"already holds an automatic {underlying} position ({same.strategy}): one per underlying"
    heat = sum(worst_case(st) for st in auto) + max(0.0, new_risk)
    if equity <= 0 or 100 * heat / equity > MAX_HEAT_PCT:
        return f"open automatic risk would be {heat:,.2f}, over {MAX_HEAT_PCT:g}% of equity {equity:,.2f}"
    return ""
