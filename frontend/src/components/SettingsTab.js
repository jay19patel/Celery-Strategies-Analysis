"use client";

import { useState, useEffect } from "react";
import { Sliders, ToggleLeft, ToggleRight, KeyRound, RefreshCcw } from "lucide-react";
import { api } from "@/lib/api";

export default function SettingsTab() {
  const [settings, setSettings] = useState({});
  const [toggles, setToggles] = useState([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null);

  // Change PIN state
  const [currentPin, setCurrentPin] = useState("");
  const [newPin, setNewPin] = useState("");
  const [pinFeedback, setPinFeedback] = useState(null);

  // Reset balance
  const [resetBalance, setResetBalance] = useState("10000");

  const loadSettings = async () => {
    try {
      const [sData, tData] = await Promise.all([
        api.getSettings().catch(() => ({})),
        api.getToggles().catch(() => []),
      ]);
      setSettings(sData);
      setToggles(tData);
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleToggle = async (key, currentStatus) => {
    try {
      await api.setToggle(key, !currentStatus);
      setToggles((prev) =>
        prev.map((t) => (t.key === key ? { ...t, enabled: !currentStatus } : t))
      );
    } catch (err) {
      alert(err.message || "Failed to update toggle");
    }
  };

  const handleSettingChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const handleSaveSettings = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);

    try {
      const res = await api.updateSettings(settings);
      setMessage({ type: "success", text: "Settings successfully updated!" });
      if (res.warnings && Object.keys(res.warnings).length > 0) {
        alert(JSON.stringify(res.warnings));
      }
    } catch (err) {
      setMessage({ type: "error", text: err.message || "Failed to update settings" });
    } finally {
      setSaving(false);
    }
  };

  const handleChangePin = async (e) => {
    e.preventDefault();
    setPinFeedback(null);
    try {
      await api.changePin(currentPin, newPin);
      setPinFeedback({ type: "success", text: "PIN successfully changed!" });
      setCurrentPin("");
      setNewPin("");
    } catch (err) {
      setPinFeedback({ type: "error", text: err.message || "Failed to change PIN" });
    }
  };

  const handleResetAccount = async () => {
    if (!confirm(`Reset paper trading account balance to $${resetBalance}?`)) return;
    try {
      await api.resetPaperAccount(Number(resetBalance));
      alert(`Paper account successfully reset to $${resetBalance}`);
    } catch (err) {
      alert(err.message || "Failed to reset paper account");
    }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1.5rem" }}>
      {/* Column 1: Feature Flags & Toggles */}
      <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
            <ToggleRight size={20} color="var(--accent-cyan)" />
            <h3 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Operational Toggles & Strategies</h3>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
            {toggles.map((t) => (
              <div
                key={t.key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0.75rem 1rem",
                  background: "var(--bg-input)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, fontSize: "0.88rem", textTransform: "capitalize" }}>
                    {t.key.replace(/_/g, " ")}
                  </div>
                  <div style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                    {t.enabled ? "Enabled & Active" : "Disabled"}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => handleToggle(t.key, t.enabled)}
                  style={{
                    background: "transparent",
                    border: "none",
                    cursor: "pointer",
                    color: t.enabled ? "var(--accent-emerald)" : "var(--text-muted)",
                    display: "flex",
                    alignItems: "center",
                  }}
                >
                  {t.enabled ? <ToggleRight size={28} /> : <ToggleLeft size={28} />}
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Change PIN Panel */}
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
            <KeyRound size={20} color="var(--accent-purple)" />
            <h3 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Security: Update Access PIN</h3>
          </div>

          <form onSubmit={handleChangePin} style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
            <div className="form-group">
              <label className="form-label">Current PIN</label>
              <input
                type="password"
                value={currentPin}
                onChange={(e) => setCurrentPin(e.target.value)}
                placeholder="Current PIN"
                className="input-field"
                required
              />
            </div>
            <div className="form-group">
              <label className="form-label">New PIN</label>
              <input
                type="password"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
                placeholder="New 4-16 digit PIN"
                className="input-field"
                required
              />
            </div>

            {pinFeedback && (
              <div
                style={{
                  fontSize: "0.8rem",
                  color: pinFeedback.type === "success" ? "var(--accent-emerald)" : "var(--accent-rose)",
                }}
              >
                {pinFeedback.text}
              </div>
            )}

            <button type="submit" className="btn-primary" style={{ marginTop: "0.5rem" }}>
              Update Security PIN
            </button>
          </form>
        </div>
      </div>

      {/* Column 2: Risk Parameters & Reset Account */}
      <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1.25rem" }}>
            <Sliders size={20} color="var(--accent-cyan)" />
            <h3 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Risk & Execution Parameters</h3>
          </div>

          <form onSubmit={handleSaveSettings} style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
              <div className="form-group">
                <label className="form-label">Default Stop Loss (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={settings.stop_loss_pct || "2.0"}
                  onChange={(e) => handleSettingChange("stop_loss_pct", e.target.value)}
                  className="input-field"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Take Profit Target (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={settings.take_profit_pct || "4.0"}
                  onChange={(e) => handleSettingChange("take_profit_pct", e.target.value)}
                  className="input-field"
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
              <div className="form-group">
                <label className="form-label">Paper Leverage (x)</label>
                <input
                  type="number"
                  step="1"
                  value={settings.paper_leverage || "1"}
                  onChange={(e) => handleSettingChange("paper_leverage", e.target.value)}
                  className="input-field"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Risk Per Trade (%)</label>
                <input
                  type="number"
                  step="0.1"
                  value={settings.risk_per_trade_pct || "1.0"}
                  onChange={(e) => handleSettingChange("risk_per_trade_pct", e.target.value)}
                  className="input-field"
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
              <div className="form-group">
                <label className="form-label">ATR Stop Multiplier</label>
                <input
                  type="number"
                  step="0.1"
                  value={settings.atr_stop_mult || "1.5"}
                  onChange={(e) => handleSettingChange("atr_stop_mult", e.target.value)}
                  className="input-field"
                />
              </div>

              <div className="form-group">
                <label className="form-label">Daily Loss Limit (%)</label>
                <input
                  type="number"
                  step="0.5"
                  value={settings.daily_loss_limit_pct || "5.0"}
                  onChange={(e) => handleSettingChange("daily_loss_limit_pct", e.target.value)}
                  className="input-field"
                />
              </div>
            </div>

            {message && (
              <div
                style={{
                  fontSize: "0.8rem",
                  color: message.type === "success" ? "var(--accent-emerald)" : "var(--accent-rose)",
                }}
              >
                {message.text}
              </div>
            )}

            <button type="submit" disabled={saving} className="btn-primary" style={{ marginTop: "0.5rem" }}>
              {saving ? "Saving Changes..." : "Save Configuration"}
            </button>
          </form>
        </div>

        {/* Paper Account Reset */}
        <div className="glass-panel" style={{ padding: "1.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
            <RefreshCcw size={20} color="var(--accent-amber)" />
            <h3 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Paper Account Reset</h3>
          </div>

          <p style={{ color: "var(--text-secondary)", fontSize: "0.82rem", marginBottom: "1rem" }}>
            Reset paper trading simulation balance to start a fresh trading experiment.
          </p>

          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="number"
              value={resetBalance}
              onChange={(e) => setResetBalance(e.target.value)}
              className="input-field"
              style={{ width: "160px" }}
            />
            <button type="button" onClick={handleResetAccount} className="btn-danger" style={{ flex: 1, padding: "0.75rem" }}>
              Reset Simulation Balance
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
