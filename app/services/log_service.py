"""Log reader, stats, and file retrieval service.

Provides:
  - Line-paginated access to log files (success, errors, warnings, signals, performance)
  - Structured log stats: file sizes, line counts, last-modified timestamps
  - In-process log-level counters (delegated to CountingHandler in logger.py)
  - File path resolution for direct downloads
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_LOG_TYPES: set[str] = {"success", "errors", "warnings", "signals", "performance"}
LOGS_DIR: Path = Path(__file__).parent.parent.parent.resolve() / "logs"


class LogService:
    """Service encapsulating log retrieval, stats, and file streaming operations."""

    def __init__(self, logs_dir: Path | None = None) -> None:
        """Initialize with optional log directory override."""
        self.logs_dir = logs_dir or LOGS_DIR
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Line-paginated log reader
    # ------------------------------------------------------------------

    def get_logs(self, log_type: str, lines_count: int = 200) -> list[str]:
        """Fetch the last N lines of a specific log file.

        Args:
            log_type:    One of the ALLOWED_LOG_TYPES.
            lines_count: Maximum number of tail lines to return.

        Returns:
            List of stripped log line strings.

        Raises:
            ValueError:   Unknown log type.
            RuntimeError: File read failure.
        """
        clean_type = log_type.strip().lower()
        if clean_type not in ALLOWED_LOG_TYPES:
            raise ValueError(
                f"Invalid log type '{log_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_LOG_TYPES))}"
            )

        log_file = self.logs_dir / f"{clean_type}.log"
        if not log_file.exists():
            return [f"Log file {clean_type}.log does not exist yet."]

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            return [line.strip() for line in lines[-lines_count:] if line.strip()]
        except Exception as exc:
            logger.exception("Error reading log file %s", clean_type)
            raise RuntimeError(f"Could not read log file: {exc}") from exc

    # ------------------------------------------------------------------
    # File path resolver (for downloads)
    # ------------------------------------------------------------------

    def get_log_file_path(self, log_type: str) -> Path:
        """Return the resolved Path for a valid log file.

        Raises:
            ValueError:      Unknown log type.
            FileNotFoundError: Log file not yet created.
        """
        clean_type = log_type.strip().lower()
        if clean_type not in ALLOWED_LOG_TYPES:
            raise ValueError(
                f"Invalid log type '{log_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_LOG_TYPES))}"
            )

        log_file = self.logs_dir / f"{clean_type}.log"
        if not log_file.exists():
            raise FileNotFoundError(f"Log file {clean_type}.log does not exist yet.")
        return log_file

    # ------------------------------------------------------------------
    # Structured error log parser
    # ------------------------------------------------------------------

    def get_parsed_error_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        """Parse errors.log into discrete structured error records.

        Supports both JSON Lines format (new) and legacy pipe-delimited format.
        Returns records sorted reverse-chronologically (newest first).

        Args:
            limit: Maximum number of recent error events to return.

        Returns:
            List of dicts with keys: timestamp, level, logger, file, func,
            message, traceback (may be empty string).
        """
        log_file = self.logs_dir / "errors.log"
        if not log_file.exists():
            return []

        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                raw_lines = f.readlines()
        except Exception:
            logger.exception("Error reading errors.log for parsing")
            return []

        entries: list[dict[str, Any]] = []

        # ── Try JSON Lines first (new format) ─────────────────────────────
        import json

        json_entries: list[dict[str, Any]] = []
        legacy_lines: list[str] = []

        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("{"):
                try:
                    record = json.loads(stripped)
                    json_entries.append({
                        "timestamp": record.get("ts", ""),
                        "level": record.get("level", "ERROR"),
                        "logger": record.get("logger", ""),
                        "source": record.get("file", ""),
                        "function": record.get("func", ""),
                        "message": record.get("msg", ""),
                        "traceback": record.get("exc", ""),
                    })
                    continue
                except json.JSONDecodeError:
                    pass
            legacy_lines.append(stripped)

        # ── Parse legacy pipe-delimited lines ─────────────────────────────
        current_header: str | None = None
        current_tb_lines: list[str] = []

        def _flush() -> None:
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
                "level": lvl,
                "logger": "",
                "source": src,
                "function": fn,
                "message": msg,
                "traceback": tb_text,
            })
            current_header = None
            current_tb_lines = []

        for line in legacy_lines:
            is_log_header = (
                (" | ERROR | " in line or " | CRITICAL | " in line or " | WARNING | " in line)
                and len(line) > 20
                and line[:4].isdigit()
            )
            if is_log_header:
                _flush()
                current_header = line
            elif current_header is not None:
                current_tb_lines.append(line)
            else:
                current_header = line
        _flush()

        # Merge: JSON entries (newest at top when reversed) + legacy entries
        all_entries = json_entries + entries
        all_entries.reverse()
        return all_entries[:limit]

    # ------------------------------------------------------------------
    # Log stats — file inventory + in-memory counters
    # ------------------------------------------------------------------

    def get_log_file_info(self) -> list[dict[str, Any]]:
        """Scan all known log files and return size, line count, and last-modified.

        Returns:
            List of dicts, one per log file type, sorted by size descending.
            Fields: name, log_type, path, exists, size_bytes, size_mb,
                    line_count, last_modified_iso, last_modified_epoch.
        """
        file_info: list[dict[str, Any]] = []

        for log_type in sorted(ALLOWED_LOG_TYPES):
            log_file = self.logs_dir / f"{log_type}.log"
            if not log_file.exists():
                file_info.append({
                    "name": f"{log_type}.log",
                    "log_type": log_type,
                    "exists": False,
                    "size_bytes": 0,
                    "size_mb": 0.0,
                    "line_count": 0,
                    "last_modified_iso": None,
                    "last_modified_epoch": None,
                })
                continue

            try:
                stat = log_file.stat()
                size_bytes = stat.st_size
                size_mb = round(size_bytes / (1024 * 1024), 3)
                mtime = stat.st_mtime
                last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                # Count lines efficiently without loading the whole file
                line_count = 0
                with open(log_file, "rb") as f:
                    for _ in f:
                        line_count += 1

                file_info.append({
                    "name": f"{log_type}.log",
                    "log_type": log_type,
                    "exists": True,
                    "size_bytes": size_bytes,
                    "size_mb": size_mb,
                    "line_count": line_count,
                    "last_modified_iso": last_modified,
                    "last_modified_epoch": mtime,
                })
            except Exception as exc:
                logger.warning("Could not stat log file %s: %s", log_type, exc)
                file_info.append({
                    "name": f"{log_type}.log",
                    "log_type": log_type,
                    "exists": True,
                    "size_bytes": 0,
                    "size_mb": 0.0,
                    "line_count": 0,
                    "last_modified_iso": None,
                    "last_modified_epoch": None,
                    "error": str(exc),
                })

        # Sort by size descending so largest files appear first
        file_info.sort(key=lambda x: x["size_bytes"], reverse=True)
        return file_info

    def get_log_counts(self) -> dict[str, Any]:
        """Return in-memory log-level counters from the CountingHandler.

        Returns:
            Dict: ``{debug, info, warning, error, critical, total, session_start}``
        """
        try:
            from app.core.logger import get_log_counts
            return get_log_counts()
        except Exception as exc:
            logger.warning("Could not retrieve log counts: %s", exc)
            return {
                "debug": 0, "info": 0, "warning": 0,
                "error": 0, "critical": 0, "total": 0,
                "session_start": None,
            }

    def get_log_stats(self) -> dict[str, Any]:
        """Combine in-memory level counts with per-file disk stats.

        This is the primary response payload for ``GET /api/logs/stats``.

        Returns:
            Dict with keys: ``counts`` (level counters), ``files`` (list of
            file stats), ``logs_dir`` (absolute path string).
        """
        return {
            "counts": self.get_log_counts(),
            "files": self.get_log_file_info(),
            "logs_dir": str(self.logs_dir),
        }


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_log_service: LogService | None = None


def get_log_service() -> LogService:
    """Singleton accessor for LogService."""
    global _log_service
    if _log_service is None:
        _log_service = LogService()
    return _log_service
