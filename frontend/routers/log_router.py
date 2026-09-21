"""System logs viewer and log file downloader router.

Routes incoming HTTP requests to app.services.LogService.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.services.log_service import get_log_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["Logs"])


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
