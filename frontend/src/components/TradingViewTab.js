"use client";

import { useState, useEffect } from "react";
import { TrendingUp, ArrowDownRight, ArrowUpRight, ShieldAlert, XCircle, Radio } from "lucide-react";
import CandlestickChart from "./CandlestickChart";
import { api } from "@/lib/api";

export default function TradingViewTab({ wsEvents = [] }) {
  const [symbol, setSymbol] = useState("BTCUSD");
  const [interval, setInterval] = useState("15m");
  const [bars, setBars] = useState([]);
  const [positions, setPositions] = useState([]);
  const [orders, setOrders] = useState([]);
  const [activeBottomTab, setActiveBottomTab] = useState("positions");

  // Order form state
  const [side, setSide] = useState("buy");
  const [size, setSize] = useState("1");
  const [price, setPrice] = useState("");
  const [stopLoss, setStopLoss] = useState("");
  const [takeProfit, setTakeProfit] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [orderFeedback, setOrderFeedback] = useState(null);

  // Fetch initial market data & positions
  const refreshData = async () => {
    try {
      const [barsData, posData, ordersData] = await Promise.all([
        api.getBars(symbol, interval, 300).catch(() => []),
        api.getPositions().catch(() => []),
        api.getOrders(50).catch(() => []),
      ]);

      setBars(barsData);
      setPositions(posData);
      setOrders(ordersData);

      // Autofill price if empty
      if (barsData.length > 0 && !price) {
        setPrice(String(barsData[barsData.length - 1].close));
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    refreshData();
    const timer = setInterval(refreshData, 10000);
    return () => clearInterval(timer);
  }, [symbol, interval]);

  // Handle order submission
  const handlePlaceOrder = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setOrderFeedback(null);

    try {
      const res = await api.placeOrder({
        symbol,
        side,
        size: Number(size),
        price: Number(price),
        stop_loss: stopLoss ? Number(stopLoss) : null,
        take_profit: takeProfit ? Number(takeProfit) : null,
        strategy: "manual",
        broker: "paper",
      });

      setOrderFeedback({ type: "success", message: `Order placed: ${res.client_order_id}` });
      refreshData();
    } catch (err) {
      setOrderFeedback({ type: "error", message: err.message || "Failed to place order" });
    } finally {
      setSubmitting(false);
    }
  };

  // Close position
  const handleClosePosition = async (sym) => {
    try {
      await api.closePosition(sym);
      refreshData();
    } catch (err) {
      alert(err.message || "Could not close position");
    }
  };

  // Close all positions
  const handleCloseAll = async () => {
    if (!confirm("Are you sure you want to close ALL positions?")) return;
    try {
      await api.closeAllPositions();
      refreshData();
    } catch (err) {
      alert(err.message || "Could not close all positions");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Upper Grid: Chart & Order Form */}
      <div className="terminal-layout">
        {/* Left: Chart Container */}
        <div className="glass-panel chart-card">
          <div className="chart-header">
            <div className="symbol-selector">
              <span className="symbol-pill">{symbol}</span>
              <div style={{ display: "flex", gap: "0.3rem" }}>
                {["BTCUSD", "ETHUSD", "SOLUSD"].map((s) => (
                  <button
                    key={s}
                    onClick={() => {
                      setSymbol(s);
                      setPrice("");
                    }}
                    className={`interval-pill ${symbol === s ? "active" : ""}`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="interval-pills">
              {["1m", "5m", "15m", "1h"].map((tf) => (
                <button
                  key={tf}
                  onClick={() => setInterval(tf)}
                  className={`interval-pill ${interval === tf ? "active" : ""}`}
                >
                  {tf}
                </button>
              ))}
            </div>
          </div>

          <div className="chart-canvas-container">
            <CandlestickChart bars={bars} symbol={symbol} />
          </div>
        </div>

        {/* Right: Order Entry Panel */}
        <div className="glass-panel order-form-card">
          <div className="card-title">
            <span>Execute Trade</span>
            <span className="badge badge-blue">Paper Engine</span>
          </div>

          {/* Buy/Sell Selector */}
          <div className="side-toggle">
            <button
              type="button"
              className={`side-btn buy ${side === "buy" ? "active" : ""}`}
              onClick={() => setSide("buy")}
            >
              LONG (BUY)
            </button>
            <button
              type="button"
              className={`side-btn sell ${side === "sell" ? "active" : ""}`}
              onClick={() => setSide("sell")}
            >
              SHORT (SELL)
            </button>
          </div>

          <form onSubmit={handlePlaceOrder} style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
            <div className="form-group">
              <label className="form-label">Order Size (Contracts)</label>
              <input
                type="number"
                min="1"
                step="1"
                value={size}
                onChange={(e) => setSize(e.target.value)}
                className="input-field"
                required
              />
            </div>

            <div className="form-group">
              <label className="form-label">Limit / Entry Price ($)</label>
              <input
                type="number"
                step="0.01"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="0.00"
                className="input-field"
                required
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.6rem" }}>
              <div className="form-group">
                <label className="form-label" style={{ color: "var(--accent-rose)" }}>Stop Loss ($)</label>
                <input
                  type="number"
                  step="0.01"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(e.target.value)}
                  placeholder="Optional"
                  className="input-field"
                />
              </div>

              <div className="form-group">
                <label className="form-label" style={{ color: "var(--accent-emerald)" }}>Take Profit ($)</label>
                <input
                  type="number"
                  step="0.01"
                  value={takeProfit}
                  onChange={(e) => setTakeProfit(e.target.value)}
                  placeholder="Optional"
                  className="input-field"
                />
              </div>
            </div>

            {orderFeedback && (
              <div
                style={{
                  padding: "0.5rem 0.75rem",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.8rem",
                  background: orderFeedback.type === "success" ? "rgba(16, 185, 129, 0.15)" : "rgba(244, 63, 94, 0.15)",
                  border: `1px solid ${orderFeedback.type === "success" ? "rgba(16, 185, 129, 0.3)" : "rgba(244, 63, 94, 0.3)"}`,
                  color: orderFeedback.type === "success" ? "var(--accent-emerald)" : "var(--accent-rose)",
                }}
              >
                {orderFeedback.message}
              </div>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="btn-primary"
              style={{
                background: side === "buy" ? "var(--accent-emerald)" : "var(--accent-rose)",
                marginTop: "0.4rem",
              }}
            >
              {submitting ? "Sending..." : `Submit ${side.toUpperCase()} Order`}
            </button>
          </form>
        </div>
      </div>

      {/* Lower Section: Tabs for Positions, Orders, and Live Stream */}
      <div className="glass-panel table-card">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
          <div className="nav-tabs" style={{ background: "transparent", padding: 0 }}>
            <button
              className={`nav-tab-btn ${activeBottomTab === "positions" ? "active" : ""}`}
              onClick={() => setActiveBottomTab("positions")}
            >
              Open Positions ({positions.length})
            </button>
            <button
              className={`nav-tab-btn ${activeBottomTab === "orders" ? "active" : ""}`}
              onClick={() => setActiveBottomTab("orders")}
            >
              Recent Orders ({orders.length})
            </button>
            <button
              className={`nav-tab-btn ${activeBottomTab === "events" ? "active" : ""}`}
              onClick={() => setActiveBottomTab("events")}
            >
              <Radio size={14} color="var(--accent-cyan)" />
              WebSocket Feed ({wsEvents.length})
            </button>
          </div>

          {activeBottomTab === "positions" && positions.length > 0 && (
            <button onClick={handleCloseAll} className="btn-danger" style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
              <XCircle size={14} />
              Close All Positions
            </button>
          )}
        </div>

        {/* Tab 1: Positions */}
        {activeBottomTab === "positions" && (
          positions.length === 0 ? (
            <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
              No active open positions. Place an order above to start trading.
            </div>
          ) : (
            <table className="custom-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Size</th>
                  <th>Entry Price</th>
                  <th>Stop Loss</th>
                  <th>Take Profit</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.symbol}>
                    <td style={{ fontWeight: 600 }}>{p.symbol}</td>
                    <td>
                      <span className={`badge ${p.side === "buy" ? "badge-green" : "badge-red"}`}>
                        {p.side}
                      </span>
                    </td>
                    <td>{p.size}</td>
                    <td>${Number(p.entry_price).toFixed(2)}</td>
                    <td style={{ color: "var(--accent-rose)" }}>{p.stop_loss ? `$${Number(p.stop_loss).toFixed(2)}` : "—"}</td>
                    <td style={{ color: "var(--accent-emerald)" }}>{p.take_profit ? `$${Number(p.take_profit).toFixed(2)}` : "—"}</td>
                    <td>
                      <button onClick={() => handleClosePosition(p.symbol)} className="btn-danger">
                        Close
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}

        {/* Tab 2: Orders */}
        {activeBottomTab === "orders" && (
          orders.length === 0 ? (
            <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
              No orders found.
            </div>
          ) : (
            <table className="custom-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Size</th>
                  <th>Price</th>
                  <th>Status</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.client_order_id}>
                    <td style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>{o.client_order_id.slice(-12)}</td>
                    <td style={{ fontWeight: 600 }}>{o.symbol}</td>
                    <td>
                      <span className={`badge ${o.side === "buy" ? "badge-green" : "badge-red"}`}>
                        {o.side}
                      </span>
                    </td>
                    <td>{o.size}</td>
                    <td>${Number(o.price).toFixed(2)}</td>
                    <td>
                      <span className="badge badge-blue">{o.status}</span>
                    </td>
                    <td style={{ color: "var(--text-secondary)" }}>
                      {o.ts ? new Date(o.ts * 1000).toLocaleTimeString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}

        {/* Tab 3: Real-time WebSocket Feed */}
        {activeBottomTab === "events" && (
          wsEvents.length === 0 ? (
            <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
              Listening for live Redis PubSub events over WebSocket...
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", maxHeight: "300px", overflowY: "auto" }}>
              {wsEvents.map((evt, idx) => (
                <div
                  key={idx}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "0.5rem 0.8rem",
                    borderRadius: "var(--radius-sm)",
                    background: "rgba(255, 255, 255, 0.02)",
                    border: "1px solid var(--border-subtle)",
                    fontSize: "0.8rem",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  <span style={{ color: "var(--accent-cyan)", fontWeight: 600 }}>{evt.type}</span>
                  <span style={{ color: "var(--text-secondary)", maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {JSON.stringify(evt.data || evt)}
                  </span>
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}
