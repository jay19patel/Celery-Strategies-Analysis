"""Mistral chat completions, for TradeBuddy AI (TB-AI).

The model never decides anything. Insights, forecasts and the playbook are computed first; Mistral
only writes them up. What is sent is chosen in `tbai.py`: market numbers and findings always; trades,
positions, orders and account figures only when Settings allows it. Never API keys or secrets.

A 429 is Mistral's rate limit for the account or the model. The error carries Mistral's own limit
headers and its Retry-After, so the caller can wait exactly as long as asked and the dashboard can
say which limit was hit.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://api.mistral.ai/v1"


@dataclass
class MistralError(Exception):
    message: str
    status: int | None = None
    retry_after: float | None = None  # seconds, when Mistral said
    limits: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


def limit_headers(res: httpx.Response) -> dict[str, str]:
    """Mistral's x-ratelimit-* headers (limit and remaining, per minute and per month) and Retry-After."""
    return {k.lower(): v for k, v in res.headers.items() if k.lower().startswith(("x-ratelimit", "ratelimit", "retry-after"))}


def _retry_after(res: httpx.Response) -> float | None:
    raw = res.headers.get("retry-after")
    try:
        return max(0.0, float(raw)) if raw else None
    except ValueError:
        return None


def _error(res: httpx.Response) -> MistralError:
    try:
        body = res.json()
        detail = body.get("message") or body.get("detail") or ""
        if isinstance(detail, list | dict):
            detail = json.dumps(detail)[:160]
    except ValueError:
        detail = ""
    # The body can echo the request; keep only the status and Mistral's own short message.
    return MistralError(f"Mistral answered HTTP {res.status_code} {str(detail)[:160]}".strip(), res.status_code, _retry_after(res), limit_headers(res))


async def complete(
    api_key: str, model: str, system: str, payload: Any, max_tokens: int = 1200, http: httpx.AsyncClient | None = None, timeout: float = 45.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One JSON-mode chat completion. Returns (parsed JSON answer, meta: model, usage, ms, limits)."""
    own = http is None
    http = http or httpx.AsyncClient(timeout=timeout)
    started = time.perf_counter()
    try:
        res = await http.post(
            f"{API}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
            json={
                "model": model,
                "temperature": 0.2,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"), default=str)},
                ],
            },
        )
    except httpx.HTTPError as exc:
        raise MistralError(f"could not reach Mistral: {type(exc).__name__}") from exc
    finally:
        if own:
            await http.aclose()
    if res.status_code != 200:
        raise _error(res)
    try:
        body = res.json()
        content = body["choices"][0]["message"]["content"]
        answer = json.loads(content[content.find("{") : content.rfind("}") + 1])  # small models sometimes wrap JSON in prose
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise MistralError("Mistral's answer was not the JSON asked for", res.status_code, limits=limit_headers(res)) from exc
    if not isinstance(answer, dict):
        raise MistralError("Mistral's answer was not a JSON object", res.status_code, limits=limit_headers(res))
    meta = {"model": body.get("model", model), "usage": body.get("usage", {}), "ms": round(1000 * (time.perf_counter() - started)), "limits": limit_headers(res)}
    return answer, meta


async def check(api_key: str, model: str, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Diagnose the key and model: is the key accepted, can it use this model, what are its limits.
    Two small requests: the model list, and a 1-token completion."""
    own = http is None
    http = http or httpx.AsyncClient(timeout=20)
    out: dict[str, Any] = {"model": model, "ok": False}
    try:
        res = await http.get(f"{API}/models", headers={"Authorization": f"Bearer {api_key}"})
        if res.status_code != 200:
            err = _error(res)
            return out | {"error": str(err), "status": err.status, "limits": err.limits, "step": "models"}
        ids = sorted({m.get("id", "") for m in res.json().get("data", [])} - {""})
        out["models_available"] = len(ids)
        out["model_listed"] = model in ids
        out["similar"] = [i for i in ids if i.split("-")[0] == model.split("-")[0]][:12]
        started = time.perf_counter()
        res = await http.post(
            f"{API}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
        )
        out["latency_ms"] = round(1000 * (time.perf_counter() - started))
        out["limits"] = limit_headers(res)
        if res.status_code != 200:
            err = _error(res)
            return out | {"error": str(err), "status": err.status, "retry_after": err.retry_after, "step": "completion"}
        return out | {"ok": True, "status": 200}
    except httpx.HTTPError as exc:
        return out | {"error": f"could not reach Mistral: {type(exc).__name__}"}
    finally:
        if own:
            await http.aclose()


# -- per-symbol market review (kept for the Market page) -----------------------------------------

def clean_symbol_review(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        return {}
    return {
        "summary": str(v.get("summary", ""))[:600],
        "points": [str(p)[:300] for p in (v.get("points") or [])][:4],
        "risks": [str(p)[:300] for p in (v.get("risks") or [])][:2],
        "playbook_view": str(v.get("playbook_view", ""))[:300],
    }
