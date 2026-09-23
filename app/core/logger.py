#!/usr/bin/env python3
"""
Production-Grade Centralized Logging System.

Architecture:
  - Console handler  -> deterministic one-line format (for `docker logs`)
  - success.log      → JSON Lines, DEBUG–WARNING       (10 MB rotating, 5 backups)
  - errors.log       → JSON Lines, ERROR+CRITICAL      (10 MB rotating, 5 backups)
  - warnings.log     → JSON Lines, WARNING only        (10 MB rotating, 3 backups)
  - signals.log      -> JSON Lines signal audit events (10 MB rotating, 5 backups)
  - performance.log  -> JSON Lines timing events       (10 MB rotating, 5 backups)
  - CountingHandler  → in-memory level counters        (zero I/O overhead)

Usage:
    from app.core.logger import get_logger, get_log_counts, reset_log_counts

    logger = get_logger("my_module")
    logger.info("Trade placed", extra={"symbol": "BTCUSD", "qty": 1})

    counts = get_log_counts()
    # → {"debug": 0, "info": 412, "warning": 23, "error": 7, "critical": 0, "total": 442}
"""

import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# CountingHandler — zero-overhead in-memory level counter
# ---------------------------------------------------------------------------

class CountingHandler(logging.Handler):
    """Intercepts every log record and increments a per-level counter.

    Thread-safe via RLock. Zero I/O — never writes anywhere.
    Reset with :func:`reset_log_counts`.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self._lock = threading.RLock()
        self._counts: Dict[str, int] = {
            "debug": 0,
            "info": 0,
            "warning": 0,
            "error": 0,
            "critical": 0,
        }
        self._session_start: str = datetime.now(timezone.utc).isoformat()

    def emit(self, record: logging.LogRecord) -> None:
        level = record.levelname.lower()
        with self._lock:
            if level in self._counts:
                self._counts[level] += 1

    def get_counts(self) -> Dict[str, Any]:
        """Return a copy of current level counts plus total and session start."""
        with self._lock:
            counts = dict(self._counts)
        counts["total"] = sum(counts.values())
        counts["session_start"] = self._session_start
        return counts

    def reset(self) -> None:
        """Reset all counters and refresh session start timestamp."""
        with self._lock:
            for key in self._counts:
                self._counts[key] = 0
            self._session_start = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# JSON Lines formatter — machine-parseable, grep/jq friendly
# ---------------------------------------------------------------------------

class JsonLinesFormatter(logging.Formatter):
    """Format each log record as a single JSON object on one line.

    Fields:
        ts       — ISO-8601 timestamp with timezone
        level    — DEBUG / INFO / WARNING / ERROR / CRITICAL
        logger   — logger hierarchy name  (e.g. stockanalysis.tasks)
        file     — source filename
        line     — line number
        func     — function name
        msg      — log message string
        exc      — exception text (only when exc_info is set)
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "file": record.filename,
            "line": record.lineno,
            "func": record.funcName,
            "msg": record.getMessage(),
        }

        # Attach any extra fields passed via `extra={...}`
        _standard_attrs = {
            "args", "asctime", "created", "exc_info", "exc_text", "filename",
            "funcName", "id", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "thread", "threadName",
        }
        for key, val in record.__dict__.items():
            if key not in _standard_attrs and not key.startswith("_"):
                try:
                    json.dumps(val)  # check serializability
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = str(val)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Colored console formatter — human-readable, `docker logs` friendly
# ---------------------------------------------------------------------------

class ConsoleFormatter(logging.Formatter):
    """Deterministic single-line console output suitable for containers."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        msg = record.getMessage()
        base = f"{ts} | {record.levelname:8s} | {record.name} | {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ---------------------------------------------------------------------------
# StockAnalysisLogger — singleton setup
# ---------------------------------------------------------------------------

class StockAnalysisLogger:
    """Singleton logging system for the trading engine.

    Initializes all handlers exactly once across the entire process lifetime.
    Thread-safe via double-checked locking.
    """

    _instance: Optional["StockAnalysisLogger"] = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> "StockAnalysisLogger":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._setup_logging()
                    self._initialized = True

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        """Configure all handlers and attach to root stockanalysis logger."""
        self.log_dir = Path(__file__).parent.parent.parent / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("stockanalysis")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        self.logger.propagate = False

        json_fmt = JsonLinesFormatter()
        console_fmt = ConsoleFormatter()
        # ── CountingHandler (must be first — catches everything) ──────────
        self.counting_handler = CountingHandler()
        self.logger.addHandler(self.counting_handler)

        # ── Console (INFO+, human-readable colored) ────────────────────────
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_fmt)
        self.logger.addHandler(console_handler)

        # ── success.log (DEBUG–WARNING, JSON Lines) ────────────────────────
        class _BelowError(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return record.levelno < logging.ERROR

        success_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "success.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        success_handler.setLevel(logging.DEBUG)
        success_handler.addFilter(_BelowError())
        success_handler.setFormatter(json_fmt)
        self.logger.addHandler(success_handler)

        # ── errors.log (ERROR+CRITICAL, JSON Lines) ───────────────────────
        error_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "errors.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(json_fmt)
        self.logger.addHandler(error_handler)

        # ── warnings.log (WARNING only, JSON Lines) ────────────────────────
        class _WarningOnly(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                return record.levelno == logging.WARNING

        warnings_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "warnings.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        warnings_handler.setLevel(logging.WARNING)
        warnings_handler.addFilter(_WarningOnly())
        warnings_handler.setFormatter(json_fmt)
        self.logger.addHandler(warnings_handler)

        # ── signals logger (plain text, signal events) ─────────────────────
        self.signals_logger = logging.getLogger("signals")
        self.signals_logger.setLevel(logging.INFO)
        self.signals_logger.propagate = False
        self.signals_logger.handlers.clear()
        sig_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "signals.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        sig_handler.setFormatter(json_fmt)
        self.signals_logger.addHandler(sig_handler)
        self.signals_logger.addHandler(console_handler)

        # ── performance logger (plain text, timing events) ──────────────────
        self.performance_logger = logging.getLogger("performance")
        self.performance_logger.setLevel(logging.INFO)
        self.performance_logger.propagate = False
        self.performance_logger.handlers.clear()
        perf_handler = logging.handlers.RotatingFileHandler(
            self.log_dir / "performance.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        perf_handler.setFormatter(json_fmt)
        self.performance_logger.addHandler(perf_handler)
        self.performance_logger.addHandler(console_handler)

        self.logger.debug("StockAnalysisLogger initialized (JSON file handlers active)")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_logger(self, name: Optional[str] = None) -> logging.Logger:
        """Return a child logger scoped to *name* under 'stockanalysis.*'."""
        if name:
            return self.logger.getChild(name)
        return self.logger

    def get_counts(self) -> Dict[str, Any]:
        """Delegate to CountingHandler — returns live level counters."""
        return self.counting_handler.get_counts()

    def reset_counts(self) -> None:
        """Reset in-memory level counters (call on system reset)."""
        self.counting_handler.reset()


# ---------------------------------------------------------------------------
# Module-level singletons & convenience API
# ---------------------------------------------------------------------------

_logger_instance = StockAnalysisLogger()


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a module-scoped child logger.

    Args:
        name: Sub-logger name (e.g. ``'tasks'``, ``'broker'``).
              When *None*, returns the root ``stockanalysis`` logger.

    Returns:
        A ``logging.Logger`` instance pre-configured with all handlers.

    Example::

        logger = get_logger("strategy")
        logger.info("Signal generated", extra={"symbol": "BTCUSD", "signal": "BUY"})
    """
    return _logger_instance.get_logger(name)


def get_log_counts() -> Dict[str, Any]:
    """Return in-memory log-level counters accumulated since startup (or last reset).

    Returns:
        Dict with keys: ``debug``, ``info``, ``warning``, ``error``,
        ``critical``, ``total``, ``session_start`` (ISO-8601 string).

    Example::

        counts = get_log_counts()
        # {"debug": 0, "info": 412, "warning": 23, "error": 7,
        #  "critical": 0, "total": 442, "session_start": "2026-09-22T16:44:00+00:00"}
    """
    return _logger_instance.get_counts()


def reset_log_counts() -> None:
    """Reset all in-memory level counters and refresh session-start timestamp.

    Call this from the system reset endpoint so the UI shows counts since
    the last manual reset, not since process startup.
    """
    _logger_instance.reset_counts()


# ---------------------------------------------------------------------------
# Module-specific convenience getters (backward-compatible)
# ---------------------------------------------------------------------------

def get_data_provider_logger() -> logging.Logger:
    """Get logger for data provider module."""
    return get_logger("data_provider")


def get_redis_logger() -> logging.Logger:
    """Get logger for Redis operations."""
    return get_logger("redis")


def get_celery_logger() -> logging.Logger:
    """Get logger for Celery tasks."""
    return get_logger("celery")


def get_strategies_logger() -> logging.Logger:
    """Get logger for strategy modules."""
    return get_logger("strategies")


def get_main_logger() -> logging.Logger:
    """Get the root application logger."""
    return get_logger("main")


def get_signals_logger() -> logging.Logger:
    """Get the signals logger — writes to logs/signals.log."""
    return _logger_instance.signals_logger


def get_performance_logger() -> logging.Logger:
    """Get the performance logger — writes to logs/performance.log."""
    return _logger_instance.performance_logger
