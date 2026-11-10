# Adding a strategy

A strategy is **one file** in `backend/app/strategies/`. The registry finds it by looking, the
settings store its switches, the Strategies page draws its form from its `Params`. Nothing else is
edited. Deleting the file removes the strategy; the trades it made stay in the history under its name.

## A position on the perpetual

Start from `backend/app/strategies/_template.py`:

```bash
cp backend/app/strategies/_template.py backend/app/strategies/donchian_4h.py
```

Then, in the new file:

1. Rename the class and set `name` (stable forever: trades and settings are keyed by it), `label`,
   `summary`, `stop_rule`, `target_rule`, `interval` (`5m` `15m` `1h` `4h` `1d`) and `lookback`.
2. Put every number you may want to tune in its `Params` model, with a default, bounds and a
   `description`. Behaviour must be identical at the defaults to what you tested.
3. Write `decide(ctx)`: closed bars in, a `Decision` out. Pure: no clock, network, database or
   orders. Return `none("why")` when it does nothing. When it trades, the stop is where the idea is
   wrong and the target a price beyond the entry.
4. Ask for more than bars only through the flags `wants_outlook`, `wants_structure`, `wants_flow`
   and `wants_bias`. A strategy that uses them cannot be backtested yet.

## An option structure

Subclass `OptionStrategy` (`app/strategies/base.py`). See `iron_condor.py` for the shape:

- `view`, `premium` (`credit`/`debit`) and `risk` (`defined`/`undefined`) describe it.
  `required_checks` names which assessment checks bind (default: a seller's four).
- `Params` extends `OptionParams` (expiry window, minimum POP, credit/width), plus its own
  strike numbers. `_options.py` has the common ones.
- `pick(quotes, spot)` returns `(quote, side)` pairs from one expiry's chain, using the pickers in
  `app/options/strategies.py` (`otm_by_delta`, `atm`, `wing`).
- `score(regime)` returns 0–100 and the reason for every point, from the helpers in
  `app/options/regime.py`. A strategy needs `MIN_SCORE` (60) before it is built.
- Override `expiry(ctx)` or `build(ctx, expiry)` only if it does not trade a strike rule on the
  nearest expiry in its window (see `expiry_decay.py`).

Pricing, the checks, sizing, execution and exits are shared: a strategy file never touches them.

## Prove it before it trades

```bash
cd backend
../.venv/bin/pytest tests/test_directional.py   # add a test: one context that fires it, one that refuses
../.venv/bin/python -m app.backtest.perp donchian_4h --underlying BTC          # stored bars, live exits
../.venv/bin/python -m app.backtest.perp donchian_4h --params '{"reward_r": 2}' # another setting
make check
```

The backtester runs the same `decide()` the engine trades, enters on the next bar's open, applies
the live trailing stop, stop slippage, fees and funding, and reports walk-forward folds and a Monte
Carlo of the drawdown. If most folds lose, delete the file.

## Switching it on

A new strategy starts **off and on paper**. On the Strategies page: switch it on, pick its symbols,
adjust its settings. Its record builds up in R. Sizing follows the record (`trading/edge.py`):

- under 30 automatic trades it gets the configured risk;
- proven losing over 30 or more trades (its whole 95% range below zero), it gets nothing;
- proven winning, up to twice the risk;
- on Delta real money, nothing until paper has proven it.

"Trade on Delta too" is a separate switch per strategy, behind the account-wide
"automatic entries on Delta" switch.

## Removing one

Delete the file. Its stored settings are dropped on the next start. Close its open positions first;
otherwise the guard still manages them, named by their legs.
