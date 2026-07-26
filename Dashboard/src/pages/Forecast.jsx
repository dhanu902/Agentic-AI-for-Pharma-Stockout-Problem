/*Dashboard -> src -> pages -> Forecast.jsx */
// UI v2 — visual upgrade only. All state, fetch, memo and chart logic unchanged.
// v2.1 — adds /skus_full dropdown (includes leftover/non-focus SKUs) and
//        data_completeness: "LIGHTWEIGHT" handling for the fallback dashboard.

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ReferenceLine
} from "recharts";
import T from "../theme";

const API_BASE = "/api/forecast";

let forecastMemory = { itemCode: "", result: null};

const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.05)";
const SHADOW_MD = "0 1px 3px rgba(16,24,40,0.06), 0 12px 28px -16px rgba(16,24,40,0.18)";
const SHADOW_LG = "0 2px 6px rgba(16,24,40,0.06), 0 24px 48px -24px rgba(16,24,40,0.22)";

/* ─── Helpers (unchanged) ────────────────────────────────────── */
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
  if (s.includes("declining")) return T.red;
  if (s.includes("low")) return T.amber;
  if (s.includes("growing") || s.includes("active")) return T.green;

  return T.muted;
}

const GlobalStyle = () => (
  <style>{`
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700;800&display=swap');
    @keyframes ui-fade-up { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    @keyframes ui-spin { to { transform: rotate(360deg); } }
    .ui-anim { animation: ui-fade-up 0.45s cubic-bezier(0.22,1,0.36,1) both; }
    .ui-card { transition: transform 0.2s cubic-bezier(0.22,1,0.36,1), box-shadow 0.2s ease; }
    .ui-card:hover { transform: translateY(-2px); box-shadow: ${SHADOW_LG}; }
    .ui-btn  { transition: transform 0.15s ease, box-shadow 0.15s ease; }
    .ui-btn:not(:disabled):hover  { transform: translateY(-1px); box-shadow: ${SHADOW_MD}; }
    .ui-btn:not(:disabled):active { transform: translateY(0); }
    .ui-spinner { width: 13px; height: 13px; border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff;
      display: inline-block; animation: ui-spin 0.7s linear infinite; vertical-align: -2px; }
    .hero-grid   { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: 16px; margin-bottom: 16px; }
    .kpi-stack   { display: flex; flex-direction: column; gap: 10px; }
    .signal-row  { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
    .signal-row > * { flex: 1; min-width: 180px; }
    .bottom-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .diag-grid   { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    @media (max-width: 900px) { .hero-grid { grid-template-columns: 1fr !important; } .kpi-stack { flex-direction: row !important; flex-wrap: wrap; } .kpi-stack > * { min-width: 160px; flex: 1; } .diag-grid { grid-template-columns: repeat(2, 1fr) !important; } }
    @media (max-width: 620px) { .bottom-grid { grid-template-columns: 1fr !important; } }
  `}</style>
);

/* ─── Tooltip ────────────────────────────────────────────────── */
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: T.card + "F5", backdropFilter: "blur(6px)", border: `1px solid ${T.borderHi}`, borderRadius: 10, padding: "10px 14px", fontSize: 12, boxShadow: SHADOW_LG }}>
      <p style={{ color: T.muted, marginBottom: 6, fontWeight: 800, fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>{label}</p>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, marginBottom: 3, display: "flex", justifyContent: "space-between", gap: 20 }}>
          <span style={{ opacity: 0.7, fontSize: 11 }}>{p.name}</span>
          <strong style={{ fontFamily: FONT_MONO, fontVariantNumeric: "tabular-nums" }}>{typeof p.value === "number" ? p.value.toLocaleString() : "—"}</strong>
        </div>
      ))}
    </div>
  );
};

/* ─── KPI Card ───────────────────────────────────────────────── */
const KPICard = ({ label, value, sub, accent, icon }) => (
  <div className="ui-card" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 14, padding: "16px 18px", flex: 1, minWidth: 140, position: "relative", overflow: "hidden", boxShadow: SHADOW_SM }}>
    <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110, background: `radial-gradient(circle, ${accent}26, transparent 70%)`, pointerEvents: "none" }} />
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
      {icon && <span style={{
        width: 24, height: 24, borderRadius: 7, flexShrink: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 12, color: accent,
        background: `linear-gradient(135deg, ${accent}22, ${accent}0D)`,
        border: `1px solid ${accent}2E` }}>{icon}</span>}
      <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 1.3, fontWeight: 800 }}>{label}</div>
    </div>
    <div style={{ fontSize: 24, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, lineHeight: 1, marginBottom: 6, letterSpacing: -0.5, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: accent, fontWeight: 700 }}>{sub}</div>}
    <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${accent}, ${accent}22 70%, transparent)` }} />
  </div>
);

/* ─── Diagnostic Card ────────────────────────────────────────── */
const DiagCard = ({ label, value, accent }) => {
  const col = accent || T.muted;
  const isEmpty = value === null || value === undefined || value === "" || value === "—";
  return (
    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderLeft: `3px solid ${isEmpty ? T.border : col}`, borderRadius: 10, padding: "11px 14px" }}>
      <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 800, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: isEmpty ? T.muted : T.text, fontFamily: FONT_MONO }}>{isEmpty ? "—" : String(value)}</div>
    </div>
  );
};

/* ─── Signal Card ────────────────────────────────────────────── */
const SignalCard = ({ label, value, sub, accent }) => (
  <div className="ui-card" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: "13px 16px", boxShadow: SHADOW_SM, position: "relative", overflow: "hidden" }}>
    <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: 3, background: `linear-gradient(180deg, ${accent}, ${accent}33)` }} />
    <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 1.3, fontWeight: 800, marginBottom: 7 }}>{label}</div>
    <div style={{ fontSize: 18, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    {sub && <div style={{ fontSize: 10, color: accent, marginTop: 4, fontWeight: 600 }}>{sub}</div>}
  </div>
);

/* ─── Panel ──────────────────────────────────────────────────── */
const Panel = ({ children, style = {} }) => (
  <div className="ui-anim" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "20px 22px 12px", boxShadow: SHADOW_MD, ...style }}>{children}</div>
);

const SectionHeader = ({ title, subtitle }) => (
  <div style={{ marginBottom: 16 }}>
    <div style={{ fontSize: 12.5, fontWeight: 900, color: T.text, letterSpacing: 0.2 }}>{title}</div>
    {subtitle && <div style={{ fontSize: 10, color: T.muted, marginTop: 3 }}>{subtitle}</div>}
  </div>
);

const Divider = ({ label }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0 14px" }}>
    <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, transparent, ${T.border})` }} />
    {label && <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 900 }}>{label}</span>}
    <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${T.border}, transparent)` }} />
  </div>
);

const Badge = ({ label, color }) => (
  <span style={{ background: color + "14", border: `1px solid ${color}3A`, color, borderRadius: 999, padding: "2px 9px", fontSize: 9, fontWeight: 800, textTransform: "uppercase", letterSpacing: 1 }}>{label}</span>
);

/* ─── SKU Info Strip ─────────────────────────────────────────── */
/* NEW: shows product_name/agency (present for leftover SKUs, and for
   focus SKUs if the backend ever attaches them) and a "Limited data"
   badge when the backend fell back to the lightweight dashboard. */
const SkuInfoStrip = ({ result }) => {
  if (!result || result.error) return null;
  const abcCat       = result.abc_category;
  const abcCol       = abcColor(abcCat);
  const demStatus    = result.demand_status;
  const demCol       = demandStatusColor(demStatus);
  const segment      = result.segment;
  const isLightweight = result.data_completeness === "LIGHTWEIGHT";
  return (
    <div className="ui-anim" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", padding: "9px 16px", background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`, backdropFilter: "blur(8px)", border: `1px solid ${T.border}`, borderRadius: 12, marginBottom: 18, boxShadow: SHADOW_SM }}>
      <span style={{ fontSize: 13, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, letterSpacing: 0.5 }}>{result.item_code || result.ItemCode || "—"}</span>
      {result.product_name && (<>
        <span style={{ color: T.borderHi, fontSize: 14 }}>·</span>
        <span style={{ fontSize: 12, color: T.text, fontWeight: 600 }}>{result.product_name}</span>
      </>)}
      {result.agency && (
        <span style={{ fontSize: 11, color: T.muted }}>({result.agency})</span>
      )}
      <span style={{ color: T.borderHi, fontSize: 14 }}>·</span>
      {abcCat && <span style={{ display: "inline-flex", alignItems: "center", background: abcCol + "14", border: `1px solid ${abcCol}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 800, color: abcCol, textTransform: "uppercase", letterSpacing: 1 }}>ABC · {abcCat}</span>}
      {demStatus && <span style={{ display: "inline-flex", alignItems: "center", background: demCol + "14", border: `1px solid ${demCol}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 700, color: demCol }}>{demStatus}</span>}
      {segment && <span style={{ display: "inline-flex", alignItems: "center", background: T.blue + "14", border: `1px solid ${T.blue}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 700, color: T.blue, textTransform: "uppercase", letterSpacing: 0.8 }}>Seg: {segment}</span>}
      {isLightweight && <Badge label="Limited data" color={T.amber} />}
      {result.as_of && <span style={{ marginLeft: "auto", fontSize: 10, color: T.muted }}>As of <span style={{ color: T.text, fontWeight: 700 }}>{result.as_of}</span></span>}
    </div>
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

  const handleForecast = async (codeArg) => {
    const raw = typeof codeArg === "string" ? codeArg : itemCode;
    const code = String(raw || "").trim();
    if (!code) return;

    setLoading(true);

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
        forecastMemory = { itemCode: code, result: errObj };
        return;
      }

      const dashboardData = data.data || data;
      setResult(dashboardData);
      setItemCode(code);
      setSearchParams({ sku: code });

      forecastMemory = { itemCode: code, result: dashboardData };
    } catch (error) {
      const errObj = { error: "Failed to fetch dashboard" };
      setResult(errObj);
      forecastMemory = { itemCode: code, result: errObj };
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sku = params.get("sku");
    if (sku && !forecastMemory.result) { setItemCode(sku); handleForecast(sku); }
  }, []);

  // CHANGED: /skus -> /skus_full, so leftover (non-focus) SKUs are
  // selectable too. Response shape is now [{item_code, is_focus}, ...]
  // instead of a plain string list.
  useEffect(() => {
    const loadSkus = async () => {
      setSkuLoading(true);
      try {
        const res  = await fetch(`${API_BASE}/skus_full`);
        const data = await res.json().catch(() => ({}));
        if (res.ok) setSkuOptions(data?.skus || []);
      } catch {} finally { setSkuLoading(false); }
    };
    loadSkus();
  }, []);

  const salesTrend     = useMemo(() => result?.sales_trend || [], [result]);
  const inventoryTrend = useMemo(() => result?.inventory_trend || [], [result]);
  const shockTrend     = useMemo(() => result?.shock_trend || [], [result]);

  const mom         = result?.mom_change;
  const momPositive = typeof mom === "number" ? mom > 0 : false;

  // NEW: true when the backend used the leftover-SKU fallback path
  // (no preprocessing, no engineered features — see leftover_sku_engine).
  const isLightweight = result?.data_completeness === "LIGHTWEIGHT";

  const forecastSplitLabel = useMemo(() => {
    if (!salesTrend?.length) return null;
    const firstForecast = salesTrend.find(d => d?.isForecast);
    return firstForecast?.label || null;
  }, [salesTrend]);

  const latestShock   = useMemo(() => shockTrend.length ? shockTrend[shockTrend.length - 1] : null, [shockTrend]);
  const previousShock = useMemo(() => shockTrend.length >= 2 ? shockTrend[shockTrend.length - 2] : null, [shockTrend]);

  const horizonTrend = useMemo(() => {
    return salesTrend || [];
  }, [salesTrend]);

  return (
    <div style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.blue}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.teal}0C, transparent 55%),
                   ${T.bg}`,
      fontFamily: FONT_UI, color: T.text, padding: "26px 34px 40px" }}>
      <GlobalStyle />

      {/* Top Bar (glass) */}
      <div className="ui-anim" style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div style={{ width: 44, height: 44, background: `linear-gradient(135deg, ${T.blue}, ${T.teal})`, borderRadius: 13, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: "#fff", boxShadow: `0 8px 20px -8px ${T.blue}AA` }}>◈</div>
          <div>
            <div style={{ fontSize: 9, color: T.blue, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>Sales Intelligence Platform</div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.5,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>SKU Forecast Dashboard</h1>
          </div>
          <div style={{ marginLeft: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Badge label="Multi-Model" color={T.blue} />
            <Badge label="Champion Map" color={T.teal} />
            <Badge label="Forecast File" color={T.purple} />
            <Badge label="v1.0" color={T.muted} />
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", background: T.card, border: `1px solid ${T.borderHi}`, borderRadius: 10, boxShadow: SHADOW_SM, padding: "9px 14px", gap: 8, width: 260 }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
            <input list="sku-options" value={itemCode} onChange={e => setItemCode(e.target.value)}
              placeholder={skuLoading ? "Loading SKUs..." : "SKU / Item Code…"}
              onKeyDown={e => e.key === "Enter" && handleForecast()}
              style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 13, width: "100%", fontFamily: FONT_MONO }} />
            {/* CHANGED: skuOptions is now [{item_code, is_focus}], render item_code */}
            <datalist id="sku-options">{skuOptions.map(s => <option key={s.item_code} value={s.item_code} />)}</datalist>
          </div>
          <button className="ui-btn" onClick={() => handleForecast()} disabled={loading}
            style={{ background: loading ? T.subtle : `linear-gradient(135deg, ${T.blue}, ${T.teal})`, border: "none", color: loading ? T.muted : "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10, padding: "11px 20px", cursor: loading ? "not-allowed" : "pointer", fontFamily: FONT_UI, letterSpacing: 0.3, boxShadow: loading ? "none" : `0 8px 18px -8px ${T.blue}AA`, display: "inline-flex", alignItems: "center", gap: 8 }}>
            {loading ? <><span className="ui-spinner" /> Loading…</> : "Load SKU Dashboard"}
          </button>
        </div>
      </div>

      <SkuInfoStrip result={result} />

      {result?.error && (
        <div style={{ background: T.red + "0D", border: `1px solid ${T.red}33`, borderLeft: `4px solid ${T.red}`, borderRadius: 12, padding: "13px 18px", color: T.red, marginBottom: 20, fontSize: 13, boxShadow: SHADOW_SM }}>⚠ {result.error}</div>
      )}

      {/* NEW: explains why several sections are hidden/blank for leftover SKUs */}
      {result && !result.error && isLightweight && (
        <div style={{ background: T.amber + "0D", border: `1px solid ${T.amber}33`, borderLeft: `4px solid ${T.amber}`, borderRadius: 12, padding: "13px 18px", color: T.amber, marginBottom: 20, fontSize: 12.5, boxShadow: SHADOW_SM }}>
          ⓘ This SKU is outside the AI model's focus list. Showing available sales history and a trend-baseline forecast only — inventory, stock cover, and shock signals aren't available for it.
        </div>
      )}

      {result && !result.error && (<>
        <div className="hero-grid">
          <div className="kpi-stack">
            <KPICard label="Next Month Forecast"  value={fmt(result.next_month_forecast)}  sub={result.next_month_label}   accent={T.blue}   icon="📈" />
            <KPICard label="Current Month Actual"  value={fmt(result.current_month_actual)} sub={result.current_month_label} accent={T.green}  icon="✓" />
            <KPICard label="MoM Change" value={typeof mom === "number" ? `${momPositive ? "+" : ""}${fmt(mom)}%` : "—"} sub={typeof mom === "number" ? (momPositive ? "▲ Growing" : "▼ Declining") : "No previous baseline"} accent={typeof mom === "number" ? (momPositive ? T.green : T.red) : T.muted} icon={typeof mom === "number" ? (momPositive ? "↑" : "↓") : "—"}/>
            {!isLightweight && (
              <KPICard label="L3M Moving AVG" value={result.current_l3m_avg != null? fmt(Math.round(result.current_l3m_avg)): "—"} sub="Business demand baseline" accent={T.purple} icon="∅" />
            )}
          </div>

          <Panel>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
              <SectionHeader title="Sales Trend — Actual vs Forecast" subtitle="Past 12 months Clean_Demand + 6-month forecast horizon" />
              <div style={{ display: "flex", gap: 6 }}><Badge label="Actual" color={T.green} /><Badge label="Forecast" color={T.blue} /><Badge label="Budget" color={T.muted} /></div>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={horizonTrend} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="predGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={T.blue} stopOpacity={0.15} />
                    <stop offset="100%" stopColor={T.blue} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 10, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} interval={2} />
                <YAxis tick={{ fill: T.muted, fontSize: 10, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} width={40} tickFormatter={fmtK} />
                <Tooltip content={<CustomTooltip />} />
                {forecastSplitLabel && <ReferenceLine x={forecastSplitLabel} stroke={T.blue} strokeDasharray="5 4" strokeOpacity={0.6} label={{ value: "▶ Forecast", fill: T.blue, fontSize: 10, fontWeight: 700, position: "insideTopRight" }} />}
                <Line dataKey="budget" name="Budget" stroke={T.muted} strokeWidth={1.8} strokeDasharray="2 3" dot={false} connectNulls={false} />
                <Area dataKey="predicted" name="Predicted" fill="url(#predGrad)" stroke={T.blue} strokeWidth={2.5} strokeDasharray="7 4" dot={false} connectNulls={false} />
                <Line dataKey="pastForecast" name="Past Forecast" stroke={T.purple} strokeWidth={2.2} strokeDasharray="4 4" dot={{ r: 3, fill: T.purple, strokeWidth: 0 }}connectNulls={false}/>
                <Line dataKey="actual" name="Actual" stroke={T.green} strokeWidth={2.5} dot={{ r: 3, fill: T.green, strokeWidth: 0 }} activeDot={{ r: 6, fill: T.green, stroke: T.bg, strokeWidth: 2 }} connectNulls={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* CHANGED: Business Signals row needs stock/SHP/shock fields that
            don't exist for lightweight SKUs — hide instead of showing blanks. */}
        {!isLightweight && (<>
          <Divider label="Business Signals" />
          <div className="signal-row">
            <SignalCard label="Last Month Actual"         value={fmt(result.last_month_actual)}sub={result.last_month_label}accent={T.teal}/>
            <SignalCard label="Bonus Qty (Cur | Last)"    value={`${fmt(latestShock?.bonusQty)} / ${fmt(previousShock?.bonusQty)}`}sub={`Flag: ${fmt(latestShock?.bonusFlag)} / ${fmt(previousShock?.bonusFlag)}`}accent={T.amber}/>
            <SignalCard label="Supply Shock (Cur | Last)" value={`${fmt(latestShock?.supplyFlag)} / ${fmt(previousShock?.supplyFlag)}`}sub="Recent stockout indicators"accent={T.red}/>
            <SignalCard label="Current DB SHP"            value={result.current_db_shp != null ? result.current_db_shp.toFixed(2): "—"}sub={`Stock: ${fmt(result.current_db_stock)}`}accent={T.purple}/>
            <SignalCard label="Current WH SHP"            value={result.current_wh_shp != null ? result.current_wh_shp.toFixed(2): "—"}sub={`Stock: ${fmt(result.current_wh_stock)}`}accent={T.teal}/>
          </div>
        </>)}

        {/* CHANGED: Inventory/shock charts need engineered fields that don't
            exist for lightweight SKUs — hide instead of rendering empty charts. */}
        {!isLightweight && (<>
          <Divider label="Market Signals" />
          <div className="bottom-grid">
            <Panel>
              <SectionHeader title="Inventory Positions" subtitle="Primary inventory vs distributor stock — past 12 months" />
              <ResponsiveContainer width="100%" height={230}>
                <ComposedChart data={inventoryTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 9, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} interval={3} />
                  <YAxis tick={{ fill: T.muted, fontSize: 9, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
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
                  <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 9, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} interval={3} />
                  <YAxis yAxisId="left" tick={{ fill: T.muted, fontSize: 9 }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
                  <YAxis yAxisId="right" orientation="right" domain={[0, 1.5]} hide />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }} iconType="circle" iconSize={7} />
                  <Bar yAxisId="left"  dataKey="bonusQty"   name="Bonus Qty"    fill={T.amber} opacity={0.85} maxBarSize={16} radius={[3,3,0,0]} />
                  <Bar yAxisId="right" dataKey="bonusFlag"  name="Bonus Flag"   fill="#c47d18" opacity={0.7}  maxBarSize={10} radius={[3,3,0,0]} />
                  <Bar yAxisId="right" dataKey="supplyFlag" name="Supply Shock" fill={T.red}   opacity={0.7}  maxBarSize={10} radius={[3,3,0,0]} />
                </ComposedChart>
              </ResponsiveContainer>
            </Panel>
          </div>
        </>)}

        <Divider label="Model & Routing Details" />
        <div className="ui-anim" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "18px 20px", marginBottom: 16, boxShadow: SHADOW_MD, position: "relative", overflow: "hidden" }}>
          <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.purple}, ${T.purple}22 60%, transparent)` }} />
          <div style={{ fontSize: 12.5, fontWeight: 900, color: T.text, marginBottom: 4 }}>Forecast Diagnostics</div>
          <div style={{ fontSize: 10, color: T.muted, marginBottom: 14 }}>Model selection, routing decisions, and forecast metadata for this SKU</div>
          <div className="diag-grid">
            <DiagCard label="Segment"        value={result.segment}          accent={T.teal} />
            <DiagCard label="Used Model"     value={result.used_model}       accent={T.blue} />
            <DiagCard label="Forecast Source" value={result.forecast_source} accent={T.amber} />
            <DiagCard label="Target Mode"    value={result.target_mode}      accent={T.purple} />
            <DiagCard label="Routing Reason" value={result.routing_reason}   accent={T.muted} />
            <DiagCard label="Baseline Used"  value={result.baseline_used != null ? fmt(result.baseline_used) : null} accent={T.purple} />
            <DiagCard label="Fallback Used"  value={result.fallback_used != null ? (result.fallback_used ? "Yes" : "No") : null} accent={result.fallback_used ? T.red : T.green} />
            <DiagCard label="Forecast Month" value={result.next_month_label} accent={T.blue} />
          </div>
        </div>

      </>)}

      {!result && !loading && (
        <div className="ui-anim" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", marginTop: 80, gap: 14, color: T.muted }}>
          <div style={{ width: 76, height: 76, borderRadius: 22, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32, color: T.blue, background: `linear-gradient(135deg, ${T.blue}14, ${T.teal}0D)`, border: `1px solid ${T.blue}22` }}>◈</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: T.text }}>Enter a SKU to load the forecast dashboard</div>
          <div style={{ fontSize: 12, opacity: 0.7 }}>Powered by Multi-Model Forecast Routing</div>
        </div>
      )}
    </div>
  );
}