// Shapes returned by the FastAPI backend (backend/app/api/routes). Kept in one place so a renamed
// field is one edit and one type error, not a blank cell somewhere on a page.

export type Broker = "paper" | "delta";
export type Side = "buy" | "sell";
export type OptionKind = "call" | "put";

export interface StrategySpec {
  key: string;
  label: string;
  view: "neutral" | "bullish" | "bearish" | "volatile";
  premium: "credit" | "debit";
  risk: "defined" | "undefined";
  summary: string;
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
  mark_pnl: number | null;
  opened_at: number;
  closed_at: number | null;
  close_reason: string;
  error: string;
  decision_id: number | null;
  legs: Leg[];
  live: { pnl: number | null; pnl_mid: number | null; missing: string[]; spot: number | null } | null;
  orders?: OrderRecord[];
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
}

export interface Accounts {
  accounts: Partial<Record<Broker, Account | { error: string }>>;
  brokers: BrokerStatus[];
  /** each broker's trading day: the equity the daily loss limit measures from, and any halt */
  today: Partial<Record<Broker, { day: string; start_equity: number | null; halted: string }>>;
}

export interface Summary {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  pnl: number;
  fees: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  best: number | null;
  worst: number | null;
  avg_hold_hours: number | null;
}

export interface Stats {
  overall: Summary & { max_drawdown: number };
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

export interface AiReview {
  used: boolean;
  note?: string;
  error?: string;
  decision?: "trade" | "skip";
  candidate?: string;
  confidence?: "high" | "medium" | "low";
  reasons?: string[];
  risks?: string[];
  invalid_choice?: boolean;
  model?: string;
  ms?: number;
  fallback?: boolean;
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
  regime: Omit<Regime, "underlying" | "expiry"> | null;
  candidates: Candidate[] | null;
  ai: AiReview | null;
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
  config: Record<string, unknown> & { auto_strategies: string[]; auto_interval_minutes: number };
  ai_ready: string;
  latest: Decision[];
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

export interface AiBrief {
  id: number;
  ts: number;
  underlying: string;
  model: string;
  latency_ms: number | null;
  error: string;
  content: {
    headline: string;
    volatility: string;
    positioning: string;
    summary: string;
    watch: string[];
    confidence: "high" | "medium" | "low";
  } | null;
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
  auto_strategies: string[];
  ai_fallback_models: string[];
  day_timezone: string;
};

export interface SettingsResponse {
  values: SettingsValues;
  limits: Record<string, { minimum?: number; maximum?: number }>;
  live_confirm_phrase: string;
  strategies: StrategySpec[];
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

export interface DirectionalStrategy {
  name: string;
  label: string;
  summary: string;
  stop_rule: string;
  target_rule: string;
  interval: string;
  lookback: number;
  enabled: boolean;
  /** what it decided on the last closed bar, per symbol */
  latest: { strategy: string; symbol: string; bar_time: number; at: number; reason: string; decision: DirectionalDecision | null }[];
}

export interface DirectionalStatus {
  strategies: DirectionalStrategy[];
  job: JobStatus | null;
  auto_options: boolean;
  brokers: BrokerStatus[];
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
    supply?: { top: number; bottom: number; strength: number }[];
    demand?: { top: number; bottom: number; strength: number }[];
    [key: string]: unknown;
  };
  profile: {
    ready?: boolean;
    poc?: number;
    vah?: number;
    val?: number;
    source?: string;
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
}

export interface VerdictItem {
  key: string;
  stance: string;
  label: string;
  detail: string;
  confidence: number;
  horizon_hours: number;
  numbers: Record<string, unknown>;
  measure: string;
  scorable: boolean;
  source: string;
  symbol: string;
}

export interface MarketVerdicts {
  symbol: string;
  days: number;
  verdicts: VerdictItem[];
  record: {
    cards: Record<string, unknown>[];
    total: number;
    min_marked: number;
    tests: number;
  };
  history: Record<string, unknown>[];
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

