# TradeBuddy

An options desk for Delta Exchange India. It trades multi-leg BTC/ETH option structures — iron
condors, iron butterflies, credit spreads, strangles, straddles — **automatically** from the
market's own numbers, and **manually** from the dashboard, on a paper account and/or your Delta
account.

- **backend/** — FastAPI engine: live market feed, structure builder and pricing, trading, risk
  guard, auto-trader, AI review (LiteLLM: any model), Telegram/email notifications. SQLite.
- **frontend/** — Next.js 16 + TypeScript + Tailwind + shadcn/ui dashboard.

## Quick start

Needs Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node 20.9+.

```bash
cp .env.example .env      # set AUTH_PIN, and an AI provider key if you want AI review
./start.sh                # backend on :8000, dashboard on :3000
```

Open **http://localhost:3000** and unlock with your PIN. Paper trading on live market data starts
immediately: no exchange keys needed. Run the halves on their own with `backend/start.sh` and
`frontend/start.sh`.

## How the auto-trader decides

Every `auto_interval_minutes`, for each underlying:

1. **Expiry** — the nearest one between `expiry_min_hours` and `expiry_max_hours`.
2. **Regime** — ATM implied volatility against volatility actually realised (24h, 7d), and the
   trend. Each strategy gets a 0–100 rule score; every point is listed with its reason.
3. **Build and check** — every enabled strategy scoring at least `min_score` is built from the live
   chain and checked: two-sided quotes, credit vs width, probability of profit, and **edge at
   realised volatility** (does the premium beat what the market has actually been moving, after
   fees?). Selling options is only worth it when that is positive.
4. **AI review (optional)** — the top three go to the model you configure. It may approve one or
   skip; it cannot invent strikes or sizes. If no model answers, the rules decide (or nothing
   trades, with `ai_required`).
5. **Size and open** — lots whose worst case is `risk_per_trade_pct` of equity, through the same
   gate as a manual order.

Open structures are watched every 5 seconds: take profit, stop loss (on what closing would actually
book, fees included), exit before expiry, and a per-broker daily loss limit.

Every cycle is recorded on the Auto Trader page, including why it did *not* trade.

## AI models

Settings → AI takes any [LiteLLM](https://docs.litellm.ai/docs/providers) model id plus fallbacks,
tried in order — switch providers without touching code or restarting:

`mistral/mistral-small-latest` · `openai/gpt-4o-mini` · `anthropic/claude-haiku-4-5` ·
`gemini/gemini-2.0-flash` · `groq/llama-3.3-70b-versatile` · `ollama/llama3.1`

Provider keys go in `.env` (`MISTRAL_API_KEY`, `OPENAI_API_KEY`, ...). They are never stored in the
database or returned by the API. Only market numbers are ever sent to a model.

## Real money

Delta is off and on its demo (testnet) account by default. Going live requires typing the
confirmation phrase, and Delta auto-trading switches itself off at every restart and after any
change to the account or keys.

## Development

```bash
make install    # backend venv + npm install
make check      # ruff, eslint, tsc, pytest
```

API docs: http://127.0.0.1:8000/docs. Architecture and invariants: [CLAUDE.md](CLAUDE.md).
