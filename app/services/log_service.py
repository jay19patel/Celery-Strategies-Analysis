"""Log reader and file retrieval service.

Provides safe access to system log files (success, errors, signals, performance)
with line-count pagination and file path resolution for downloads.
"""

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_LOG_TYPES: set[str] = {"success", "errors", "signals", "performance"}
LOGS_DIR: Path = Path(__file__).parent.parent.parent.resolve() / "logs"


class LogService:
    """Service encapsulating log retrieval and file streaming operations."""

    def __init__(self, logs_dir: Path | None = None) -> None:
        """Initialize log directory."""
        self.logs_dir = logs_dir or LOGS_DIR
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def get_logs(self, log_type: str, lines_count: int = 200) -> list[str]:
        """Fetch the last N lines of a specific log file with input sanitization."""
        clean_type = log_type.strip().lower()
        if clean_type not in ALLOWED_LOG_TYPES:
            raise ValueError(
                f"Invalid log type '{log_type}'. Allowed types: {', '.join(sorted(ALLOWED_LOG_TYPES))}"
            )

        log_file = self.logs_dir / f"{clean_type}.log"
        if not log_file.exists():
            return [f"Log file {clean_type}.log does not exist yet."]

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            return [line.strip() for line in lines[-lines_count:]]
        except Exception as exc:
            logger.exception("Error reading log file %s", clean_type)
            raise RuntimeError(f"Could not read log file: {exc}") from exc

    def get_log_file_path(self, log_type: str) -> Path:
        """Return the resolved Path for a valid log file, raising FileNotFoundError if missing."""
        clean_type = log_type.strip().lower()
        if clean_type not in ALLOWED_LOG_TYPES:
            raise ValueError(
                f"Invalid log type '{log_type}'. Allowed types: {', '.join(sorted(ALLOWED_LOG_TYPES))}"
            )

        log_file = self.logs_dir / f"{clean_type}.log"
        if not log_file.exists():
            raise FileNotFoundError(f"Log file {clean_type}.log does not exist yet.")

        return log_file

    def get_parsed_error_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Parse errors.log into discrete error records sorted reverse-chronologically (newest first).

        Args:
            limit: Maximum number of recent error events to return.

        Returns:
            List of structured error dicts with timestamp, level, source, message, and traceback.
        """
        log_file = self.logs_dir / "errors.log"
        if not log_file.exists():
            return []

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                raw_lines = f.readlines()
        except Exception as exc:
            logger.exception("Error reading errors.log for parsing")
            return []

        # Parse entries by grouping on timestamp/level headers
        entries: list[dict[str, Any]] = []
        current_header: str | None = None
        current_tb_lines: list[str] = []

        def _flush():
            nonlocal current_header, current_tb_lines
            if not current_header:
                return
            parts = [p.strip() for p in current_header.split("|")]
            ts = parts[0] if len(parts) > 0 else ""
            src = parts[1] if len(parts) > 1 else ""
            fn = parts[2] if len(parts) > 2 else ""
            lvl = parts[3] if len(parts) > 3 else "ERROR"
            msg = " | ".join(parts[4:]) if len(parts) > 4 else current_header

            tb_text = "\n".join(current_tb_lines).strip()
            entries.append({
                "timestamp": ts,
                "source": src,
                "function": fn,
                "level": lvl,
                "message": msg,
                "traceback": tb_text,
                "raw_header": current_header,
            })
            current_header = None
            current_tb_lines = []

        for line in raw_lines:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue

            # Standard logger format: 'YYYY-MM-DD HH:MM:SS | module.py:line | func() | LEVEL | message'
            if (" | ERROR | " in stripped or " | CRITICAL | " in stripped or " | WARNING | " in stripped) and len(stripped) > 20 and stripped[:4].isdigit():
                _flush()
                current_header = stripped
            elif current_header is not None:
                current_tb_lines.append(stripped)
            else:
                # Fallback line without header
                current_header = stripped

        _flush()

        # Reverse so newest errors appear at the top (#1)
        entries.reverse()
        return entries[:limit]


_log_service: LogService | None = None


def get_log_service() -> LogService:
    """Singleton accessor for LogService."""
    global _log_service
    if _log_service is None:
        _log_service = LogService()
    return _log_service
