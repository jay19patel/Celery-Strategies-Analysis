/**
 * API client for TradeBuddy FastAPI backend.
 * Uses HTTP-only cookie credentials and JSON payloads.
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function request(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  const contentType = response.headers.get("content-type");
  const isJson = contentType && contentType.includes("application/json");
  const data = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const errorMsg = (typeof data === "object" && data !== null && (data.error || data.detail)) || `HTTP ${response.status}`;
    throw new Error(errorMsg);
  }

  return data;
}

export const api = {
  // Auth
  login: (pin) => request("/auth/login", { method: "POST", body: JSON.stringify({ pin }) }),
  logout: () => request("/auth/logout", { method: "POST" }),
  verifyAuth: () => request("/auth/verify"),
  changePin: (current_pin, new_pin) =>
    request("/auth/change-pin", { method: "POST", body: JSON.stringify({ current_pin, new_pin }) }),

  // Trading
  placeOrder: (order) => request("/trading/orders", { method: "POST", body: JSON.stringify(order) }),
  getOrders: (limit = 100, broker = null) => {
    const params = new URLSearchParams({ limit });
    if (broker) params.append("broker", broker);
    return request(`/trading/orders?${params.toString()}`);
  },
  getOpenOrders: () => request("/trading/orders/open"),
  getPositions: () => request("/trading/positions"),
  closePosition: (symbol) => request(`/trading/positions/${encodeURIComponent(symbol)}/close`, { method: "POST" }),
  closeAllPositions: () => request("/trading/positions/close-all", { method: "POST" }),
  setTrailingStop: (symbol, trailing, max_steps = 5, broker = "paper") =>
    request(`/trading/positions/${encodeURIComponent(symbol)}/trailing-stop`, {
      method: "POST",
      body: JSON.stringify({ trailing, max_steps, broker }),
    }),

  // Market
  getBars: (symbol = "BTCUSD", interval = "15m", limit = 500, since = null) => {
    const params = new URLSearchParams({ symbol, interval, limit });
    if (since) params.append("since", since);
    return request(`/market/bars?${params.toString()}`);
  },
  getSnapshots: (symbol = "BTCUSD", limit = 50) =>
    request(`/market/snapshots?symbol=${encodeURIComponent(symbol)}&limit=${limit}`),
  getPredictions: (symbol = "BTCUSD", source = null, scored_only = false, limit = 100) => {
    const params = new URLSearchParams({ symbol, scored_only, limit });
    if (source) params.append("source", source);
    return request(`/market/predictions?${params.toString()}`);
  },

  // Paper Trading
  getPaperAccount: () => request("/paper/account"),
  getPaperTrades: (limit = 100, symbol = null) => {
    const params = new URLSearchParams({ limit });
    if (symbol) params.append("symbol", symbol);
    return request(`/paper/trades?${params.toString()}`);
  },
  getPaperStats: () => request("/paper/stats"),
  resetPaperAccount: (starting_balance = 10000.0) =>
    request("/paper/reset", { method: "POST", body: JSON.stringify({ starting_balance }) }),

  // Journal
  getDayJournal: (day) => request(`/journal/day?day=${encodeURIComponent(day)}`),
  getMonthJournal: (month) => request(`/journal/month?month=${encodeURIComponent(month)}`),

  // Settings
  getSettings: () => request("/settings"),
  updateSettings: (settings, confirm = "") =>
    request("/settings", { method: "PUT", body: JSON.stringify({ settings, confirm }) }),
  getToggles: () => request("/settings/toggles"),
  setToggle: (key, enabled) => request("/settings/toggle", { method: "POST", body: JSON.stringify({ key, enabled }) }),

  // System
  getHealth: () => request("/system/health"),
  getEvents: (limit = 100, event_type = null, since_id = null) => {
    const params = new URLSearchParams({ limit });
    if (event_type) params.append("event_type", event_type);
    if (since_id) params.append("since_id", since_id);
    return request(`/system/events?${params.toString()}`);
  },
};
