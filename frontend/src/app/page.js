"use client";

import { useState, useEffect } from "react";
import {
  TrendingUp,
  BrainCircuit,
  BookOpen,
  Sliders,
  Activity,
  LogOut,
  Wallet,
  Zap,
} from "lucide-react";
import TradingViewTab from "@/components/TradingViewTab";
import AnalyticsTab from "@/components/AnalyticsTab";
import JournalTab from "@/components/JournalTab";
import SettingsTab from "@/components/SettingsTab";
import SystemHealthTab from "@/components/SystemHealthTab";
import LoginModal from "@/components/LoginModal";
import { useWebSocket } from "@/lib/useWebSocket";
import { api } from "@/lib/api";

export default function Home() {
  const [activeTab, setActiveTab] = useState("terminal");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [account, setAccount] = useState({ balance: 0, realized_pnl: 0 });

  // Real-time WebSocket connection
  const { isConnected, eventHistory } = useWebSocket();

  // Check auth status on load
  const checkAuth = async () => {
    try {
      const res = await api.verifyAuth();
      setIsAuthenticated(res.authenticated);
      if (res.authenticated) {
        loadAccount();
      }
    } catch {
      setIsAuthenticated(false);
    } finally {
      setCheckingAuth(false);
    }
  };

  const loadAccount = async () => {
    try {
      const acc = await api.getPaperAccount();
      setAccount(acc);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    checkAuth();
  }, []);

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch {
      // ignore
    }
    setIsAuthenticated(false);
  };

  if (checkingAuth) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
        INITIALIZING TRADEBUDDY TERMINAL...
      </div>
    );
  }

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="top-header">
        <div className="brand-section">
          <div className="brand-logo">
            <Zap size={20} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span className="brand-title">TradeBuddy</span>
              <span className="brand-badge">v4 PRO</span>
            </div>
          </div>
        </div>

        {/* Center: Navigation Tabs */}
        <nav className="nav-tabs">
          <button
            className={`nav-tab-btn ${activeTab === "terminal" ? "active" : ""}`}
            onClick={() => setActiveTab("terminal")}
          >
            <TrendingUp size={16} />
            Terminal
          </button>
          <button
            className={`nav-tab-btn ${activeTab === "analytics" ? "active" : ""}`}
            onClick={() => setActiveTab("analytics")}
          >
            <BrainCircuit size={16} />
            Analytics & AI
          </button>
          <button
            className={`nav-tab-btn ${activeTab === "journal" ? "active" : ""}`}
            onClick={() => setActiveTab("journal")}
          >
            <BookOpen size={16} />
            Journal
          </button>
          <button
            className={`nav-tab-btn ${activeTab === "settings" ? "active" : ""}`}
            onClick={() => setActiveTab("settings")}
          >
            <Sliders size={16} />
            Settings
          </button>
          <button
            className={`nav-tab-btn ${activeTab === "system" ? "active" : ""}`}
            onClick={() => setActiveTab("system")}
          >
            <Activity size={16} />
            System
          </button>
        </nav>

        {/* Right Section: Balance & WebSocket status */}
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          {isAuthenticated && (
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", background: "var(--bg-input)", padding: "0.4rem 0.85rem", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
              <Wallet size={16} color="var(--accent-cyan)" />
              <div style={{ fontSize: "0.82rem", fontFamily: "var(--font-mono)" }}>
                <span style={{ color: "var(--text-muted)", marginRight: "0.4rem" }}>BAL:</span>
                <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>
                  ${Number(account.balance || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
                <span style={{ marginLeft: "0.6rem", color: (account.realized_pnl || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-rose)", fontWeight: 600 }}>
                  {(account.realized_pnl || 0) >= 0 ? "+" : ""}${Number(account.realized_pnl || 0).toFixed(2)}
                </span>
              </div>
            </div>
          )}

          {/* WebSocket Live Pill */}
          <div className={`ws-pill ${isConnected ? "" : "disconnected"}`}>
            <div className="pulsing-dot" />
            <span>{isConnected ? "WS STREAM" : "RECONNECTING"}</span>
          </div>

          {isAuthenticated && (
            <button
              onClick={handleLogout}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-muted)",
                cursor: "pointer",
                padding: "0.4rem",
                display: "flex",
                alignItems: "center",
              }}
              title="Lock Terminal"
            >
              <LogOut size={18} />
            </button>
          )}
        </div>
      </header>

      {/* Main Tab Content */}
      <main>
        {activeTab === "terminal" && <TradingViewTab wsEvents={eventHistory} />}
        {activeTab === "analytics" && <AnalyticsTab />}
        {activeTab === "journal" && <JournalTab />}
        {activeTab === "settings" && <SettingsTab />}
        {activeTab === "system" && <SystemHealthTab />}
      </main>

      {/* Security PIN Login Modal when not authenticated */}
      {!isAuthenticated && (
        <LoginModal
          onAuthenticated={() => {
            setIsAuthenticated(true);
            loadAccount();
          }}
        />
      )}
    </div>
  );
}
