"""Runtime settings. Secrets are masked on the way out and blank means "keep" on the way in."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import EngineDep
from app.options.strategies import CATALOG
from app.settings import LIVE_CONFIRM_PHRASE, Settings

router = APIRouter(prefix="/settings", tags=["settings"])


class Update(BaseModel):
    changes: dict[str, Any]
    confirm: str = ""


class DeltaTest(BaseModel):
    env: str | None = None
    api_key: str = ""
    api_secret: str = ""


def _limits() -> dict[str, dict[str, Any]]:
    """Each numeric field's bounds, so the form validates with the same numbers the server does."""
    props = Settings.model_json_schema()["properties"]
    return {
        name: {k: p[k] for k in ("minimum", "maximum") if k in p}
        for name, p in props.items() if "minimum" in p or "maximum" in p
    }


def _view(engine: Any) -> dict[str, Any]:
    return {
        "values": engine.settings().public(),
        "limits": _limits(),
        "live_confirm_phrase": LIVE_CONFIRM_PHRASE,
        "strategies": [asdict(s) for s in CATALOG.values()],
    }


@router.get("")
def get_settings(engine: EngineDep) -> dict[str, Any]:
    return _view(engine)


@router.patch("")
async def update(body: Update, engine: EngineDep) -> dict[str, Any]:
    await engine.update_settings(body.changes, body.confirm)
    return _view(engine)


@router.post("/delta/test")
async def test_delta(body: DeltaTest, engine: EngineDep) -> dict[str, Any]:
    return await engine.test_delta(body.env, body.api_key, body.api_secret)


@router.delete("/delta/credentials")
async def clear_delta(engine: EngineDep) -> dict[str, Any]:
    await engine.clear_delta_credentials()
    return _view(engine)


@router.post("/notify/test")
async def test_notify(engine: EngineDep) -> dict[str, Any]:
    results = await engine.notifier.send("Test", "Notifications from TradeBuddy are working.")
    return {"results": results, "note": "" if results else "no channel is enabled and complete"}
