"use client";

import { useState } from "react";
import { Lock, ArrowRight, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";

export default function LoginModal({ onAuthenticated }) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (pin.length < 4) {
      setError("PIN must be at least 4 digits");
      return;
    }

    setLoading(true);
    setError("");

    try {
      await api.login(pin);
      onAuthenticated();
    } catch (err) {
      setError(err.message || "Invalid PIN. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="login-card glass-panel">
        <div style={{ display: "flex", justifyContent: "center" }}>
          <div className="brand-logo" style={{ width: 48, height: 48 }}>
            <Lock size={24} />
          </div>
        </div>

        <div>
          <h2 style={{ fontSize: "1.35rem", fontWeight: 700, marginBottom: "0.4rem" }}>
            Unlock Terminal
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            Enter your TradeBuddy security PIN to access live trading controls
          </p>
        </div>

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          <div className="form-group">
            <input
              type="password"
              maxLength={16}
              value={pin}
              onChange={(e) => {
                setPin(e.target.value);
                if (error) setError("");
              }}
              placeholder="Enter PIN (Default: 242425)"
              className="input-field"
              style={{ textAlign: "center", fontSize: "1.4rem", letterSpacing: "0.3em", padding: "0.85rem" }}
              autoFocus
            />
          </div>

          {error && (
            <div
              style={{
                background: "rgba(244, 63, 94, 0.15)",
                border: "1px solid rgba(244, 63, 94, 0.3)",
                color: "var(--accent-rose)",
                fontSize: "0.8rem",
                padding: "0.5rem",
                borderRadius: "var(--radius-sm)",
              }}
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || pin.length < 4}
            className="btn-primary"
            style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "0.5rem" }}
          >
            {loading ? "Authenticating..." : "Access Terminal"}
            <ArrowRight size={18} />
          </button>
        </form>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "0.4rem", color: "var(--text-muted)", fontSize: "0.75rem" }}>
          <ShieldCheck size={14} color="var(--accent-emerald)" />
          <span>Secured with HMAC Session & Argon2 hashing</span>
        </div>
      </div>
    </div>
  );
}
