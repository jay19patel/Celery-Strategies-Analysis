"""Trading calendar — exchange-aware scheduling helpers.

Determines whether to run strategies based on market hours, weekends, and
holidays. Crypto markets trade 24/7 so they always return True, but this
module is essential once Indian equity instruments are added.

Inspired by OpenAlgo's ``utils/trading_calendar.py`` which handles NSE/BSE
holidays, Muhurat trading sessions, MCX timings, and calendar period queries
(first/last trading day of week/month/quarter).

Usage:
    from app.utility.trading_calendar import TradingCalendar
    
    calendar = TradingCalendar()
    if calendar.should_execute("ETHUSD"):     # True (24/7 crypto)
        run_strategy(...)
    if calendar.should_execute("RELIANCE"):   # False on Saturday
        run_strategy(...)
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# NSE/BSE trading hours (IST)
_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)

# MCX trading hours (IST) — commodity segment
_MCX_OPEN = time(9, 0)
_MCX_CLOSE = time(23, 30)

# Gazetted NSE holidays for 2026 (add more years as needed)
# Source: NSE circular
_NSE_HOLIDAYS_2026: frozenset[date] = frozenset([
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 10),   # Maha Shivaratri
    date(2026, 3, 31),   # Id-Ul-Fitr (Ramadan)
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 6, 7),    # Bakri Id
    date(2026, 7, 7),    # Muharram
    date(2026, 8, 15),   # Independence Day
    date(2026, 9, 5),    # Milad-un-Nabi (Prophet's Birthday)
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 21),  # Diwali (Lakshmi Puja)
    date(2026, 10, 22),  # Diwali Balipratipada
    date(2026, 11, 5),   # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
])

# Cache for holiday sets by year
_holiday_cache: dict[int, frozenset[date]] = {
    2026: _NSE_HOLIDAYS_2026,
}


class TradingCalendar:
    """Exchange-aware scheduling for strategy execution.

    Classifies symbols by exchange type and answers questions about market
    hours, trading days, and holidays.
    """

    # Symbols that map to specific exchanges
    _CRYPTO_SYMBOLS = {"ETHUSD", "BTCUSD", "SOLUSD", "BNBUSD", "XRPUSD", "ADAUSD",
                       "DOTUSD", "LINKUSD", "MATICUSD", "AVAXUSD"}

    def classify_exchange(self, symbol: str) -> str:
        """Determine the exchange type for a symbol.

        Args:
            symbol: Instrument identifier.

        Returns:
            One of "crypto", "nse", "mcx".
        """
        upper = symbol.upper()
        if upper in self._CRYPTO_SYMBOLS or upper.endswith("USD"):
            return "crypto"
        # Default to NSE for Indian equity symbols
        return "nse"

    def should_execute(self, symbol: str) -> bool:
        """Check whether strategies should run for this symbol right now.

        Args:
            symbol: Instrument identifier.

        Returns:
            True if the market is open for this symbol.
        """
        exchange = self.classify_exchange(symbol)

        if exchange == "crypto":
            return True  # 24/7

        if exchange == "nse":
            return self.is_nse_trading_hours() and self.is_trading_day()

        if exchange == "mcx":
            return self.is_mcx_trading_hours() and self.is_trading_day()

        return True  # Unknown exchange → assume tradable

    def is_trading_day(self, day: date | None = None) -> bool:
        """Whether the given date is a trading day (not weekend, not holiday).

        Args:
            day: Date to check. Defaults to today (IST).

        Returns:
            True if the exchange is open on this date.
        """
        if day is None:
            day = datetime.now(IST).date()

        # Weekend check
        if day.weekday() >= 5:
            return False

        # Holiday check
        holidays = _holiday_cache.get(day.year, frozenset())
        if day in holidays:
            return False

        return True

    def is_nse_trading_hours(self) -> bool:
        """Whether the current time falls within NSE trading hours (IST)."""
        now = datetime.now(IST).time()
        return _NSE_OPEN <= now <= _NSE_CLOSE

    def is_mcx_trading_hours(self) -> bool:
        """Whether the current time falls within MCX trading hours (IST)."""
        now = datetime.now(IST).time()
        return _MCX_OPEN <= now <= _MCX_CLOSE

    def prev_trading_day(self, day: date | None = None) -> date:
        """The most recent trading day strictly before the given date.

        Args:
            day: Reference date. Defaults to today (IST).

        Returns:
            The previous trading day.
        """
        if day is None:
            day = datetime.now(IST).date()
        cursor = day - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        return cursor

    def next_trading_day(self, day: date | None = None) -> date:
        """The next trading day strictly after the given date.

        Args:
            day: Reference date. Defaults to today (IST).

        Returns:
            The next trading day.
        """
        if day is None:
            day = datetime.now(IST).date()
        cursor = day + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return cursor

    def describe(self, day: date | None = None) -> dict:
        """Full calendar metadata for a date — useful for dashboard display.

        Args:
            day: Date to describe. Defaults to today (IST).

        Returns:
            Dictionary with all calendar properties.
        """
        if day is None:
            day = datetime.now(IST).date()

        holidays = _holiday_cache.get(day.year, frozenset())

        return {
            "date": day.isoformat(),
            "is_trading_day": self.is_trading_day(day),
            "is_weekend": day.weekday() >= 5,
            "is_holiday": day in holidays,
            "weekday": day.strftime("%A"),
            "nse_open": self.is_nse_trading_hours(),
            "mcx_open": self.is_mcx_trading_hours(),
            "prev_trading_day": self.prev_trading_day(day).isoformat(),
            "next_trading_day": self.next_trading_day(day).isoformat(),
        }
