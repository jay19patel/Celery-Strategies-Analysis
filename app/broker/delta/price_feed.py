"""Delta Exchange public WebSocket price feed with ZeroMQ PUB broadcaster.

Connects to the Delta Exchange India public ticker WebSocket (no API keys required).
Maintains an in-memory full ticker cache per symbol and publishes every update
via ZeroMQ PUB on tcp://0.0.0.0:5557 for downstream consumers (OpenAlgo pattern).

Topic format: price.<SYMBOL>  (e.g.  price.BTCUSD)
Payload:      JSON-encoded full ticker dict including mark_price, volume, oi, etc.

This module is intentionally decoupled from the authenticated DeltaWebSocketClient
so that live market data works even when the REST API IP is not whitelisted.
"""

import json
import logging
import threading
import time
from typing import Any

try:
    import websocket
except ImportError:
    websocket = None  # type: ignore[assignment]

try:
    import zmq  # type: ignore[import]
except ImportError:
    zmq = None  # type: ignore[assignment]

from app.core.settings import get_symbols, settings

logger = logging.getLogger(__name__)

# ─── In-process caches ────────────────────────────────────────────────────────

# Simple price map: { "BTCUSD": 65432.5 }
_live_prices: dict[str, float] = {}

# Full ticker objects: { "BTCUSD": { mark_price, volume, oi, funding_rate, ... } }
_live_tickers: dict[str, dict[str, Any]] = {}

# Per-symbol price history ring-buffer (last 60 ticks) for sparklines
_price_history: dict[str, list[float]] = {}
_HISTORY_LEN = 60

_cache_lock = threading.Lock()
_packets_published: int = 0


def get_live_price(symbol: str) -> float | None:
    """Return the latest mark price for a symbol, or None if not yet received.

    Args:
        symbol: Raw symbol string, e.g. 'BTC-USD' or 'BTCUSD'.

    Returns:
        Latest mark price as float, or None if unavailable.
    """
    clean = _normalize(symbol)
    with _cache_lock:
        return _live_prices.get(clean)


def get_live_ticker(symbol: str) -> dict[str, Any] | None:
    """Return the full ticker dict for a symbol, or None if not yet received.

    Args:
        symbol: Raw symbol string, e.g. 'BTC-USD' or 'BTCUSD'.

    Returns:
        Dict with mark_price, volume, oi, funding_rate, bid, ask, etc., or None.
    """
    clean = _normalize(symbol)
    with _cache_lock:
        return dict(_live_tickers[clean]) if clean in _live_tickers else None


def get_all_live_prices() -> dict[str, float]:
    """Return a snapshot of all currently cached live mark prices.

    Returns:
        Dict mapping symbol (e.g. 'BTCUSD') to mark price float.
    """
    with _cache_lock:
        return dict(_live_prices)


def get_all_live_tickers() -> dict[str, dict[str, Any]]:
    """Return a snapshot of all full ticker objects.

    Returns:
        Dict mapping symbol to full ticker dict (mark_price, volume, oi, etc.).
    """
    with _cache_lock:
        return {k: dict(v) for k, v in _live_tickers.items()}


def get_price_history(symbol: str) -> list[float]:
    """Return the last N mark prices for sparkline rendering.

    Args:
        symbol: Raw symbol string.

    Returns:
        List of floats (oldest → newest), up to _HISTORY_LEN entries.
    """
    clean = _normalize(symbol)
    with _cache_lock:
        return list(_price_history.get(clean, []))


def get_packets_published() -> int:
    """Return total number of ZMQ packets published since process start."""
    return _packets_published


def _normalize(symbol: str) -> str:
    """Convert any symbol format to Delta Exchange uppercase format.

    Args:
        symbol: e.g. 'BTC-USD', 'btc_usd', 'BTCUSD'

    Returns:
        Normalized form, e.g. 'BTCUSD'
    """
    return symbol.replace("-", "").replace("/", "").replace("_", "").upper()


def _build_ticker_dict(symbol: str, data: dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize all useful fields from a Delta v2/ticker message.

    Args:
        symbol: Normalized symbol string.
        data: Raw WebSocket message dict.

    Returns:
        Structured ticker dict with defaults for missing fields.
    """
    def _f(key: str, default: float = 0.0) -> float:
        v = data.get(key)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    mark_price = _f("mark_price") or _f("last_price") or _f("close")
    last_price = _f("last_price") or mark_price
    best_bid = _f("best_bid") or _f("bid_price") or mark_price
    best_ask = _f("best_ask") or _f("ask_price") or mark_price
    spread = round(best_ask - best_bid, 4) if best_ask and best_bid else 0.0
    volume = _f("volume") or _f("volume_24h") or _f("turnover_24h")
    oi = _f("oi") or _f("open_interest")
    funding_rate = _f("funding_rate")
    high_24h = _f("high") or _f("high_24h") or mark_price
    low_24h = _f("low") or _f("low_24h") or mark_price

    # 24h price change
    price_change = _f("price_change") or _f("price_change_24h")
    prev_close = _f("prev_price")
    if price_change == 0.0 and prev_close and mark_price:
        price_change = round(((mark_price - prev_close) / prev_close) * 100, 4) if prev_close else 0.0

    return {
        "symbol": symbol,
        "mark_price": mark_price,
        "last_price": last_price,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "volume_24h": volume,
        "open_interest": oi,
        "funding_rate": funding_rate,
        "high_24h": high_24h,
        "low_24h": low_24h,
        "price_change_pct": price_change,
        "timestamp": time.time(),
    }


class DeltaLivePriceFeed:
    """Public WebSocket price feed for Delta Exchange India.

    Subscribes to the v2/ticker channel for all configured symbols without
    requiring authentication. Full ticker objects (mark price, volume, OI,
    funding rate, bid/ask) are cached in-process and broadcast via ZeroMQ PUB.

    Architecture (OpenAlgo-style ZMQ pattern):
        Delta WSS → DeltaLivePriceFeed → ZMQ PUB :5557
                                        → _live_tickers (in-memory)
                                        → _price_history (sparkline ring-buffer)
        /ws/live broadcaster ← get_all_live_tickers() (every 1s)
    """

    def __init__(self, symbols: list[str] | None = None) -> None:
        """Initialize the price feed.

        Args:
            symbols: List of symbols (e.g. ['BTC-USD', 'ETH-USD']). Defaults to
                     all symbols from settings.
        """
        self._symbols = symbols or get_symbols()
        self._delta_symbols = [_normalize(s) for s in self._symbols]
        self._ws_url = settings.delta_websocket_url
        self._running = False
        self._is_connected = False
        self._reconnect_count = 0
        self._messages_received = 0
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        self._ws: Any = None

        # ZeroMQ PUB socket setup (best-effort)
        self._zmq_ctx: Any = None
        self._zmq_pub: Any = None
        self._zmq_port = 5557
        self._zmq_bound = False
        self._init_zmq()

    def _init_zmq(self) -> None:
        """Set up ZeroMQ PUB socket for broadcasting ticker updates."""
        if zmq is None:
            logger.info("ZeroMQ not installed — price updates will not be published via ZMQ.")
            return
        try:
            self._zmq_ctx = zmq.Context()
            self._zmq_pub = self._zmq_ctx.socket(zmq.PUB)
            self._zmq_pub.bind(f"tcp://0.0.0.0:{self._zmq_port}")
            self._zmq_bound = True
            logger.info("price_feed_zeromq_bound port=%s", self._zmq_port)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not bind ZeroMQ PUB socket: %s", exc)
            self._zmq_bound = False

    def start(self) -> None:
        """Start the WebSocket price feed in a background daemon thread."""
        if websocket is None:
            logger.warning("websocket-client not installed — Delta live price feed disabled.")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="DeltaPriceFeed",
        )
        self._thread.start()
        logger.info(
            "delta_price_feed_started symbols=%s",
            ", ".join(self._delta_symbols),
        )

    def stop(self) -> None:
        """Stop the WebSocket price feed."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        logger.info("delta_price_feed_stopped")

    def update_symbols(self, symbols: list[str]) -> None:
        """Reload the symbol list and re-subscribe (for settings changes).

        Args:
            symbols: New list of symbols from settings.
        """
        self._symbols = symbols
        self._delta_symbols = [_normalize(s) for s in symbols]
        if self._ws and self._is_connected:
            try:
                payload = {
                    "type": "subscribe",
                    "payload": {"channels": [
                        {"name": "v2/ticker", "symbols": self._delta_symbols},
                    ]},
                }
                self._ws.send(json.dumps(payload))
                logger.info("delta_price_feed_resubscribed symbols=%s", self._delta_symbols)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to re-subscribe: %s", exc)

    def _run_loop(self) -> None:
        """Reconnect loop — re-establishes the connection on any disconnect."""
        while self._running:
            try:
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=8)
            except Exception:  # noqa: BLE001
                logger.exception("Delta price feed WebSocket loop error")

            if self._running:
                self._reconnect_count += 1
                backoff = min(30, 5 * self._reconnect_count)
                logger.info(
                    "delta_price_feed_reconnecting delay_seconds=%s attempt=%s",
                    backoff, self._reconnect_count,
                )
                time.sleep(backoff)

    def _on_open(self, ws: Any) -> None:
        """Send subscription payload on connection open."""
        self._is_connected = True
        self._reconnect_count = 0  # Reset on successful connect
        logger.info("delta_price_feed_connected channel=v2/ticker")
        payload = {
            "type": "subscribe",
            "payload": {"channels": [
                {"name": "v2/ticker", "symbols": self._delta_symbols},
            ]},
        }
        ws.send(json.dumps(payload))

    def _on_message(self, ws: Any, raw_message: str) -> None:
        """Parse full ticker tick and update all caches + publish via ZMQ."""
        global _packets_published
        self._messages_received += 1

        try:
            data = json.loads(raw_message)
        except (json.JSONDecodeError, Exception):  # noqa: BLE001
            return

        msg_type = data.get("type", "")
        if msg_type != "v2/ticker":
            return

        symbol = str(data.get("symbol", "")).upper()
        if not symbol:
            return

        ticker = _build_ticker_dict(symbol, data)
        mark_price = ticker["mark_price"]
        if not mark_price:
            return

        # Update caches atomically
        with _cache_lock:
            _live_prices[symbol] = mark_price
            _live_tickers[symbol] = ticker
            # Ring-buffer history for sparklines
            hist = _price_history.setdefault(symbol, [])
            hist.append(mark_price)
            if len(hist) > _HISTORY_LEN:
                del hist[:-_HISTORY_LEN]

        # Publish via ZMQ PUB (OpenAlgo pattern: topic = "price.BTCUSD")
        if self._zmq_pub and self._zmq_bound:
            try:
                topic = f"price.{symbol}"
                self._zmq_pub.send_multipart(
                    [topic.encode(), json.dumps(ticker).encode()],
                    flags=zmq.NOBLOCK if zmq else 0,
                )
                _packets_published += 1
            except Exception:  # noqa: BLE001
                pass  # Never crash the feed on ZMQ error

    def _on_error(self, ws: Any, error: Exception) -> None:
        """Log WebSocket errors."""
        self._last_error = str(error)
        logger.warning("delta_price_feed_error error=%s", error)

    def _on_close(self, ws: Any, close_code: Any, close_msg: Any) -> None:
        """Handle WebSocket disconnection."""
        self._is_connected = False
        logger.info("delta_price_feed_disconnected code=%s message=%s", close_code, close_msg)

    def get_status(self) -> dict[str, Any]:
        """Return status telemetry for the system monitor dashboard.

        Returns:
            Dict with connection state, packet counts, ZMQ binding info, and live prices.
        """
        return {
            "is_running": self._running,
            "is_connected": self._is_connected,
            "is_bound": self._zmq_bound,
            "ws_url": self._ws_url,
            "symbols": self._delta_symbols,
            "messages_received": self._messages_received,
            "packets_published": _packets_published,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "live_prices": get_all_live_prices(),
            "live_tickers": get_all_live_tickers(),
        }


# ─── Singleton ────────────────────────────────────────────────────────────────

_price_feed_instance: DeltaLivePriceFeed | None = None
_price_feed_lock = threading.Lock()


def get_delta_price_feed() -> DeltaLivePriceFeed:
    """Singleton accessor for DeltaLivePriceFeed.

    Returns:
        The global DeltaLivePriceFeed instance (created on first call).
    """
    global _price_feed_instance
    if _price_feed_instance is None:
        with _price_feed_lock:
            if _price_feed_instance is None:
                _price_feed_instance = DeltaLivePriceFeed()
    return _price_feed_instance
