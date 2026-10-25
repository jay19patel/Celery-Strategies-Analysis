"""Language models through LiteLLM: one call shape for every provider.

The model is a setting, not code: `mistral/mistral-small-latest`, `openai/gpt-4o-mini`,
`anthropic/claude-haiku-4-5`, `gemini/gemini-2.0-flash`, `groq/llama-3.3-70b-versatile`,
`ollama/llama3.1` — any LiteLLM id. The primary is tried first, then each fallback in order, so one
provider's outage or rate limit never stops the system; every failure is recorded per model.

Provider keys stay in the environment (MISTRAL_API_KEY, OPENAI_API_KEY, ...), where LiteLLM reads
them. Nothing here stores, logs or returns a key: the status reports only whether one is present.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import litellm

from app.settings import Settings

# LiteLLM looks for GEMINI_API_KEY when calling gemini/*
if not os.environ.get("GEMINI_API_KEY") and os.environ.get("GOOGLE_API_KEY"):
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

log = logging.getLogger(__name__)
litellm.drop_params = True  # a provider without JSON mode gets the request without it, not an error
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

TIMEOUT = 45.0


class LLMError(Exception):
    pass


def parse_json(content: str) -> dict[str, Any]:
    """The JSON object in an answer. Small models sometimes wrap it in prose or a code fence."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("the answer held no JSON object")
    try:
        answer = json.loads(content[start : end + 1])
    except ValueError as exc:
        raise LLMError(f"the answer's JSON did not parse: {exc}") from exc
    if not isinstance(answer, dict):
        raise LLMError("the answer was not a JSON object")
    return answer


def _short(exc: BaseException) -> str:
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:240]


class LLM:
    def __init__(self, settings: Any) -> None:
        self.settings: Any = settings  # () -> Settings
        self.models_seen: dict[str, dict[str, Any]] = {}
        self.last: dict[str, Any] = {}

    def models(self) -> list[str]:
        s: Settings = self.settings()
        return list(dict.fromkeys([s.ai_model, *s.ai_fallback_models]))

    def status(self) -> dict[str, Any]:
        rows = []
        for model in self.models():
            try:
                env = litellm.validate_environment(model=model)
                ready, missing = bool(env.get("keys_in_environment")), list(env.get("missing_keys") or [])
            except Exception as exc:
                ready, missing = False, [_short(exc)]
            rows.append({"model": model, "key_present": ready, "missing": missing} | self.models_seen.get(model, {}))
        return {"enabled": self.settings().ai_enabled, "models": rows, "last": self.last}

    async def json(self, system: str, payload: dict[str, Any], max_tokens: int = 900) -> tuple[dict[str, Any], dict[str, Any]]:
        """One JSON answer from the first model that gives one. Raises LLMError when none does."""
        errors: list[str] = []
        for i, model in enumerate(self.models()):
            seen = self.models_seen.setdefault(model, {"calls": 0, "failures": 0, "last_error": "", "last_ms": None})
            seen["calls"] += 1
            started = time.perf_counter()
            try:
                resp = await litellm.acompletion(
                    model=model, temperature=0.2, max_tokens=max_tokens, timeout=TIMEOUT, num_retries=1,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, separators=(",", ":"), default=str)},
                    ],
                )
                answer = parse_json(resp.choices[0].message.content or "")
            except Exception as exc:
                seen["failures"] += 1
                seen["last_error"] = _short(exc)
                errors.append(f"{model}: {seen['last_error']}")
                log.warning("llm_failed model=%s error=%s", model, seen["last_error"])
                continue
            ms = round(1000 * (time.perf_counter() - started))
            seen.update(last_ms=ms, last_error="", last_ok=time.time())
            usage = getattr(resp, "usage", None)
            meta = {
                "model": model, "ms": ms, "fallback": i > 0, "errors": errors,
                "tokens": getattr(usage, "total_tokens", None) if usage else None,
            }
            self.last = meta | {"at": time.time(), "ok": True}
            return answer, meta
        self.last = {"at": time.time(), "ok": False, "errors": errors}
        raise LLMError("; ".join(errors) or "no model configured")
