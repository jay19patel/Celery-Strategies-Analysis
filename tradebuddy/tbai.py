"""TradeBuddy AI (TB-AI): one written report over everything the engine knows, in four sections.

    market     per-symbol numbers, findings, forecast and playbook (from the analyst)
    trading    strategies, signals and skips, orders, open positions
    portfolio  accounts, today's P&L against the daily limit, closed-trade performance
    system     feed, processes, background jobs, warnings and errors

The engine builds the digest (`Api.ai_digest`): numbers only, no keys, no order or exchange ids.
Account figures are left out unless Settings → AI → "Include trades, positions and portfolio" is on.
One Mistral request per report covers every section and every symbol.
"""

from __future__ import annotations

from typing import Any

from tradebuddy.mistral import clean_symbol_review

SECTIONS = ("market", "trading", "portfolio", "system")
STATUSES = ("good", "watch", "act")

SYSTEM = """You are TB-AI, the analyst inside TradeBuddy, a crypto trading dashboard (Delta Exchange
perpetuals on a paper broker and optionally a real account). You receive one JSON digest of computed
numbers: market (per symbol), trading (strategies, signals, orders, positions), portfolio (accounts,
P&L, risk limits) and system (feed, processes, background jobs, errors).

Write for the trader who runs it. Rules:
- Use only the digest. Never invent prices, events, news or numbers. Cite the number behind each point.
- Short plain sentences. No disclaimers, no hype, no generic trading advice.
- "status" per section: "good" (nothing to do), "watch" (keep an eye on it) or "act" (needs attention now).
- "actions" are concrete things the trader can do in TradeBuddy (e.g. "switch off rsi_5m on ETHUSD: 6 errors today",
  "check the feed: down 12 min"). Empty when nothing is needed.
- A section with no data in the digest: status "good", summary saying it is not shared or empty.
- You do not decide trades. You may say whether a playbook or open position looks consistent with the numbers.

Answer with JSON only:
{"headline": "<one sentence, the most important thing now>",
 "health": "good|watch|act",
 "priorities": ["<up to 3, most urgent first>"],
 "sections": {
   "market":    {"status": "...", "summary": "<2 sentences>", "points": ["<up to 4>"], "actions": ["<up to 3>"]},
   "trading":   {...same...},
   "portfolio": {...same...},
   "system":    {...same...}},
 "symbols": {"<SYMBOL>": {"summary": "<2 sentences>", "points": ["<up to 4>"], "risks": ["<up to 2>"], "playbook_view": "<1 sentence>"}}}"""


def payload(digest: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    """The request body's user message: the engine's digest plus the analyst's per-symbol briefs."""
    return {"market": market, **{k: digest.get(k) for k in ("trading", "portfolio", "system")}, "as_of": digest.get("as_of")}


def _status(v: Any) -> str:
    return v if v in STATUSES else "watch"


def _strings(v: Any, n: int, width: int = 300) -> list[str]:
    return [str(x)[:width] for x in (v if isinstance(v, list) else [])][:n]


def clean(answer: dict[str, Any], symbols: list[str]) -> dict[str, Any]:
    """Keep only the shape asked for, with lengths capped: a small model sometimes adds or drops keys."""
    sections = answer.get("sections") if isinstance(answer.get("sections"), dict) else {}
    out_sections = {}
    for name in SECTIONS:
        s = sections.get(name) if isinstance(sections.get(name), dict) else {}
        out_sections[name] = {
            "status": _status(s.get("status")),
            "summary": str(s.get("summary", ""))[:700],
            "points": _strings(s.get("points"), 4),
            "actions": _strings(s.get("actions"), 3),
        }
    raw_symbols = answer.get("symbols") if isinstance(answer.get("symbols"), dict) else {}
    return {
        "headline": str(answer.get("headline", ""))[:300],
        "health": _status(answer.get("health")),
        "priorities": _strings(answer.get("priorities"), 3),
        "sections": out_sections,
        "symbols": {sym: clean_symbol_review(raw_symbols.get(sym)) for sym in symbols if raw_symbols.get(sym)},
    }
