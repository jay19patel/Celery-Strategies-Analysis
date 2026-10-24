"use client";

import { useState, useEffect } from "react";
import { BrainCircuit, Activity, CheckCircle2, XCircle } from "lucide-react";
import { api } from "@/lib/api";

export default function AnalyticsTab() {
  const [symbol, setSymbol] = useState("BTCUSD");
  const [predictions, setPredictions] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [preds, snaps] = await Promise.all([
          api.getPredictions(symbol, null, false, 50).catch(() => []),
          api.getSnapshots(symbol, 20).catch(() => []),
        ]);
        setPredictions(preds);
        setSnapshots(snaps);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [symbol]);

  const latestSnapshot = snapshots[0] || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Symbol Selection */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {["BTCUSD", "ETHUSD", "SOLUSD"].map((s) => (
            <button
              key={s}
              onClick={() => setSymbol(s)}
              className={`interval-pill ${symbol === s ? "active" : ""}`}
              style={{ padding: "0.45rem 1rem", fontSize: "0.85rem" }}
            >
              {s}
            </button>
          ))}
        </div>
        <div style={{ color: "var(--text-muted)", fontSize: "0.8rem" }}>
          Model Analytics & Technical Snapshots
        </div>
      </div>

      {/* Snapshot Cards */}
      <div className="metrics-grid">
        <div className="glass-panel metric-card accent-cyan">
          <span className="metric-label">24h Price Change</span>
          <span
            className="metric-value"
            style={{
              color: (latestSnapshot.change_24h_pct || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)",
            }}
          >
            {latestSnapshot.change_24h_pct ? `${Number(latestSnapshot.change_24h_pct).toFixed(2)}%` : "0.00%"}
          </span>
          <span className="metric-sub">1h: {latestSnapshot.change_1h_pct || 0}%</span>
        </div>

        <div className="glass-panel metric-card accent-purple">
          <span className="metric-label">Volatility & ATR</span>
          <span className="metric-value">
            {latestSnapshot.atr_pct ? `${Number(latestSnapshot.atr_pct).toFixed(2)}%` : "—"}
          </span>
          <span className="metric-sub">Implied Move: {latestSnapshot.implied_move_pct || 0}%</span>
        </div>

        <div className="glass-panel metric-card accent-blue">
          <span className="metric-label">EMA Trends</span>
          <span className="metric-value font-mono" style={{ fontSize: "1.2rem" }}>
            20: ${Number(latestSnapshot.ema20 || 0).toFixed(1)}
          </span>
          <span className="metric-sub">50: ${Number(latestSnapshot.ema50 || 0).toFixed(1)}</span>
        </div>

        <div className="glass-panel metric-card accent-emerald">
          <span className="metric-label">Options Sentiment</span>
          <span className="metric-value font-mono">
            PCR: {latestSnapshot.pcr_oi ? Number(latestSnapshot.pcr_oi).toFixed(2) : "—"}
          </span>
          <span className="metric-sub">Skew (25d): {latestSnapshot.skew_25d || "—"}</span>
        </div>
      </div>

      {/* Predictions Table */}
      <div className="glass-panel table-card">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
          <BrainCircuit size={18} color="var(--accent-cyan)" />
          <h3 style={{ fontSize: "1rem", fontWeight: 700 }}>AI Model Forecasts & Accuracy Scoring</h3>
        </div>

        {predictions.length === 0 ? (
          <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
            No prediction logs available for {symbol}.
          </div>
        ) : (
          <table className="custom-table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Stance</th>
                <th>Confidence</th>
                <th>Reference Price</th>
                <th>Horizon</th>
                <th>Actual Return</th>
                <th>R-Multiple</th>
                <th>Accuracy</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((p, idx) => {
                const isBull = (p.stance || "").toLowerCase().includes("bull") || (p.label || "").toLowerCase().includes("buy");
                const isBear = (p.stance || "").toLowerCase().includes("bear") || (p.label || "").toLowerCase().includes("sell");
                return (
                  <tr key={idx}>
                    <td style={{ color: "var(--accent-cyan)", fontWeight: 600 }}>{p.source || "Mistral-7B"}</td>
                    <td>
                      <span className={`badge ${isBull ? "badge-green" : isBear ? "badge-red" : "badge-blue"}`}>
                        {p.stance || p.label || "NEUTRAL"}
                      </span>
                    </td>
                    <td>{p.confidence ? `${(p.confidence * 100).toFixed(0)}%` : "—"}</td>
                    <td>${Number(p.price || 0).toFixed(2)}</td>
                    <td>{p.horizon_hours ? `${p.horizon_hours}h` : "15m"}</td>
                    <td
                      style={{
                        color: (p.actual_return || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)",
                        fontWeight: 600,
                      }}
                    >
                      {p.actual_return !== null && p.actual_return !== undefined ? `${(p.actual_return * 100).toFixed(2)}%` : "Pending"}
                    </td>
                    <td>{p.r_multiple !== null && p.r_multiple !== undefined ? `${p.r_multiple}R` : "—"}</td>
                    <td>
                      {p.correct === true && <CheckCircle2 size={16} color="var(--accent-emerald)" />}
                      {p.correct === false && <XCircle size={16} color="var(--accent-rose)" />}
                      {p.correct === null && <span style={{ color: "var(--text-muted)" }}>Active</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
