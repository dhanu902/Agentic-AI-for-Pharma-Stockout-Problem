// src/pages/HorizonPage.jsx  [light theme]
// UI v2 — visual upgrade only. All state, fetch and table logic unchanged.
// v3 — AGENCY-WISE (business change 5): the inventory projection is now
//      shown per AGENCY instead of per item. Same projection numbers,
//      summed across every SKU of the agency (backend:
//      GET /api/horizon/results_by_agency). No projection logic change.

import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import T from "../theme";

const API_BASE = "/api/horizon";

const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.05)";
const SHADOW_MD = "0 1px 3px rgba(16,24,40,0.06), 0 12px 28px -16px rgba(16,24,40,0.18)";
const SHADOW_LG = "0 2px 6px rgba(16,24,40,0.06), 0 24px 48px -24px rgba(16,24,40,0.22)";

const RISK_CONFIG = {
  ENOUGH_STOCK: { color: T.green, label: "Enough Stock", icon: "✓" },
  SHORT_STOCK:  { color: T.red,   label: "Short Stock",  icon: "✕" },
};

function getRisk(level) { return RISK_CONFIG[level] || { color: T.muted, label: level || "Unknown", icon: "?" }; }
function toNumber(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function formatNum(v) { return toNumber(v).toLocaleString(); }

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
    .ui-scroll::-webkit-scrollbar { height: 8px; width: 8px; }
    .ui-scroll::-webkit-scrollbar-track { background: transparent; }
    .ui-scroll::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 8px; }
    .ui-scroll::-webkit-scrollbar-thumb:hover { background: ${T.muted}66; }
    .horizon-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
    @media(max-width:900px){.horizon-kpis{grid-template-columns:repeat(2,1fr)}}
    @media(max-width:600px){.horizon-kpis{grid-template-columns:1fr}}
  `}</style>
);

function RiskBadge({ level }) {
  if (!level) return <span style={{ color: T.muted, fontSize: 10 }}>—</span>;
  const cfg = getRisk(level);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 11px", background: cfg.color + "14", border: `1px solid ${cfg.color}3A`, color: cfg.color, borderRadius: 999, fontWeight: 800, fontSize: 10, textTransform: "uppercase", letterSpacing: 0.8, whiteSpace: "nowrap" }}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

function KpiCard({ title, value, subtitle, accent, delay = 0 }) {
  const col = accent || T.blue;
  return (
    <div className="ui-card ui-anim" style={{ animationDelay: `${delay}ms`, background: T.card, border: `1px solid ${T.border}`, borderRadius: 14, padding: "16px 18px", position: "relative", overflow: "hidden", boxShadow: SHADOW_SM }}>
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${col}26, transparent 70%)`, pointerEvents: "none" }} />
      <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 1.4, fontWeight: 800, marginBottom: 9 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, lineHeight: 1, marginBottom: 6, letterSpacing: -0.5, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {subtitle && <div style={{ fontSize: 10.5, color: T.muted, fontWeight: 500 }}>{subtitle}</div>}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${col}, ${col}22 70%, transparent)` }} />
    </div>
  );
}

const thBase = {
  borderBottom: `1px solid ${T.border}`,
  background: T.surface,
  color: T.text,
  padding: "11px 14px",
  textAlign: "center",
  verticalAlign: "top",
  minWidth: 135,
  position: "sticky",
  top: 0,
  zIndex: 2,
};

const tdBase = { borderBottom: `1px solid ${T.border}`, padding: "11px 14px", textAlign: "right", fontVariantNumeric: "tabular-nums" };

const tdLabelBase = {
  ...tdBase,
  textAlign: "left",
  color: T.muted,
  fontWeight: 800,
  minWidth: 190,
  position: "sticky",
  left: 0,
  background: T.card,
  zIndex: 1,
  fontFamily: FONT_UI,
  fontSize: 10.5,
  textTransform: "uppercase",
  letterSpacing: 0.6,
};

// Columns whose values are text/labels/dates, not numeric quantities.
const TEXT_VALUE_KEYS = [
  "M1_Stock_Status",
];

// CHANGED (agency-wise): table shows one agency's aggregated projection.
function HorizonTable({ selectedAgency, rows, loading }) {
  const agencyRows = useMemo(() => {
    if (!selectedAgency) return [];
    return rows.filter(r => String(r.Agency) === String(selectedAgency))
      .sort((a, b) => toNumber(String(a.Horizon || "").replace("M+", "")) - toNumber(String(b.Horizon || "").replace("M+", "")));
  }, [rows, selectedAgency]);

  const monthCols = agencyRows.slice(0, 6);

  const metricRows = [
    ["Forecast Demand", "Forecast_Qty"],
    ["DB Trade Stock", "Distributor_Trade_Qty"],
    ["WH Trade Stock", "Primary_Trade_Qty"],
    ["Total Trade Stock", "Total_Trade_Stock"],
    ["M+1 Stock Status", "M1_Stock_Status"],
    ["PO Qty", "PO_Qty"],
    ["GRN Qty", "GRN_Qty"],
    ["Projected Closing Stock", "Projected_Closing_Stock"],
    ["SKUs Tracked", "SKU_Count"],
    ["Short-Stock Items", "Risk_Level_SHORT_STOCK_Count"],
  ];

  const rowBg = (idx) => idx % 2 === 0 ? T.card : T.surface + "66";

  if (loading) return <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>Loading horizon projection…</div>;
  if (!selectedAgency) return <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>Select an agency to view horizon projection.</div>;
  if (monthCols.length === 0) return <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>No horizon data found for agency {selectedAgency}. Run horizon engine first.</div>;

  return (
    <div className="ui-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: FONT_MONO, fontSize: 11 }}>
        <thead>
          <tr>
            <th style={{ ...thBase, textAlign: "left", left: 0, zIndex: 3, minWidth: 190, fontFamily: FONT_UI, fontSize: 10, textTransform: "uppercase", letterSpacing: 1 }}>KPI</th>
            {monthCols.map(r => {
              const riskCfg = getRisk(r.Risk_Level);
              return (
                <th key={r.Horizon} style={{ ...thBase, background: `linear-gradient(180deg, ${riskCfg.color}10, ${T.surface})`, borderTop: `3px solid ${riskCfg.color}` }}>
                  <div style={{ fontWeight: 900, marginBottom: 3, fontSize: 12 }}>{r.Horizon}</div>
                  <div style={{ color: T.muted, fontSize: 9, marginBottom: 7 }}>{r.Forecast_Month}</div>
                  <RiskBadge level={r.Risk_Level} />
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {metricRows.map(([label, key], rowIdx) => {
            const bg = rowBg(rowIdx);
            const isText = TEXT_VALUE_KEYS.includes(key);
            return (
              <tr key={key}>
                <td style={{ ...tdLabelBase, background: bg }}>{label}</td>
                {monthCols.map(r => {
                  const rawVal = r[key];
                  let color = T.text;
                  if (key === "M1_Stock_Status") {
                    color = rawVal === "ENOUGH_STOCK" ? T.green : rawVal === "SHORT_STOCK" ? T.red : T.muted;
                  }
                  return (
                    <td key={`${r.Horizon}-${key}`} style={{ ...tdBase, textAlign: isText ? "center" : "right", background: bg, color, fontWeight: key === "M1_Stock_Status" ? 800 : 500 }}>
                      {isText ? (rawVal || "—") : formatNum(rawVal)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function HorizonPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const [agency, setAgency]               = useState(searchParams.get("agency") || "");
  const [rows, setRows]                   = useState([]);
  const [selectedAgency, setSelectedAgency] = useState(searchParams.get("agency") || "");
  const [loading, setLoading]             = useState(false);
  const [running, setRunning]             = useState(false);
  const [error, setError]                 = useState("");

  const agencyOptions = useMemo(() => [...new Set(rows.map(r => String(r.Agency || "")))].filter(Boolean).sort(), [rows]);

  const selectedRows = useMemo(() => {
    return rows
      .filter(r => String(r.Agency) === String(selectedAgency))
      .sort((a, b) =>
        toNumber(String(a.Horizon || "").replace("M+", "")) -
        toNumber(String(b.Horizon || "").replace("M+", ""))
      );
  }, [rows, selectedAgency]);

  const firstMonth = selectedRows?.[0];

  // CHANGED (agency-wise): /results -> /results_by_agency
  const fetchHorizonResults = async () => {
    setLoading(true); setError("");
    try {
      const res  = await fetch(`${API_BASE}/results_by_agency`);
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; } catch { throw new Error("Backend did not return valid JSON for /horizon/results_by_agency"); }
      if (!res.ok) throw new Error(result.error || "Failed to load horizon results");
      const dataRows = Array.isArray(result.rows) ? result.rows : [];
      setRows(dataRows);
      const urlAgency  = searchParams.get("agency") || "";
      const nextAgency = urlAgency || selectedAgency || dataRows[0]?.Agency || "";
      setSelectedAgency(String(nextAgency)); setAgency(String(nextAgency));
    } catch (err) { setError(err.message || "Failed to fetch horizon results"); }
    finally { setLoading(false); }
  };

  const runHorizonEngine = async () => {
    setRunning(true); setError("");
    try {
      const res  = await fetch(`${API_BASE}/run`, { method: "POST", headers: { "Content-Type": "application/json" } });
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; } catch { throw new Error("Backend did not return valid JSON for /horizon/run"); }
      if (!res.ok || !result.ok) throw new Error(result.error || "Horizon engine failed");
      await fetchHorizonResults();
    } catch (err) { setError(err.message || "Failed to run horizon engine"); }
    finally { setRunning(false); }
  };

  const selectAgency = (name) => {
    const clean = String(name || "").trim();
    setAgency(clean); setSelectedAgency(clean);
    if (clean) setSearchParams({ agency: clean });
  };

  useEffect(() => { fetchHorizonResults(); }, []);

  return (
    <div style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.purple}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.blue}0C, transparent 55%),
                   ${T.bg}`,
      color: T.text, padding: "26px 34px 40px", fontFamily: FONT_UI }}>
      <GlobalStyle />

      {/* Header (glass) */}
      <div className="ui-anim" style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 22 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 15 }}>
          <div style={{ width: 44, height: 44, background: `linear-gradient(135deg, ${T.purple}, ${T.blue})`, borderRadius: 13, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: "#fff", boxShadow: `0 8px 20px -8px ${T.purple}AA` }}>◷</div>
          <div>
            <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>Horizon Planning</div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.5,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>6-Month Forecast Horizon</h1>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", background: T.card, border: `1px solid ${T.borderHi}`, borderRadius: 10, boxShadow: SHADOW_SM, padding: "9px 14px", gap: 8, width: 260 }}>
            <input list="horizon-agency-options" value={agency} onChange={e => setAgency(e.target.value)}
              onKeyDown={e => e.key === "Enter" && selectAgency(agency)}
              placeholder="Search Agency…"
              style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 13, width: "100%", fontFamily: FONT_MONO }} />
            <datalist id="horizon-agency-options">{agencyOptions.map(c => <option key={c} value={c} />)}</datalist>
          </div>
          <button className="ui-btn" onClick={() => selectAgency(agency)}
            style={{ background: `linear-gradient(135deg, ${T.blue}, ${T.blue}D9)`, border: "none", color: "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10, padding: "10px 18px", cursor: "pointer", fontFamily: FONT_UI, boxShadow: `0 8px 18px -8px ${T.blue}AA` }}>
            Load Agency
          </button>
          <button className="ui-btn" onClick={runHorizonEngine} disabled={running}
            style={{ background: running ? T.subtle : `linear-gradient(135deg, ${T.purple}, ${T.blue})`, border: "none", color: running ? T.muted : "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10, padding: "10px 18px", cursor: running ? "not-allowed" : "pointer", fontFamily: FONT_UI, boxShadow: running ? "none" : `0 8px 18px -8px ${T.purple}AA`, display: "inline-flex", alignItems: "center", gap: 8 }}>
            {running ? <><span className="ui-spinner" /> Running…</> : <>▶ Run Horizon Engine</>}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ background: T.red + "0D", border: `1px solid ${T.red}33`, borderLeft: `4px solid ${T.red}`, borderRadius: 12, padding: "13px 18px", color: T.red, marginBottom: 18, fontSize: 13, boxShadow: SHADOW_SM }}>⚠ {error}</div>
      )}

      <div className="horizon-kpis">
        <KpiCard delay={0}   title="Selected Agency" value={selectedAgency || "—"} subtitle={firstMonth?.SKU_Count != null ? `${formatNum(firstMonth.SKU_Count)} SKUs tracked` : "Focused agency"} accent={T.blue} />
        <KpiCard delay={40}  title="M+1 Forecast" value={firstMonth ? formatNum(firstMonth.Forecast_Qty) : "—"} subtitle={firstMonth?.Forecast_Month || "First forecast month"} accent={T.teal} />
        <KpiCard delay={80}  title="M+1 Trade Stock" value={firstMonth ? formatNum(firstMonth.Total_Trade_Stock) : "—"} subtitle="DB + WH trade stock" accent={T.green} />
        <KpiCard delay={120} title="M+1 Stock Status" value={firstMonth?.M1_Stock_Status || "—"} subtitle={firstMonth?.Risk_Level_SHORT_STOCK_Count > 0 ? `${formatNum(firstMonth.Risk_Level_SHORT_STOCK_Count)} item(s) short` : "All items covered"} accent={firstMonth?.M1_Stock_Status === "ENOUGH_STOCK" ? T.green : T.red} />
      </div>

      <div className="ui-anim" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "18px 20px", boxShadow: SHADOW_MD, position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.purple}, ${T.purple}22 60%, transparent)` }} />
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 4 }}>Horizon Projection — Agency Wise</div>
          <div style={{ fontSize: 15, fontWeight: 900, color: T.text }}>Forecast Horizon with M+1 Stock Status</div>
          <div style={{ fontSize: 10.5, color: T.muted, marginTop: 3 }}>Shows M+1 to M+6 forecast demand summed across every SKU of the agency. Stock status is the worst case among the agency's items (short if ANY item is short). PO/GRN fields are placeholders until structured supply data is available.</div>
        </div>
        <HorizonTable selectedAgency={selectedAgency} rows={rows} loading={loading} />
      </div>
    </div>
  );
}