from datetime import datetime, UTC
from app.database.sqlite_db import get_sqlite_db

db = get_sqlite_db()
now_iso = datetime.now(UTC).isoformat()
db.execute_modify("""
    INSERT OR IGNORE INTO strategy_configs (strategy_id, name, symbols, timeframe, is_paper_enabled, is_real_enabled, created_at, updated_at)
    VALUES ('DummyHeavyStrategy', 'Dummy Heavy Strategy', 'BTC-USD', '1m', 1, 0, ?, ?);
""", (now_iso, now_iso))
print("Inserted DummyHeavyStrategy into strategy_configs")
