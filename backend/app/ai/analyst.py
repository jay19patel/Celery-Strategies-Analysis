"""What the language model is asked, what it is shown, and what of its answer is kept.

One job: the **market brief**, the measured numbers read back as a few plain sentences. The model
never decides a trade; the rules do.

What leaves the machine is market data only. `digest()` builds the payload from an allowlist
(`ALLOWED`), enforced in code: no balance, position, order, P&L or key is ever in it.
"""

from __future__ import annotations

import time
from typing import Any

from sqlmodel import col, select

from app.ai.llm import LLM, LLMError
from app.core.db import session
from app.core.events import EventHub
from app.core.models import AiBrief
from app.settings import Settings

CONFIDENCE = ("high", "medium", "low")

BRIEF_SYSTEM = """You are the market writer inside TradeBuddy, a crypto options desk.

You receive one JSON digest of numbers ALREADY measured for one underlying: spot, implied volatility
(ATM IV) against realised volatility, the trend read, the option chain's positioning (put/call ratio,
max pain, OI walls, skew), perpetual positioning (funding, open interest, basis), technical structure,
multi-timeframe trend, momentum, volume and flow, plus the move the options price to expiry.

Write what kind of options market this is, for someone about to choose a structure.

Rules:
- Use ONLY numbers in the digest; quote the number behind each claim. Never invent a price or level.
- Never predict a direction or give a target, an entry or a stop. Describe the present.
- Say plainly when the volatility premium is small or negative: selling options then has no edge.
- Separate measured facts from interpretations. If a perspective has no data, say it is unavailable.
- Explain conflicting perspectives instead of forcing one market direction.
- Short, plain sentences. No hype, no disclaimers.

Answer with JSON only:
{"headline": "<one sentence>",
 "volatility": "<1-2 sentences: IV vs realised and what it means for buyers and sellers>",
 "positioning": "<1-2 sentences: put/call, max pain, walls, skew>",
 "trend": "<1-2 sentences: short, medium and long timeframe trend and agreement/conflict>",
 "momentum_flow": "<1-2 sentences: price momentum, volume, open interest, funding and flow if available>",
 "summary": "<2-3 sentences pulling it together>",
 "watch": ["<up to 3 things that would change this read>"],
 "confidence": "high|medium|low"}"""

MARKET_SYSTEM = """You are the market writer on TradeBuddy's crypto perpetual futures desk.

You receive one JSON digest of numbers ALREADY measured for one underlying: spot and its changes,
realised volatility and ATR, the trend read on several timeframes, market structure (swing highs and
lows, the last break, the volume profile, the nearest levels above and below), momentum (RSI, MACD,
ADX), volume, order flow, perpetual positioning (funding, open interest, basis) and the option
book's own levels (max pain, OI walls, put/call).

Write what state this market is in, for someone about to take a position on the perpetual.

Rules:
- Use ONLY numbers in the digest; quote the number behind each claim. Never invent a price or level.
- Describe the present. Do NOT give an entry, a stop, a target or a direction to trade.
- Say plainly when the timeframes disagree or momentum is flat: a range is a state, not a failure to
  find a trend, and a trader is better told it than sold a direction.
- Name levels only where the digest names them, and say which side of price they sit on.
- Separate measured facts from interpretations. If a perspective has no data, say it is unavailable.
- Short, plain sentences. No hype, no disclaimers.

Answer with JSON only:
{"headline": "<one sentence: the state of this market>",
 "trend": "<1-2 sentences: short, medium and long timeframe trend and agreement/conflict>",
 "levels": "<1-2 sentences: the nearest levels above and below, the volume profile, the last break>",
 "momentum": "<1-2 sentences: RSI, MACD, ADX, ATR and volume — is there strength behind the move>",
 "flow": "<1-2 sentences: funding, open interest, basis and order flow if available>",
 "summary": "<2-3 sentences pulling it together>",
 "watch": ["<up to 3 things that would change this read>"],
 "confidence": "high|medium|low"}"""

# The only top-level keys a digest may contain — the gate on what leaves this machine.
ALLOWED = frozenset({
    "underlying", "spot", "hours_to_expiry", "regime", "direction", "direction_parts", "atm_iv_pct",
    "realised_vol_24h_pct", "realised_vol_7d_pct", "vol_premium_pts", "change_24h_pct", "rsi_1h",
    "range_position_24h", "implied_move_pct", "max_pain", "pcr_oi", "skew_25d", "call_wall", "put_wall",
    "strategy_scores", "market",
})

MARKET_ALLOWED = frozenset({
    "price", "change_1h_pct", "change_4h_pct", "change_24h_pct", "rv_24h", "rv_7d", "high_24h", "low_24h",
    "range_pos_24h", "atr_pct", "ema20", "ema50", "ema200", "volume_z_1h", "oi", "oi_change_1h_pct",
    "oi_change_24h_pct", "funding_pct", "funding_avg_24h_pct", "oi_usd", "turnover_24h_usd", "basis_pct",
    "iv_rv_ratio", "pcr_volume", "options_oi_usd", "options_turnover_usd", "implied_move_label",
    "implied_move_hours", "max_pain_hours", "max_pain_gap_pct", "call_wall_gap_pct", "put_wall_gap_pct",
    "term_slope", "rsi_1h", "macd_1h", "adx_1h", "atr_pct_1h", "change_1d_pct", "change_7d_pct",
    "alignment", "timeframes", "structure", "insights", "flow", "pulse",
})


def _pct(v: float | None) -> float | None:
    return None if v is None else round(100 * v, 2)


def digest(underlying: str, regime: dict[str, Any], market: dict[str, Any] | None = None) -> dict[str, Any]:
    m = regime.get("metrics") or {}
    out: dict[str, Any] = {
        "underlying": underlying,
        "spot": m.get("spot"),
        "hours_to_expiry": regime.get("hours_to_expiry"),
        "regime": regime.get("label"),
        "direction": regime.get("direction"),
        "direction_parts": regime.get("direction_parts"),
        "atm_iv_pct": _pct(m.get("atm_iv")),
        "realised_vol_24h_pct": _pct(m.get("rv_24h")),
        "realised_vol_7d_pct": _pct(m.get("rv_7d")),
        "vol_premium_pts": _pct(m.get("vol_premium")),
        "change_24h_pct": m.get("change_24h_pct"),
        "rsi_1h": m.get("rsi_1h"),
        "range_position_24h": m.get("range_position_24h"),
        "implied_move_pct": m.get("implied_move_pct"),
        "max_pain": m.get("max_pain"),
        "pcr_oi": m.get("pcr_oi"),
        "skew_25d": m.get("skew_25d"),
        "call_wall": m.get("call_wall"),
        "put_wall": m.get("put_wall"),
        "strategy_scores": {k: v.get("score") for k, v in (regime.get("scores") or {}).items()},
    }
    if market:
        out["market"] = {k: v for k, v in market.items() if k in MARKET_ALLOWED and v is not None}
    return {k: v for k, v in out.items() if k in ALLOWED}


def _lines(value: Any, n: int, width: int = 280) -> list[str]:
    return [str(x)[:width] for x in (value if isinstance(value, list) else [])][:n]


def clean_brief(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": str(answer.get("headline", ""))[:300],
        "volatility": str(answer.get("volatility", ""))[:500],
        "positioning": str(answer.get("positioning", ""))[:500],
        "trend": str(answer.get("trend", ""))[:500],
        "momentum_flow": str(answer.get("momentum_flow", ""))[:500],
        "summary": str(answer.get("summary", ""))[:900],
        "watch": _lines(answer.get("watch"), 3),
        "confidence": answer.get("confidence") if answer.get("confidence") in CONFIDENCE else "low",
    }


def clean_market_brief(answer: dict[str, Any]) -> dict[str, Any]:
    return {
        "headline": str(answer.get("headline", ""))[:300],
        "trend": str(answer.get("trend", ""))[:500],
        "levels": str(answer.get("levels", ""))[:500],
        "momentum": str(answer.get("momentum", ""))[:500],
        "flow": str(answer.get("flow", ""))[:500],
        "summary": str(answer.get("summary", ""))[:900],
        "watch": _lines(answer.get("watch"), 3),
        "confidence": answer.get("confidence") if answer.get("confidence") in CONFIDENCE else "low",
    }


# Each desk's brief: the prompt it is written from and the shape kept of the answer.
BRIEFS: dict[str, tuple[str, Any]] = {
    "options": (BRIEF_SYSTEM, clean_brief),
    "market": (MARKET_SYSTEM, clean_market_brief),
}


class Analyst:
    def __init__(self, llm: LLM, settings: Any, hub: EventHub) -> None:
        self.llm = llm
        self.settings: Any = settings
        self.hub = hub

    async def brief(
        self, underlying: str, regime: dict[str, Any], market: dict[str, Any] | None = None, kind: str = "options",
    ) -> dict[str, Any]:
        """One desk's brief. `kind` picks the prompt and the shape kept; both are sent the same
        allowlisted digest, so neither ever sees an account, a position or a P&L."""
        system, clean = BRIEFS[kind]
        row = AiBrief(ts=time.time(), underlying=underlying, kind=kind)
        try:
            answer, meta = await self.llm.json(system, digest(underlying, regime, market))
            row.content, row.model, row.latency_ms = clean(answer), meta["model"], meta["ms"]
        except LLMError as exc:
            row.error = str(exc)[:1000]
        with session() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        self.hub.publish("AiBrief", row.content["headline"] if row.content else f"brief failed: {row.error}",
                         "info" if row.content else "warning", persist=not row.content,
                         underlying=underlying, kind=kind, id=row.id)
        return row.model_dump()

    @staticmethod
    def latest(underlying: str, limit: int = 1, kind: str = "options") -> list[dict[str, Any]]:
        with session() as s:
            rows = s.exec(
                select(AiBrief).where(AiBrief.underlying == underlying, AiBrief.kind == kind)
                .order_by(col(AiBrief.ts).desc()).limit(limit)
            ).all()
        return [r.model_dump() for r in rows]

    def ready(self) -> str:
        """Why the model cannot be asked, or ""."""
        s: Settings = self.settings()
        if not s.ai_enabled:
            return "AI is switched off in Settings"
        if not any(m["key_present"] for m in self.llm.status()["models"]):
            return "no configured model has its provider key in the environment"
        return ""
