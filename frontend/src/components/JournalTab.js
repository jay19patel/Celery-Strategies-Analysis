"use client";

import { useState, useEffect } from "react";
import { BookOpen, Calendar, Award, TrendingUp, TrendingDown } from "lucide-react";
import { api } from "@/lib/api";

export default function JournalTab() {
  const today = new Date().toISOString().slice(0, 10);
  const currentMonth = today.slice(0, 7);

  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [selectedDay, setSelectedDay] = useState(today);
  const [monthData, setMonthData] = useState(null);
  const [dayData, setDayData] = useState(null);
  const [stats, setStats] = useState(null);

  useEffect(() => {
    const fetchJournal = async () => {
      try {
        const [mRes, dRes, sRes] = await Promise.all([
          api.getMonthJournal(selectedMonth).catch(() => null),
          api.getDayJournal(selectedDay).catch(() => null),
          api.getPaperStats().catch(() => null),
        ]);
        setMonthData(mRes);
        setDayData(dRes);
        setStats(sRes);
      } catch {
        // ignore
      }
    };
    fetchJournal();
  }, [selectedMonth, selectedDay]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* Overview Performance Metrics */}
      <div className="metrics-grid">
        <div className="glass-panel metric-card accent-cyan">
          <span className="metric-label">Win Rate</span>
          <span className="metric-value font-mono">
            {stats ? `${Number(stats.win_rate || 0).toFixed(1)}%` : "0.0%"}
          </span>
          <span className="metric-sub">{stats ? `${stats.winning_trades || 0} Wins / ${stats.losing_trades || 0} Losses` : "0 Trades"}</span>
        </div>

        <div className="glass-panel metric-card accent-emerald">
          <span className="metric-label">Total Realized PnL</span>
          <span
            className="metric-value font-mono"
            style={{ color: (stats?.total_pnl || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)" }}
          >
            ${stats ? Number(stats.total_pnl || 0).toFixed(2) : "0.00"}
          </span>
          <span className="metric-sub">Profit Factor: {stats?.profit_factor ? Number(stats.profit_factor).toFixed(2) : "—"}</span>
        </div>

        <div className="glass-panel metric-card accent-rose">
          <span className="metric-label">Max Drawdown</span>
          <span className="metric-value font-mono" style={{ color: "var(--accent-rose)" }}>
            {stats ? `${Number(stats.max_drawdown_pct || 0).toFixed(1)}%` : "0.0%"}
          </span>
          <span className="metric-sub">Account Peak Protection</span>
        </div>

        <div className="glass-panel metric-card accent-blue">
          <span className="metric-label">Selected Day PnL</span>
          <span
            className="metric-value font-mono"
            style={{ color: (dayData?.total_pnl || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)" }}
          >
            ${dayData ? Number(dayData.total_pnl || 0).toFixed(2) : "0.00"}
          </span>
          <span className="metric-sub">{selectedDay} ({dayData?.trade_count || 0} trades)</span>
        </div>
      </div>

      {/* Calendar Heatmap & Daily Breakdown */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
        {/* Heatmap Panel */}
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <Calendar size={18} color="var(--accent-cyan)" />
              <h3 style={{ fontSize: "1rem", fontWeight: 700 }}>Monthly Activity Heatmap</h3>
            </div>
            <input
              type="month"
              value={selectedMonth}
              onChange={(e) => setSelectedMonth(e.target.value)}
              className="input-field"
              style={{ padding: "0.3rem 0.6rem", fontSize: "0.85rem" }}
            />
          </div>

          <div className="heatmap-grid">
            {monthData?.days?.map((d) => {
              const pnl = Number(d.pnl || 0);
              const count = Number(d.trade_count || 0);
              const isSelected = d.day === selectedDay;
              const cellClass = count === 0 ? "heatmap-neutral" : pnl > 0 ? "heatmap-positive" : "heatmap-negative";

              return (
                <div
                  key={d.day}
                  onClick={() => setSelectedDay(d.day)}
                  className={`heatmap-cell ${cellClass}`}
                  style={{
                    outline: isSelected ? "2px solid var(--accent-cyan)" : "none",
                  }}
                >
                  <span style={{ fontSize: "0.7rem", opacity: 0.8 }}>{d.day.slice(-2)}</span>
                  {count > 0 && (
                    <span style={{ fontSize: "0.68rem" }}>
                      {pnl >= 0 ? `+$${pnl.toFixed(0)}` : `-$${Math.abs(pnl).toFixed(0)}`}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Selected Day Breakdown */}
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
            <BookOpen size={18} color="var(--accent-cyan)" />
            <h3 style={{ fontSize: "1rem", fontWeight: 700 }}>Day Review: {selectedDay}</h3>
          </div>

          {!dayData || dayData.trade_count === 0 ? (
            <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
              No trades recorded for this day.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "0.75rem" }}>
                <div style={{ padding: "0.75rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)" }}>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>WIN RATE</div>
                  <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent-cyan)" }}>
                    {dayData.win_rate}%
                  </div>
                </div>
                <div style={{ padding: "0.75rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)" }}>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>BEST TRADE</div>
                  <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent-emerald)" }}>
                    +${dayData.best_trade}
                  </div>
                </div>
                <div style={{ padding: "0.75rem", background: "var(--bg-input)", borderRadius: "var(--radius-sm)" }}>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>WORST TRADE</div>
                  <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--accent-rose)" }}>
                    ${dayData.worst_trade}
                  </div>
                </div>
              </div>

              <div style={{ maxHeight: "250px", overflowY: "auto" }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>PnL</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dayData.trades?.map((t, idx) => (
                      <tr key={idx}>
                        <td style={{ fontWeight: 600 }}>{t.symbol}</td>
                        <td>
                          <span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`}>
                            {t.side}
                          </span>
                        </td>
                        <td style={{ color: t.pnl >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)", fontWeight: 700 }}>
                          ${Number(t.pnl).toFixed(2)}
                        </td>
                        <td style={{ color: "var(--text-secondary)" }}>{t.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
