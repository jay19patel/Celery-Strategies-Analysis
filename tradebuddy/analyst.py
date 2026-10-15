"""The market analyst: every 5 minutes, one MarketAnalysis per symbol.

    candles, OI, funding (REST)  +  latest OptionsSnapshot and MarketStats (from the engine)
        -> insights.market_context -> insights.insights          rule-based, always, milliseconds
        -> forecast.Forecaster.predict                            when a model is trained for the symbol
        -> playbook.decide                                        options structure or NO_TRADE
        -> TradeBuddy AI (tbai + mistral)                         when AI is on: one request per report, every
                                                                  ai_interval_minutes, covering every symbol and the
                                                                  engine's digest of trading, portfolio and system

It reads market data and settings; it writes nothing but events. Distributed, it is its own process
(`python -m tradebuddy analyst`) and PUSHes its results to the engine like a Celery worker; in a single
process the System runs it and it publishes onto the bus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from tradebuddy import tbai
from tradebuddy.delta import RESOLUTION_SECONDS, DeltaClient
from tradebuddy.events import AIReport, MarketAnalysis
from tradebuddy.insights import insights, lean, market_context
from tradebuddy.jobs import NULL_JOB
from tradebuddy.mistral import MistralError, complete
from tradebuddy.options import UNDERLYINGS
from tradebuddy.playbook import decide
from tradebuddy.settings import Settings

log = logging.getLogger(__name__)

HISTORY_BARS = 700  # 7 days of 15m bars plus room: 7-day realised vol, EMA200, the forecaster's 4-day vol
# The TB-AI schema (4 sections plus a review per symbol) does not fit in 1600 tokens: a small model
# overruns it about half the time and the cut-off answer is not JSON. One cut-off answer is asked for
# again with twice this; a second failure is recorded with what the model wrote.
AI_MAX_TOKENS = 3000


class Analyst:
    def __init__(
        self, client: Callable[[], DeltaClient], settings: Callable[[], Settings], publish: Callable[[Any], None],
        options: Callable[[str], dict[str, Any] | None], stats: Callable[[str], dict[str, Any] | None],
        model_dir: Path | None = None, every: float = 300.0, symbols: tuple[str, ...] = tuple(UNDERLYINGS), http: Any = None,
        digest: Callable[[bool], Awaitable[dict[str, Any]]] | None = None, bundled_models: Path | None = None,
    ) -> None:
        self.bundled_models = BUNDLED_MODELS if bundled_models is None else bundled_models
        self.digest = digest  # the engine's numbers on trading, portfolio and system (Api.ai_digest)
        self.client, self.settings, self.publish = client, settings, publish
        self.options, self.stats = options, stats
        self.model_dir, self.every, self.symbols, self.http = model_dir, every, symbols, http
        self._models: dict[str, tuple[Any, Any]] = {}  # symbol -> ((file, mtime), Forecaster)
        self._unloadable: dict[tuple[str, float], str] = {}  # (file, mtime) -> why, so a bad file is read once
        self.runs = 0
        self.last_error = ""
        self.ai_calls = 0
        self.ai_errors = 0
        # TB-AI pacing: at most one request per interval; after a rate limit, nothing until paused_until.
        self.ai_next_at = 0.0
        self.ai_paused_until = 0.0
        self.ai_backoff = 0.0
        self.ai_limits: dict[str, str] = {}
        self.ai_last_ok_at = 0.0
        self._ai_symbols: dict[str, dict[str, Any]] = {}  # last good per-symbol review, shown (marked stale) while the next one fails
        self.job = NULL_JOB

    def status(self) -> dict[str, Any]:
        s = self.settings()
        return {
            "runs": self.runs, "ai": "on" if s.ai_ready else ("no key" if s.ai_enabled else "off"), "ai_model": s.mistral_model,
            "ai_calls": self.ai_calls, "ai_errors": self.ai_errors, "last_error": self.last_error,
            "ai_next_at": self.ai_next_at or None, "ai_paused_until": self.ai_paused_until if self.ai_paused_until > time.time() else None,
            "ai_limits": self.ai_limits, "ai_last_ok_at": self.ai_last_ok_at or None,
            "models": {sym: self._model(sym)[1].get("status") for sym in self.symbols},
        }

    async def run(self) -> None:
        await asyncio.sleep(10)  # let the first options snapshot and prices arrive
        while True:
            try:
                with self.job.tick() as job:
                    found = await self.cycle()
                    job.note = f"{len(found)} symbols" + (f", AI {'failed' if self.last_error else 'ok'}" if self.settings().ai_ready else "")
            except Exception:
                log.exception("analyst_cycle_failed")
            # Wake 20s after each 5-minute mark: the 15m bar that just closed is in the REST history by then.
            await asyncio.sleep(self.every - (time.time() % self.every) + 20)

    async def cycle(self) -> list[MarketAnalysis]:
        started = time.perf_counter()
        results = {sym: await self.analyse(sym) for sym in self.symbols}
        s = self.settings()
        ai: dict[str, Any] = {}
        if s.ai_ready:
            ai = await self._tbai(s, results)
        out = []
        for sym, r in results.items():
            r["context"]["took_ms"] = round(1000 * (time.perf_counter() - started))
            event = MarketAnalysis(symbol=sym, context=r["context"], insights=r["insights"], forecast=r["forecast"], playbook=r["playbook"], ai=ai.get(sym))
            self.publish(event)
            out.append(event)
        self.runs += 1
        return out

    async def analyse(self, symbol: str) -> dict[str, Any]:
        client = self.client()
        errors: list[str] = []

        async def fetch(series: str) -> list:
            try:
                return await client.candles(series, "15m", HISTORY_BARS)
            except Exception as exc:
                errors.append(f"{series}: {exc}")
                return []

        candles, oi, funding = await asyncio.gather(fetch(symbol), fetch(f"OI:{symbol}"), fetch(f"FUNDING:{symbol}"))
        opts = self.options(symbol)
        ctx = market_context(symbol, candles, oi, funding, self.stats(symbol), opts)
        found = insights(ctx)
        ctx["lean"] = lean(found)
        ctx["errors"] = errors
        forecast, model = await self._forecast(symbol, candles, oi, funding)
        ctx["model"] = model
        return {"context": ctx, "insights": found, "forecast": forecast, "playbook": decide(forecast, opts)}

    # -- model ----------------------------------------------------------------------

    def _model(self, symbol: str) -> tuple[Any, dict[str, Any]]:
        if self.model_dir is None:
            return None, {"status": "off"}
        found = model_paths(self.model_dir, symbol, self.bundled_models)
        if not found:
            return None, {"status": "not_trained", "hint": f"python -m tradebuddy train --symbols {symbol}"}
        try:
            from tradebuddy.forecast import Forecaster  # needs the ml extra; loaded only once a model exists
        except ImportError:
            return None, {"status": "unavailable", "hint": "install the ml extra (scikit-learn)"}
        errors = []
        for path, source in found:
            stamp = (str(path), path.stat().st_mtime)
            if (why := self._unloadable.get(stamp)) is not None:
                errors.append(why)  # already tried this exact file; do not unpickle it again every cycle
                continue
            cached = self._models.get(symbol)
            if cached is None or cached[0] != stamp:
                try:
                    self._models[symbol] = (stamp, Forecaster.load(path))
                except Exception as exc:
                    self._unloadable[stamp] = f"{path.name} ({source}): {str(exc)[:120]}"
                    errors.append(self._unloadable[stamp])
                    log.warning("model_unusable symbol=%s path=%s error=%s", symbol, path, exc)
                    continue
            card = self._models[symbol][1].card
            return self._models[symbol][1], {
                "status": "ready", "source": source, "trained_at": card.trained_at, "train_from": card.train_from, "train_to": card.train_to,
                "metrics": card.metrics, "baselines": card.baselines, "skill": card.skill,
                "skipped": errors,  # a better file that this code could not use, so the page says which one it is on
            }
        return None, {"status": "error", "hint": "; ".join(errors)[:200]}

    async def _forecast(self, symbol: str, candles: list, oi: list, funding: list) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        model, info = self._model(symbol)
        if model is None:
            return None, info
        try:
            return await asyncio.to_thread(model.predict, candles, oi, funding), info
        except Exception as exc:
            return None, info | {"status": "error", "hint": str(exc)[:200]}

    # -- TradeBuddy AI --------------------------------------------------------------

    async def _tbai(self, s: Settings, results: dict[str, dict[str, Any]], now: float | None = None) -> dict[str, Any]:
        """One TB-AI report when due. Returns the per-symbol reviews to attach to MarketAnalysis:
        fresh when this cycle made a report, otherwise the last good ones marked stale."""
        now = time.time() if now is None else now
        if now < self.ai_paused_until or now < self.ai_next_at:
            return self._stale_reviews()
        self.ai_next_at = now + s.ai_interval_minutes * 60 - 30  # a little early, so the 5-minute cycle does not skip a slot
        digest: dict[str, Any] = {}
        if self.digest is not None:
            try:
                digest = await self.digest(s.ai_share_account)
            except Exception as exc:
                log.warning("ai_digest_failed error=%s", exc)
                digest = {"error": f"engine digest unavailable: {exc}"[:200]}
        body = tbai.payload(digest, {sym: self._brief(r) for sym, r in results.items()})
        self.ai_calls += 1
        attempts = 0
        try:
            for budget in (AI_MAX_TOKENS, AI_MAX_TOKENS * 2):
                attempts += 1
                try:
                    answer, meta = await complete(s.mistral_api_key, s.mistral_model, tbai.SYSTEM, body, max_tokens=budget, http=self.http)
                    break
                except MistralError as exc:
                    # A cut-off answer is worth asking for once more with twice the budget; nothing else is.
                    if not exc.truncated or budget != AI_MAX_TOKENS:
                        raise
                    log.info("tbai_truncated retrying with max_tokens=%d", AI_MAX_TOKENS * 2)
        except MistralError as exc:
            self.ai_errors += 1
            self.last_error = str(exc)
            self.ai_limits = exc.limits or self.ai_limits
            if exc.rate_limited:
                # Wait as long as Mistral asks, and never less than a backoff that doubles 5 -> 10 -> 20 -> 40 -> 60 min.
                self.ai_backoff = min(3600.0, max(300.0, self.ai_backoff * 2))
                self.ai_paused_until = now + max(exc.retry_after or 0.0, self.ai_backoff)
            log.warning("tbai_failed status=%s finish=%s error=%s paused_until=%s", exc.status, exc.finish_reason, exc, self.ai_paused_until)
            self.publish(AIReport(
                ok=False, model=s.mistral_model, error=str(exc), status=exc.status, limits=exc.limits,
                raw=exc.raw, finish_reason=exc.finish_reason, attempts=attempts,
                paused_until=self.ai_paused_until if exc.rate_limited else None, shared_account=s.ai_share_account,
            ))
            return self._stale_reviews()
        self.ai_backoff, self.ai_paused_until, self.last_error = 0.0, 0.0, ""
        self.ai_limits, self.ai_last_ok_at = meta["limits"], now
        report = tbai.clean(answer, list(results))
        self.publish(AIReport(
            ok=True, model=meta["model"], report=report, limits=meta["limits"], usage=meta["usage"], ms=meta["ms"],
            attempts=attempts, shared_account=s.ai_share_account,
        ))
        self._ai_symbols = {sym: {**review, "model": meta["model"], "at": now} for sym, review in report["symbols"].items()}
        return self._ai_symbols

    def _stale_reviews(self) -> dict[str, Any]:
        note = {"stale": True} | ({"error": self.last_error} if self.last_error else {})
        return {sym: review | note for sym, review in self._ai_symbols.items()}

    @staticmethod
    def _brief(r: dict[str, Any]) -> dict[str, Any]:
        """What the AI is shown: numbers and findings. Nothing about accounts, positions or keys."""
        keep = (
            "price", "change_1h_pct", "change_24h_pct", "rv_24h", "rv_7d", "atr_pct", "range_pos_24h", "ema50", "ema200",
            "volume_z_1h", "oi_change_24h_pct", "funding_avg_24h_pct", "basis_pct", "atm_iv", "iv_rv_ratio", "skew_25d",
            "pcr_oi", "pcr_volume", "implied_move_pct", "implied_move_label", "max_pain", "max_pain_gap_pct", "call_wall",
            "put_wall", "term_slope",
        )
        ctx = r["context"]
        brief: dict[str, Any] = {"numbers": {k: ctx.get(k) for k in keep if ctx.get(k) is not None}}
        brief["findings"] = [f"[{i['severity']}/{i['bias']}] {i['title']}" for i in r["insights"]]
        if r["forecast"]:
            f = r["forecast"]
            brief["forecast_24h"] = {k: f[k] for k in ("up_probability", "expected_abs_move", "predicted_realized_vol", "breakout_probability")} | {"skill": f["skill"]}
        if r["playbook"]:
            brief["playbook"] = {"strategy": r["playbook"]["strategy"], "reason": r["playbook"]["reason"]}
        return brief


# Models trained once and shipped with the code (git and the image), so a new server need not train.
# One trained on this server (data/models) wins over them.
BUNDLED_MODELS = Path(__file__).resolve().parent.parent / "models"


def model_paths(model_dir: Path, symbol: str, bundled: Path = BUNDLED_MODELS) -> list[tuple[Path, str]]:
    """Every file that could serve this symbol, best first. More than one, because the better file is
    not always the usable one: a model trained on this server before a MODEL_VERSION bump is refused
    by the loader, and falling back to the shipped one is better than showing an error on a page that
    has a working model sitting next to it."""
    name = f"forecast_{symbol}.joblib"
    return [(p, s) for p, s in ((model_dir / name, "trained here"), (bundled / name, "bundled")) if p.exists()]


def model_path(model_dir: Path, symbol: str, bundled: Path = BUNDLED_MODELS) -> tuple[Path | None, str]:
    """The file that would be tried first, whether or not this code can load it."""
    found = model_paths(model_dir, symbol, bundled)
    return found[0] if found else (None, "")


def model_dir_for(db_path: str) -> Path:
    """Trained models live next to the database (data/models), on the same volume."""
    return Path(db_path).parent / "models"


def for_system(system: Any) -> Analyst:
    """The analyst inside a single-process System: its clients, prices and options, publishing on its bus."""
    return Analyst(
        client=lambda: system.market_client, settings=lambda: system.settings, publish=system.bus.publish,
        options=lambda sym: (system.options_latest.get(sym) or {}).get("summary"),
        stats=lambda sym: (system.prices.snapshot().get(sym) or {}).get("stats"),
        model_dir=model_dir_for(system.cfg.db_path),
        digest=lambda include_account: _local_api(system).ai_digest(include_account),
    )


def _local_api(system: Any) -> Any:
    from tradebuddy.api import Api  # the API module imports the System; import it only when called

    return Api(system)


async def backfill(db_path: str, symbols: list[str], days: int, interval: str = "15m", client: DeltaClient | None = None) -> dict[str, int]:
    """Fill the bars table with `days` of history per symbol, so base rates and backtests do not
    have to wait for the system to have been running that long.

    Safe to re-run: the store ignores a bar it already has, so this only ever adds what is missing."""
    from tradebuddy.store import Store

    own = client is None
    client = client or DeltaClient("https://api.india.delta.exchange")  # history is public
    store = Store(db_path)
    step = RESOLUTION_SECONDS[interval]
    end = int(time.time()) // step * step  # the last boundary: never store a bar still forming
    start = end - days * 86_400
    written = {}
    try:
        for symbol in symbols:
            candles = await client.history_range(symbol, interval, start, end)
            oi = await client.history_range(f"OI:{symbol}", interval, start, end)
            funding = await client.history_range(f"FUNDING:{symbol}", interval, start, end)
            closed = [c for c in candles if c.time + step <= end]
            written[symbol] = await asyncio.to_thread(
                store.record_bars, symbol, interval, closed,
                {int(c.time): c.close for c in oi}, {int(c.time): c.close for c in funding},
            )
            log.info("backfill symbol=%s fetched=%d stored=%d", symbol, len(closed), written[symbol])
    finally:
        store.db.close()
        if own:
            await client.aclose()
    return written


async def train(db_path: str, symbols: list[str], days: int, client: DeltaClient | None = None) -> list[dict[str, Any]]:
    """Download `days` of 15m candles, OI and funding per symbol, train, validate and save a model each."""
    from tradebuddy.forecast import Forecaster

    own = client is None
    client = client or DeltaClient("https://api.india.delta.exchange")  # history is public; live data trains best
    end = int(time.time())
    start = end - days * 86_400
    cards = []
    try:
        for symbol in symbols:
            log.info("train_fetch symbol=%s days=%d", symbol, days)
            candles = await client.history_range(symbol, "15m", start, end)
            oi = await client.history_range(f"OI:{symbol}", "15m", start, end)
            funding = await client.history_range(f"FUNDING:{symbol}", "15m", start, end)
            log.info("train_fit symbol=%s candles=%d oi=%d funding=%d", symbol, len(candles), len(oi), len(funding))
            model = await asyncio.to_thread(Forecaster.train, symbol, candles, oi, funding)
            path = model.save(model_dir_for(db_path))
            log.info("train_saved symbol=%s path=%s skill=%s metrics=%s", symbol, path, model.card.skill, model.card.metrics)
            cards.append({"symbol": symbol, "path": str(path), "skill": model.card.skill, "metrics": model.card.metrics, "baselines": model.card.baselines})
    finally:
        if own:
            await client.aclose()
    return cards
