# Analytics Plan

How TradeBuddy goes from "5 EMA cross + PCR se trade le leta hai" to a system that
**measures the market, scores itself, and then either trades on that or hands it to a person**.

Written against the code as it stands on `v2`. Every file path below is real; every number is
either measured or derived from the current defaults (2 symbols, 15m bars).

## Status

| Phase | State |
| --- | --- |
| **0** — Lock the dashboard, clean the box | **done** — private by default, `/ws` gated, dead files gone |
| **1** — The history layer | **done** — `bars`, `market_snapshots`, `process_health`, retention, rate counters |
| 2 — The conviction engine | **partly** — `outlook.py` does the multi-timeframe read and counted odds; weights not fitted |
| 2b — Indicators, patterns, labels | **partly** — `structure.py` (BOS/CHoCH/FVG/squeeze) and MTF done; triple-barrier labels not |
| 3 — The scorecard | **partly** — `predictions` stores and settles every card verdict; calibration not drawn |
| 4 — The advisor UI | **partly** — Market page Outlook, structure chart, verdict badges; `/accuracy` page not built |
| 5 — Auto mode | not started |
| 6 — Models and training | not started |

Phase 1 stores from now on. For base rates sooner than three months:
`docker compose run --rm analyst backfill --days 365`.

---

## 0. Read this first: what already exists

A lot of what the ask describes is already built. The plan **extends** it; it does not rebuild it.

| Piece | Where | State |
| --- | --- | --- |
| Market numbers from candles/OI/funding/options | `insights.market_context` | Done — ~45 named numbers |
| Rule findings with severity + bias | `insights.insights`, `insights.lean` | Done — ~15 rules |
| 24h forecaster (5 models, holdout, skill gates) | `forecast.py` | Done, honest about having no direction skill |
| "Kal jo bola tha woh hua?" | `forecasts` table + `System.score_forecasts` + `insights.score_forecasts` | **Done, but only for the forecaster** |
| Options structure suggestion | `playbook.decide` | Done |
| Auto-trading from analysis | `trading.AutoStructures` | **Done, but only options structures on paper** |
| Mistral narrative | `tbai.py` + `mistral.py`, 137/141 OK | Done |
| Hand-rolled SVG charts | `static/js/analysis.js` (`TBA.*`) | ~8 charts, no library |
| Per-process CPU / RSS / loop lag | `system.Monitor` | Measured every 2s, **never stored** |
| Retention + hourly prune job | `store.RETENTION_DAYS`, `System.housekeeping` | Done for 4 tables |

### The six real gaps

1. **Nothing historical is stored except options and forecasts.** There is no OHLCV table. Every
   analysis cycle re-downloads 700 bars from Delta REST, and `MarketAnalysis` events are deleted
   after 3 days. So nothing can be backtested, no base rate can be computed, and a Delta outage
   erases the analysis for that window permanently.
2. **Only the forecaster is scored.** The rules, the lean, the playbook and the AI make claims
   every 5 minutes that nobody ever checks.
3. **No probability is offered to a person.** The page shows findings and a model's confidence. It
   never says "is setup me 412 baar aisa hua, 58% baar upar gaya, baseline 54%".
4. **Analysis cannot trade the perpetual.** Only options structures auto-trade.
5. **Process load is not stored**, so "pichle 2 din ka CPU dikhao" has no answer
   (`analytics.py` says this outright in its docstring).
6. **Thin indicators, no patterns, and the wrong outcome.** ~45 numbers with no trend-strength
   measure at all, no market-structure detection (breakout / break-in), everything on a single
   15-minute timeframe — and the one outcome that *is* measured asks "did the close go up in 24h"
   while the Trader actually places a stop and a target. §5 fixes all four, mostly by porting work
   that already exists in **Master Backtester**.

---

## 1. Shape of the system

Ek analysis engine, uske do consumers. Dono ek hi number dekhte hain — warna auto aur manual
alag-alag baat karenge aur kisi par bharosa nahi rahega.

```
         bars + OI + funding + options chain            (stored, not just fetched)
              │                    │
              │                    └─► 1h / 4h / 1d resample (shift 1)
              ▼
     insights.market_context  +  indicators  +  patterns      §5
     ~45 numbers                 ADX, bands,    BOS, CHoCH,
                                 volume, candle FVG, squeeze,
                                 anatomy, MTF   wall break
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   insights()      Forecaster      options book         rules · model · chain
         └──────────────┼──────────────┘
                        ▼
              conviction.score()          ← NEW, pure, deterministic, no network
          side · score −100..+100 · drivers[] · empirical probability + n
                        │
         ┌──────────────┴──────────────┐
         ▼                             ▼
   AUTO  analysis_edge strategy   ADVISOR  Market Data page
         → Trader (existing gate)          → dial, evidence, probability,
         → Executor → broker                  scorecard, "what I said vs what happened"
                        │
                        ▼
          predictions + patterns tables   ← every claim, by every source,
          settled by one job, scored on a triple barrier (§5.4)
```

**Rule:** `conviction.score()` is one pure function, like `tb_master.decide()`. It runs in the
analyst, on a Celery worker, and in the backtest, and gives the same answer every time. The UI
renders its output; it never computes its own version.

---

## 2. Phase 0 — Lock the dashboard, clean the box  (P0, do this first)

The dashboard is on the public internet at `https://tb.njtechstudio.in`. Writes are protected.
Reads are not, and that is where every analytics number this plan adds will live.

### What is open right now

`app.py:196` — the middleware lets every `GET` through unless the path is in `LOCKED_READS`
(only `/api/settings`):

```python
if path.startswith("/api/") and path not in PUBLIC_WRITES and (request.method not in READ_METHODS or path in LOCKED_READS):
    return JSONResponse({"detail": LOCKED}, status_code=401)
```

So, with no cookie: `/api/account` (balance, realized PnL, margin), `/api/positions` (entry,
liquidation, unrealised), `/api/paper/history`, `/api/orders`, `/api/journal/summary`, and the
pages `/`, `/positions`, `/account`, `/journal` all return 200.

`/ws` is worse than the GETs: it is not under `/api/`, so the middleware never looks at it, and it
streams **every event live** — `PositionClosed` carries entry, exit and PnL (`app.py:511`).

`DASHBOARD_LOOPBACK_ONLY: "true"` in `docker-compose.yml` makes `config.require_safe_bind` pass
without an `API_TOKEN`. That flag was true when it was written and is now false — nginx fronts the
port. It is giving a safety check that no longer checks anything.

### Fix (pick one, then do the hygiene)

**Recommended — flip the default in the app.** Invert the middleware: authenticated by default,
with a small public allowlist (`/login`, `/static/*`, `/api/auth/*`, `/healthz`). One line of
policy, applies to pages, API and the WebSocket alike, and survives an nginx config being
rewritten. Add an auth check at the top of the `/ws` handler before `accept()`.

Alternative — nginx basic-auth or an IP allowlist in front. Faster, but it is a second place where
access policy lives, and the app still believes it is on loopback.

Do both if you want defence in depth. **Either way this lands before Phase 4**, because Phase 4
adds a dozen new read routes.

### Also in Phase 0

- **Confirm the PIN is not `242425`.** `auth_pin_hash` is set in `settings` (the row count going
  14 → 15 is consistent with a PIN change through the UI), but `config.auth_pin` still defaults to
  the compose value and `api.get_auth_status` reports `has_custom` separately. Verify, don't assume.
- **Set `AUTH_SECRET` explicitly in `.env`.** It is currently derived from
  `sha256("tb_auth_seed:" + API_TOKEN + DB_PATH)` (`config.py:80`). With no `API_TOKEN` set, that
  seed is guessable from public information, and it signs the 7-day session cookies.
- **Disk: 74% of 20G used, but not by TradeBuddy.** The DB is under 1 MB and this plan takes it to
  ~180 MB in a year (§3.4). The 14G is Docker — `docker compose up` rebuilds the image on every
  deploy and the builder cache is never trimmed. Add `docker system prune -af --filter "until=72h"`
  to the deploy script and reclaim it before adding anything.
- **Dead files, delete them:** `logs/` (last written 28 Sep; nothing writes it any more — logging
  is `basicConfig` to stdout, `__main__.py:22`, and Docker rotates at 10 MB × 3),
  `data/test_*.py` (6 scratch scripts), `data/celerybeat-schedule*`, `data/stockanalysis.db`.
  Add `logs/` and `data/*.db*` to `.gitignore`.
- **Memory is the binding constraint: 229 Mi available, worker at 105 MiB.** Do not raise worker
  concurrency. Every design choice below respects this — see §3.5.

**Tests:** `test_auth.py` — an unauthenticated GET of `/api/account`, `/api/positions` and `/` is
401/303; `/login`, `/static/app.css` and `/healthz` still 200; an unauthenticated `/ws` connect is
refused before any event is sent.

---

## 3. Phase 1 — The history layer

Without this nothing else in the plan is possible: no base rates, no backtest, no retraining
without re-downloading, no "2 din ka CPU".

### 3.1 `bars` — the OHLCV we never kept

```sql
CREATE TABLE bars (
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,          -- '15m'
    time        INTEGER NOT NULL,          -- bar open, epoch seconds
    open, high, low, close, volume  REAL NOT NULL,
    oi          REAL,                      -- from OI:<symbol>, carried forward
    funding     REAL,                      -- from FUNDING:<symbol>
    PRIMARY KEY (symbol, interval, time)
);
```

Written by the **engine** on `CandleClosed` (engine is the only writer — CLAUDE.md), backfilled
once from `DeltaClient.history_range` for 365 days. `INSERT OR IGNORE`, so a backfill and the live
writer never fight.

What it unlocks: training and backtesting stop depending on Delta being up; a gap in the feed
becomes visible instead of silently changing a model's input; `_score_once` stops re-fetching
candles it already had.

### 3.2 `market_snapshots` — the analysis, kept

Every `MarketAnalysis` currently lives 3 days in the event log as a JSON blob and then is gone.
Store it properly instead:

```sql
CREATE TABLE market_snapshots (
    symbol        TEXT    NOT NULL,
    bar_time      INTEGER NOT NULL,
    ts            REAL    NOT NULL,
    keep          INTEGER NOT NULL DEFAULT 0,   -- 1 on a 15-minute boundary
    price, change_1h_pct, change_24h_pct, rv_24h, rv_7d, atr_pct, range_pos_24h,
    ema20, ema50, ema200, volume_z_1h,
    oi_change_1h_pct, oi_change_24h_pct, funding_avg_24h_pct, basis_pct,
    atm_iv, iv_rv_ratio, skew_25d, pcr_oi, pcr_volume, implied_move_pct,
    max_pain_gap_pct, call_wall_gap_pct, put_wall_gap_pct, term_slope   REAL,
    insight_keys  TEXT NOT NULL,     -- 'iv_rich,funding_high,trend' — short, greppable
    lean          REAL,              -- bullish weight − bearish weight
    conviction    REAL,              -- conviction.score()
    conviction_side TEXT,
    PRIMARY KEY (symbol, bar_time, ts)
);
```

**Typed columns, not a JSON blob.** The whole point of this table is SQL like
`WHERE iv_rv_ratio > 1.3 AND range_pos_24h > 0.9` running over a year of rows to produce a base
rate. JSON extraction would make that scan unusable and the rows 3× fatter.

Retention mirrors `options_history`: `keep = 1` rows forever, the rest 30 days.

### 3.3 `process_health` — so CPU over 2 days is answerable

```sql
CREATE TABLE process_health (
    role TEXT NOT NULL, ts REAL NOT NULL,
    cpu_pct, memory_mb, loop_lag_max_ms REAL,
    PRIMARY KEY (role, ts)
);
```

One row per process per 5 minutes, written by the engine from the heartbeats it already receives
(`System.on_process`). 30 days. Answers "CPU 2 din me kaisa raha", and on a 229 Mi box the
`memory_mb` series is the one that will actually save you.

### 3.4 Storage arithmetic — the honest answer

Aapka sawaal tha: "100 din chalega to kitna bada ho jayega?" Numbers, 2 symbols, current defaults:

| Table | rows/day | ≈ bytes/row | /day | retention | steady state |
| --- | ---: | ---: | ---: | --- | ---: |
| `events` | ~800 | 0.3–5 KB | 2.9 MB | 2–90 d by type | **~18 MB** |
| `ai_reports` | 96 | ~4 KB | 0.4 MB | 90 d / failed 14 d | **~35 MB** |
| `options_history` | 2,880 + 192 | ~130 B | 0.4 MB | 30 d / 15m forever | ~12 MB + 9 MB/yr |
| `forecasts` | 192 | ~200 B | 38 KB | 180 d | ~7 MB |
| `orders`, `paper_trades` | tens | | | forever | <1 MB/yr |
| **today's total** | | | | | **~75 MB** |
| `bars` *(new)* | 192 | ~80 B | 15 KB | forever | 5.6 MB/yr |
| `market_snapshots` *(new)* | 576 | ~450 B | 0.26 MB | 15m forever / 30 d | 8 MB + 31 MB/yr |
| `predictions` *(new, §6)* | ~670 | ~350 B | 0.23 MB | 180 d | ~42 MB |
| `process_health` *(new)* | 1,152 | ~90 B | 104 KB | 30 d | ~3 MB |
| **after this plan** | | | | | **~180 MB at one year** |

So: **storage was never the problem, and it still isn't** — 180 MB against ~5 GB free. What would
have made it a problem is adding analytics history with no retention rule, which is exactly why
every new table above ships with one, wired into the existing `System.housekeeping` job.

Two real ops items while we are here:

- **The file never shrinks.** Deletes free pages for reuse but `VACUUM` is never run. Add
  `PRAGMA auto_vacuum=INCREMENTAL` for new databases plus a monthly
  `PRAGMA incremental_vacuum` in housekeeping. (`VACUUM` on a live WAL database needs care; the
  incremental form doesn't.)
- **`Api.database` runs `COUNT(*)` on every table.** Cached 30 s today, fine at 1 MB. At 180 MB
  with `predictions` the count is a full scan — switch to the cached count plus
  `dbstat`-derived sizes, or widen the cache to 5 minutes.

### 3.5 Logs and counters — what I'd actually change

You suspected the per-event counters were the storage problem. They are not, and I'd rather tell
you that than remove something harmless:

- `EventBus._types[name].count` and `_Subscription.handled` (`events.py:364-406`) are **in-memory
  Python ints**. Nothing is written to disk, they reset on restart, and Python ints don't
  overflow. One per second for 100 days is 8.6 million — eight bytes of RAM.
- The problem is **readability, not size**. "`Tick: 8,640,217`" tells a person nothing. Replace
  the lifetime count on the System page with **rate/hour + last-hour count + last-seen**, keeping
  the lifetime number as a tooltip. That is the number you actually wanted to read.
- `logs/` is dead — delete it (§2). Logging goes to stdout and Docker caps it at 30 MB total.
  Add a `LOG_LEVEL` env var (default `INFO`, set `WARNING` in production) and demote the chatty
  per-cycle `log.info` lines in `analyst.py` and `system.py` to `debug`.

**Tests:** `test_history.py` — a `CandleClosed` writes one bar and a replay writes none; the
backfill and the live writer produce the same row; a snapshot older than 30 days with `keep = 0`
is pruned and `keep = 1` survives; `process_health` keeps one row per role per 5 min.

---

## 4. Phase 2 — The conviction engine

New module `tradebuddy/conviction.py`. Pure, no I/O, deterministic.

```python
def score(ctx: dict, found: list[dict], forecast: dict | None,
          base: BaseRates | None) -> Conviction
```

returning:

```python
@dataclass(frozen=True)
class Conviction:
    side: str          # "long" | "short" | "flat"
    score: float       # -100 .. +100
    drivers: list[Driver]      # name, weight, direction, the number behind it
    probability: float | None  # empirical, from history
    sample_n: int              # how many bars that probability came from
    baseline: float | None     # what you'd get with no model at all
    verdict: str               # "no history yet" when sample_n < MIN_SAMPLE
```

### Where the score comes from

Weighted sum of the existing families, so nothing new has to be invented:

| Family | Input | Weight |
| --- | --- | --- |
| Trend | EMA stack, `range_pos_24h` | ×2 |
| Options positioning | `skew_25d`, `pcr_oi`, wall gaps, `max_pain_gap_pct` | ×2 |
| Volatility regime | `iv_rv_ratio`, `rv_24h / rv_7d`, `term_slope` | ×1 (gates size, not side) |
| Futures positioning | funding, OI-vs-price flow | ×2 |
| Model | forecaster direction — **only when `skill["direction"]` is true** | ×3 |

The weights are a starting point. **They get fitted against the stored history in Phase 6**, not
guessed forever. Until then the score is a transparent tally, exactly as `insights.lean` is today.

### The probability must be empirical

This is the part that decides whether the whole thing is worth building. The number shown to a
person must be *measured*, not asserted:

```sql
SELECT AVG(CASE WHEN p.actual_return > 0 THEN 1.0 ELSE 0.0 END) AS hit, COUNT(*) AS n
FROM market_snapshots s JOIN forecasts p USING (symbol, bar_time)
WHERE s.conviction BETWEEN ? AND ?          -- the bucket this setup falls in
  AND s.iv_rv_ratio BETWEEN ? AND ?         -- the regime it is in
  AND s.keep = 1;
```

Three rules, all of them already how `forecast.py` reasons about skill:

1. **Always beside a baseline.** "58%" means nothing until you know that always calling the busier
   side scored 54.8% on BTC over the same bars. Show both or show neither.
2. **Always with `n`.** Below `MIN_SAMPLE` (start at 100 settled outcomes in the bucket) the panel
   says *"not enough history yet — 34 similar bars"* and shows no percentage.
3. **Buckets stay coarse.** 5 conviction bands × 3 volatility regimes = 15 cells. Any finer and
   every cell is below `MIN_SAMPLE` forever, and you have built a machine for over-fitting.

For the first ~3 months of live data the honest answer will be "not enough history". Phase 6
closes that gap by computing base rates from **backfilled** bars rather than only from bars the
system was running for.

**Tests:** `test_conviction.py` — `score()` is pure (same input → same output, no clock, no
network); a model without direction skill contributes zero; a bucket under `MIN_SAMPLE` returns
`probability=None` and the "no history" verdict; a probability is never returned without its
baseline.

---

## 5. Phase 2b — Indicators, patterns and labels

This section exists because of `strategy_finder/oracle_labeler.py` in **Master Backtester**. That
file already solves three problems this plan needs solved — a wide causal feature set, market
structure (breakout) detection, and triple-barrier labelling — and solves them better than starting
from scratch. What follows is what to port, what to change, and what *not* to bring across.

### 5.1 The one decision that matters: two layers, two budgets

More indicators help one layer and hurt the other. Keep them separate or you will undo the work
`forecast.py` already did.

| | **Evidence layer** (rules, conviction, the UI) | **Model layer** (`forecast.FEATURES`) |
| --- | --- | --- |
| Consumers | `insights()`, `conviction.score()`, advisor page | 5 gradient-boosted models |
| More indicators → | **better** — each is a named finding with its own stored hit rate | **worse** — more ways to fit noise |
| Budget | 40+, add freely | 22 today; add ≤8, one family at a time |
| Gate to add one | does it produce a finding a person can act on? | does it beat the purged holdout baseline? |

The model budget is not a guess. `forecast.py:CAPACITY` records the measurement: at 250 iterations
and 15 leaves the direction model scored **0.513 AUC on BTC and 0.475 on ETH** — worse than a coin
on unseen data. A third the size scored 0.567 / 0.548. Eighty features into that model will
reproduce the first result. `oracle_labeler.get_feature_columns` already fights the same fight
(dropping `condition_*` flags as redundant `sign()` of continuous features, dropping duplicate
`dist_*`, dropping slow MA slopes) — that instinct is right; apply it harder here.

**So:** everything in §5.2 and §5.3 lands in the evidence layer and in `market_snapshots`. Only a
handful graduates to the model, each one gated by the existing holdout and reverted if it doesn't
pay.

### 5.2 Indicators to add

TradeBuddy has ~45 numbers in `market_context`; `oracle_labeler` has ~120. The gap, by family:

| Family | Add | Lands in | Why it earns its place |
| --- | --- | --- | --- |
| **Trend strength** | ADX, DMI+/−, efficiency ratio(10), range compression(10/50) | evidence + **model** | The biggest single hole. Nothing in TradeBuddy today separates *trending* from *chopping*, and that is exactly the regime split the conviction base rates (§4) need to bucket on. An EMA cross in chop and the same cross in a trend are different trades. |
| **Oscillators** | RSI(7/14/21) + slope, StochRSI k/d, MACD + histogram, ROC(3/6/12/24) | evidence | Standard, cheap, and each becomes a rule with a measurable hit rate |
| **Bands / squeeze** | BB width, BB position, BB squeeze flag, ATR ratio, realized vol 12/24, vol regime 72 | evidence + **model** | `tb_master` already has a 15m squeeze leg built ad hoc; this gives it a measured definition |
| **MA structure** | HMA(9/21/50) beside the existing EMAs, `dist_*_pct`, `slope_*_pct`, sma50/200 and ema50/200 spreads | evidence | HMA turns faster than EMA; the spreads are the golden/death-cross state as a continuous number instead of a boolean |
| **Volume** | volume ratio 5/10/20, volume surprise z-score, OBV slope, volume momentum, vol trend 5/20 | evidence | `volume_z_1h` exists; this is the rest of it. Breakouts without volume are the ones that fail |
| **Candle anatomy** | body % of range, upper/lower wick imbalance, buy pressure, candle vs ATR, displacement | evidence | Cheap, and the most direct read on *who won this bar* — directly feeds breakout confirmation (§5.3) |
| **Location** | distance to 24/72/96/192-bar high and low, intraday position, premium/discount zone, VWAP distance | evidence + **model** | `range_pos_24h` exists for one horizon only. Multi-horizon location is what tells a breakout from a mean-reversion |
| **Multi-timeframe** | 1h / 4h / 1d trend regime, RSI, ADX, NATR — resampled with `shift(1)` | evidence + **model** | §5.5 |

**Do not dilute what TradeBuddy already has that the generic file doesn't.** `oracle_labeler` has
no funding, no open interest, no basis, no IV/RV, no skew, no put/call, no max pain, no walls.
Those are derivatives-market positioning — they say what other traders have actually *done with
money*, not what a transform of the price says. On a crypto perpetual they are far likelier to
carry signal than the fortieth oscillator. If the model budget only has room for eight new
features, spend some of it there before spending it on StochRSI.

### 5.3 Patterns: breakout, break-in, and whether they held

This is the "koi pattern dekh ke breakout / break-in" ask. `oracle_labeler._add_market_structure_features`
is the right implementation and ports almost unchanged.

**Port as-is** (the logic is sound and strictly causal):

- **Swing levels** — rolling high/low over 5 / 10 / 20 / 48 bars: the level the market must break
- **BOS (Break of Structure)** — the bar where close *first crosses* the prior swing level. Note
  the implementation detail that makes it correct: it tests `close > sh1 AND close.shift(1) <= sh1.shift(1)`,
  i.e. a **fresh cross**, not a continuous `>`. Without that you get `pcr_options` v1's bug all
  over again — a signal on every bar while nothing changes.
- **HH / HL / LH / LL** → `struct_bullish_n`, `struct_bearish_n`
- **CHoCH (Change of Character)** — a bullish BOS while structure is bearish = reversal candidate
- **FVG (Fair Value Gap)** — the 3-bar imbalance, with its size as % of price
- **Displacement** — candle range vs its 20-bar mean: the "something just happened" bar
- **Premium / discount zone** over 96 and 192 bars

**The TradeBuddy-specific part — this is where it stops being a generic indicator pack:**

1. **A breakout is only worth knowing with its follow-through rate.** `oracle_labeler` computes
   BOS as a feature and stops. Here, every BOS becomes a stored row that gets *settled*, exactly
   like a forecast:

   ```sql
   CREATE TABLE patterns (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       symbol TEXT NOT NULL, interval TEXT NOT NULL, bar_time INTEGER NOT NULL, ts REAL NOT NULL,
       kind TEXT NOT NULL,          -- 'bos' | 'choch' | 'fvg' | 'squeeze_break' | 'wall_break'
       direction TEXT NOT NULL,     -- 'bull' | 'bear'
       level REAL NOT NULL,         -- the price that broke
       strength REAL,               -- displacement x volume ratio
       context TEXT,                -- JSON: oi_change, funding, adx, which wall
       settled_at REAL, followed_through INTEGER, excursion_pct REAL, baseline INTEGER,
       UNIQUE (symbol, interval, bar_time, kind, direction)
   );
   ```

   Settled by the same job as `predictions` (§6): did price hold beyond the level for N bars, or
   close back inside? That turns a pattern flag into a sentence a person can use —
   *"BTC 20-bar BOS up. 112 of these in 90 days, 41% followed through; the baseline for any random
   bar was 38%. This one has rising OI and 2.3× displacement, which historically ran 54%."*

2. **Break the option wall, not just a swing high.** TradeBuddy knows where the call wall and put
   wall are (`options.py`), and `tb_master` already caps targets at them. A close through the call
   wall is a structurally different event from a close through an arbitrary 20-bar high — real
   dealer positioning has to be unwound. Nothing in a generic feature pack can see this, and it is
   the most interesting pattern on the list.

3. **Squeeze → expansion.** BB squeeze followed by a BOS is *the* classic breakout setup, and
   `tb_master`'s 15m squeeze leg is already half of it. Store the pair as one `squeeze_break`
   pattern so the two halves are scored together.

4. **Confirmation gates, measured not assumed.** `tb_master` already requires rising OI. Store OI
   direction, volume ratio and ADX in `context` and let the scorecard *measure* whether each gate
   actually improves follow-through, instead of assuming it does.

### 5.4 Labels: adopt the triple barrier

This is the most valuable thing in `oracle_labeler.py`, and fixing it fixes a real inconsistency in
TradeBuddy today.

**The problem:** `forecasts.actual_return` asks *"did the close go up over 24 hours?"* But `Trader`
places a **bracket** — a stop and a target, invariant 7. So the system measures a question it never
trades. A forecast can be "right" while the trade it implies was stopped out eight hours earlier.

**The fix:** make the outcome the thing the broker actually does. Port `_simulate_trade_path` as a
small pure function over the `bars` table:

```python
def barrier_outcome(bars, i, tp_pct, sl_pct, lookahead, side, cost_pct) -> Outcome
    # -> ('tp' | 'sl' | 'timeout', bars_to_exit, net_return_pct)
```

with three properties worth keeping exactly as written in the original:

- **SL wins a tie.** If both barriers are touched inside one candle, intrabar order is unknown, so
  assume the loss. Conservative and correct — and it matches the pessimism `paper.py` already
  applies with slippage and fees.
- **Entry at next open plus slippage**, not at the signal close. The signal bar's close is not
  tradeable.
- **Net of costs.** `cost_pct` comes from the broker's own `round_trip_cost_pct`, which `Trader.edge_error`
  already gates on — so a "win" in the scorecard can never be a loss on the account, the same rule
  as invariant 7b.

And make the barriers **the Settings the Trader actually uses**: TP = `atr_target_mult × ATR%`,
SL = `atr_stop_mult × ATR%`. Then:

- `predictions.correct` becomes *"TP first, net positive"* instead of *"closed higher"*
- the conviction base rate (§4) becomes a number you can size against: *"in this bucket a
  2.0×ATR-stop / 3.5×ATR-target trade hit TP first 41% of the time — at 1.75R that is +0.13R per
  trade, against a baseline of −0.02R"*
- the backtest, the scorecard and the live Trader finally measure one thing

**On `oracle_best` — use it, but not for this.** It grid-searches TP × SL *using the future* and
keeps the best TP-first trade. As a supervised target that is a trap twice over: the model must
learn "which of 25 TP/SL combinations would have worked", a far noisier question than "would this
one rule have worked"; and the resulting win rate systematically overstates how much opportunity
existed, because every row got to pick its own best case after the fact. The file's own docstring
already says `fixed_rule` is the realistic one. Go further:

- **`fixed_rule` is the default** for every label that drives a decision or a probability.
- **`oracle_best` measures the ceiling only** — "the best available trade on this bar averaged
  2.3%" is a genuinely useful number for judging whether a strategy is leaving money on the table.
  It never trains anything that trades.

### 5.5 Multi-timeframe

Everything in TradeBuddy is 15m today, except `tb_master`'s 1d bias, which it fetches ad hoc. With
`bars` (§3.1) in place, resample to 1h / 4h / 1d and compute trend regime, RSI, ADX and NATR per
timeframe.

The original's leakage discipline is correct and must be preserved: resample with
`label="right", closed="right"` so the bar stamped T covers `(T−4h, T]`, then **`.shift(1)`** so a
15m bar only ever sees the previous *completed* higher-timeframe bar, then `reindex(..., method="ffill")`.
Get this wrong by one bar and every holdout score in the system becomes fiction. It gets its own test.

### 5.6 The dependency boundary — do not skip this

`oracle_labeler.py` needs **pandas and numba**. TradeBuddy's runtime has numpy and scikit-learn
(the `ml` extra) and nothing else, and the VM has **229 Mi of RAM free** with the worker already at
105 MiB. Pandas alone is ~50 MB resident.

- **`research/`** — a new package and a `research` extra (pandas, numba, matplotlib). Offline only:
  runs on the Mac, never enters the Docker image, never runs on the VM. Labelling, grid searches,
  feature studies and backtests live here.
- **Live indicators stay pure numpy**, in `strategies/indicators.py` and `forecast.features()`,
  exactly as now.
- **The numba fallback must not stay silent.** `_NumbaFallback` turns `njit` into a no-op, so a
  missing numba converts an O(rows × tp × sl × lookahead) loop into pure Python — minutes become
  hours with no warning. Log a warning at import.

**The mandatory test — the highest-value test in this whole plan:**

> The offline pandas implementation and the live numpy implementation of every shared indicator
> must agree to within 1e-9 over the same 5,000 bars.

Without it you train on one definition of RSI and trade on another, the holdout score is
meaningless, and nothing downstream will tell you. This is classic training/serving skew and it is
silent by nature — the only way to catch it is to assert it.

### 5.7 QA notes on `oracle_labeler.py`

Things to fix in the port — and, where they also affect Master Backtester, worth fixing there too:

1. **The confirmation lists are asymmetric.** `BULLISH_CONFIRMATION_COLUMNS` has 21 entries,
   `BEARISH_CONFIRMATION_COLUMNS` has 19. `condition_ema9_above_ema21` and `condition_ema21_above_ema50`
   have bullish entries with no bearish counterparts, while `condition_hma9_above_hma21` does.
   Since `confirmation_edge = |bull_score − bear_score|` and both are compared against a single
   `MIN_CONFIRMATION_SCORE`, the threshold means something different for a long than for a short —
   a structural bullish tilt baked into the labels. **Fix:** make the lists exact mirror images, or
   normalise each score by its own list length. This matters here because §4's conviction drivers
   are the same idea, and the same bug would reach live trading.
2. **Label / feature coupling.** `condition_*` flags are correctly excluded from
   `get_feature_columns` — they are `sign()` of continuous features already present. But they
   *decide the label* through `_apply_confirmation_filter`. So the label encodes the filter. If a
   model trained on those labels is later used to pick trades that are then filtered by the same
   rules, the filter is applied twice and the live hit rate will not match the backtest.
   **Decide once:** filter at labelling time *or* at trade time, never both.
3. **`_causal_atr_pct` fills NaN with 0.0.** The label loop skips `atr <= 0` rows, so labelling is
   safe — but a fabricated zero also flows into features elsewhere, where it reads as "no
   volatility" rather than "unknown". Prefer NaN: `HistGradientBoosting` handles NaN natively and
   `forecast.py` already relies on that.
4. **`vol_percentile_100` is slow.** `rolling(100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)`
   builds a Series per window — roughly 100× slower than `rolling(100).rank(pct=True)`. Noticeable
   over a year of 15m bars; irrelevant to correctness.
5. **Keep what is already right:** the SL-wins-ties rule, `label_valid` marking the last `lookahead`
   rows invalid (the same guarantee `forecast.targets()` gives with NaN), the fresh-cross test in
   BOS, and the `LABEL_COLUMNS` / `get_feature_columns` leakage firewall. That firewall is the
   single best thing in the file — TradeBuddy should adopt the same explicit never-a-feature list.

### 5.8 What this adds to the schedule

| Work | Size | Depends on |
| --- | --- | --- |
| Indicator expansion in `insights.market_context` + `market_snapshots` columns | 1.5 days | §3 |
| `patterns` table, detectors, settling | 1.5 days | §3, §6 |
| `barrier_outcome()` + rewiring `predictions.correct` | 1 day | §6 |
| `research/` package, parity test, offline labeller port | 2 days | §3.1 |
| Multi-timeframe resample + leakage test | 0.5 day | §3.1 |

**Tests:** `test_indicators.py` — pandas/numpy parity to 1e-9 on 5,000 bars, per indicator;
`test_patterns.py` — a BOS fires on the crossing bar and not on the next one, a CHoCH requires
opposing structure, an FVG needs the full 3-bar gap; `test_labels.py` — SL wins when both barriers
are touched in one candle, entry is the next open, a timeout is net of costs, the last `lookahead`
rows are invalid; `test_leakage.py` — every MTF feature at bar *t* is unchanged when every bar after
*t* is overwritten with noise.

---

## 6. Phase 3 — The scorecard: "kal jo bola tha, woh hua?"

The mechanism exists for the forecaster and is well built (`forecasts` + `System.score_forecasts`
+ `insights.score_forecasts` + the Forecast-vs-actual card). **Generalise it to every source.**

### `predictions` — one table, one scorer

```sql
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, bar_time INTEGER NOT NULL, ts REAL NOT NULL,
    source TEXT NOT NULL,          -- 'conviction' | 'rules' | 'playbook' | 'ai' | 'strategy:<name>'
    horizon_hours REAL NOT NULL,
    side TEXT, confidence REAL, claim TEXT,   -- claim: JSON, source-specific detail
    price REAL NOT NULL, tp_pct REAL, sl_pct REAL,   -- the barrier this claim is judged on
    settled_at REAL, actual_return REAL, actual_excursion REAL,
    exit_reason TEXT, bars_to_exit INTEGER, net_return_pct REAL,   -- triple barrier, §5.4
    correct INTEGER, baseline_correct INTEGER,   -- the naive call over the same bar
    UNIQUE (symbol, bar_time, source, horizon_hours)
);
```

`correct` is **not** "price went the right way" — it is `barrier_outcome()` from §5.4: a TP-first
exit, net of the broker's round-trip cost, at the Trader's own ATR multiples. That is the only
definition under which a scorecard win and an account win are the same event.

`forecasts` stays as it is — its typed columns feed the existing charts and tests, and reshaping
it would break both for no gain. `predictions` is the general case; the two are read together by
one settler.

`System.score_forecasts` becomes `System.score_claims`: same loop, same once-per-symbol candle
fetch (now from `bars`, so usually no fetch at all), settling both tables.

`insights.score_forecasts` generalises to `score(points, baseline_fn)` and gets a new companion:

```python
def calibration(points, buckets=10) -> list[dict]
    # per predicted-probability bucket: predicted, actual, n
```

**Calibration is the chart that answers your question honestly.** A hit rate says "58% right".
Calibration says "jab system ne 70% bola, tab 68% baar hua; jab 90% bola, tab 55% baar hua" — the
second one tells you when to trust it and when not to, and it is the only chart that exposes an
over-confident model.

### Horizons

Score every claim at **1h, 4h and 24h**. A 24h-only horizon is useless to someone taking a 15m
scalp, and the three together show whether the edge decays — which is the single most useful thing
to know before letting auto mode size up.

**Tests:** `test_scorecard.py` — a prediction is settled exactly once; an unsettled row never
reaches the scorer (today `Api.forecast_record` does `int(r["actual_return"] > 0)`, which would
raise on a NULL — `forecast_history` filters on `settled_at`, keep that guarantee under test);
`calibration()` buckets sum to the input count; a perfectly calibrated series sits on the diagonal.

---

## 7. Phase 4 — The advisor UI

Current state: `market.html` + `analysis.js` already hand-roll ~8 SVG charts (`TBA.oiChart`,
`smileChart`, `termChart`, `spark`, `twoLines`, `hitTrack`). **No chart library** — and keep it
that way. The pattern works, it is theme-safe, and the page already pulls two CDNs; a third is
weight for no benefit.

Extract the repeated scale/axis/tooltip code into `static/js/chart.js` first, then build:

| # | Visual | Replaces | Why |
| --- | --- | --- | --- |
| 1 | **Conviction dial** — semicircular gauge, −100…+100, coloured bands | `lean: "mixed"` text | One glance = side + strength |
| 2 | **Evidence bars** — diverging horizontal bars, bear left / bull right, width = weight, hover = the number behind it | the insight list | Shows *why*, and which driver is carrying the call |
| 3 | **Probability card** — big %, baseline as a ghost bar behind it, `n = 412` underneath | nothing | The number you asked for, with the context that makes it honest |
| 4 | **Reliability plot** — predicted vs actual by bucket, with the diagonal | nothing | The trust meter (§6) |
| 5 | **Hit-rate ribbon** by day, per source | `TBA.hitTrack`, forecaster only | "Pichle 30 din me kaun sa source sahi tha" |
| 6 | **Forecast-vs-actual fan** + baseline as a third dimmed line | `TBA.twoLines` | Skill vs drift, visible |
| 7 | **Regime heatmap** — IV/RV bucket × trend bucket, cell = win rate, greyed when `n < MIN_SAMPLE` | nothing | "Mera edge kis market me hai" |
| 8 | **Price chart with markers** — 15m candles from `bars`, entry/exit/SL/TP, signal pins | **nothing — there is no price chart today** | Biggest single hole on a trading dashboard |
| 9 | **Options strip** — ATM IV, PCR, skew, max-pain gap sparklines in one row | three separate cards | Density |
| 10 | **System sparklines** — CPU / RSS / loop lag per process, 48 h | live numbers only | From `process_health` |
| 11 | **Event-rate bars** — per hour, last 24 h | lifetime counters | §3.5 |
| 12 | **Structure overlay** on the price chart — swing levels, BOS/CHoCH markers, FVG boxes, option walls as horizontal lines | nothing | The breakout picture, drawn where it happened (§5.3) |
| 13 | **Pattern scoreboard** — per pattern kind: fired, followed through, baseline, as a small bar pair | nothing | A flag you can't trust is noise; a flag with 41% vs 38% is information |

### Page layout

Keep CLAUDE.md's rule — **every number belongs to one page**:

- `/` **Market Data** — the call: dial (1), evidence (2), probability (3), price chart (8),
  options strip (9), chain. Decision-making lives here.
- `/accuracy` **Scorecard** (new page, Monitoring group) — reliability (4), ribbon (5), fan (6),
  heatmap (7). Not on `/`: `/` is "what now", this is "how have I been doing".
- `/system` — sparklines (10), rates (11).

Greyscale-safe colours and a shape or label on every series: the bull/bear green/red pair is the
first thing to fail for a colour-blind reader, and these are money decisions.

---

## 8. Phase 5 — Auto mode

**Design decision: auto mode is a `Strategy`, not a new order path.**

A tempting shortcut is to have the analyst place orders when conviction is high. Don't. It would
break four invariants at once — one order per broker × strategy × symbol × bar (2), broker
independence (4), the broker-decides read before entry (6), and every refusal becoming a
`TradeSkipped` (10) — and it would make something other than `System` a writer of trading state.

Instead: `tradebuddy/strategies/analysis_edge.py`, a normal `Strategy` subclass. It is discovered
automatically, toggled like any other, gated by `Trader` (risk sizing, edge check, strategy halt,
daily loss limit), and idempotent on `client_order_id`. Auto mode becomes an ordinary strategy
toggle plus a threshold — nothing new to secure, nothing new to reconcile.

```python
class AnalysisEdge(Strategy):
    name = "analysis_edge"
    interval = "15m"
    symbols = ("BTCUSD", "ETHUSD")
    is_default_sl_tp = False          # the ATR regime sets its own stop
    analysis_history_hours = 2.0      # NEW extension point, mirrors options_history_hours
    min_conviction = 55.0
    min_probability_edge = 0.03       # over the baseline, or no trade
    min_sample = 100                  # no history, no trade
    max_age_seconds = 900             # absence over staleness (invariant 8)
```

### The one new extension point

Strategies run on whichever Celery worker is free and must keep no state between bars. `pcr_options`
already solves exactly this problem for options history: it sets `options_history_hours`, and
`runner.py:92` makes the engine attach the rows to the job. Copy that pattern:

- `Market.analysis_history(symbol, hours)` on the protocol (`strategies/base.py`)
- `runner.py` attaches `market_snapshots` rows when `strategy.analysis_history_hours` is set
- the engine serves them from its own table — same shape, same staleness rule

### Rollout — do not skip steps

1. **Paper only**, Delta excluded, for 30 days minimum. Both brokers are independent (invariant 4),
   so this costs nothing.
2. **Shadow mode first**: the strategy writes a `predictions` row with
   `source='strategy:analysis_edge'` but returns `None`. Two weeks of "what would it have done",
   scored by Phase 3's settler, before a single order.
3. Then live on paper, sized by the existing `risk_per_trade_pct`.
4. Delta only after the scorecard shows an edge over the baseline on **unseen** bars — the same bar
   `forecast.py` already holds its own models to.

**Tests:** `test_analysis_edge.py` — no signal when the snapshot is older than `max_age_seconds`;
no signal when `sample_n < min_sample`; no signal when the probability does not clear the baseline
by `min_probability_edge`; shadow mode returns `None` but still records the prediction; the
strategy holds no state across two `on_candle` calls on different instances.

---

## 9. Phase 6 — Models, training, and the GCP constraint

### The local-train / remote-serve contract already exists

`docker compose run --rm analyst train …` → `data/models/forecast_<sym>.joblib` → `cp` to `models/`
→ git → image. `analyst.model_paths` prefers a locally-trained file and falls back to the shipped
one, and `Forecaster.load` refuses a stale `MODEL_VERSION`. That is exactly "training yahan, model
wahan". **Extend this contract rather than inventing a second one.**

What changes: training reads `bars` instead of re-downloading from Delta, so a retrain is
reproducible and offline, and the backtest runs on the identical rows the live system saw.

### MLX: the honest answer

> *"laya-mlx ya uske jaisa architecture use karna ho to kar sakte hain… GCP pe model jayega,
> GCP pe training nahi hogi"*

Two hard constraints make MLX the wrong shipping format here:

1. **MLX is Apple-silicon only.** It targets the Metal unified-memory stack. An MLX artifact
   trained on your Mac **cannot run on a GCP x86 VM** at all. It is not a performance question;
   the runtime does not exist there.
2. **That VM has 229 Mi of RAM free**, with the worker alone at 105 MiB. Even a 1 B parameter model
   at 4-bit is ~700 MB resident. There is no on-box LLM of any kind on this machine today.

So:

- **Numbers stay in scikit-learn → joblib.** Already working, already validated against baselines,
  already shipping, ~2 MB per model, negligible RAM. Nothing about this plan needs more capacity —
  `forecast.py`'s own comment records that a *bigger* model scored worse out of sample.
- **MLX is useful locally, at training time.** Fit, distil, explore features, generate candidate
  rule weightings on the Mac — then export something portable. If an on-box LLM is ever genuinely
  wanted, the portable route is a quantized model through `llama.cpp` (runs on amd64 CPU), on a
  bigger VM, in the analyst process only, advisory only (invariant 15).
- **For now, narrative reasoning stays with Mistral over the API.** 137/141 reports OK (97.2%) is
  working well. The 4 non-JSON answers were handled correctly by the retry-with-double-budget path
  in `analyst._tbai` and recorded in `ai_reports` — that is the design working, not a bug. Worth
  adding: those 4 failures should become a `predictions` row too, so AI reliability appears on the
  scorecard alongside everything else.
- **Build images with `--platform linux/amd64`** if you ever build on the Mac for that VM.

### Fitting the conviction weights

Once `bars` + `market_snapshots` + `predictions` hold ~3 months, fit the §4 weights by logistic
regression on the stored rows, with the same discipline `forecast.py` already enforces: a purged
holdout, a naive baseline, and `MODEL_VERSION` bumped so a stale file is refused rather than
silently used. If the fitted weights do not beat the hand-set tally out of sample, **keep the
tally** — it is explainable and costs nothing.

**Tests:** `test_backtest.py` — the backtest over stored `bars` reproduces the live
`conviction.score()` for the same bar, bit for bit (the `tb_master.decide()` discipline); a
`MODEL_VERSION` bump makes `load` refuse the old file.

---

## 10. Order of work

| Phase | What | Blocks | Rough size |
| --- | --- | --- | --- |
| **0** | Lock reads + `/ws`, confirm PIN, set `AUTH_SECRET`, prune Docker, delete dead files | **everything** | half a day |
| **1** | `bars`, `market_snapshots`, `process_health`, retention, counter/log cleanup | 2–6 | 2 days |
| **2** | `conviction.py` + empirical base rates | 4, 5 | 2 days |
| **2b** | Indicator expansion, `patterns` table, `barrier_outcome()`, `research/` + parity test, MTF | 3, 5 | 6.5 days |
| **3** | `predictions`, one settler, calibration, 1h/4h/24h | 4, 5 | 2 days |
| **4** | `chart.js` kit, 13 visuals, `/accuracy` page | — | 3–4 days |
| **5** | `analysis_edge` + `analysis_history` extension point, shadow → paper | needs 3 months of 3 | 2 days + 6 weeks of waiting |
| **6** | Backtest on `bars`, fit weights, retrain loop | needs 1 | 2 days |

Phases 2 and 2b can run in parallel; 3 wants `barrier_outcome()` from 2b before it defines
`correct`, so do that piece first. 5 cannot start honestly until 3 has collected data.

If you want the shortest path to something visibly better: **0 → 1 → 2b indicators + patterns → 4**.
That gives a person a far richer, graphical analysis page with measured patterns, while the
probability and auto-trading layers accumulate the history they need to be honest.

## 11. Invariants this plan adds

In the spirit of CLAUDE.md — each one should fail the build if broken:

16. **A probability is never shown without its baseline and its sample size.** Below `MIN_SAMPLE`
    the system says "not enough history", never a number.
17. **One conviction function.** `conviction.score()` is pure and is the only thing that decides a
    side; the analyst, the worker, the backtest and the UI all read it, none of them re-implement it.
18. **Analysis still never places an order.** Auto mode is a `Strategy` behind the existing
    `Trader` gate. `System` remains the only writer of trading state.
19. **Every stored series has a retention rule**, wired into `System.housekeeping`, with the cost
    per day written down next to it.
20. **Read routes are authenticated by default.** New endpoints are private unless explicitly
    allowlisted — the opposite of today's default.
21. **One definition per indicator.** The offline (pandas) and live (numpy) implementations agree
    to 1e-9 on the same bars, asserted by test. Train on one definition, trade on another, and
    every holdout number in the system is fiction.
22. **A label is what the broker would have done.** An outcome is a triple barrier — TP first,
    SL first, or timeout — at the Trader's own ATR multiples and net of the broker's round-trip
    cost. Never "did the close go up". Future-optimal labelling (`oracle_best`) measures the
    ceiling and never trains anything that trades.
23. **A pattern is nothing without its follow-through rate.** Every detected breakout is stored and
    settled; the UI shows the rate and its baseline, never the bare flag.
