/*Dashboard -> src -> pages -> Forecast.jsx */
// UI v2 — visual upgrade only. All state, fetch, memo and chart logic unchanged.
// v2.1 — adds /skus_full dropdown (includes leftover/non-focus SKUs) and
//        data_completeness: "LIGHTWEIGHT" handling for the fallback dashboard.
// v3   — AGENCY-WISE (business change 4): the selector now lists AGENCIES
//        instead of items. KPIs and charts are unchanged — every number is
//        the same computation aggregated across all SKUs of the agency
//        (backend: POST /agency_dashboard, GET /agencies).
// v4   — LAYOUT REWORK:
//        · Left KPI stack   = totals for the SELECTED AGENCY (unchanged calc)
//        · Right section    = SKU-WISE: second search bar for SKUs within the
//          agency, item name + code subheading, the same 4 KPIs for the
//          selected SKU, its trend chart, then SKU business signals
//        · Market Signals   = agency-overall KPI row, then the SKU-wise
//          inventory/shock charts (respond to the SKU filter)
//        · Forecast Diagnostics = hidden by default, expandable (SKU-wise —
//          the forecast model runs per SKU, not per agency)

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ReferenceLine
} from "recharts";
import T from "../theme";

const API_BASE = "/api/forecast";

let forecastMemory = { agency: "", result: null };

const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.04), 0 6px 18px -12px rgba(16,24,40,0.10)";
const SHADOW_MD = "0 2px 4px rgba(16,24,40,0.05), 0 16px 40px -20px rgba(16,24,40,0.22)";
const SHADOW_LG = "0 4px 10px rgba(16,24,40,0.06), 0 34px 68px -30px rgba(16,24,40,0.30)";

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

    /* ═══ v5 visual refresh — pure CSS, no logic ═══ */
    .page-shell { position: relative; }
    .page-shell::before {
      content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background-image: radial-gradient(${T.muted}30 1px, transparent 1px);
      background-size: 24px 24px;
      -webkit-mask-image: radial-gradient(1000px 560px at 15% -5%, black, transparent 70%);
              mask-image: radial-gradient(1000px 560px at 15% -5%, black, transparent 70%);
    }
    .page-shell::after {
      content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
      background:
        radial-gradient(760px 380px at 108% 8%, ${T.blue}14, transparent 65%),
        radial-gradient(640px 340px at -8% 88%, ${T.teal}12, transparent 60%);
      animation: aurora-drift 18s ease-in-out infinite alternate;
    }
    .page-shell > * { position: relative; z-index: 1; }
    @keyframes aurora-drift {
      from { opacity: 0.6; transform: translate3d(0,-10px,0); }
      to   { opacity: 1;   transform: translate3d(0,12px,0); }
    }
    @keyframes hero-float {
      0%, 100% { transform: translateY(0) rotate(0deg); }
      50%      { transform: translateY(-4px) rotate(-3deg); }
    }
    .hero-icon { animation: hero-float 5.5s ease-in-out infinite; }
    .ui-card { border-radius: 18px !important; }
    .ui-card:hover {
      transform: translateY(-3px);
      border-color: ${T.blue}55 !important;
      box-shadow: 0 2px 8px rgba(16,24,40,0.06), 0 30px 60px -26px ${T.blue}4D !important;
    }
    .ui-btn { border-radius: 11px !important; }
    .ui-btn:not(:disabled):hover  { transform: translateY(-1px) scale(1.015); filter: saturate(1.12) brightness(1.03); }
    .ui-btn:not(:disabled):active { transform: translateY(0) scale(0.985); }
    input::placeholder { color: ${T.muted}AA; }
    ::selection { background: ${T.blue}2E; }
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

/* ─── Agency Info Strip ──────────────────────────────────────── */
/* CHANGED (agency-wise): shows the selected agency, its SKU coverage
   (total / focus / forecast-source mix) and the aggregated demand
   status. Same visual chrome as the old SKU strip. */
const AgencyInfoStrip = ({ result }) => {
  if (!result || result.error) return null;
  const demStatus    = result.demand_status;
  const demCol       = demandStatusColor(demStatus);
  const isLightweight = result.data_completeness === "LIGHTWEIGHT";
  const srcCounts    = result.forecast_source_counts || {};
  const budgetOnly   = srcCounts.BUDGET_ONLY || 0;
  return (
    <div className="ui-anim" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", padding: "9px 16px", background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`, backdropFilter: "blur(8px)", border: `1px solid ${T.border}`, borderRadius: 12, marginBottom: 18, boxShadow: SHADOW_SM }}>
      <span style={{ fontSize: 13, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, letterSpacing: 0.5 }}>{result.agency || result.item_code || "—"}</span>
      {result.agency_code && (
        <span style={{ fontSize: 11, color: T.muted }}>({result.agency_code})</span>
      )}
      <span style={{ color: T.borderHi, fontSize: 14 }}>·</span>
      {result.sku_count != null && (
        <span style={{ display: "inline-flex", alignItems: "center", background: T.teal + "14", border: `1px solid ${T.teal}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 800, color: T.teal, letterSpacing: 0.5 }}>
          {result.sku_count} SKUs · {result.focus_sku_count ?? 0} focus
        </span>
      )}
      {budgetOnly > 0 && (
        <span style={{ display: "inline-flex", alignItems: "center", background: T.amber + "14", border: `1px solid ${T.amber}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 700, color: T.amber }}>
          {budgetOnly} budget-only
        </span>
      )}
      {demStatus && <span style={{ display: "inline-flex", alignItems: "center", background: demCol + "14", border: `1px solid ${demCol}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 700, color: demCol }}>{demStatus}</span>}
      <span style={{ display: "inline-flex", alignItems: "center", background: T.blue + "14", border: `1px solid ${T.blue}3A`, borderRadius: 999, padding: "3px 10px", fontSize: 10, fontWeight: 700, color: T.blue, textTransform: "uppercase", letterSpacing: 0.8 }}>Agency view</span>
      {isLightweight && <Badge label="Limited data" color={T.amber} />}
      {result.as_of && <span style={{ marginLeft: "auto", fontSize: 10, color: T.muted }}>As of <span style={{ color: T.text, fontWeight: 700 }}>{result.as_of}</span></span>}
    </div>
  );
};


/* ─── Main ───────────────────────────────────────────────────── */
export default function Forecast() {
  const [, setSearchParams] = useSearchParams();
  const [agency, setAgency]     = useState(forecastMemory.agency || "");
  const [result, setResult]     = useState(forecastMemory.result || null);
  const [loading, setLoading]   = useState(false);
  const [agencyOptions, setAgencyOptions] = useState([]);
  const [agencyLoading, setAgencyLoading] = useState(false);

  // SKU-wise section state (SKUs WITHIN the selected agency)
  const [skuQuery, setSkuQuery]       = useState(forecastMemory.selectedSku || "");
  const [selectedSku, setSelectedSku] = useState(forecastMemory.selectedSku || "");
  const [skuResult, setSkuResult]     = useState(forecastMemory.skuResult || null);
  const [skuLoading, setSkuLoading]   = useState(false);
  const [diagOpen, setDiagOpen]       = useState(false);

  // SKU dashboard (item-wise endpoint — the model runs per SKU)
  const loadSkuDashboard = async (codeArg) => {
    const code = String(codeArg || "").trim();
    if (!code) return;
    setSkuLoading(true);
    try {
      const response = await fetch(`${API_BASE}/dashboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_code: code }),
      });
      const data = await response.json();
      const payload = response.ok
        ? (data.data || data)
        : { error: data?.error || "Failed to fetch SKU dashboard" };
      setSkuResult(payload);
      setSelectedSku(code);
      setSkuQuery(code);
      forecastMemory = { ...forecastMemory, selectedSku: code, skuResult: payload };
    } catch {
      const payload = { error: "Failed to fetch SKU dashboard" };
      setSkuResult(payload);
      setSelectedSku(code);
      forecastMemory = { ...forecastMemory, selectedSku: code, skuResult: payload };
    } finally {
      setSkuLoading(false);
    }
  };

  // Agency dashboard (left KPI totals + SKU list for the SKU search bar)
  const handleForecast = async (agencyArg) => {
    const raw = typeof agencyArg === "string" ? agencyArg : agency;
    const name = String(raw || "").trim();
    if (!name) return;

    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/agency_dashboard`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agency: name }),
      });

      const data = await response.json();

      if (!response.ok) {
        const errObj = { error: data?.error || "Failed to fetch dashboard" };
        setResult(errObj);
        forecastMemory = { ...forecastMemory, agency: name, result: errObj };
        return;
      }

      const dashboardData = data.data || data;
      setResult(dashboardData);
      setAgency(name);
      setSearchParams({ agency: name });
      forecastMemory = { ...forecastMemory, agency: name, result: dashboardData };

      // reset SKU section and auto-load the first SKU of this agency
      setSkuResult(null); setSelectedSku(""); setSkuQuery("");
      const firstSku = dashboardData?.skus?.[0]?.item_code;
      if (firstSku) loadSkuDashboard(firstSku);
    } catch (error) {
      const errObj = { error: "Failed to fetch dashboard" };
      setResult(errObj);
      forecastMemory = { ...forecastMemory, agency: name, result: errObj };
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlAgency = params.get("agency");
    if (urlAgency && !forecastMemory.result) { setAgency(urlAgency); handleForecast(urlAgency); }
  }, []);

  useEffect(() => {
    const loadAgencies = async () => {
      setAgencyLoading(true);
      try {
        const res  = await fetch(`${API_BASE}/agencies`);
        const data = await res.json().catch(() => ({}));
        if (res.ok) setAgencyOptions(data?.agencies || []);
      } catch {} finally { setAgencyLoading(false); }
    };
    loadAgencies();
  }, []);

  /* ── Agency-level memos (left KPI stack + Market Signals row) ── */
  const mom         = result?.mom_change;
  const momPositive = typeof mom === "number" ? mom > 0 : false;

  const skuOptions = useMemo(() => result?.skus || [], [result]);
  const selectedSkuInfo = useMemo(
    () => skuOptions.find(s => String(s.item_code) === String(selectedSku)) || null,
    [skuOptions, selectedSku]
  );

  /* ── SKU-level memos (right section + Market Signals charts) ── */
  const skuOk             = skuResult && !skuResult.error;
  const skuSalesTrend     = useMemo(() => (skuOk && skuResult.sales_trend) || [], [skuResult, skuOk]);
  const skuInventoryTrend = useMemo(() => (skuOk && skuResult.inventory_trend) || [], [skuResult, skuOk]);
  const skuShockTrend     = useMemo(() => (skuOk && skuResult.shock_trend) || [], [skuResult, skuOk]);

  const skuMom         = skuOk ? skuResult.mom_change : null;
  const skuMomPositive = typeof skuMom === "number" ? skuMom > 0 : false;
  const isSkuLightweight = skuOk && skuResult.data_completeness === "LIGHTWEIGHT";

  const forecastSplitLabel = useMemo(() => {
    if (!skuSalesTrend?.length) return null;
    const firstForecast = skuSalesTrend.find(d => d?.isForecast);
    return firstForecast?.label || null;
  }, [skuSalesTrend]);

  const latestShock   = useMemo(() => skuShockTrend.length ? skuShockTrend[skuShockTrend.length - 1] : null, [skuShockTrend]);
  const previousShock = useMemo(() => skuShockTrend.length >= 2 ? skuShockTrend[skuShockTrend.length - 2] : null, [skuShockTrend]);

  return (
    <div className="page-shell" style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.blue}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.teal}0C, transparent 55%),
                   ${T.bg}`,
      fontFamily: FONT_UI, color: T.text, padding: "26px 34px 40px" }}>
      <GlobalStyle />

      {/* ── Top Bar (glass) — agency selector, unchanged ── */}
      <div className="ui-anim" style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <div className="hero-icon" style={{ width: 44, height: 44, background: `linear-gradient(135deg, ${T.blue}, ${T.teal})`, borderRadius: 13, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: "#fff", boxShadow: `0 8px 20px -8px ${T.blue}AA` }}>◈</div>
          <div>
            <div style={{ fontSize: 9, color: T.blue, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>Sales Intelligence Platform</div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.5,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>Agency Forecast Dashboard</h1>
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
            <input list="agency-options" value={agency} onChange={e => setAgency(e.target.value)}
              placeholder={agencyLoading ? "Loading agencies..." : "Agency name / code…"}
              onKeyDown={e => e.key === "Enter" && handleForecast()}
              style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 13, width: "100%", fontFamily: FONT_MONO }} />
            <datalist id="agency-options">{agencyOptions.map(a => <option key={a.agency} value={a.agency}>{`${a.sku_count} SKUs · ${a.focus_sku_count} focus`}</option>)}</datalist>
          </div>
          <button className="ui-btn" onClick={() => handleForecast()} disabled={loading}
            style={{ background: loading ? T.subtle : `linear-gradient(135deg, ${T.blue}, ${T.teal})`, border: "none", color: loading ? T.muted : "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10, padding: "11px 20px", cursor: loading ? "not-allowed" : "pointer", fontFamily: FONT_UI, letterSpacing: 0.3, boxShadow: loading ? "none" : `0 8px 18px -8px ${T.blue}AA`, display: "inline-flex", alignItems: "center", gap: 8 }}>
            {loading ? <><span className="ui-spinner" /> Loading…</> : "Load Agency Dashboard"}
          </button>
        </div>
      </div>

      <AgencyInfoStrip result={result} />

      {result?.error && (
        <div style={{ background: T.red + "0D", border: `1px solid ${T.red}33`, borderLeft: `4px solid ${T.red}`, borderRadius: 12, padding: "13px 18px", color: T.red, marginBottom: 20, fontSize: 13, boxShadow: SHADOW_SM }}>⚠ {result.error}</div>
      )}

      {result && !result.error && (<>
        <div className="hero-grid">
          {/* ── LEFT: KPI totals for the selected agency ── */}
          <div className="kpi-stack">
            <KPICard label="Next Month Forecast"  value={fmt(result.next_month_forecast)}  sub={result.next_month_label}   accent={T.blue}   icon="📈" />
            <KPICard label="Current Month Actual" value={fmt(result.current_month_actual)} sub={result.current_month_label} accent={T.green}  icon="✓" />
            <KPICard label="MoM Change" value={typeof mom === "number" ? `${momPositive ? "+" : ""}${fmt(mom)}%` : "—"} sub={typeof mom === "number" ? (momPositive ? "▲ Growing" : "▼ Declining") : "No previous baseline"} accent={typeof mom === "number" ? (momPositive ? T.green : T.red) : T.muted} icon={typeof mom === "number" ? (momPositive ? "↑" : "↓") : "—"}/>
            <KPICard label="L3M Moving AVG" value={result.current_l3m_avg != null ? fmt(Math.round(result.current_l3m_avg)) : "—"} sub="Business demand baseline" accent={T.purple} icon="∅" />
          </div>

          {/* ── RIGHT: SKU-WISE section (search within the agency) ── */}
          <Panel>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 9, color: T.blue, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>SKU Detail — {result.agency}</div>
                <div style={{ fontSize: 16, fontWeight: 900, color: T.text }}>
                  {selectedSkuInfo?.product_name || skuResult?.product_name || (selectedSku ? `SKU ${selectedSku}` : "Select a SKU")}
                </div>
                <div style={{ fontSize: 10.5, color: T.muted, marginTop: 2, fontFamily: FONT_MONO }}>
                  {selectedSku || "—"}
                  {selectedSkuInfo && !selectedSkuInfo.is_focus && <span style={{ color: T.amber }}>  ·  non-focus</span>}
                  {selectedSkuInfo?.forecast_source && <span>  ·  {selectedSkuInfo.forecast_source}</span>}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", background: T.card, border: `1px solid ${T.borderHi}`, borderRadius: 10, boxShadow: SHADOW_SM, padding: "8px 12px", gap: 8, width: 230 }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
                  <input list="agency-sku-options" value={skuQuery} onChange={e => setSkuQuery(e.target.value)}
                    placeholder="SKU within agency…"
                    onKeyDown={e => e.key === "Enter" && loadSkuDashboard(skuQuery)}
                    style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 12, width: "100%", fontFamily: FONT_MONO }} />
                  <datalist id="agency-sku-options">
                    {skuOptions.map(s => <option key={s.item_code} value={s.item_code}>{s.product_name}</option>)}
                  </datalist>
                </div>
                <button className="ui-btn" onClick={() => loadSkuDashboard(skuQuery)} disabled={skuLoading}
                  style={{ background: skuLoading ? T.subtle : `linear-gradient(135deg, ${T.blue}, ${T.blue}D9)`, border: "none", color: skuLoading ? T.muted : "#fff", fontWeight: 800, fontSize: 12, borderRadius: 10, padding: "9px 16px", cursor: skuLoading ? "not-allowed" : "pointer", fontFamily: FONT_UI, display: "inline-flex", alignItems: "center", gap: 7 }}>
                  {skuLoading ? <><span className="ui-spinner" /> Loading…</> : "Load SKU"}
                </button>
              </div>
            </div>

            {skuResult?.error && (
              <div style={{ background: T.red + "0D", border: `1px solid ${T.red}33`, borderLeft: `4px solid ${T.red}`, borderRadius: 10, padding: "10px 14px", color: T.red, marginBottom: 12, fontSize: 12 }}>⚠ {skuResult.error}</div>
            )}
            {isSkuLightweight && (
              <div style={{ background: T.amber + "0D", border: `1px solid ${T.amber}33`, borderLeft: `4px solid ${T.amber}`, borderRadius: 10, padding: "10px 14px", color: T.amber, marginBottom: 12, fontSize: 11.5 }}>
                ⓘ This SKU is outside the AI model's focus list — showing available history and its trend/budget data only.
              </div>
            )}

            {/* Same 4 KPIs as the left stack, for the selected SKU */}
            <div className="diag-grid" style={{ marginBottom: 16 }}>
              <SignalCard label="Next Month Forecast"  value={skuOk ? fmt(skuResult.next_month_forecast) : "—"}  sub={skuOk ? skuResult.next_month_label : null}    accent={T.blue} />
              <SignalCard label="Current Month Actual" value={skuOk ? fmt(skuResult.current_month_actual) : "—"} sub={skuOk ? skuResult.current_month_label : null} accent={T.green} />
              <SignalCard label="MoM Change" value={typeof skuMom === "number" ? `${skuMomPositive ? "+" : ""}${fmt(skuMom)}%` : "—"} sub={typeof skuMom === "number" ? (skuMomPositive ? "▲ Growing" : "▼ Declining") : null} accent={typeof skuMom === "number" ? (skuMomPositive ? T.green : T.red) : T.muted} />
              <SignalCard label="L3M Moving AVG" value={skuOk && skuResult.current_l3m_avg != null ? fmt(Math.round(skuResult.current_l3m_avg)) : "—"} sub="Business demand baseline" accent={T.purple} />
            </div>

            {/* SKU trend chart */}
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 12 }}>
              <SectionHeader title="Sales Trend — Actual vs Forecast" subtitle={`Past 12 months + 6-month forecast horizon · SKU ${selectedSku || "—"}`} />
              <div style={{ display: "flex", gap: 6 }}><Badge label="Actual" color={T.green} /><Badge label="Forecast" color={T.blue} /><Badge label="Budget" color={T.muted} /></div>
            </div>
            {skuSalesTrend.length === 0 ? (
              <div style={{ color: T.muted, fontSize: 12, padding: "30px 0", textAlign: "center" }}>
                {skuLoading ? "Loading SKU data…" : "Select a SKU to view its trend."}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={skuSalesTrend} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
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
                  <Line dataKey="pastForecast" name="Past Forecast" stroke={T.purple} strokeWidth={2.2} strokeDasharray="4 4" dot={{ r: 3, fill: T.purple, strokeWidth: 0 }} connectNulls={false}/>
                  <Line dataKey="actual" name="Actual" stroke={T.green} strokeWidth={2.5} dot={{ r: 3, fill: T.green, strokeWidth: 0 }} activeDot={{ r: 6, fill: T.green, stroke: T.bg, strokeWidth: 2 }} connectNulls={false} />
                </ComposedChart>
              </ResponsiveContainer>
            )}

            {/* Business signals — SKU wise, below the graph */}
            <Divider label="Business Signals — SKU" />
            <div className="signal-row" style={{ marginBottom: 6 }}>
              <SignalCard label="Last Month Actual"         value={skuOk ? fmt(skuResult.last_month_actual) : "—"} sub={skuOk ? skuResult.last_month_label : null} accent={T.teal}/>
              <SignalCard label="Bonus Qty (Cur | Last)"    value={`${fmt(latestShock?.bonusQty)} / ${fmt(previousShock?.bonusQty)}`} sub={`Flag: ${fmt(latestShock?.bonusFlag)} / ${fmt(previousShock?.bonusFlag)}`} accent={T.amber}/>
              <SignalCard label="Supply Shock (Cur | Last)" value={`${fmt(latestShock?.supplyFlag)} / ${fmt(previousShock?.supplyFlag)}`} sub="Recent stockout indicators" accent={T.red}/>
              <SignalCard label="Current DB SHP"            value={skuOk && skuResult.current_db_shp != null ? skuResult.current_db_shp.toFixed(2) : "—"} sub={`Stock: ${skuOk ? fmt(skuResult.current_db_stock) : "—"}`} accent={T.purple}/>
              <SignalCard label="Current WH SHP"            value={skuOk && skuResult.current_wh_shp != null ? skuResult.current_wh_shp.toFixed(2) : "—"} sub={`Stock: ${skuOk ? fmt(skuResult.current_wh_stock) : "—"}`} accent={T.teal}/>
            </div>
          </Panel>
        </div>

        {/* ── MARKET SIGNALS ─────────────────────────────────────
             Agency-overall KPI row first (like the left stack),
             then the SKU-wise charts driven by the SKU filter. ── */}

        <div className="bottom-grid">
          <Panel>
            <SectionHeader title={`Inventory Positions — SKU ${selectedSku || "—"}`} subtitle="Primary inventory vs distributor stock — past 12 months" />
            {skuInventoryTrend.length === 0 ? (
              <div style={{ color: T.muted, fontSize: 12, padding: "30px 0", textAlign: "center" }}>
                {isSkuLightweight ? "Not available for non-focus SKUs." : "Select a SKU to view inventory positions."}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={230}>
                <ComposedChart data={skuInventoryTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: T.muted, fontSize: 9, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} interval={3} />
                  <YAxis tick={{ fill: T.muted, fontSize: 9, fontFamily: FONT_MONO }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
                  <Tooltip content={<CustomTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }} iconType="circle" iconSize={7} />
                  <Line dataKey="primaryInventory" name="Primary Inventory" stroke={T.purple} strokeWidth={2} dot={false} />
                  <Line dataKey="distInventory" name="Distributor Stock" stroke={T.amber} strokeWidth={2} dot={false} strokeDasharray="5 3" />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </Panel>

          <Panel>
            <SectionHeader title={`Bonus & Demand Shock Events — SKU ${selectedSku || "—"}`} subtitle="Free_Qty per month + disruption flags — past 12 months" />
            {skuShockTrend.length === 0 ? (
              <div style={{ color: T.muted, fontSize: 12, padding: "30px 0", textAlign: "center" }}>
                {isSkuLightweight ? "Not available for non-focus SKUs." : "Select a SKU to view shock events."}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={230}>
                <ComposedChart data={skuShockTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
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
            )}
          </Panel>
        </div>

        {/* ── FORECAST DIAGNOSTICS — hidden by default, SKU-wise ──
             The forecast model runs per SKU, so this section follows the
             SKU filter above, never the agency aggregate. ── */}
        <Divider label="Model & Routing Details" />
        <button className="ui-btn" onClick={() => setDiagOpen(o => !o)} style={{
          display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
          width: "100%", padding: "11px 20px", marginBottom: diagOpen ? 14 : 16,
          background: diagOpen ? `linear-gradient(135deg, ${T.purple}0A, ${T.card})` : T.card,
          border: `1px solid ${diagOpen ? T.purple + "55" : T.border}`,
          borderRadius: 12, cursor: "pointer",
          color: diagOpen ? T.purple : T.muted, fontSize: 12, fontWeight: 800,
          fontFamily: FONT_UI, boxShadow: SHADOW_SM }}>
          <div style={{ flex: 1, height: 1, background: diagOpen ? T.purple + "33" : T.border }} />
          <span style={{ whiteSpace: "nowrap" }}>
            {diagOpen ? "▲ Hide" : "▼ Show"} Forecast Diagnostics — SKU {selectedSku || "—"}
          </span>
          <div style={{ flex: 1, height: 1, background: diagOpen ? T.purple + "33" : T.border }} />
        </button>

        {diagOpen && (
          <div className="ui-anim" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "18px 20px", marginBottom: 16, boxShadow: SHADOW_MD, position: "relative", overflow: "hidden" }}>
            <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.purple}, ${T.purple}22 60%, transparent)` }} />
            <div style={{ fontSize: 12.5, fontWeight: 900, color: T.text, marginBottom: 4 }}>Forecast Diagnostics</div>
            <div style={{ fontSize: 10, color: T.muted, marginBottom: 14 }}>Model selection, routing decisions, and forecast metadata for SKU {selectedSku || "—"}</div>
            {!skuOk ? (
              <div style={{ color: T.muted, fontSize: 12, padding: "16px 0", textAlign: "center" }}>Select a SKU to view its diagnostics.</div>
            ) : (
              <div className="diag-grid">
                <DiagCard label="Segment"         value={skuResult.segment}          accent={T.teal} />
                <DiagCard label="Used Model"      value={skuResult.used_model}       accent={T.blue} />
                <DiagCard label="Forecast Source" value={skuResult.forecast_source}  accent={T.amber} />
                <DiagCard label="Target Mode"     value={skuResult.target_mode}      accent={T.purple} />
                <DiagCard label="Routing Reason"  value={skuResult.routing_reason}   accent={T.muted} />
                <DiagCard label="Baseline Used"   value={skuResult.baseline_used != null ? fmt(skuResult.baseline_used) : null} accent={T.purple} />
                <DiagCard label="Fallback Used"   value={skuResult.fallback_used != null ? (skuResult.fallback_used ? "Yes" : "No") : null} accent={skuResult.fallback_used ? T.red : T.green} />
                <DiagCard label="Forecast Month"  value={skuResult.next_month_label} accent={T.blue} />
              </div>
            )}
          </div>
        )}

      </>)}

      {!result && !loading && (
        <div className="ui-anim" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", marginTop: 80, gap: 14, color: T.muted }}>
          <div style={{ width: 76, height: 76, borderRadius: 22, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32, color: T.blue, background: `linear-gradient(135deg, ${T.blue}14, ${T.teal}0D)`, border: `1px solid ${T.blue}22` }}>◈</div>
          <div style={{ fontSize: 14, fontWeight: 700, color: T.text }}>Select an agency to load the forecast dashboard</div>
          <div style={{ fontSize: 12, opacity: 0.7 }}>Powered by Multi-Model Forecast Routing</div>
        </div>
      )}
    </div>
  );
}