import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ReferenceLine
} from "recharts";

const API_BASE = "http://127.0.0.1:5000/api/forecast";
const XAI_API_BASE = "http://127.0.0.1:5000/api/xai";

let forecastMemory = {
  itemCode: "",
  result: null,
  xai: null,
};

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

/* ─── Helpers ────────────────────────────────────────────────── */
function fmt(n) {
  if (n === null || n === undefined) return "—";
  if (typeof n !== "number") return String(n);
  return n.toLocaleString();
}
function fmtK(n) {
  if (typeof n !== "number") return n;
  if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return n.toString();
}
function abcColor(cat) {
  return { A: T.green, B: T.amber, C: T.muted }[cat] ?? T.muted;
}
function demandStatusColor(status) {
  if (!status) return T.muted;
  const s = status.toLowerCase();
  if (s.includes("inactive") || s.includes("near-zero")) return T.red;
  if (s.includes("low")) return T.amber;
  return T.green;
}

/* ─── Tooltip ────────────────────────────────────────────────── */
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "#0e1420", border: `1px solid ${T.borderHi}`,
      borderRadius: 8, padding: "10px 14px", fontSize: 12,
      boxShadow: "0 12px 40px rgba(0,0,0,0.7)",
    }}>
      <p style={{ color: T.muted, marginBottom: 6, fontWeight: 700, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>{label}</p>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, marginBottom: 3, display: "flex", justifyContent: "space-between", gap: 20 }}>
          <span style={{ opacity: 0.7, fontSize: 11 }}>{p.name}</span>
          <strong style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            {typeof p.value === "number" ? p.value.toLocaleString() : "—"}
          </strong>
        </div>
      ))}
    </div>
  );
};

/* ─── KPI Card ───────────────────────────────────────────────── */
const KPICard = ({ label, value, sub, accent, icon }) => (
  <div style={{
    background: T.card, border: `1px solid ${T.border}`, borderRadius: 10,
    padding: "16px 18px", flex: 1, minWidth: 140,
    position: "relative", overflow: "hidden", transition: "border-color 0.2s",
  }}
    onMouseEnter={e => e.currentTarget.style.borderColor = accent + "66"}
    onMouseLeave={e => e.currentTarget.style.borderColor = T.border}
  >
    <div style={{ position: "absolute", top: -20, right: -20, width: 80, height: 80, background: accent, borderRadius: "50%", opacity: 0.05, filter: "blur(20px)", pointerEvents: "none" }} />
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
      {icon && <span style={{ fontSize: 13 }}>{icon}</span>}
      <div style={{ fontSize: 10, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700 }}>{label}</div>
    </div>
    <div style={{ fontSize: 24, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1, marginBottom: 6 }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: accent, fontWeight: 600 }}>{sub}</div>}
    <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, ${accent}, transparent)` }} />
  </div>
);

/* ─── Diagnostic Card ────────────────────────────────────────── */
const DiagCard = ({ label, value, accent }) => {
  const col = accent || T.muted;
  const isEmpty = value === null || value === undefined || value === "" || value === "—";
  return (
    <div style={{
      background: T.surface, border: `1px solid ${T.border}`,
      borderLeft: `2px solid ${isEmpty ? T.border : col}`,
      borderRadius: 8, padding: "10px 14px",
    }}>
      <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: isEmpty ? T.muted : T.text, fontFamily: "'JetBrains Mono', monospace" }}>
        {isEmpty ? "—" : String(value)}
      </div>
    </div>
  );
};

/* ─── Signal Card ────────────────────────────────────────────── */
const SignalCard = ({ label, value, sub, accent }) => (
  <div style={{ background: T.card, border: `1px solid ${T.border}`, borderLeft: `3px solid ${accent}`, borderRadius: 8, padding: "12px 16px" }}>
    <div style={{ fontSize: 10, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    {sub && <div style={{ fontSize: 10, color: accent, marginTop: 3, fontWeight: 500 }}>{sub}</div>}
  </div>
);

/* ─── Panel ──────────────────────────────────────────────────── */
const Panel = ({ children, style = {} }) => (
  <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: "20px 22px 12px", ...style }}>
    {children}
  </div>
);

/* ─── Section Header ─────────────────────────────────────────── */
const SectionHeader = ({ title, subtitle }) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{ fontSize: 12, fontWeight: 700, color: T.text, letterSpacing: 0.3 }}>{title}</div>
    {subtitle && <div style={{ fontSize: 10, color: T.muted, marginTop: 3 }}>{subtitle}</div>}
  </div>
);

/* ─── Divider ────────────────────────────────────────────────── */
const Divider = ({ label }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "20px 0 14px" }}>
    <div style={{ flex: 1, height: 1, background: T.border }} />
    {label && <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 700 }}>{label}</span>}
    <div style={{ flex: 1, height: 1, background: T.border }} />
  </div>
);

/* ─── Badge ──────────────────────────────────────────────────── */
const Badge = ({ label, color }) => (
  <span style={{
    background: color + "22", border: `1px solid ${color}44`, color,
    borderRadius: 4, padding: "2px 7px", fontSize: 9,
    fontWeight: 700, textTransform: "uppercase", letterSpacing: 1,
  }}>{label}</span>
);

/* ─── SKU Info Strip ─────────────────────────────────────────── */
const SkuInfoStrip = ({ result }) => {
  if (!result || result.error) return null;

  const abcCat    = result.abc_category;
  const abcCol    = abcColor(abcCat);
  const demStatus = result.demand_status;
  const demCol    = demandStatusColor(demStatus);
  const segment   = result.segment;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
      padding: "7px 14px", background: T.surface,
      border: `1px solid ${T.borderHi}`, borderRadius: 8, marginBottom: 18,
    }}>
      <span style={{ fontSize: 12, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace", letterSpacing: 0.5 }}>
        {result.item_code || result.ItemCode || "—"}
      </span>
      <span style={{ color: T.borderHi, fontSize: 14 }}>·</span>

      {abcCat && (
        <span style={{
          display: "inline-flex", alignItems: "center",
          background: abcCol + "18", border: `1px solid ${abcCol}44`,
          borderRadius: 5, padding: "2px 8px",
          fontSize: 10, fontWeight: 800, color: abcCol,
          textTransform: "uppercase", letterSpacing: 1,
        }}>ABC · {abcCat}</span>
      )}

      {demStatus && (
        <span style={{
          display: "inline-flex", alignItems: "center",
          background: demCol + "18", border: `1px solid ${demCol}44`,
          borderRadius: 5, padding: "2px 8px",
          fontSize: 10, fontWeight: 700, color: demCol,
        }}>{demStatus}</span>
      )}

      {segment && (
        <span style={{
          display: "inline-flex", alignItems: "center",
          background: T.blue + "18", border: `1px solid ${T.blue}44`,
          borderRadius: 5, padding: "2px 8px",
          fontSize: 10, fontWeight: 700, color: T.blue,
          textTransform: "uppercase", letterSpacing: 0.8,
        }}>Seg: {segment}</span>
      )}

      {result.as_of && (
        <span style={{ marginLeft: "auto", fontSize: 10, color: T.muted }}>
          As of <span style={{ color: T.text, fontWeight: 600 }}>{result.as_of}</span>
        </span>
      )}
    </div>
  );
};


const XAIExplanationCard = ({ xai, loading, error }) => {
  if (loading) {
    return (
      <Panel style={{ borderTop: `2px solid ${T.blue}`, marginBottom: 16 }}>
        <SectionHeader title="Explainable AI" subtitle="Generating forecast explanation..." />
        <div style={{ color: T.muted, fontSize: 13 }}>Loading explanation...</div>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel style={{ borderTop: `2px solid ${T.red}`, marginBottom: 16 }}>
        <SectionHeader title="Explainable AI" subtitle="Explanation unavailable" />
        <div style={{ color: T.red, fontSize: 13 }}>⚠ {error}</div>
      </Panel>
    );
  }

  if (!xai) return null;

  return (
    <Panel style={{ borderTop: `2px solid ${T.teal}`, marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <SectionHeader
          title="Why this forecast?"
          subtitle="Explainable AI summary for the selected SKU"
        />

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <Badge label={xai.used_model || "Model"} color={T.blue} />
          <Badge label={xai.xai_method || "XAI"} color={T.teal} />
        </div>
      </div>

      <div style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: 10,
        padding: "12px 14px",
        fontSize: 13,
        color: T.text,
        lineHeight: 1.6,
        marginBottom: 14,
      }}>
        {xai.explanation_text}
      </div>

      {xai.top_drivers?.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: 12,
          }}>
            <thead>
              <tr style={{ color: T.muted, textAlign: "left", borderBottom: `1px solid ${T.border}` }}>
                <th style={{ padding: "8px" }}>Feature</th>
                <th style={{ padding: "8px" }}>Value</th>
                <th style={{ padding: "8px" }}>Impact</th>
              </tr>
            </thead>
            <tbody>
              {xai.top_drivers.map((d, i) => (
                <tr key={i} style={{ borderBottom: `1px solid ${T.border}` }}>
                  <td style={{ padding: "8px", color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>
                    {d.feature}
                  </td>
                  <td style={{ padding: "8px", color: T.muted }}>
                    {typeof d.value === "number" ? d.value.toFixed(2) : String(d.value ?? "—")}
                  </td>
                  <td style={{
                    padding: "8px",
                    color: d.impact === "increase" ? T.green : d.impact === "decrease" ? T.red : T.amber,
                    fontWeight: 700,
                  }}>
                    {d.shap_value !== undefined
                      ? `${d.impact} (${Number(d.shap_value).toFixed(2)})`
                      : d.impact}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
};


/* ─── Main ───────────────────────────────────────────────────── */
export default function Forecast() {
  const [, setSearchParams] = useSearchParams();
  const [itemCode, setItemCode] = useState(forecastMemory.itemCode || "");
  const [result, setResult]     = useState(forecastMemory.result || null);
  const [loading, setLoading]   = useState(false);
  const [skuOptions, setSkuOptions] = useState([]);
  const [skuLoading, setSkuLoading] = useState(false);
  const [xai, setXai] = useState(forecastMemory.xai || null);
  const [xaiLoading, setXaiLoading] = useState(false);
  const [xaiError, setXaiError] = useState("");

  const fetchXAIExplanation = async (code) => {
    const response = await fetch(`${XAI_API_BASE}/forecast-explanation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_code: code }),
    });
  
    const data = await response.json();
  
    if (!response.ok || !data.success) {
      throw new Error(data?.error || "Failed to fetch XAI explanation");
    }
  
    return data.data;
  };

  const handleForecast = async (codeArg) => {
    const raw = typeof codeArg === "string" ? codeArg : itemCode;
    const code = String(raw || "").trim();
    if (!code) return;
  
    setLoading(true);
    setXai(null);
    setXaiError("");
    setXaiLoading(true);
  
    try {
      const response = await fetch(`${API_BASE}/dashboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_code: code }),
      });
  
      const data = await response.json();
  
      if (!response.ok) {
        const errObj = { error: data?.error || "Failed to fetch dashboard" };
  
        setResult(errObj);
        setXai(null);
        forecastMemory = { itemCode: code, result: errObj, xai: null };
  
        return;
      }
  
      setResult(data);
      setItemCode(code);
      setSearchParams({ sku: code });
  
      try {
        const xaiData = await fetchXAIExplanation(code);
  
        setXai(xaiData);
        forecastMemory = { itemCode: code, result: data, xai: xaiData };
      } catch (xaiErr) {
        console.error("XAI Error:", xaiErr);
  
        setXai(null);
        setXaiError(xaiErr.message || "Failed to load explanation");
        forecastMemory = { itemCode: code, result: data, xai: null };
      }
  
    } catch (error) {
      console.error("Error:", error);
  
      const errObj = { error: "Failed to fetch dashboard" };
  
      setResult(errObj);
      setXai(null);
      forecastMemory = { itemCode: code, result: errObj, xai: null };
  
    } finally {
      setLoading(false);
      setXaiLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sku = params.get("sku");
    if (sku && !forecastMemory.result) {
      setItemCode(sku);
      handleForecast(sku);
    }
  }, []);

  useEffect(() => {
    const loadSkus = async () => {
      setSkuLoading(true);
      try {
        const res = await fetch(`${API_BASE}/skus`);
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          setSkuOptions(data?.skus || []);
        }
      } catch {
        // silent fail
      } finally {
        setSkuLoading(false);
      }
    };
  
    loadSkus();
  }, []);

  
  const salesTrend     = useMemo(() => result?.sales_trend || [], [result]);
  const inventoryTrend = useMemo(() => result?.inventory_trend || [], [result]);
  const shockTrend     = useMemo(() => result?.shock_trend || [], [result]);

  const mom         = result?.mom_change;
  const momPositive = typeof mom === "number" ? mom > 0 : false;

  const forecastSplitLabel = useMemo(() => {
    if (!salesTrend?.length) return null;
    const firstForecast = salesTrend.find(d => d?.isForecast);
    return firstForecast?.label || null;
  }, [salesTrend]);

  const latestShock = useMemo(() => {
    if (!shockTrend.length) return null;
    return shockTrend[shockTrend.length - 1];
  }, [shockTrend]);

  const previousShock = useMemo(() => {
    if (shockTrend.length < 2) return null;
    return shockTrend[shockTrend.length - 2];
  }, [shockTrend]);

  const recentActuals = useMemo(() => {
    return salesTrend
      .filter(d => typeof d.actual === "number")
      .slice(-3);
  }, [salesTrend]);
  
  const latest3MAvg = useMemo(() => {
    if (!recentActuals.length) return null;
    const total = recentActuals.reduce((sum, d) => sum + d.actual, 0);
    return total / recentActuals.length;
  }, [recentActuals]);
  
  const calcSHP = (invRow) => {
    if (!invRow || !latest3MAvg || latest3MAvg <= 0) return null;
  
    const primary = Number(invRow.primaryInventory || 0);
    const dist = Number(invRow.distInventory || 0);
    const totalQty = primary + dist;
  
    return totalQty / latest3MAvg;
  };
  
  const currentInv = inventoryTrend?.[inventoryTrend.length - 1] || null;
  const lastInv = inventoryTrend?.[inventoryTrend.length - 2] || null;
  
  const currentSHP = calcSHP(currentInv);
  const lastSHP = calcSHP(lastInv);

  return (
    <div style={{ minHeight: "100vh", background: T.bg, fontFamily: "'IBM Plex Sans', sans-serif", color: T.text, padding: "28px 32px" }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      {/* ── Top Bar ───────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 20, paddingBottom: 20, borderBottom: `1px solid ${T.border}`,
        flexWrap: "wrap", gap: 16,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{
            width: 36, height: 36,
            background: `linear-gradient(135deg, ${T.blue}, ${T.teal})`,
            borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18,
          }}>◈</div>
          <div>
            <div style={{ fontSize: 9, color: T.blue, letterSpacing: 3, textTransform: "uppercase", fontWeight: 700, marginBottom: 2 }}>
              Sales Intelligence Platform
            </div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: T.text, letterSpacing: -0.5 }}>
              SKU Forecast Dashboard
            </h1>
          </div>
          <div style={{ marginLeft: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Badge label="Multi-Model" color={T.blue} />
            <Badge label="Champion Map"    color={T.teal} />
            <Badge label="Forecast File"    color={T.purple} />
            <Badge label="v1.0"        color={T.muted} />
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{
            display: "flex", alignItems: "center",
            background: T.surface, border: `1px solid ${T.borderHi}`,
            borderRadius: 10, padding: "9px 14px", gap: 8, width: 260,
          }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
            </svg>
            <input
              list="sku-options"
              value={itemCode}
              onChange={e => setItemCode(e.target.value)}
              placeholder={skuLoading ? "Loading SKUs..." : "SKU / Item Code…"}
              onKeyDown={e => e.key === "Enter" && handleForecast()}
              style={{
                background: "transparent", border: "none", outline: "none",
                color: T.text, fontSize: 13, width: "100%",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            />
            <datalist id="sku-options">
              {skuOptions.map((sku) => (
                <option key={sku} value={sku} />
              ))}
            </datalist>
          </div>
          <button
            onClick={() => handleForecast()}
            disabled={loading}
            style={{
              background: loading ? T.subtle : T.blue,
              border: "none", color: loading ? T.muted : "#fff",
              fontWeight: 700, fontSize: 13, borderRadius: 10,
              padding: "10px 20px", cursor: loading ? "not-allowed" : "pointer",
              transition: "background 0.2s", fontFamily: "'IBM Plex Sans', sans-serif", letterSpacing: 0.3,
            }}
          >
            {loading ? "Loading…" : "Load SKU Dashboard"}
          </button>
        </div>
      </div>

      {/* ── SKU Info Strip ────────────────────────────────────── */}
      <SkuInfoStrip result={result} />

      {/* ── Error ─────────────────────────────────────────────── */}
      {result?.error && (
        <div style={{
          background: T.card, border: `1px solid ${T.red}44`,
          borderLeft: `3px solid ${T.red}`, borderRadius: 10,
          padding: "12px 16px", color: T.red, marginBottom: 20, fontSize: 13,
        }}>
          ⚠ {result.error}
        </div>
      )}

      {result && !result.error && (<>
        <style>{`
          .hero-grid    { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: 16px; margin-bottom: 16px; }
          .kpi-stack    { display: flex; flex-direction: column; gap: 10px; }
          .signal-row   { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
          .signal-row > * { flex: 1; min-width: 180px; }
          .bottom-grid  { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
          .diag-row     { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
          .diag-row > * { flex: 1; min-width: 150px; }
          .diag-grid    { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
          @media (max-width: 900px) {
            .hero-grid  { grid-template-columns: 1fr !important; }
            .kpi-stack  { flex-direction: row !important; flex-wrap: wrap; }
            .kpi-stack > * { min-width: 160px; flex: 1; }
            .diag-grid  { grid-template-columns: repeat(2, 1fr) !important; }
          }
          @media (max-width: 620px) {
            .bottom-grid { grid-template-columns: 1fr !important; }
            .kpi-stack > * { min-width: 140px; }
            .diag-grid  { grid-template-columns: repeat(2, 1fr) !important; }
          }
        `}</style>

        {/* SECTION 1: Hero */}
        <div className="hero-grid">
          <div className="kpi-stack">
            <KPICard label="Next Month Forecast" value={fmt(result.next_month_forecast)} sub={result.next_month_label} accent={T.blue} icon="📈" />
            <KPICard label="Current Month Actual" value={fmt(result.current_month_actual)} sub={result.current_month_label} accent={T.green} icon="✓" />
            <KPICard
              label="MoM Change"
              value={`${momPositive ? "+" : ""}${fmt(result.mom_change)}%`}
              sub={momPositive ? "▲ Growing" : "▼ Declining"}
              accent={momPositive ? T.green : T.red}
              icon={momPositive ? "↑" : "↓"}
            />
            <KPICard label="L3M Moving AVG" value={latest3MAvg != null ? fmt(Math.round(latest3MAvg)) : "—"} sub="Recent demand baseline" accent={T.purple} icon="∅" />
          </div>

          <Panel>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
              <SectionHeader title="Sales Trend — Actual vs Forecast" subtitle="Past 12 months Clean_Demand + next-month forecast" />
              <div style={{ display: "flex", gap: 6 }}>
                <Badge label="Actual" color={T.green} />
                <Badge label="Forecast" color={T.blue} />
              </div>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={salesTrend} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="predGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={T.blue} stopOpacity={0.2} />
                    <stop offset="100%" stopColor={T.blue} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} tickLine={false} axisLine={false} interval={2} />
                <YAxis tick={{ fill: T.muted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} tickLine={false} axisLine={false} width={40} tickFormatter={fmtK} />
                <Tooltip content={<CustomTooltip />} />
                {forecastSplitLabel && (
                  <ReferenceLine x={forecastSplitLabel} stroke={T.blue} strokeDasharray="5 4" strokeOpacity={0.6}
                    label={{ value: "▶ Forecast", fill: T.blue, fontSize: 10, fontWeight: 700, position: "insideTopRight" }}
                  />
                )}
                <Area dataKey="predicted" name="Predicted" fill="url(#predGrad)" stroke={T.blue} strokeWidth={2.5} strokeDasharray="7 4" dot={false} connectNulls={false} />
                <Line dataKey="actual" name="Actual" stroke={T.green} strokeWidth={2.5} dot={{ r: 3, fill: T.green, strokeWidth: 0 }} activeDot={{ r: 6, fill: T.green, stroke: T.bg, strokeWidth: 2 }} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* SECTION 2: Business Signals */}
        <Divider label="Business Signals" />
        <div className="signal-row">
          <SignalCard
            label="Last Month Actual"
            value={fmt(result.last_month_actual)}
            sub={result.last_month_label}
            accent={T.teal}
          />
          <SignalCard
            label="Bonus Qty (Cur | Last)"
            value={`${fmt(latestShock?.bonusQty)} / ${fmt(previousShock?.bonusQty)}`}
            sub={`Flag: ${fmt(latestShock?.bonusFlag)} / ${fmt(previousShock?.bonusFlag)}`}
            accent={T.amber}
          />
          <SignalCard
            label="Supply Shock (Cur | Last)"
            value={`${fmt(latestShock?.supplyFlag)} / ${fmt(previousShock?.supplyFlag)}`}
            sub="Recent stockout indicators"
            accent={T.red}
          />
          <SignalCard
            label="Current Month SHP"
            value={currentSHP != null ? currentSHP.toFixed(2) : "—"}
            sub="Current stock ÷ latest 3M moving avg"
            accent={T.purple}
          />

          <SignalCard
            label="Last Month SHP"
            value={lastSHP != null ? lastSHP.toFixed(2) : "—"}
            sub="Last stock ÷ latest 3M moving avg"
            accent={T.blue}
          />
        </div>

        {/* SECTION 3: Charts */}
        <Divider label="Market Signals" />
        <div className="bottom-grid">
          <Panel>
            <SectionHeader title="Inventory Positions" subtitle="Primary inventory vs distributor stock — past 12 months" />
            <ResponsiveContainer width="100%" height={230}>
              <ComposedChart data={inventoryTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }} tickLine={false} axisLine={false} interval={3} />
                <YAxis tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }} iconType="circle" iconSize={7} />
                <Line dataKey="primaryInventory" name="Primary Inventory" stroke={T.purple} strokeWidth={2} dot={false} />
                <Line dataKey="distInventory" name="Distributor Stock" stroke={T.amber} strokeWidth={2} dot={false} strokeDasharray="5 3" />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>

          <Panel>
            <SectionHeader title="Bonus & Demand Shock Events" subtitle="Free_Qty per month + disruption flags — past 12 months" />
            <ResponsiveContainer width="100%" height={230}>
              <ComposedChart data={shockTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }} tickLine={false} axisLine={false} interval={3} />
                <YAxis yAxisId="left" tick={{ fill: T.muted, fontSize: 9 }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 1.5]} hide />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }} iconType="circle" iconSize={7} />
                <Bar yAxisId="left" dataKey="bonusQty" name="Bonus Qty" fill={T.amber} opacity={0.85} maxBarSize={16} radius={[3,3,0,0]} />
                <Bar yAxisId="right" dataKey="bonusFlag" name="Bonus Flag" fill="#e3b341" opacity={0.7} maxBarSize={10} radius={[3,3,0,0]} />
                <Bar yAxisId="right" dataKey="supplyFlag" name="Supply Shock" fill={T.red} opacity={0.7} maxBarSize={10} radius={[3,3,0,0]} />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* SECTION 4: Full Diagnostics table */}
        <Divider label="Model & Routing Details" />
        <div style={{
          background: T.card, border: `1px solid ${T.border}`,
          borderTop: `2px solid ${T.purple}`, borderRadius: 12,
          padding: "18px 20px", marginBottom: 16,
        }}>
          <div style={{ fontSize: 12, fontWeight: 800, color: T.text, marginBottom: 4 }}>Forecast Diagnostics</div>
          <div style={{ fontSize: 10, color: T.muted, marginBottom: 14 }}>
            Model selection, routing decisions, and forecast metadata for this SKU
          </div>
          <div className="diag-grid">
            <DiagCard label="Segment" value={result.segment} accent={T.teal} />
            <DiagCard label="Used Model" value={result.used_model} accent={T.blue} />
            <DiagCard label="Forecast Source" value={result.forecast_source} accent={T.amber} />
            <DiagCard label="Target Mode" value={result.target_mode} accent={T.purple} />
            <DiagCard label="Routing Reason" value={result.routing_reason} accent={T.muted} />
            <DiagCard label="Baseline Used" value={result.baseline_used != null ? fmt(result.baseline_used) : null} accent={T.purple} />
            <DiagCard label="Fallback Used" value={result.fallback_used != null ? (result.fallback_used ? "Yes" : "No") : null} accent={result.fallback_used ? T.red : T.green} />
            <DiagCard label="Forecast Month" value={result.next_month_label} accent={T.blue} />
          </div>
        </div>

        <XAIExplanationCard
          xai={xai}
          loading={xaiLoading}
          error={xaiError}
        />
      </>)}

      {/* ── Empty state ───────────────────────────────────────── */}
      {!result && !loading && (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", marginTop: 80, gap: 14, color: T.muted }}>
          <div style={{ fontSize: 40, opacity: 0.3 }}>◈</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Enter a SKU to load the forecast dashboard</div>
          <div style={{ fontSize: 12, opacity: 0.6 }}>Powered by Multi-Model Forecast Routing</div>
        </div>
      )}
    </div>
  );
}