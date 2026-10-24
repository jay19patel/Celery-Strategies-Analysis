"use client";

import { useState, useEffect } from "react";
import { Cpu, HardDrive, Clock, Activity, FileText } from "lucide-react";
import { api } from "@/lib/api";

export default function SystemHealthTab() {
  const [health, setHealth] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchSystemData = async () => {
    try {
      const [hData, eData] = await Promise.all([
        api.getHealth().catch(() => null),
        api.getEvents(100).catch(() => []),
      ]);
      setHealth(hData);
      setEvents(eData);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    fetchSystemData();
    const interval = setInterval(fetchSystemData, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      {/* System Health Cards */}
      <div className="metrics-grid">
        <div className="glass-panel metric-card accent-emerald">
          <span className="metric-label">Operational Health</span>
          <span className="metric-value" style={{ color: "var(--accent-emerald)" }}>
            {health?.status ? health.status.toUpperCase() : "ONLINE"}
          </span>
          <span className="metric-sub">FastAPI + Redis + SQLite</span>
        </div>

        <div className="glass-panel metric-card accent-cyan">
          <span className="metric-label">CPU Utilization</span>
          <span className="metric-value font-mono">
            {health?.cpu_percent !== undefined ? `${health.cpu_percent}%` : "—"}
          </span>
          <span className="metric-sub">Multi-core Processor Load</span>
        </div>

        <div className="glass-panel metric-card accent-purple">
          <span className="metric-label">Memory Usage</span>
          <span className="metric-value font-mono">
            {health?.memory_usage_mb ? `${health.memory_usage_mb} MB` : "—"}
          </span>
          <span className="metric-sub">Total Available: {health?.total_system_memory_gb || 0} GB</span>
        </div>

        <div className="glass-panel metric-card accent-blue">
          <span className="metric-label">System Uptime</span>
          <span className="metric-value font-mono">
            {health?.uptime_seconds ? `${(health.uptime_seconds / 60).toFixed(0)} min` : "0 min"}
          </span>
          <span className="metric-sub">Process Execution Runtime</span>
        </div>
      </div>

      {/* Audit Log Events Table */}
      <div className="glass-panel table-card">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
          <FileText size={18} color="var(--accent-cyan)" />
          <h3 style={{ fontSize: "1rem", fontWeight: 700 }}>System Audit Trail & Event Ledger</h3>
        </div>

        {events.length === 0 ? (
          <div style={{ padding: "2.5rem", textAlign: "center", color: "var(--text-muted)" }}>
            No audit events logged yet.
          </div>
        ) : (
          <table className="custom-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Event Type</th>
                <th>Details</th>
                <th>Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>#{e.id}</td>
                  <td>
                    <span className="badge badge-blue">{e.type}</span>
                  </td>
                  <td style={{ color: "var(--text-secondary)", maxWidth: "500px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {JSON.stringify(e)}
                  </td>
                  <td style={{ color: "var(--text-muted)", fontSize: "0.75rem" }}>
                    {e.ts ? new Date(e.ts * 1000).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
