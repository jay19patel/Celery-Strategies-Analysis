"""System logs viewer, stats, and log file downloader router.

Routes incoming HTTP requests to app.services.LogService.

Endpoints:
    GET  /api/logs/stats          — level counters + file sizes (Log Intelligence panel)
    GET  /api/logs                — tail last N lines (default: success)
    GET  /api/logs/errors/parsed  — structured error records
    GET  /api/logs/{type}         — tail last N lines of specific log type
    GET  /api/logs/{type}/download — download raw log file
    POST /api/logs/counts/reset   — reset in-memory level counters
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.log_service import get_log_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["Logs"])


# ---------------------------------------------------------------------------
# Log stats — primary endpoint for the Log Intelligence UI panel
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_log_stats() -> dict[str, Any]:
    """Retrieve live log-level counters and per-file disk statistics.

    Returns a combined payload used by the dashboard Log Intelligence panel:
    - ``counts``: in-memory level counts since startup or last reset
      (debug, info, warning, error, critical, total, session_start)
    - ``files``: list of log file stats (name, size_mb, size_bytes, line_count,
      last_modified_iso) for all known log types
    - ``logs_dir``: absolute path to the logs directory
    """
    try:
        service = get_log_service()
        return service.get_log_stats()
    except Exception as exc:
        logger.exception("Failed to fetch log stats")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Counter reset
# ---------------------------------------------------------------------------

@router.post("/counts/reset")
def reset_log_counts() -> dict[str, Any]:
    """Reset in-memory log-level counters and refresh session-start timestamp.

    Call this after a system reset so the UI shows counts since the last
    manual reset, not since process startup.
    """
    try:
        from app.core.logger import reset_log_counts as _reset
        _reset()
        return {"status": "success", "message": "Log counters reset successfully."}
    except Exception as exc:
        logger.exception("Failed to reset log counts")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Line-paginated log readers
# ---------------------------------------------------------------------------

@router.get("")
@router.get("/")
def get_default_logs(
    log_type: str = Query("success"),
    lines: int = Query(300, ge=1, le=2000),
) -> dict[str, Any]:
    """Retrieve system logs for the terminal viewer with type selection and line limit."""
    try:
        service = get_log_service()
        lines_list = service.get_logs(log_type=log_type, lines_count=lines)
        return {
            "status": "success",
            "log_type": log_type,
            "lines_count": len(lines_list),
            "logs": "\n".join(lines_list),
        }
    except Exception as exc:
        logger.exception("Failed to read default logs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/download")
def download_default_logs(log_type: str = Query("success")) -> FileResponse:
    """Download default or specified log file."""
    try:
        service = get_log_service()
        log_file = service.get_log_file_path(log_type=log_type)
        return FileResponse(str(log_file), media_type="text/plain", filename=f"{log_type}.log")
    except Exception as exc:
        logger.exception("Failed to download default log file %s", log_type)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/errors/parsed")
def get_parsed_error_logs(limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Retrieve structured error records sorted reverse-chronologically (newest first)."""
    try:
        service = get_log_service()
        return service.get_parsed_error_entries(limit=limit)
    except Exception as exc:
        logger.exception("Failed to parse error logs")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{log_type}")
def get_file_logs(log_type: str, lines_count: int = Query(200, ge=1, le=2000)) -> list[str]:
    """Retrieve the last N lines of a specific system log file."""
    try:
        service = get_log_service()
        return service.get_logs(log_type=log_type, lines_count=lines_count)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to read log file %s", log_type)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{log_type}/download")
def download_file_logs(log_type: str) -> FileResponse:
    """Download the raw log file as text/plain attachment."""
    try:
        service = get_log_service()
        log_file = service.get_log_file_path(log_type=log_type)
        return FileResponse(str(log_file), media_type="text/plain", filename=f"{log_type}.log")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to download log file %s", log_type)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
