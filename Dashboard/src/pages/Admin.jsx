import { useEffect, useMemo, useState } from "react";

/* ─── Tokens ─────────────────────────────────────────────────── */
const T = {
  bg:       "#080c12",
  surface:  "#0e1420",
  card:     "#111827",
  border:   "#1e2a3a",
  borderHi: "#2a3a52",
  text:     "#e2eaf6",
  muted:    "#4a6080",
  subtle:   "#243044",
  blue:     "#3b82f6",
  green:    "#22c55e",
  amber:    "#f59e0b",
  red:      "#ef4444",
  purple:   "#a78bfa",
  teal:     "#2dd4bf",
};

const API_BASE = "http://127.0.0.1:5000/api/forecast";

function pretty(obj) {
  try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
}

/* ─── Badge ──────────────────────────────────────────────────── */
const Badge = ({ label, color }) => (
  <span style={{
    background: color + "22",
    border: `1px solid ${color}44`,
    color,
    borderRadius: 6,
    padding: "3px 8px",
    fontSize: 10,
    fontWeight: 800,
    textTransform: "uppercase",
    letterSpacing: 1,
  }}>{label}</span>
);

/* ─── Button ─────────────────────────────────────────────────── */
const Button = ({ children, onClick, disabled, variant = "primary", fullWidth }) => {
  const styles = {
    primary: { background: T.blue,    color: "#fff" },
    ghost:   { background: T.surface, color: T.text, border: `1px solid ${T.borderHi}` },
    danger:  { background: T.red,     color: "#fff" },
    amber:   { background: T.amber,   color: "#111" },
    green:   { background: T.green,   color: "#111" },
  }[variant];

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        ...styles,
        border: styles.border || "none",
        borderRadius: 8,
        padding: "9px 16px",
        fontWeight: 800,
        fontSize: 12,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "opacity 0.15s, transform 0.05s",
        fontFamily: "'IBM Plex Sans', sans-serif",
        letterSpacing: 0.3,
        width: fullWidth ? "100%" : undefined,
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
      onMouseDown={e => !disabled && (e.currentTarget.style.transform = "scale(0.98)")}
      onMouseUp={e => (e.currentTarget.style.transform = "scale(1)")}
    >
      {children}
    </button>
  );
};

/* ─── Panel ──────────────────────────────────────────────────── */
const Panel = ({ title, subtitle, right, children, accent }) => (
  <div style={{
    background: T.card,
    border: `1px solid ${T.border}`,
    borderTop: accent ? `2px solid ${accent}` : `1px solid ${T.border}`,
    borderRadius: 12,
    padding: "18px 20px",
    height: "100%",
    boxSizing: "border-box",
  }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14, gap: 10 }}>
      <div>
        <div style={{ fontSize: 12, fontWeight: 800, color: T.text, letterSpacing: 0.2 }}>{title}</div>
        {subtitle && <div style={{ fontSize: 10, color: T.muted, marginTop: 3 }}>{subtitle}</div>}
      </div>
      {right}
    </div>
    {children}
  </div>
);

/* ─── Health Stat ────────────────────────────────────────────── */
const HealthStat = ({ label, value, color }) => (
  <div style={{
    display: "flex", flexDirection: "column", gap: 4,
    padding: "10px 16px",
    background: T.surface,
    border: `1px solid ${T.border}`,
    borderLeft: `3px solid ${color || T.border}`,
    borderRadius: 8,
    flex: 1, minWidth: 110,
  }}>
    <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.5, fontWeight: 700 }}>{label}</div>
    <div style={{ fontSize: 13, fontWeight: 800, color: color || T.text, fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
  </div>
);

/* ─── Op Group ───────────────────────────────────────────────── */
const OpGroup = ({ label, children }) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{
      fontSize: 9, color: T.muted, textTransform: "uppercase",
      letterSpacing: 2, fontWeight: 700, marginBottom: 8,
      display: "flex", alignItems: "center", gap: 8,
    }}>
      <div style={{ flex: 1, height: 1, background: T.border }} />
      {label}
      <div style={{ flex: 1, height: 1, background: T.border }} />
    </div>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      {children}
    </div>
  </div>
);

/* ─── Toast ──────────────────────────────────────────────────── */
const Toast = ({ alert, onClose }) => {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (alert) {
      setVisible(false);
      requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true)));
    }
  }, [alert]);

  if (!alert) return null;

  const color = alert.type === "error" ? T.red : alert.type === "success" ? T.green : T.amber;
  const icon  = alert.type === "success" ? "✅" : alert.type === "error" ? "❌" : "⏳";

  return (
    <div style={{
      position: "fixed",
      bottom: 28,
      right: 28,
      zIndex: 9999,
      maxWidth: 380,
      minWidth: 280,
      background: T.card,
      border: `1px solid ${color}55`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 12,
      padding: "14px 16px 10px",
      boxShadow: "0 16px 48px rgba(0,0,0,0.6)",
      transform: visible ? "translateY(0)" : "translateY(20px)",
      opacity: visible ? 1 : 0,
      transition: "transform 0.25s ease, opacity 0.25s ease",
    }}>
      {/* progress bar */}
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0,
        height: 2, background: color + "33", borderRadius: "12px 12px 0 0", overflow: "hidden",
      }}>
        <div style={{
          height: "100%", background: color,
          animation: "shrink 6s linear forwards",
        }} />
      </div>

      <style>{`@keyframes shrink { from { width: 100%; } to { width: 0%; } }`}</style>

      <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
        <span style={{ fontSize: 16 }}>{icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 11, fontWeight: 800, color, textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>
            {alert.type === "success" ? "Success" : alert.type === "error" ? "Error" : "Notice"}
          </div>
          <div style={{ fontSize: 12, color: T.text, lineHeight: 1.4 }}>{alert.msg}</div>
        </div>
        <button onClick={onClose} style={{
          background: "none", border: "none", color: T.muted,
          cursor: "pointer", fontSize: 14, padding: 0, lineHeight: 1,
        }}>✕</button>
      </div>
    </div>
  );
};

/* ─── Main ───────────────────────────────────────────────────── */
export default function Admin() {
  const [health, setHealth]   = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [log, setLog]         = useState("");
  const [alert, setAlert]     = useState(null);

  const statusColor = useMemo(() => {
    if (!health) return T.muted;
    return health.status === "HEALTHY" ? T.green : T.red;
  }, [health]);

  const appendLog = (title, payload) => {
    const stamp = new Date().toLocaleString();
    setLog(prev => `[${stamp}] ${title}\n${pretty(payload)}\n\n` + prev);
  };

  const call = async (key, path, options = {}) => {
    setBusyKey(key);
    setAlert(null);
    try {
      const res  = await fetch(`${API_BASE}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await res.json().catch(() => ({}));

      if (res.status === 429) {
        const days = data?.days_remaining ?? "?";
        const last = data?.last_trained || data?.last_retuned || "";
        const msg  = `${data?.error || "Action blocked"} — ${data?.cooldown || "cooldown"}. Try again in ${days} day(s).${last ? ` Last: ${last}` : ""}`;
        setAlert({ type: "info", msg });
        appendLog(`${key} ⏳ (${res.status})`, data);
        return { ok: false, status: res.status, data };
      }

      if (!res.ok) {
        setAlert({ type: "error", msg: data?.error || `${key} failed (${res.status})` });
        appendLog(`${key} ❌ (${res.status})`, data);
        return { ok: false, status: res.status, data };
      }

      setAlert({ type: "success", msg: data?.message || `${key} completed successfully` });
      appendLog(`${key} ✅`, data);
      return { ok: true, status: res.status, data };

    } catch (e) {
      const err = { error: String(e) };
      setAlert({ type: "error", msg: "Network error: backend not reachable." });
      appendLog(`${key} ❌ (network)`, err);
      return { ok: false, status: 0, data: err };
    } finally {
      setBusyKey(null);
    }
  };

  const refreshHealth = async () => {
    const out = await call("Health Check", "/health", { method: "GET" });
    if (out.ok) setHealth(out.data);
  };

  useEffect(() => { refreshHealth(); }, []);

  useEffect(() => {
    if (!alert) return;
    const t = setTimeout(() => setAlert(null), 6000);
    return () => clearTimeout(t);
  }, [alert]);

  const isBusy = !!busyKey;

  return (
    <div style={{
      minHeight: "100vh",
      background: T.bg,
      color: T.text,
      fontFamily: "'IBM Plex Sans', sans-serif",
      padding: "28px 32px",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      <style>{`
        .admin-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .health-strip { display: flex; gap: 10px; flex-wrap: wrap; }
        @media (max-width: 860px) {
          .admin-grid { grid-template-columns: 1fr !important; }
        }
        @media (max-width: 600px) {
          .health-strip > * { min-width: 120px; }
        }
      `}</style>

      {/* ── Header ── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 20,
        paddingBottom: 18,
        borderBottom: `1px solid ${T.border}`,
        flexWrap: "wrap",
        gap: 12,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 36, height: 36,
            background: `linear-gradient(135deg, ${T.purple}, ${T.teal})`,
            borderRadius: 10,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 18,
          }}>⚙</div>
          <div>
            <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3, textTransform: "uppercase", fontWeight: 800, marginBottom: 2 }}>
              System Admin
            </div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 900, letterSpacing: -0.4 }}>
              Model Control Center
            </h1>
          </div>
        </div>

        <Button variant="ghost" onClick={refreshHealth} disabled={isBusy}>
          ↻ Refresh Health
        </Button>
      </div>

      {/* ── Health Status Strip ── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 700, marginBottom: 10 }}>
          System Status
        </div>
        <div className="health-strip">
          <HealthStat
            label="Status"
            value={health?.status ?? "—"}
            color={statusColor}
          />
          <HealthStat
            label="Model"
            value={health?.model_loaded ? "Loaded ✅" : "Not Loaded ❌"}
            color={health?.model_loaded ? T.green : T.red}
          />
          <HealthStat
            label="Prediction"
            value={health?.prediction_status ?? "—"}
            color={health?.prediction_status === "OK" ? T.green : T.amber}
          />
          <HealthStat
            label="Missing Features"
            value={(health?.missing_features_in_data?.length ?? 0) === 0 ? "None ✅" : `${health.missing_features_in_data.length} ❗`}
            color={(health?.missing_features_in_data?.length ?? 0) === 0 ? T.green : T.red}
          />
          <HealthStat
            label="Rows"
            value={health?.rows?.toLocaleString() ?? "—"}
            color={T.blue}
          />
          <HealthStat
            label="SKUs"
            value={health?.unique_skus?.toLocaleString() ?? "—"}
            color={T.teal}
          />
          <HealthStat
            label="Artifacts"
            value={health?.artifact_keys?.length ? `${health.artifact_keys.length} keys` : "—"}
            color={T.purple}
          />
        </div>

        {/* Missing features detail */}
        {health?.missing_features_in_data?.length > 0 && (
          <div style={{
            marginTop: 10,
            background: T.card,
            border: `1px solid ${T.red}44`,
            borderLeft: `3px solid ${T.red}`,
            borderRadius: 8,
            padding: "10px 14px",
            color: T.red,
            fontSize: 12,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            ❗ Missing: {health.missing_features_in_data.join(", ")}
          </div>
        )}
      </div>

      {/* ── Main Grid ── */}
      <div className="admin-grid">

        {/* Left: Operations */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Data Ops */}
          <Panel title="Data Operations" subtitle="Process raw sources → export forecast → reload in memory" accent={T.green}>
            <OpGroup label="Data Operations">
              <Button variant="green" disabled={isBusy} onClick={async () => {
                await call("Process Actual Raw", "/process_actual_raw", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ▶ Process Actual Raw
              </Button>
              <Button variant="green" disabled={isBusy} onClick={async () => {
                await call("Process Live Raw", "/process_live_raw", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ▶ Process Live Raw
              </Button>
            </OpGroup>
            <OpGroup label="Runtime / Export">
              <Button variant="ghost" disabled={isBusy} onClick={async () => {
                await call("Reload Data", "/reload_data", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ↺ Reload Data
              </Button>
              <Button variant="ghost" disabled={isBusy} onClick={async () => {
                await call("Generate Forecast File", "/export", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ⬇ Generate Forecast File
              </Button>
            </OpGroup>
            <div style={{
              fontSize: 11, color: T.muted, lineHeight: 1.6,
              background: T.surface, borderRadius: 8, padding: "8px 12px",
            }}>
              <div>Raw files are read from your project <span style={{ color: T.text, fontWeight: 700 }}>root</span>{" "}
              <code style={{ color: T.teal, fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>/data</code> folder.</div>
              <div>Process <span style={{ color: T.text, fontWeight: 700 }}>Live Raw</span> aplies live data immediately.
              Reload Data is mainly for <span style={{ color: T.text, fontWeight: 700 }}>manual processed</span> file updates.</div>
            </div>
          </Panel>

          {/* Model Ops */}
          <Panel
            title="Model Operations"
            subtitle="Cooldown-protected: Retrain (monthly), Retune (monthly)"
            accent={T.amber}
            right={<Badge label="Cooldown Protected" color={T.amber} />}
          >
            <OpGroup label="Scheduled">
              <Button variant="primary" disabled={isBusy} onClick={async () => {
                await call("Retrain (Monthly)", "/refresh_model", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ⟳ Retrain Monthly
              </Button>
              <Button variant="amber" disabled={isBusy} onClick={async () => {
                await call("Retune (Monthly)", "/retune_model", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ⚡ Retune Monthly
              </Button>
            </OpGroup>
            <OpGroup label="Utility">
              <Button variant="ghost" disabled={isBusy} onClick={async () => {
                await call("Reload Model", "/reload_model", { method: "POST", body: JSON.stringify({}) });
                await refreshHealth();
              }}>
                ↺ Reload Model
              </Button>
            </OpGroup>
            <div style={{
              fontSize: 11, color: T.muted, lineHeight: 1.6,
              background: T.surface, borderRadius: 8, padding: "8px 12px",
            }}>
              💡 For live demos: <span style={{ color: T.text, fontWeight: 700 }}>Process Actual/Live Raw → Reload Data</span> only.
              Retrain / Retune are optional and cooldown-protected.
            </div>
          </Panel>
        </div>

        {/* Right: Log */}
        <Panel
          title="Admin Log"
          subtitle="Latest actions — newest on top"
          accent={T.blue}
          right={
            <Button variant="ghost" disabled={!log || isBusy} onClick={() => setLog("")}>
              Clear
            </Button>
          }
        >
          <pre style={{
            background: "#060a10",
            border: `1px solid ${T.border}`,
            borderRadius: 10,
            padding: "14px 16px",
            color: T.text,
            fontSize: 11,
            overflow: "auto",
            height: "calc(100% - 52px)",
            minHeight: 340,
            maxHeight: 520,
            lineHeight: 1.6,
            fontFamily: "'JetBrains Mono', monospace",
            whiteSpace: "pre-wrap",
            boxSizing: "border-box",
          }}>
            {log || <span style={{ color: T.muted }}>No actions yet. Click a button to test endpoints.</span>}
          </pre>
        </Panel>
      </div>

      {/* ── Toast ── */}
      <Toast alert={alert} onClose={() => setAlert(null)} />
    </div>
  );
}