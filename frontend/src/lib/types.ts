// Shapes returned by the FastAPI backend (backend/app/api/routes). Kept in one place so a renamed
// field is one edit and one type error, not a blank cell somewhere on a page.

export type Broker = "paper" | "delta";
export type Side = "buy" | "sell";
export type OptionKind = "call" | "put";

/** A strategy's measured record in R (`trading/edge.py`). */
export interface StrategyRecord {
  trades_r: number;
  expectancy_r: number | null;
  ci_low_r: number | null;
  ci_high_r: number | null;
  kelly: number | null;
  verdict: "unproven" | "positive" | "negative" | "unclear";
}

/** A Pydantic model's JSON schema, as far as a form needs it. */
export interface ParamSchema {
  properties: Record<string, { type?: string; title?: string; description?: string; default?: unknown; minimum?: number; maximum?: number }>;
}

export interface StrategyConfig {
  enabled: boolean;
  live: boolean;
  underlyings: string[];
  params: Record<string, unknown>;
}

/** One strategy as GET /strategies describes it: one file in backend/app/strategies. */
export interface StrategyInfo {
  name: string;
  family: "perp" | "options";
  version: number;
  label: string;
  summary: string;
  // options
  view?: "neutral" | "bullish" | "bearish" | "volatile";
  premium?: "credit" | "debit";
  risk?: "defined" | "undefined";
  required_checks?: string[];
  // perpetual
  stop_rule?: string;
  target_rule?: string;
  interval?: string;
  lookback?: number;
  max_hold_hours?: number;
  config: StrategyConfig;
  params: Record<string, number | boolean | string>;
  schema: ParamSchema;
  /** the enabled underlyings it runs on, both switches applied */
  underlyings_on: string[];
  /** the brokers its automatic entries go to */
  brokers: Broker[];
  record: StrategyRecord;
  latest: { strategy?: string; symbol?: string; bar_time?: number; at?: number; reason: string; decision?: DirectionalDecision | null }[];
}

export interface StrategiesResponse {
  strategies: StrategyInfo[];
  underlyings: string[];
  brokers: BrokerStatus[];
  jobs: { perp: JobStatus | null; options: JobStatus | null };
  /** the regime score an option strategy needs before it is built */
  min_score: number;
}

export interface PerpStats {
  open_24h: number | null;
  high_24h: number | null;
  low_24h: number | null;
  change_24h_pct: number | null;
  volume_24h: number | null;
  turnover_24h_usd: number | null;
  oi_usd: number | null;
  funding_rate_pct: number | null;
  bid: number | null;
  ask: number | null;
}

export interface ExpirySummary {
  expiry: number;
  label: string;
  hours: number;
  contracts: number;
  atm_strike: number | null;
  atm_iv: number | null;
  skew_25d: number | null;
  call_oi: number;
  put_oi: number;
  pcr_oi: number | null;
  pcr_volume: number | null;
  max_pain: number | null;
  call_wall: number | null;
  put_wall: number | null;
  straddle: number | null;
  implied_move: number | null;
  implied_move_pct: number | null;
}

export interface Overview {
  underlying: string;
  spot: number | null;
  perpetual: string;
  mark: number | null;
  stats: PerpStats | null;
  expiries: ExpirySummary[];
  default_expiry: number;
  realised_vol: { "24h": number | null; "7d": number | null; reference: number | null };
}

export interface ChainSide {
  symbol: string;
  mark: number | null;
  bid: number | null;
  ask: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  oi: number;
  volume: number;
}

export interface ChainRow {
  strike: number;
  call?: ChainSide;
  put?: ChainSide;
}

export interface Chain {
  underlying: string;
  expiry: number;
  spot: number;
  summary: ExpirySummary;
  rows: ChainRow[];
}

export interface StrategyScore {
  score: number;
  reasons: string[];
}

export interface RegimeMetrics {
  spot: number;
  atm_iv: number | null;
  rv_24h: number | null;
  rv_7d: number | null;
  vol_premium: number | null;
  rv_ratio: number | null;
  change_24h_pct: number | null;
  z_24h: number | null;
  rsi_1h: number | null;
  range_position_24h: number | null;
  implied_move_pct: number | null;
  max_pain: number | null;
  max_pain_distance_pct: number | null;
  pcr_oi: number | null;
  skew_25d: number | null;
  call_wall: number | null;
  put_wall: number | null;
  bars: number;
}

export interface Regime {
  underlying: string;
  expiry: number;
  label: string;
  direction: number;
  direction_parts: string[];
  metrics: RegimeMetrics;
  scores: Record<string, StrategyScore>;
  notes: string[];
  hours_to_expiry?: number;
}

export interface PlanLeg {
  symbol: string;
  kind: OptionKind;
  strike: number;
  side: Side;
  ratio: number;
  price: number;
  mark: number | null;
  bid: number | null;
  ask: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  contract_value: number;
  spread_pct: number | null;
}

export interface Plan {
  strategy: string;
  underlying: string;
  expiry: number;
  spot: number;
  legs: PlanLeg[];
}

export interface Analysis {
  net_premium: number;
  kind: "credit" | "debit";
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  pop: number | null;
  expected_value: number | null;
  pop_realised: number | null;
  expected_value_realised: number | null;
  reward_risk: number | null;
  margin: number;
  greeks: { delta: number | null; gamma: number | null; theta: number | null; vega: number | null };
  expected_move: number;
  curve?: { price: number; pnl: number }[];
  entry_fees: number;
  round_trip_fees: number;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  required: boolean;
}

export interface Sizing {
  broker: Broker;
  unit_risk: number;
  equity?: number;
  available?: number;
  suggested_qty?: number;
  worst_case_pct?: number | null;
  margin_ok?: boolean;
  error?: string;
}

export interface Preview {
  strategy: string;
  label: string;
  plan: Plan;
  qty: number;
  hours_to_expiry: number;
  iv: number | null;
  analysis: Analysis;
  checks: Check[];
  ok: boolean;
  sizing: Sizing[];
}

export interface Leg {
  id: number;
  structure_id: string;
  idx: number;
  symbol: string;
  kind: OptionKind | "perp";
  strike: number;
  side: Side;
  size: number;
  contract_value: number;
  entry_price: number | null;
  exit_price: number | null;
  entry_fee: number;
  exit_fee: number;
  mark: number | null;
  bid: number | null;
  ask: number | null;
}

export type StructureStatus = "opening" | "open" | "closing" | "closed" | "failed" | "error";

export interface OrderRecord {
  client_order_id: string;
  structure_id: string;
  broker: Broker;
  purpose: "open" | "close" | "unwind" | "settle";
  symbol: string;
  side: Side;
  size: number;
  limit_price: number;
  status: "pending" | "filled" | "failed" | "unknown";
  fill_price: number | null;
  filled_size: number;
  fee: number;
  exchange_id: string;
  error: string;
  created_at: number;
  updated_at: number;
}

export interface Structure {
  id: string;
  broker: Broker;
  underlying: string;
  strategy: string;
  label: string;
  source: "auto" | "manual";
  status: StructureStatus;
  /** options structures exit on P&L; a perp position at its strategy's stop and target prices */
  instrument: "options" | "perp";
  kind: "credit" | "debit" | "long" | "short";
  stop_price: number | null;
  target_price: number | null;
  reason: string;
  expiry: number;
  expiry_label: string;
  hours_left: number;
  qty: number;
  entry_spot: number | null;
  exit_spot: number | null;
  entry_cash: number;
  exit_cash: number;
  fees: number;
  pnl: number | null;
  max_profit: number | null;
  max_loss: number | null;
  margin: number;
  take_profit_pct: number;
  stop_loss_pct: number;
  take_profit_usd: number | null;
  stop_loss_usd: number | null;
  held_to_settlement: boolean; // a capped credit: no stop, take profit or time exit
  // A perpetual's exits as % from entry (positive is profit), and its trailing stop.
  stop_pct?: number | null;
  target_pct?: number | null;
  initial_stop_pct?: number | null;
  trail?: {
    active: boolean;
    initial_stop: number | null;
    moved: number;
    moved_r: number | null;
    best: number | null;
    best_pct: number | null;
    locked_in_pct: number | null;
    rule: string;
  };
  exit_prices?: { target: number; stop: number }; // one option: its premium at the take profit and the stop
  mark_pnl: number | null;
  opened_at: number;
  closed_at: number | null;
  close_reason: string;
  error: string;
  decision_id: number | null;
  legs: Leg[];
  live: {
    pnl: number | null;
    pnl_mid: number | null;
    missing: string[];
    spot: number | null;
    /** fees already charged on this structure's legs */
    fees_paid?: number;
    /** fees closing the held legs now would charge; null without a quote on every leg */
    exit_fees?: number | null;
  } | null;
  orders?: OrderRecord[];
  /** of the legs still held, in the underlying's units; null when a held option has no fresh quote */
  greeks?: { delta: number; gamma: number; theta: number; vega: number } | null;
}

export interface AppNotification {
  id: number;
  ts: number;
  type: string;
  level: "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown> | null;
  read_at: number | null;
}

export interface NotificationPage {
  rows: AppNotification[];
  total: number;
  unread: number;
  page: number;
  pages: number;
  size: number;
}

export interface OrderPage {
  rows: OrderRecord[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

export interface Account {
  broker: Broker;
  currency: string;
  equity: number;
  available: number;
  margin_used: number;
  cash: number;
  extra: Record<string, unknown>;
}

export interface BrokerStatus {
  name: Broker;
  active: boolean;
  ready: boolean;
  not_ready: string;
  auto: boolean;
  halted: string;
  real_money: boolean;
  env: string;
  /** why its positions do not match the exchange, which blocks its entries */
  reconcile?: string;
}

/** Every entry stopped (Stop all, or the drawdown limit), until resumed. */
export interface StopState {
  reason: string;
  at: number;
}

export interface Accounts {
  accounts: Partial<Record<Broker, Account | { error: string }>>;
  brokers: BrokerStatus[];
  /** each broker's trading day: the equity the daily loss limit measures from, and any halt */
  today: Partial<Record<Broker, { day: string; start_equity: number | null; halted: string }>>;
  stopped: StopState | null;
}

export interface Summary {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  pnl: number;
  gross_pnl?: number;
  fees: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  best: number | null;
  worst: number | null;
  avg_hold_hours: number | null;
  max_drawdown?: number;
  sharpe?: number | null;
  sortino?: number | null;
  /** P&L over what each trade risked at entry (`trading/edge.py`), with its 95% bootstrap range */
  trades_r?: number;
  expectancy_r?: number | null;
  ci_low_r?: number | null;
  ci_high_r?: number | null;
  verdict?: EdgeVerdict;
}

/** positive / negative: the whole 95% range on one side of zero; unclear: it spans zero; unproven: under 30 trades */
export type EdgeVerdict = "unproven" | "positive" | "negative" | "unclear";

/** a broker's equity at the start of a trading day */
export interface EquityPoint {
  broker: Broker;
  day: string;
  equity: number;
}

/** from what the rules said to what paid, in [since, until) */
export interface Funnel {
  since: number;
  until: number;
  signals: number;
  passed: number;
  orders: number;
  filled: number;
  profitable: number;
}

export interface Stats {
  overall: Summary & { max_drawdown: number; sharpe?: number | null; sortino?: number | null };
  /** auto trades by the regime score they were opened on ("60-69"), plus "manual" */
  by_score?: (Summary & { score: string })[];
  by_strategy: (Summary & { strategy: string })[];
  by_source: (Summary & { source: string })[];
  by_broker: (Summary & { broker: string })[];
  curve: { ts: number; pnl: number; trade: number; strategy: string; id: string }[];
  daily: Record<string, number>;
}

export interface Candidate {
  strategy: string;
  label: string;
  score: number;
  reasons: string[];
  status?: string;
  analysis?: Omit<Analysis, "curve">;
  legs?: { symbol: string; side: Side; kind: OptionKind; strike: number; price: number }[];
  checks?: Check[];
  ok?: boolean;
}

export interface Decision {
  id: number;
  ts: number;
  underlying: string;
  action: "open" | "skip" | "error";
  strategy: string;
  reason: string;
  expiry: number | null;
  spot: number | null;
  /** plus what the auto-trader recorded about this cycle: the score a strategy had to beat */
  regime: (Omit<Regime, "underlying" | "expiry"> & { min_score?: number; hours_to_expiry?: number }) | null;
  candidates: Candidate[] | null;
  orders: Record<string, { structure_id?: string; status?: string; qty?: number; error?: string; refused?: string }> | null;
}

export interface JobStatus {
  id: string;
  label: string;
  schedule: string;
  runs: number;
  failures: number;
  last_run: number | null;
  last_ms: number | null;
  last_error: string;
  last_result: string;
  next_run: number | null;
}

export interface AutoStatus {
  brokers: BrokerStatus[];
  job: JobStatus | null;
  config: {
    interval_minutes: number;
    min_score: number;
    underlyings: string[];
    strategies: string[];
    risk_per_trade_pct: number;
    max_spread_pct: number;
    exit_before_expiry_minutes: number;
  };
  now: Decision[]; // the latest decision per underlying, skips included: live, not stored
}

export interface AiModelStatus {
  model: string;
  key_present: boolean;
  missing: string[];
  calls?: number;
  failures?: number;
  last_error?: string;
  last_ms?: number | null;
  last_ok?: number;
}

export interface AiStatus {
  enabled: boolean;
  models: AiModelStatus[];
  last: { at?: number; ok?: boolean; model?: string; ms?: number; errors?: string[] };
  ready: string;
}

/** Which desk asked: the options desk writes about volatility, the market desk about direction. */
export type BriefKind = "options" | "market";

export interface AiBrief {
  id: number;
  ts: number;
  underlying: string;
  kind: BriefKind;
  model: string;
  latency_ms: number | null;
  error: string;
  // Only the sections of this brief's kind are present; both carry headline, summary and watch.
  content: ({
    headline: string;
    summary: string;
    watch: string[];
    confidence: "high" | "medium" | "low";
  } & Partial<Record<"volatility" | "positioning" | "trend" | "momentum_flow" | "levels" | "momentum" | "flow", string>>) | null;
}

export interface EventRow {
  id: number;
  ts: number;
  type: string;
  level: "info" | "warning" | "error";
  message: string;
  data: Record<string, unknown> | null;
}

export interface LiveEvent {
  type: string;
  message: string;
  level: "info" | "warning" | "error";
  data: Record<string, unknown>;
  ts: number;
}

export interface SystemStatus {
  started_at: number;
  stopped: StopState | null;
  uptime_seconds: number;
  feed: {
    ws_url: string;
    rest_url: string;
    websocket: { connected: boolean; since: number | null; last_message: number | null; reconnects: number; messages: number; error: string };
    rest: { last_refresh: number | null; error: string };
    prices: Record<string, { price: number; index: number | null; age_seconds: number; fresh: boolean }>;
    options: Record<string, number>;
    rest_calls: Record<string, { count: number; errors: number; last_ms: number; last_error: string }>;
  };
  jobs: JobStatus[];
  brokers: BrokerStatus[];
  ai: Omit<AiStatus, "ready">;
  notifications: { enabled: string[]; channels: Record<string, { sent: number; failed: number; last_at: number | null; last_error: string }> };
  events: { counts: Record<string, number>; dashboards: number };
  delta_api: Record<string, { count: number; errors: number; last_ms: number; last_error: string }>;
}

export type SettingsValues = Record<string, unknown> & {
  paper_active: boolean;
  delta_active: boolean;
  delta_env: "demo" | "live";
  is_real_money: boolean;
  active_brokers: Broker[];
  underlyings: string[];
  ai_fallback_models: string[];
  day_timezone: string;
};

export interface SettingsResponse {
  values: SettingsValues;
  limits: Record<string, { minimum?: number; maximum?: number }>;
  live_confirm_phrase: string;
}

export interface JournalMonth {
  year: number;
  month: number;
  timezone: string;
  days: Record<string, { pnl: number; trades: number; wins: number; decisions: number; opened: number }>;
  pnl: number;
  trades: number;
  wins: number;
}

export interface JournalDay {
  date: string;
  timezone: string;
  pnl: number;
  structures: Structure[];
  decisions: Decision[];
  events: EventRow[];
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface LivePrice {
  spot: number | null;
  mark: number | null;
  change_24h_pct: number | null;
  high_24h: number | null;
  low_24h: number | null;
  funding_rate_pct: number | null;
  oi_usd: number | null;
  bid: number | null;
  ask: number | null;
  atm_iv: number | null;
  pcr_oi: number | null;
  max_pain: number | null;
}

export interface DirectionalDecision {
  side: Side | null;
  reason: string;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
}

export interface SignalRow {
  id: number;
  ts: number;
  strategy: string;
  symbol: string;
  interval: string;
  bar_time: number;
  side: Side | "";
  action: "open" | "skip" | "error";
  reason: string;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  orders: Record<string, { structure_id?: string; status?: string; qty?: number; error?: string; refused?: string }> | null;
}

export interface MarketPulse {
  ready: boolean;
  state: string;
  headline: string;
  symbol: string;
  bar_time: number;
  price: number;
  measured: Record<string, unknown>;
  reasons: string[];
  size: Record<string, unknown> | string;
  way: {
    horizon_hours?: number;
    baseline?: number | null;
    conditional?: number | null;
    edge?: number | null;
    sample?: number;
    matched?: number;
    note?: string;
  }[] | string;
  note: string;
  sources: Record<string, string> | string[];
  loud: boolean;
}

export interface OutlookSummaryItem {
  scale: string;
  interval: string;
  label: string;
  good_for: string;
  direction: number;
  trend: string;
  horizon_hours: number;
  horizon_label: string;
  move_pct: number;
  move_usd: number;
  band: [number, number];
  probability: number | null;
  baseline: number | null;
  direction_source: string;
  move_source: string;
  sample: number;
  edge: number | null;
  note: string;
}

export interface MarketOutlook {
  symbol: string;
  ready: boolean;
  price: number;
  bar_time: number;
  bars: number;
  days: number;
  realised_vol: Record<string, number | null>;
  timeframes: Record<string, unknown>[];
  alignment: Record<string, unknown>;
  horizons: Record<string, unknown>;
  summary: OutlookSummaryItem[];
  direction: number;
  patterns: Record<string, unknown>[];
  sources: string[];
}

export interface NearbyLevelItem {
  price: number;
  label: string;
  confluence: number;
  distance_pct?: number;
}

export interface TrendlineItem {
  id: string;
  kind: "support" | "resistance";
  start_time: number;
  start_price: number;
  end_time: number;
  end_price: number;
  current_price: number;
  slope: number;
  touches: number;
  touch_times?: number[];
  distance_pct: number;
  status: "holding" | "approaching" | "broken";
  label: string;
}

export interface HorizontalClusterItem {
  id: string;
  price: number;
  kind: "support" | "resistance" | "cross_level";
  touches: number;
  touch_times?: number[];
  distance_pct: number;
  status: "holding" | "approaching" | "broken";
  label: string;
}

export interface TrendlinesResponse {
  ready: boolean;
  symbol: string;
  interval?: string;
  bars: number;
  latest_close: number;
  trendlines: TrendlineItem[];
  horizontal_clusters: HorizontalClusterItem[];
  ema_20?: number | null;
  ema_50?: number | null;
}

export interface StructureZone {
  kind: "fvg" | "ob" | "sr";
  direction: "bull" | "bear";
  low: number;
  high: number;
  mid?: number;
  width?: number;
  created_at: number;
  timeframe: string;
  state: "fresh" | "tested" | "mitigated" | "invalidated";
  touches?: number;
  last_touched_at?: number | null;
  strength?: number;
  fill_pct?: number;
  distance_pct?: number | null;
  first_touch_at?: number | null;
  reaction_pct?: number | null;
  worked?: boolean | null;
  live?: boolean;
}

export interface MarketStructure {
  symbol: string;
  ready: boolean;
  bar_time: number;
  bars: number;
  atr: number;
  price: number;
  liquidity: {
    ready?: boolean;
    pools?: { price: number; type: string; volume?: number }[];
    [key: string]: unknown;
  };
  zones: {
    ready?: boolean;
    zones?: StructureZone[];
    record?: Record<string, unknown>;
    nearest_above?: StructureZone | null;
    nearest_below?: StructureZone | null;
    states?: Record<string, number>;
    note?: string;
    supply?: { top: number; bottom: number; strength: number }[];
    demand?: { top: number; bottom: number; strength: number }[];
    [key: string]: unknown;
  };
  profile: {
    ready?: boolean;
    source?: string;
    session?: VolumeProfile | null;
    composite?: VolumeProfile | null;
    [key: string]: unknown;
  };
  flow: {
    bias?: string;
    delta?: number;
    cvd?: number;
    [key: string]: unknown;
  };
  levels: {
    supports?: number[];
    resistances?: number[];
    [key: string]: unknown;
  };
  nearby: {
    above?: NearbyLevelItem[];
    below?: NearbyLevelItem[];
  };
  smc?: {
    bias?: string;
    swing_high?: number | null;
    swing_low?: number | null;
    position?: number | null;
    squeeze?: boolean;
    bars?: number;
    breaks?: Array<{
      kind: "choch" | "bos";
      direction: "up" | "down";
      bar_time: number;
      level: number;
      close: number;
      displacement: number;
      volume_ratio: number;
      index: number;
    }>;
    gaps?: Array<{
      direction: "up" | "down";
      low: number;
      high: number;
      bar_time: number;
      filled: boolean;
    }>;
  };
}

/** One distribution of volume over price (`analysis/profile.py`). */
export interface VolumeProfile {
  label: string;
  poc: number;
  value_low: number;
  value_high: number;
  source: string;
  nodes: { price: number; volume: number; share: number; in_value: boolean; poc: boolean }[];
}

export interface MarketAnalysisResponse {
  underlying: string;
  symbol: string;
  price: number | null;
  index: number | null;
  stats: PerpStats | null;
  options: Record<string, unknown> | null;
  analysis: {
    ts: number;
    context: Record<string, unknown>;
    insights: { key: string; label: string; detail: string; stance: string; importance: string }[];
  } | null;
}


// -- fibonacci ------------------------------------------------------------------------------

export interface FibLevel {
  ratio: number;
  price: number;
  kind: "retracement" | "extension";
  label: string;
  golden: boolean;
}

export interface Fib {
  ready: boolean;
  note: string;
  leg: { direction: "up" | "down"; start_price: number; end_price: number; start_time: number; end_time: number; size: number } | null;
  levels: FibLevel[];
  price: number | null;
  /** how far back into the leg price has come: 0 at its end, 1 at its start */
  retraced: number | null;
  in_golden: boolean;
  symbol?: string;
  interval?: string;
}

/** One single-option trade with an edge at realised volatility (`options/ideas.py`). */
export interface OptionIdea {
  symbol: string;
  kind: "call" | "put";
  strike: number;
  expiry: number;
  hours_left: number;
  side: Side;
  price: number;
  fair: number;
  vol_used: number;
  edge: number;
  edge_per_lot: number;
  edge_on_risk_pct: number;
  pop: number | null;
  max_loss: number | null;
  max_loss_per_lot: number | null;
  target_price: number;
  needed_move_pct: number | null;
  p_target: number | null;
  hold_hours: number | null;
  exit: string;
  iv: number | null;
  delta: number | null;
  bid: number | null;
  ask: number | null;
  mark: number | null;
  spread_pct: number;
  view: "bullish" | "bearish";
  trend: "with" | "against" | "neutral";
}

export interface OptionIdeas {
  underlying: string;
  expiry: number;
  spot: number;
  lean: "bullish" | "bearish" | "neutral";
  direction: number;
  vol_buyer: number | null;
  vol_seller: number | null;
  considered: number;
  ideas: OptionIdea[];
  note: string;
}

/** A flow event read off the tape (`analysis/flow.py`). */
export interface FlowEvent {
  kind: "absorption" | "exhaustion";
  side: "bullish" | "bearish";
  time: number;
  price: number;
  detail: string;
}

export interface FootprintBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  delta: number;
  levels: [number, number, number][]; // price, bought, sold
}

export interface OrderFlow {
  ready: boolean;
  note?: string;
  bars?: number;
  interval?: string;
  price?: number;
  delta?: number;
  cvd?: number;
  series?: { time: number; close: number; delta: number; cvd: number }[];
  imbalances?: { price: number; side: "buy" | "sell"; ratio: number | null }[];
  stacked?: { side: "buy" | "sell"; low: number; high: number; levels: number }[];
  events?: FlowEvent[];
  divergence?: { side: "bullish" | "bearish"; detail: string } | null;
  profile?: { ready: boolean; poc?: number; buyers_at?: number | null; sellers_at?: number | null; rows?: { price: number; bought: number; sold: number; delta: number }[] };
  book_imbalance?: number | null;
  footprint?: FootprintBar[];
}

export interface OptionsPremium {
  call_bought: number;
  call_sold: number;
  put_bought: number;
  put_sold: number;
}

export interface GammaExposure {
  ready: boolean;
  note?: string;
  spot?: number;
  net?: number;
  regime?: "positive" | "negative";
  flip?: number | null;
  call_wall?: number | null;
  put_wall?: number | null;
  by_strike?: { strike: number; call: number; put: number }[];
  history: { ts: number; spot: number | null; net: number | null; flip: number | null }[];
  options_today: OptionsPremium | null;
}

export interface DayBias {
  ready: boolean;
  note?: string;
  score?: number;
  label?: "bullish" | "bearish" | "neutral";
  style?: "range" | "trend" | "mixed";
  gamma?: "positive" | "negative" | null;
  open?: number;
  price?: number;
  reasons?: { text: string; points: number }[];
  levels?: { vah: number; val: number; poc: number; flip: number | null; call_wall: number | null; put_wall: number | null };
  options_today?: OptionsPremium | null;
  record: {
    days: { day: number; score: number; label: string; style: string; ret_pct: number | null; correct: boolean | null }[];
    scored: number;
    right: number;
    hit_rate_pct: number | null;
    note: string;
  };
}
