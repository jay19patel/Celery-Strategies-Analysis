"""SQLite persistence for the paper-position protection & exit audit trail.

Stores one append-only row per stoploss/target change, position open, and
position close in `position_protection_events`, keyed by `position_id` so
a single position's full lifetime can be fetched as one ordered history.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from app.database.sqlite_db import get_sqlite_db

logger = logging.getLogger(__name__)


def record_event(
    position_id: str,
    strategy_name: str,
    symbol: str,
    event_type: str,
    changed_by: str,
    previous_stop_price: float | None = None,
    previous_target_price: float | None = None,
    new_stop_price: float | None = None,
    new_target_price: float | None = None,
    reference_price: float | None = None,
    reason: str | None = None,
) -> None:
    """Appends one protection/exit audit event. Never raises — a logging failure
    must not roll back a trading operation that already succeeded."""
    try:
        db = get_sqlite_db()
        db.execute_modify(
            """
            INSERT INTO position_protection_events (
                position_id, strategy_name, symbol, event_type, changed_by,
                previous_stop_price, previous_target_price, new_stop_price, new_target_price,
                reference_price, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                position_id,
                strategy_name,
                symbol,
                event_type,
                changed_by,
                previous_stop_price,
                previous_target_price,
                new_stop_price,
                new_target_price,
                reference_price,
                reason,
                datetime.now(UTC).isoformat(),
            ),
        )
    except Exception:
        logger.exception(
            "position_event_record_failed position_id=%s event_type=%s", position_id, event_type
        )


def get_events_by_position(position_id: str) -> list[dict[str, Any]]:
    """Returns the full ordered event history for one position instance."""
    db = get_sqlite_db()
    return db.execute_query(
        "SELECT * FROM position_protection_events WHERE position_id = ? ORDER BY id ASC;",
        (position_id,),
    )
