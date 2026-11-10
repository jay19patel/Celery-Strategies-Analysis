# TradeBuddy

Automated trading on Delta Exchange India: defined-risk BTC/ETH option structures and positions on
the perpetuals, opened by strategy rules and from the dashboard, on a paper account and/or your
Delta account. Every strategy is one file in `backend/app/strategies/`.

- **backend/** — FastAPI engine: live market feed, strategies, risk rules, trading, the guard,
  reconciliation with Delta, the AI market brief (LiteLLM), Telegram/email notifications. SQLite.
- **frontend/** — Next.js 16 + TypeScript + Tailwind + shadcn/ui dashboard.

## Quick start

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node 20.9+.

```bash
cp .env.example .env      # set AUTH_PIN, and an AI provider key if you want the market brief
./start.sh                # backend on :8000, dashboard on :3000
```

Open **http://localhost:3000** and unlock with your PIN. Paper trading on live market data starts
immediately: no exchange keys needed. Run the halves on their own with `backend/start.sh` and
`frontend/start.sh`.

## How it decides

**Option strategies**, every 15 minutes, per underlying, for each strategy switched on:

1. **Expiry** — the nearest in the strategy's own window (its settings).
2. **Regime** — ATM implied volatility against volatility actually realised, the trend, and the
   option book's gamma. The strategy scores the fit 0–100, every point with its reason, and needs 60.
3. **Build and check** — strikes by its own rule; then two-sided quotes, credit vs width,
   probability of profit, and **edge at realised vol** (does the premium beat what the market has
   actually been moving, after fees?). No measured realised volatility means no trade.
4. **Choose and size** — the best by score, then edge, sized so its worst case is
   `risk_per_trade_pct` of equity, scaled by the strategy's measured record and the drawdown.

**Perpetual strategies** decide on every closed bar with their own stop and target, sized so the
loss at the stop is the same risk budget. The trailing stop and the exchange-held stop protect them.

Every entry passes the same gate: broker ready, not halted, not stopped, matching the exchange, one
automatic position per underlying, total automatic risk under 4% of equity. Open positions are
watched every 5 seconds; a daily loss limit and a drawdown stop close everything when hit. Every
cycle is recorded with why it did, or did not, trade. The AI writes a market brief; it never trades.

**Stop all** (top right of every page) refuses every new entry and closes every position, until you
resume it.

## Adding a strategy

One file. See [docs/ADDING_A_STRATEGY.md](docs/ADDING_A_STRATEGY.md).

## AI models

Settings → AI takes any [LiteLLM](https://docs.litellm.ai/docs/providers) model id plus fallbacks,
tried in order — switch providers without touching code or restarting:

`mistral/mistral-small-latest` · `openai/gpt-4o-mini` · `anthropic/claude-haiku-4-5` ·
`gemini/gemini-2.0-flash` · `groq/llama-3.3-70b-versatile` · `ollama/llama3.1`

Provider keys go in `.env` (`MISTRAL_API_KEY`, `OPENAI_API_KEY`, ...). They are never stored in the
database or returned by the API. Only market numbers are ever sent to a model.

## Real money

Delta is off and on its demo (testnet) account by default. Going live requires a PIN other than
the published default and typing the confirmation phrase; Delta automatic entries switch
themselves off at every restart and after any change to the account or keys; and each strategy has
its own "trade on Delta too" switch. On real money a strategy gets no size until paper has measured
an edge for it.

## Development

```bash
make install    # backend venv + npm install
make check      # ruff, eslint, tsc, pytest
```

API docs (after unlocking): http://127.0.0.1:8000/api/docs. Architecture and invariants: [CLAUDE.md](CLAUDE.md).
