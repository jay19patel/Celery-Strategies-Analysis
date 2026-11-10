# 05 — UI/UX Review

Read with: `frontend-design` skill (primary job first, restraint, copy that names actions plainly).
Scope: every file in `frontend/src`, cross-checked against `backend/app/api/routes`. `tsc`, `eslint`
and `vitest` pass. Not run in a browser: mobile overlap findings are inferred from CSS.

## 1. Screens

| Route | Job today | Can it trade? | Confirmation? |
|---|---|---|---|
| `/` Options Desk | Build and price a structure; chain; ideas; regime; AI brief | Yes — Place order | **Yes** (`desk/order-panel.tsx:98`); real-money variant |
| `/` chain ⚡ / double-click / "Use" idea → Direct option modal | One-click single option | Yes | **No** (`desk/direct-option-modal.tsx:118,298`); no real-money notice, no halt check |
| `/market` Market Desk (951 lines) | Analysis: bias, GEX, flow, footprint, fib, trendlines | Yes — Direct perp panel | **No** (`desk/direct-perp-panel.tsx:159`) |
| `/strategies` | Directional strategies, per-symbol switches, signal log | Yes — enabling a strategy can start live perp trading | **No**, saves instantly (`strategies/page.tsx:47-70`) |
| `/auto` "Auto Options" | Per-broker auto toggle, decision log | Yes — "Run a cycle now" | Toggle: only enabling Delta. Run: **No** (`auto/page.tsx:105`) |
| `/zero-hero` | Scan, budget, decisions | Yes — "Scan now" | **No** (`zero-hero/page.tsx:81`) |
| `/positions` | Open structures, needs-action list | Close, edit exits, resume | Close/resume yes; edit exits no |
| `/performance` | Stats, equity, trades | Reset paper | Yes |
| `/journal` | Day-by-day audit | No | — |
| `/orders` | Order log (server paged) | No | — |
| `/notifications` | Event inbox | No | — |
| `/system` | Jobs, feed, brokers, event log | Yes — per-job "Run" includes auto-trader and zero-hero | **No** (`system/page.tsx:246`) |
| `/settings` | 8 tabs, most of the 79 settings | Yes — `auto_delta`, `zh_enabled` toggles | Going live: typed phrase. `auto_delta` here: **No** (`settings/sections.tsx:208`) |

Global chrome on every page: price ticker, sidebar/bottom nav, header with **Close all** (confirmed),
notifications bell, positions bar.

## 2. The five-second test

The landing page (`/`) is a structure builder. It does not answer the five questions:

| Question | Answered? | Where today |
|---|---|---|
| Is the system running? | **No** | Only the dashboard's own WebSocket dot. Feed health, failed jobs and auto on/off are on `/system` and `/auto` |
| Today's P&L? | Partly, and wrong | Positions bar "Today" **sums paper and Delta into one number** (`layout/positions-bar.tsx:25-31`); hidden on phones when nothing is open |
| Equity and drawdown? | **No** | Equity only after an order preview; live drawdown from peak nowhere, though sizing depends on it |
| Any errors? | **No** | Toasts only; persistent list is on `/positions` |
| Open positions? | Count yes | Positions bar, sidebar badge |

### Proposed primary screen ("Overview", becomes `/`)

```
┌────────────────────────────────────────────────────────────────────┐
│ ● Running · last cycle 2 min ago      [ PAPER ]      [ STOP ALL ] │  status strip, always visible
├──────────────────────────────┬─────────────────────────────────────┤
│ Paper                        │ Delta (demo / LIVE)                 │  one column per broker, never summed
│ Equity 1,042.10              │ Equity —  not active                │
│ Today +12.40 (+1.2%)         │                                     │
│ Drawdown from peak −3.1%     │                                     │
│ Daily limit used 0 of 5%     │                                     │
├──────────────────────────────┴─────────────────────────────────────┤
│ Needs attention (0)   ← errors, unresolved structures, halts, recon │
├────────────────────────────────────────────────────────────────────┤
│ Open positions (2)  strategy · side · P&L · stop · why it opened    │
├────────────────────────────────────────────────────────────────────┤
│ Strategies   name · on/off · paper|live · trades · measured edge R │
└────────────────────────────────────────────────────────────────────┘
```

The current builder moves to `/desk`. Everything analytical (`/market`) stays one click away.

## 3. Kill switch

- Exists: header **Close all** → `POST /trading/close-all` with confirm. Its own copy says "Auto-trading stays as it is", so **the auto-trader re-opens positions on its next cycle**. That is a close-all, not a kill switch.
- Required: **Stop all** = turn every automatic entry off on every broker (persisted, survives restart), *then* close everything, then show what is still open with the reason. Resume is a separate, deliberate action. Per-broker variant in the broker column.
- Name the action the same everywhere: button "Stop all", toast "Stopped: 3 closed, 0 left open", event `KillSwitch`.

## 4. Per-strategy controls, mode badges, why-this-trade

- Strategy state is split across three places: directional switches on `/strategies`, option checkboxes in Settings → Auto-trader, zero-hero in Settings. **One strategies table** (see wireframe) with on/off, paper/live, symbols, trade count, measured edge (`trading/edge.py` already computes it) and a row link to its generated settings form.
- Mode badges: only "LIVE" appears in the header when real money; there is no persistent PAPER/DEMO badge and a `delta` structure card looks identical on demo and live. Show the mode on every position, order and strategy row.
- Why a trade happened: good raw material exists (`Decision.candidates`, `SignalLog.reason`, `Structure.reason`). Surface `Structure.reason` and the decision link on every open and closed position row.
- Dead UI: the "AI review" block in `components/auto/decision-row.tsx:34,89-107` renders `Decision.ai`, which nothing writes (invariant 11 forbids it). The `/auto` description still says "optionally asks the AI reviewer" (`auto/page.tsx:99`). Remove both.

## 5. Every setting: keep / advanced / remove / automatic

79 fields in `backend/app/settings.py`. Default bias: remove. "Strategy param" means it moves into that
strategy's Pydantic `Params` and its generated form, out of global settings.

| Setting(s) | Decision | Why |
|---|---|---|
| paper_active, delta_active, delta_env, delta_api_key, delta_api_secret | **Keep** | Where orders go; fail-closed flow already right |
| underlyings | **Keep** | Real choice |
| market_data | **Automatic** | Live public data unless Delta demo is active; derive it |
| paper_starting_balance | **Advanced** → move into the Reset paper dialog | Only matters at reset |
| paper_slippage_pct | **Remove** (constant) | Paper fills at the touch already; tuning it only flatters results |
| auto_paper, auto_delta | **Keep** (one switch per broker column) | Real choice; keep the Delta confirm everywhere it can be flipped |
| auto_options | **Remove** | Hidden master switch not in the UI; `/auto` can show "on" while nothing trades. Replaced by per-strategy enabled |
| auto_interval_minutes | **Remove** (15 min constant) | Not a decision a user can make well |
| auto_strategies, directional_strategies, strategy_symbols | **Replace** by per-strategy config (enabled, brokers, underlyings) | One model for every strategy |
| expiry_min_hours, expiry_max_hours, short_delta, wing_width_pct, min_pop_pct, min_credit_width_pct | **Strategy param** | Belong to the option strategies that use them |
| min_score, min_score_floor | **Remove** | Uncalibrated score; pacing logic adds a second unexplained number |
| max_spread_pct | **Advanced** | Global liquidity guard |
| event_times | **Advanced** (event calendar) | Needed, but rarely edited |
| event_buffer_hours | **Remove** (2h constant) | |
| zh_* (19 fields) | **Remove** | Strategy removed (`03_strategy_review.md` §3.1) |
| paper_leverage | **Advanced** (paper only) | Delta must read the exchange's leverage instead |
| trade_margin_pct | **Remove** | Superseded by portfolio heat cap |
| perp_max_hold_hours | **Strategy param** | Each perp strategy already has its own hold |
| risk_per_trade_pct | **Keep** | The one sizing number a user should own; default 0.5 % |
| daily_loss_limit_pct | **Keep** | |
| *new* max_drawdown_pct | **Keep** | Hard stop, manual resume |
| max_lots | **Remove** from UI (fat-finger constant) | |
| max_open_structures, max_per_underlying | **Remove** | Replaced by heat cap + one-per-underlying rule |
| day_timezone | **Advanced** | |
| credit/debit take-profit and stop-loss (4 fields) | **Remove** | Only apply to strategies being removed; per-position edit stays on the position |
| exit_before_expiry_minutes | **Advanced** | |
| stop_confirm_checks | **Remove** (constant 3) | |
| ai_enabled, ai_model | **Keep** (AI tab) | |
| ai_fallback_models | **Advanced** | |
| ai_brief_minutes | **Remove** (constant) | |
| notify_telegram, telegram_bot_token, telegram_chat_id | **Keep** | Primary alert channel |
| notify_email + 5 SMTP fields | **Advanced** | |
| notify_trades, notify_risk | **Remove** (always on) | Fills and halts must always alert |
| notify_decisions | **Remove** | Noise |
| daily_report | **Keep** | |
| daily_report_hour | **Advanced** | |

Result: **~16 visible settings, ~12 advanced, ~51 removed or automatic** (from 79).

Also not in the UI today, though they change sizing: `paper_leverage`, `trade_margin_pct`, `perp_max_hold_hours`, `auto_options`.

## 6. Unconfirmed actions that can place or modify trades

| Action | File | Fix |
|---|---|---|
| Direct single-option order | `components/desk/direct-option-modal.tsx:298` | Same confirm as Place order, real-money variant, halt/ready gating |
| Direct perp order | `components/desk/direct-perp-panel.tsx:374-384` | Same |
| Strategy on/off and symbol switches | `app/(dashboard)/strategies/page.tsx:47-70` | Confirm when the strategy routes to a live broker |
| Run a cycle now | `app/(dashboard)/auto/page.tsx:105` | Confirm naming the brokers it will trade on |
| Zero-hero Scan now | `app/(dashboard)/zero-hero/page.tsx:81` | Goes away with zero-hero |
| System job Run (auto-trader, zero-hero, strategies) | `app/(dashboard)/system/page.tsx:246` | Remove Run for trading jobs, or confirm |
| `auto_delta` toggle in Settings | `components/settings/sections.tsx:208` | Same dialog as `/auto` |
| `zh_enabled` toggle | `components/settings/sections.tsx:359` | Goes away |
| Edit exits on a position | `components/positions/structure-card.tsx:92-135` | Inputs need min/max matching the server (5–1000 / 5–500) |
| Resume halted broker on `/system` | `app/(dashboard)/system/page.tsx:147` | `/positions` confirms the same action; match it |

Also: each direct order makes a fresh request id per click (`direct-option-modal.tsx:128`), so a
double-click can send two orders. Generate the id when the ticket opens.

## 7. States, live updates, mobile, dark mode

**Loading / error / empty** — `components/shared/states.tsx` is good and used on Orders, Notifications,
System, Settings, Auto status. Gaps:
- `/market` and `/strategies` replace the **whole page** with an error when one request fails (`market/page.tsx:421`, `strategies/page.tsx:36`).
- Charts show "Loading" forever on error (`page.tsx:160`, `market/page.tsx:741`); zero-hero cards show Loading when data is simply null.
- Many secondary requests fail silently (accounts on `/positions`, `/journal`, `/performance`).
- Error copy should say what failed and what to do, not just "failed to load".

**WebSocket** (`lib/live.tsx:28-45`) — maps 16 event types to cache invalidations; reconnect revalidates
everything. Gaps: `EngineStarted`, `Log` and `DailyReport` are unmapped; nothing refreshes `/system/status`;
`KillSwitch` does not refresh `/auto`. Live P&L is not pushed, so `/positions` polls accounts and
structures **every 3 s**, and `/portfolio/accounts` calls Delta REST on each request when Delta is active.
Push a `Positions` snapshot from the guard (it already computes `mark_pnl` every 5 s) and drop the polls.

**Mobile** — bottom nav, safe-area padding and mobile cards for positions/orders exist. Problems: the
Settings save bar (`sticky bottom-4 z-10`) likely sits under the bottom nav (`z-40`); hint tooltips are
hover-only; signals and jobs tables have no mobile layout; `/market` is extremely long on a phone.

**Dark mode** — follows the OS through `prefers-color-scheme`; no manual toggle. A few hardcoded light
colours remain (`border-white` knobs, `stroke="#fff"` on equity dots, hex chart levels in
`market/page.tsx:331-352`).

## 8. Hardcoded lists that break the one-file rule

- `components/desk/strategy-picker.tsx:25-26`: which strategies use delta/wings. **Bug today:** `short_call`/`short_put` use `short_delta` but the input is disabled for them.
- `components/positions/capital-split.tsx:21`: `zero_hero`/`expiry_decay` grouping.
- View→colour maps disagree (`strategy-picker.tsx:13`, `strategy-catalog.tsx:41`, `settings/sections.tsx:193`); `momentum` view missing from `lib/types.ts:11`.
- Underlyings hardcoded in `zero-hero/page.tsx:26`, `market/page.tsx:63,205,297-305`, `settings/sections.tsx:122`; `/market` polls BTC, ETH **and XAUT** overviews every 10 s regardless of settings.
- `lib/types.ts` (1,208 lines, 87 types) is hand-mirrored from the backend. Generate it from FastAPI's OpenAPI schema (`openapi-typescript`, dev-only) so a backend change cannot silently drift.

Directional strategies already come from the API, which proves the schema-driven approach works.

## 9. Other bugs found

1. `components/performance/trades-table.tsx:36` requests `GET /trading/structures/{id}`, which does not exist (only `POST …/close` and `PATCH`); every expanded row 405s silently. `TradingService.structure()` exists but is not routed.
2. Journal day audit loads the latest 2,000 orders and filters in the browser (`journal/day-audit.tsx:111`): older days show "no orders".
3. Journal "today" uses the browser timezone; the backend uses `day_timezone` (IST).
4. Login `next` accepts `//host` (`app/login/page.tsx:24`) — open redirect after login.
5. Desk and zero-hero default to BTC even when BTC is disabled.
6. Strategies page runner dot is always green (`strategies/page.tsx:98`).
7. Naming drift: "Auto Options" in the nav, "Auto Trader" on the page; the per-broker switch on that page also gates perps and zero-hero.
8. Unused imports in `direct-option-modal.tsx:5-6` and `direct-perp-panel.tsx:3-16`.
