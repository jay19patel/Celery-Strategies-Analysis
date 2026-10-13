"""Market forecaster: 15-minute features -> five forecasts for the next 24 hours.

    candles + open interest + funding (Delta history, 15m)
        -> features      returns, realised vol, ATR, range, EMA distance, volume, OI change, funding, time of day
        -> targets       direction, return, absolute move, realised vol, breakout (an excursion of
                         breakout_sigma times what the bar's own volatility implies over the horizon)
        -> five gradient-boosted models, validated on the most recent quarter of the data
        -> MarketForecast for the bar that just closed

Every feature at bar t uses data up to and including bar t; every target uses bars after t. The
holdout is the latest 25% of history, with a horizon-long gap before it so no training target
overlaps a test bar. Each model is also scored against a naive baseline; a model that does not
beat its baseline is marked without skill, and the options playbook ignores it.

Options data (IV, skew, put/call) is not a model input: Delta keeps no history of it. The engine now
records it every minute (options_history), so a later model can add it. The playbook compares the
forecast realised vol with today's implied vol instead.

Needs the `ml` extra (scikit-learn). Only the analyst process imports this module.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score

from tradebuddy.delta import Candle

ANNUAL_BARS = 365 * 96
MODEL_VERSION = 2  # v2: the breakout target scales with volatility instead of a fixed 4%


@dataclass(frozen=True)
class ForecastConfig:
    horizon_bars: int = 96  # 24h of 15m bars
    # A breakout is an excursion of this many times what the bar's own volatility implies over the
    # horizon, not a fixed percentage. A fixed 4% was a different question on every symbol — over a
    # year of 15m bars it happened on 16.5% of BTC bars and 34.2% of ETH ones, so the BTC model was
    # learning a rare event and could not (holdout AUC 0.574). At 1.5 sigma both sit near 34%, the
    # same question in any regime, and both models clear the bar (0.677 / 0.686).
    breakout_sigma: float = 1.5
    breakout_floor_pct: float = 0.5  # a dead-quiet window must not make the threshold meaningless
    holdout: float = 0.25
    min_rows: int = 2000
    # A model has skill when it beats these on the holdout.
    min_direction_auc: float = 0.56  # 0.55 is a coin with a slight bend; spreads need better than that
    # AUC alone passed a model that was not beating the drift. Over the last quarter of a year BTC
    # closed higher 24h later on 54.8% of bars and ETH on 56.2%, so "always up" scored 54.8% / 56.2%
    # while the models scored 56.4% / 54.8% — one barely ahead, one behind, both with a respectable
    # AUC. Direction must also beat the majority call by this margin, or it is the drift wearing a
    # model's coat. The horizon overlaps 96 bars, so the holdout holds ~90 independent outcomes and
    # anything under a couple of points is noise.
    min_direction_edge: float = 0.02
    min_breakout_auc: float = 0.60
    max_error_ratio: float = 0.95  # model MAE / baseline MAE
    random_state: int = 42


FEATURES = (
    "ret_1", "ret_4", "ret_16", "ret_96", "rv_16", "rv_96", "rv_384", "rv_ratio", "atr_pct", "range_pos_96",
    "dist_ema20", "dist_ema50", "dist_ema200", "volume_z_96", "oi_chg_4", "oi_chg_96", "funding", "funding_avg_96",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
)


# -- features and targets ---------------------------------------------------------


def _shift_ratio(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    out[n:] = x[n:] / x[:-n] - 1
    return out


def _rolling(x: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if len(x) < n:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(x, n)
    out[n - 1 :] = fn(windows, axis=1)
    return out


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(x, np.nan)
    if len(x) < n:
        return out
    k = 2 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def _align(series: list[Candle] | None, times: np.ndarray) -> np.ndarray:
    """Value of a side series (OI, funding) at each candle time, carried forward over gaps."""
    out = np.full(len(times), np.nan)
    if not series:
        return out
    known = {c.time: c.close for c in series}
    last = np.nan
    for i, t in enumerate(times):
        last = known.get(int(t), last)
        out[i] = last
    return out


def features(candles: list[Candle], oi: list[Candle] | None = None, funding: list[Candle] | None = None) -> np.ndarray:
    """One row per candle, columns in FEATURES order. Rows without enough history hold NaN."""
    t = np.array([c.time for c in candles], dtype=float)
    h, lo, c = (np.array([getattr(b, k) for b in candles], dtype=float) for k in ("high", "low", "close"))
    v = np.array([b.volume for b in candles], dtype=float)
    logret = np.full_like(c, np.nan)
    logret[1:] = np.log(c[1:] / c[:-1])

    def rv(n: int) -> np.ndarray:
        return _rolling(logret, n, lambda w, axis: np.std(w, axis=axis, ddof=1)) * math.sqrt(ANNUAL_BARS)

    prev = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - prev), np.abs(lo - prev)]), axis=0)
    hi96, lo96 = _rolling(h, 96, np.max), _rolling(lo, 96, np.min)
    lv = np.log1p(v)
    sd = _rolling(lv, 96, np.std)
    with np.errstate(invalid="ignore", divide="ignore"):
        vz = np.where(sd > 0, (lv - _rolling(lv, 96, np.mean)) / sd, 0.0)
    oi_s, fund = _align(oi, t), _align(funding, t)
    hours = (t % 86_400) / 3600
    dow = ((t // 86_400) + 3) % 7  # 1970-01-01 was a Thursday
    rv16, rv96, rv384 = rv(16), rv(96), rv(384)
    cols = {
        "ret_1": _shift_ratio(c, 1), "ret_4": _shift_ratio(c, 4), "ret_16": _shift_ratio(c, 16), "ret_96": _shift_ratio(c, 96),
        "rv_16": rv16, "rv_96": rv96, "rv_384": rv384, "rv_ratio": rv16 / rv384,
        "atr_pct": _ema(tr, 14) / c, "range_pos_96": (c - lo96) / np.where(hi96 > lo96, hi96 - lo96, np.nan),
        "dist_ema20": c / _ema(c, 20) - 1, "dist_ema50": c / _ema(c, 50) - 1, "dist_ema200": c / _ema(c, 200) - 1,
        "volume_z_96": vz, "oi_chg_4": _shift_ratio(oi_s, 4), "oi_chg_96": _shift_ratio(oi_s, 96),
        "funding": fund, "funding_avg_96": _rolling(fund, 96, np.mean),
        "hour_sin": np.sin(2 * np.pi * hours / 24), "hour_cos": np.cos(2 * np.pi * hours / 24),
        "dow_sin": np.sin(2 * np.pi * dow / 7), "dow_cos": np.cos(2 * np.pi * dow / 7),
    }
    return np.column_stack([cols[name] for name in FEATURES])


def breakout_threshold(rv_96: np.ndarray, horizon: int, cfg: ForecastConfig) -> np.ndarray:
    """How far price must travel, as a fraction, to count as a breakout from a bar.

    `rv_96` is the annualised realised vol over the last day, the same column the model is fed.
    Scaled down to the horizon it is what a normal move over that horizon looks like; the threshold
    is `breakout_sigma` of those. Training and the live label read this one function, so the number
    on the dashboard is the number the model was taught."""
    with np.errstate(invalid="ignore"):
        return np.maximum(cfg.breakout_sigma * rv_96 * math.sqrt(horizon / ANNUAL_BARS), cfg.breakout_floor_pct / 100)


def _rv_96(candles: list[Candle]) -> np.ndarray:
    """The realised-vol column, read back from the feature matrix so there is one definition of it."""
    return features(candles)[:, FEATURES.index("rv_96")]


def targets(candles: list[Candle], horizon: int, cfg: ForecastConfig) -> dict[str, np.ndarray]:
    """What happened over the `horizon` bars after each bar. NaN where the future is not known yet."""
    c = np.array([b.close for b in candles], dtype=float)
    h = np.array([b.high for b in candles], dtype=float)
    lo = np.array([b.low for b in candles], dtype=float)
    n = len(c)
    fut_ret = np.full(n, np.nan)
    fut_rv = np.full(n, np.nan)
    excursion = np.full(n, np.nan)
    logret = np.diff(np.log(c))
    for i in range(n - horizon):
        fut_ret[i] = c[i + horizon] / c[i] - 1
        fut_rv[i] = np.std(logret[i : i + horizon], ddof=1) * math.sqrt(ANNUAL_BARS)
        excursion[i] = max(h[i + 1 : i + horizon + 1].max() / c[i] - 1, 1 - lo[i + 1 : i + horizon + 1].min() / c[i])
    threshold = breakout_threshold(_rv_96(candles), horizon, cfg)
    known = ~np.isnan(fut_ret) & ~np.isnan(threshold)
    return {
        "direction": np.where(known, (fut_ret > 0).astype(float), np.nan),
        "return": fut_ret,
        "abs_move": np.abs(fut_ret),
        "realized_vol": fut_rv,
        "breakout": np.where(known, (excursion >= threshold).astype(float), np.nan),
        "breakout_threshold": threshold,
    }


# -- the model ----------------------------------------------------------------------


# A 24h target on a crypto perpetual is mostly noise, and a tree deep enough to fit the training
# regime takes the noise with it: at 250 iterations and 15 leaves the direction model scored 0.513
# AUC on BTC and 0.475 on ETH — worse than a coin on data it had not seen. The same features in a
# model a third the size score 0.567 and 0.548, and every regressor improves too. Small on purpose.
CAPACITY = {"max_iter": 100, "learning_rate": 0.03, "max_leaf_nodes": 5, "l2_regularization": 10.0, "min_samples_leaf": 500}


def _classifier(cfg: ForecastConfig) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(random_state=cfg.random_state, **CAPACITY)


def _regressor(cfg: ForecastConfig, loss: str = "absolute_error") -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(loss=loss, random_state=cfg.random_state, **CAPACITY)


@dataclass
class ModelCard:
    """What was trained on what, and how it did on data it never saw."""

    symbol: str
    version: int = MODEL_VERSION
    trained_at: float = field(default_factory=time.time)
    config: dict[str, Any] = field(default_factory=dict)
    train_from: int = 0
    train_to: int = 0
    rows_train: int = 0
    rows_test: int = 0
    features: list[str] = field(default_factory=list)  # the columns it was fitted on: series with no data are left out
    metrics: dict[str, float] = field(default_factory=dict)
    baselines: dict[str, float] = field(default_factory=dict)
    skill: dict[str, bool] = field(default_factory=dict)


class Forecaster:
    def __init__(self, models: dict[str, Any], card: ModelCard, cfg: ForecastConfig) -> None:
        self.models, self.card, self.cfg = models, card, cfg

    # -- training -----------------------------------------------------------------

    @classmethod
    def train(
        cls, symbol: str, candles: list[Candle], oi: list[Candle] | None = None, funding: list[Candle] | None = None,
        cfg: ForecastConfig | None = None,
    ) -> Forecaster:
        cfg = cfg or ForecastConfig()
        X = features(candles, oi, funding)
        Y = targets(candles, cfg.horizon_bars, cfg)
        rows = np.where(~np.isnan(Y["return"]) & ~np.isnan(Y["breakout"]) & ~np.isnan(X[:, list(FEATURES).index("rv_384")]))[0]
        if len(rows) < cfg.min_rows:
            raise ValueError(f"{symbol}: {len(rows)} usable rows, need {cfg.min_rows} (about {cfg.min_rows // 96} days of 15m history)")
        # A side series that never arrived (OI, funding) is an all-NaN column: leave it out rather than fail.
        used = [i for i in range(X.shape[1]) if np.isfinite(X[rows, i]).any()]
        rv96 = X[:, list(FEATURES).index("rv_96")]
        X = X[:, used]
        split = rows[int(len(rows) * (1 - cfg.holdout))]
        train = rows[rows < split - cfg.horizon_bars]  # purge: no training target reaches into the test period
        test = rows[rows >= split]

        models = {
            "direction": _classifier(cfg), "breakout": _classifier(cfg),
            "return": _regressor(cfg), "abs_move": _regressor(cfg), "realized_vol": _regressor(cfg),
        }
        metrics: dict[str, float] = {}
        baselines: dict[str, float] = {}
        horizon_scale = math.sqrt(cfg.horizon_bars / ANNUAL_BARS)
        for name, model in models.items():
            model.fit(X[train], Y[name][train])
        p_dir = models["direction"].predict_proba(X[test])[:, 1]
        p_brk = models["breakout"].predict_proba(X[test])[:, 1]
        y_brk = Y["breakout"][test]
        metrics["direction_auc"] = float(roc_auc_score(Y["direction"][test], p_dir))
        metrics["direction_accuracy"] = float(np.mean((p_dir > 0.5) == (Y["direction"][test] > 0.5)))
        metrics["breakout_auc"] = float(roc_auc_score(y_brk, p_brk)) if 0 < y_brk.mean() < 1 else 0.5
        metrics["breakout_rate"] = float(y_brk.mean())  # near 0 or 1 means the threshold asks a question with one answer
        metrics["breakout_threshold_pct"] = float(100 * np.median(Y["breakout_threshold"][test]))
        metrics["return_mae"] = float(mean_absolute_error(Y["return"][test], models["return"].predict(X[test])))
        metrics["abs_move_mae"] = float(mean_absolute_error(Y["abs_move"][test], models["abs_move"].predict(X[test])))
        metrics["realized_vol_mae"] = float(mean_absolute_error(Y["realized_vol"][test], models["realized_vol"].predict(X[test])))
        # Naive baselines: always call the side that came up most; no change; today's realised vol
        # carries on (a normal move of that size).
        up_rate = float(Y["direction"][test].mean())
        baselines["direction_accuracy"] = max(up_rate, 1 - up_rate)
        baselines["return_mae"] = float(np.mean(np.abs(Y["return"][test])))
        baselines["abs_move_mae"] = float(mean_absolute_error(Y["abs_move"][test], rv96[test] * horizon_scale * math.sqrt(2 / math.pi)))
        baselines["realized_vol_mae"] = float(mean_absolute_error(Y["realized_vol"][test], rv96[test]))
        skill = {
            # Both, not either: ranking the bars well is worth nothing if the calls do not beat
            # the side that simply came up more often.
            "direction": metrics["direction_auc"] >= cfg.min_direction_auc
            and metrics["direction_accuracy"] >= baselines["direction_accuracy"] + cfg.min_direction_edge,
            "breakout": metrics["breakout_auc"] >= cfg.min_breakout_auc,
            "return": metrics["return_mae"] <= cfg.max_error_ratio * baselines["return_mae"],
            "abs_move": metrics["abs_move_mae"] <= cfg.max_error_ratio * baselines["abs_move_mae"],
            "realized_vol": metrics["realized_vol_mae"] <= cfg.max_error_ratio * baselines["realized_vol_mae"],
        }
        # Validated: refit on everything, so the live model has seen the latest regime.
        for name, model in models.items():
            model.fit(X[rows], Y[name][rows])
        card = ModelCard(
            symbol=symbol, config=asdict(cfg), train_from=int(candles[rows[0]].time), train_to=int(candles[rows[-1]].time),
            rows_train=len(train), rows_test=len(test), features=[FEATURES[i] for i in used], metrics={k: round(v, 5) for k, v in metrics.items()},
            baselines={k: round(v, 5) for k, v in baselines.items()}, skill=skill,
        )
        return cls(models, card, cfg)

    # -- live ---------------------------------------------------------------------

    def predict(self, candles: list[Candle], oi: list[Candle] | None = None, funding: list[Candle] | None = None) -> dict[str, Any]:
        """Forecast from the last closed bar. Needs about 400 bars of history (EMA200, 4-day vol)."""
        x = features(candles, oi, funding)[-1:]
        if np.isnan(x[0, list(FEATURES).index("rv_384")]):
            raise ValueError(f"need at least 400 bars of history, got {len(candles)}")
        rv_96 = float(x[0, FEATURES.index("rv_96")])
        x = x[:, [FEATURES.index(name) for name in self.card.features]]
        up = float(self.models["direction"].predict_proba(x)[0, 1])
        # The same threshold the model was trained on, at this bar's volatility, so the dashboard can
        # say what "breakout" means right now instead of quoting a constant.
        threshold = float(breakout_threshold(np.array([rv_96]), self.cfg.horizon_bars, self.cfg)[0])
        return {
            "bar_time": candles[-1].time,
            "horizon_hours": self.cfg.horizon_bars / 4,
            "up_probability": round(up, 4),
            "down_probability": round(1 - up, 4),
            "expected_return": round(float(self.models["return"].predict(x)[0]), 5),
            "expected_abs_move": round(max(0.0, float(self.models["abs_move"].predict(x)[0])), 5),
            "predicted_realized_vol": round(max(0.0, float(self.models["realized_vol"].predict(x)[0])), 4),
            "breakout_probability": round(float(self.models["breakout"].predict_proba(x)[0, 1]), 4),
            "breakout_pct": round(100 * threshold, 2),
            "breakout_sigma": self.cfg.breakout_sigma,
            "skill": self.card.skill,
            "trained_at": self.card.trained_at,
        }

    # -- files --------------------------------------------------------------------

    @staticmethod
    def path(model_dir: Path, symbol: str) -> Path:
        return model_dir / f"forecast_{symbol}.joblib"

    def save(self, model_dir: Path) -> Path:
        model_dir.mkdir(parents=True, exist_ok=True)
        path = self.path(model_dir, self.card.symbol)
        tmp = path.with_suffix(".tmp")
        joblib.dump({"models": self.models, "card": asdict(self.card), "config": asdict(self.cfg)}, tmp)
        tmp.replace(path)  # the analyst never reads a half-written file
        path.with_suffix(".json").write_text(json.dumps(asdict(self.card), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> Forecaster:
        data = joblib.load(path)  # our own file, written by save(); joblib files are pickles: never load one from elsewhere
        card = ModelCard(**data["card"])
        if card.version != MODEL_VERSION:
            raise ValueError(f"{path.name} is model version {card.version}, this code expects {MODEL_VERSION}: retrain")
        return cls(data["models"], card, ForecastConfig(**data["config"]))
