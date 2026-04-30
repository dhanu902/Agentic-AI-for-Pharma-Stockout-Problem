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

/* ─── Spinner ────────────────────────────────────────────────── */
const Spinner = ({ size = 12, color = "#fff" }) => (
  <>
    <style>{`@keyframes admin-spin { to { transform: rotate(360deg); } }`}</style>
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      style={{ flexShrink: 0, animation: "admin-spin 0.7s linear infinite" }}
    >
      <circle cx="12" cy="12" r="9" stroke={color} strokeOpacity="0.25" strokeWidth="3" />
      <path d="M12 3a9 9 0 0 1 9 9" stroke={color} strokeWidth="3" strokeLinecap="round" />
    </svg>
  </>
);

/* ─── Badge ──────────────────────────────────────────────────── */
const Badge = ({ label, color }) => (
  <span style={{
    background: color + "22", border: `1px solid ${color}44`, color,
    borderRadius: 6, padding: "3px 8px", fontSize: 10,
    fontWeight: 800, textTransform: "uppercase", letterSpacing: 1,
  }}>{label}</span>
);

/* ─── Button ─────────────────────────────────────────────────── */
const Button = ({ children, onClick, disabled, variant = "primary", fullWidth, loading }) => {
  const styles = {
    primary: { background: T.blue,    color: "#fff" },
    ghost:   { background: T.surface, color: T.text, border: `1px solid ${T.borderHi}` },
    danger:  { background: T.red,     color: "#fff" },
    amber:   { background: T.amber,   color: "#111" },
    green:   { background: T.green,   color: "#111" },
    teal:    { background: T.teal,    color: "#111" },
  }[variant];

  const isDisabled = disabled || loading;
  const spinColor  = (variant === "ghost")
    ? T.text
    : (variant === "amber" || variant === "green" || variant === "teal")
    ? "#111"
    : "#fff";

  return (
    <button
      onClick={onClick}
      disabled={isDisabled}
      style={{
        ...styles,
        border: styles.border || "none",
        borderRadius: 8, padding: "9px 16px",
        fontWeight: 800, fontSize: 12,
        cursor: isDisabled ? "not-allowed" : "pointer",
        opacity: loading ? 0.8 : isDisabled ? 0.5 : 1,
        transition: "opacity 0.15s, transform 0.05s",
        fontFamily: "'IBM Plex Sans', sans-serif", letterSpacing: 0.3,
        width: fullWidth ? "100%" : undefined,
        display: "flex", alignItems: "center", gap: 6,
      }}
      onMouseDown={e => !isDisabled && (e.currentTarget.style.transform = "scale(0.98)")}
      onMouseUp={e => (e.currentTarget.style.transform = "scale(1)")}
    >
      {loading && <Spinner size={12} color={spinColor} />}
      {children}
    </button>
  );
};

/* ─── Panel ──────────────────────────────────────────────────── */
const Panel = ({ title, subtitle, right, children, accent }) => (
  <div style={{
    background: T.card, border: `1px solid ${T.border}`,
    borderTop: accent ? `2px solid ${accent}` : `1px solid ${T.border}`,
    borderRadius: 12, padding: "18px 20px", boxSizing: "border-box",
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
    padding: "10px 16px", background: T.surface,
    border: `1px solid ${T.border}`, borderLeft: `3px solid ${color || T.border}`,
    borderRadius: 8, flex: 1, minWidth: 110,
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
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{children}</div>
  </div>
);

/* ─── Progress Banner ────────────────────────────────────────── */
const ProgressBanner = ({ busyKey }) => {
  const [dots, setDots]       = useState(".");
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!busyKey) return;
    setElapsed(0);
    setDots(".");
    const dotsT    = setInterval(() => setDots(d => d.length >= 3 ? "." : d + "."), 500);
    const elapsedT = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => { clearInterval(dotsT); clearInterval(elapsedT); };
  }, [busyKey]);

  if (!busyKey) return null;

  return (
    <div style={{
      position: "relative", overflow: "hidden",
      display: "flex", alignItems: "center", gap: 12,
      background: T.card, border: `1px solid ${T.amber}44`,
      borderLeft: `3px solid ${T.amber}`, borderRadius: 10,
      padding: "12px 16px", marginBottom: 18,
    }}>
      <style>{`
        @keyframes pulseRing  { 0% { transform:scale(0.8); opacity:0.8; } 100% { transform:scale(1.8); opacity:0; } }
        @keyframes indeterminate { 0% { left:-40%; width:40%; } 60% { left:100%; width:40%; } 100% { left:100%; width:40%; } }
      `}</style>
      <div style={{ position: "relative", width: 22, height: 22, flexShrink: 0 }}>
        <div style={{ position: "absolute", inset: 0, borderRadius: "50%", background: T.amber, animation: "pulseRing 1.3s ease-out infinite" }} />
        <div style={{ position: "absolute", inset: "5px", borderRadius: "50%", background: T.amber }} />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 10, fontWeight: 800, color: T.amber, textTransform: "uppercase", letterSpacing: 1.2, marginBottom: 2 }}>
          Processing{dots}
        </div>
        <div style={{ fontSize: 12, color: T.text }}>
          <span style={{ fontWeight: 700 }}>{busyKey}</span> is running — please wait
        </div>
      </div>
      <div style={{
        fontSize: 12, color: T.muted, fontFamily: "'JetBrains Mono', monospace",
        background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 6, padding: "3px 8px", flexShrink: 0,
      }}>{elapsed}s</div>
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, overflow: "hidden" }}>
        <div style={{
          position: "absolute", height: "100%",
          background: `linear-gradient(90deg, transparent, ${T.amber}, transparent)`,
          animation: "indeterminate 1.5s ease-in-out infinite",
        }} />
      </div>
    </div>
  );
};

/* ─── Alert Item ─────────────────────────────────────────────── */
const AlertItem = ({ msg, type = "warn" }) => {
  const color = type === "error" ? T.red : type === "ok" ? T.green : T.amber;
  const icon  = type === "error" ? "✕" : type === "ok" ? "✓" : "⚠";
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 8,
      padding: "8px 10px", borderRadius: 7, marginBottom: 6,
      background: color + "10", border: `1px solid ${color}30`,
    }}>
      <span style={{ fontSize: 10, color, fontWeight: 900, marginTop: 1, flexShrink: 0 }}>{icon}</span>
      <span style={{ fontSize: 11, color: T.text, lineHeight: 1.5 }}>{msg}</span>
    </div>
  );
};

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
      position: "fixed", bottom: 28, right: 28, zIndex: 9999,
      maxWidth: 380, minWidth: 280, background: T.card,
      border: `1px solid ${color}55`, borderLeft: `4px solid ${color}`,
      borderRadius: 12, padding: "14px 16px 10px",
      boxShadow: "0 16px 48px rgba(0,0,0,0.6)",
      transform: visible ? "translateY(0)" : "translateY(20px)",
      opacity: visible ? 1 : 0,
      transition: "transform 0.25s ease, opacity 0.25s ease",
    }}>
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0,
        height: 2, background: color + "33", borderRadius: "12px 12px 0 0", overflow: "hidden",
      }}>
        <div style={{ height: "100%", background: color, animation: "shrink 6s linear forwards" }} />
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
        <button onClick={onClose} style={{ background: "none", border: "none", color: T.muted, cursor: "pointer", fontSize: 14, padding: 0, lineHeight: 1 }}>✕</button>
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

  const artifactSummary = health?.artifact_summary || {};

  const modelsLoaded = useMemo(() => {
    return (
      (artifactSummary.long_models_loaded?.length ?? 0) > 0 ||
      (artifactSummary.medium_models_loaded?.length ?? 0) > 0 ||
      (artifactSummary.short_rules_loaded?.length ?? 0) > 0
    );
  }, [artifactSummary]);

  const gruLoaded = useMemo(() => {
    return artifactSummary.gru_long_loaded === true;
  }, [artifactSummary]);

  const championLoaded = useMemo(() => {
    return artifactSummary.champion_long_loaded === true &&
           artifactSummary.champion_medium_loaded === true;
  }, [artifactSummary]);

  const statusColor = useMemo(() => {
    if (!health) return T.muted;
    return health.status === "HEALTHY" ? T.green : T.red;
  }, [health]);

  const systemAlerts = useMemo(() => {
    if (!health) return [];

    const alerts = [];

    if (!modelsLoaded) {
      alerts.push({ msg: "Model artifacts not loaded — predictions may be unavailable", type: "error" });
    }

    if (health.prediction_status && health.prediction_status !== "OK") {
      alerts.push({ msg: `Prediction status: ${health.prediction_status}`, type: "warn" });
    }

    if (!gruLoaded) {
      alerts.push({ msg: "GRU model unavailable — fallback routing may be active", type: "warn" });
    }

    if (!championLoaded) {
      alerts.push({ msg: "Champion maps not fully loaded — routing may default", type: "warn" });
    }

    if ((health.rows ?? 0) === 0) {
      alerts.push({ msg: "No processed data rows found — run data processing first", type: "error" });
    }

    if (alerts.length === 0) {
      alerts.push({ msg: "All systems operational", type: "ok" });
    }

    return alerts;
  }, [health, modelsLoaded, gruLoaded, championLoaded]);

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

  const loadHealthSilently = async () => {
    try {
      const res = await fetch(`${API_BASE}/health`, { method: "GET" });
      const data = await res.json().catch(() => ({}));
      if (res.ok) setHealth(data);
    } catch {
      // keep silent
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

  const handleFutureAction = (label) => {
    setAlert({ type: "info", msg: `${label} is planned for the next backend phase and is not available yet.` });
    appendLog(`${label} ⏳ (planned)`, { message: "Not implemented in backend yet." });
  };

  return (
    <div style={{
      minHeight: "100vh", background: T.bg, color: T.text,
      fontFamily: "'IBM Plex Sans', sans-serif", padding: "28px 32px",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      <style>{`
        .admin-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .health-strip { display: flex; gap: 10px; flex-wrap: wrap; }
        @media (max-width: 1000px) { .admin-grid { grid-template-columns: 1fr !important; } }
        @media (max-width: 600px)  { .health-strip > * { min-width: 120px; } }
      `}</style>

      {/* ── Header ── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 20, paddingBottom: 18, borderBottom: `1px solid ${T.border}`,
        flexWrap: "wrap", gap: 12,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 36, height: 36, background: `linear-gradient(135deg, ${T.purple}, ${T.teal})`,
            borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18,
          }}>⚙</div>
          <div>
            <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3, textTransform: "uppercase", fontWeight: 800, marginBottom: 2 }}>
              System Admin
            </div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 900, letterSpacing: -0.4 }}>Forecast Control Center</h1>
            <div style={{ fontSize: 10, color: T.muted, marginTop: 2 }}>Forecast Operations & Model Governance</div>
          </div>
        </div>
        <Button variant="ghost" onClick={refreshHealth} disabled={isBusy} loading={busyKey === "Health Check"}>
          ↻ Refresh Health
        </Button>
      </div>

      {/* ── Progress Banner ── */}
      <ProgressBanner busyKey={busyKey} />

      {/* ── Health Status Strip ── */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 700, marginBottom: 10 }}>
          System Status
        </div>
        <div className="health-strip">
          <HealthStat label="Status" value={health?.status ?? "—"} color={statusColor} />
          <HealthStat
            label="Prediction"
            value={health?.prediction_status ?? "—"}
            color={health?.prediction_status === "OK" ? T.green : T.amber}
          />
          <HealthStat label="Data Rows" value={health?.rows?.toLocaleString() ?? "—"} color={T.blue} />
          <HealthStat label="SKU Count" value={health?.unique_skus?.toLocaleString() ?? "—"} color={T.teal} />
          <HealthStat
            label="Models Loaded"
            value={modelsLoaded ? "Yes ✅" : "No ❌"}
            color={modelsLoaded ? T.green : T.red}
          />
          <HealthStat
            label="GRU Loaded"
            value={gruLoaded ? "Yes ✅" : "No ❌"}
            color={gruLoaded ? T.green : T.amber}
          />
          <HealthStat
            label="Champion Map"
            value={championLoaded ? "Yes ✅" : "No ❌"}
            color={championLoaded ? T.green : T.amber}
          />
          <HealthStat
            label="Last Export"
            value={health?.file_status?.forecast_latest?.modified_at ?? "—"}
            color={health?.file_status?.forecast_latest?.exists ? T.purple : T.muted}
          />
        </div>
      </div>

      {/* ── Main Grid ── */}
      <div className="admin-grid">

        {/* ── LEFT COLUMN ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Data Ops */}
          <Panel title="Data Operations" subtitle="Load raw sources → process → reload into memory" accent={T.green}>
            <OpGroup label="Input Data">
              <Button
                variant="green"
                disabled={isBusy}
                loading={busyKey === "Process Actual Raw"}
                onClick={async () => {
                  await call("Process Actual Raw", "/process_actual_raw", { method: "POST", body: JSON.stringify({}) });
                  await loadHealthSilently();
                }}
              >
                ▶ Load Month-End Data
              </Button>
              <Button
                variant="green"
                disabled={isBusy}
                loading={busyKey === "Process Live Raw"}
                onClick={async () => {
                  await call("Process Live Raw", "/process_live_raw", { method: "POST", body: JSON.stringify({}) });
                  await loadHealthSilently();
                }}
              >
                ▶ Load Snapshot Data
              </Button>
            </OpGroup>

            <OpGroup label="Runtime">
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Process Updated Data")}
              >
                ⚙ Process Updated Data
              </Button>
              <Button
                variant="ghost"
                disabled={isBusy}
                loading={busyKey === "Reload Data"}
                onClick={async () => {
                  await call("Reload Data", "/reload_data", { method: "POST", body: JSON.stringify({}) });
                  await loadHealthSilently();
                }}
              >
                ↺ Reload Runtime Data
              </Button>
            </OpGroup>

            <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.6, background: T.surface, borderRadius: 8, padding: "8px 12px" }}>
              <div><span style={{ color: T.text, fontWeight: 700 }}>Month-End</span> → actual historical data for retraining.</div>
              <div><span style={{ color: T.text, fontWeight: 700 }}>Snapshot</span> → latest operational update for daily use.</div>
              <div><span style={{ color: T.text, fontWeight: 700 }}>Reload Runtime</span> → makes processed file active in memory.</div>
            </div>
          </Panel>

          {/* Model Ops */}
          <Panel
            title="Model Operations"
            subtitle="Current runtime loading active — training lifecycle kept visible for next phase"
            accent={T.amber}
            right={<Badge label="Cooldown Protected" color={T.amber} />}
          >
            <OpGroup label="Current Package">
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Load Models")}
              >
                ⬇ Load Models
              </Button>
              <Button
                variant="ghost"
                disabled={isBusy}
                loading={busyKey === "Reload Model"}
                onClick={async () => {
                  await call("Reload Model", "/reload_model", { method: "POST", body: JSON.stringify({}) });
                  await loadHealthSilently();
                }}
              >
                ↺ Reload Models
              </Button>
            </OpGroup>

            <OpGroup label="Monthly Lifecycle">
              <Button
                variant="primary"
                disabled={true}  
                onClick={() => handleFutureAction("Retrain Models")}
              >
                ⟳ Retrain
              </Button>
              <Button
                variant="amber"
                disabled={isBusy}
                onClick={() => handleFutureAction("Retune Models")}
              >
                ⚡ Retune
              </Button>
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Prune Models")}
              >
                ✂ Prune
              </Button>
            </OpGroup>

            <OpGroup label="Deployment Preparation">
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Generate Champion Map")}
              >
                🏆 Generate Champion Map
              </Button>
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Prepare Deploy Artifacts")}
              >
                📦 Prepare Deploy Artifacts
              </Button>
            </OpGroup>

            <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.6, background: T.surface, borderRadius: 8, padding: "8px 12px" }}>
              💡 Monthly flow: <span style={{ color: T.text, fontWeight: 700 }}>Load Month-End → Retrain → Retune → Prune → Champion Map → Deploy → Reload</span>
            </div>
          </Panel>

          {/* Forecast Ops */}
          <Panel title="Forecast Operations" subtitle="Generate latest forecasts for all active SKUs" accent={T.teal}>
          <OpGroup label="Generate / Export">
            <Button
              variant="teal"
              disabled={isBusy}
              loading={busyKey === "Generate Forecast File"}
              onClick={async () => {
                await call("Generate Forecast File", "/export", { method: "POST", body: JSON.stringify({}) });
                await loadHealthSilently();
              }}
            >
              📊 Generate Forecast File
            </Button>

            <Button
              variant="ghost"
              disabled={true}
              onClick={() => handleFutureAction("Download Forecast File")}
            >
              ⬇ Export Forecast File
            </Button>
          </OpGroup>

            <OpGroup label="Refresh">
              <Button
                variant="ghost"
                disabled={true}
                onClick={() => handleFutureAction("Refresh Forecasted SKU List")}
              >
                ↺ Refresh Forecasted SKU List
              </Button>
            </OpGroup>

            <div style={{ fontSize: 11, color: T.muted, lineHeight: 1.6, background: T.surface, borderRadius: 8, padding: "8px 12px" }}>
              {health?.file_status?.forecast_latest?.modified_at
                ? <><span style={{ color: T.text, fontWeight: 700 }}>Last export:</span> {health.file_status.forecast_latest.modified_at}</>
                : "No forecast file generated yet. Run Generate Forecast File to create the latest forecast output."}
            </div>
          </Panel>
        </div>

        {/* ── RIGHT COLUMN ── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Alerts / Exceptions */}
          <Panel title="Alerts & Exceptions" subtitle="Live system status warnings" accent={T.red}>
            <div>
              {systemAlerts.map((a, i) => (
                <AlertItem key={i} msg={a.msg} type={a.type} />
              ))}
              {!health && (
                <div style={{ fontSize: 11, color: T.muted }}>Loading system status…</div>
              )}
            </div>
          </Panel>

          {/* Log */}
          <Panel
            title="Operation Log"
            subtitle="Latest actions — newest on top"
            accent={T.blue}
            right={<Button variant="ghost" disabled={!log || isBusy} onClick={() => setLog("")}>Clear</Button>}
          >
            <pre style={{
              background: "#060a10", border: `1px solid ${T.border}`,
              borderRadius: 10, padding: "14px 16px", color: T.text, fontSize: 11,
              overflow: "auto", minHeight: 340, maxHeight: 560,
              lineHeight: 1.6, fontFamily: "'JetBrains Mono', monospace",
              whiteSpace: "pre-wrap", boxSizing: "border-box",
            }}>
              {log || <span style={{ color: T.muted }}>No actions yet. Click a button to test endpoints.</span>}
            </pre>
          </Panel>
        </div>
      </div>

      {/* ── Toast ── */}
      <Toast alert={alert} onClose={() => setAlert(null)} />
    </div>
  );
}