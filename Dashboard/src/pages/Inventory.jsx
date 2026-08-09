/*Dashoboard -> src -> pages -> Inventory.jsx */
// v3 — AGENCY-WISE (business change 5): the inventory projection is now
//      shown per AGENCY instead of per item. The per-item risk logic
//      (scenarios A/B/C, Risk_Level) is unchanged — the backend sums
//      quantities across every SKU of the agency and reports the WORST
//      Risk_Level among its items (GET /api/risk/results_by_agency).
// v5 — INDEPENDENT FILTERS: the page no longer reads the ?agency= URL param
//      (shared with the Forecast page). It has its OWN agency and SKU
//      selectors in the header; DATA COVERAGE badges moved to the bottom.
// v4 — LAYOUT REWORK (same pattern as the Forecast page): agency-wise
//      overview first (agency list + aggregated detail, unchanged), then a
//      SKU-WISE section for the selected agency — its own SKU search bar,
//      item name + code heading, and the ORIGINAL per-item detail (stock
//      buckets + scenario A/B/C cards with flags/reasoning). Item rows come
//      from the item-wise GET /api/risk/results (now enriched with
//      Agency/ProductName display columns). No risk logic changed.
// M+1 Inventory page uses physical DB/WH stock only.
// Pending PO/GRN and Net Demand are shown only in Horizon page.
// SKU detail header so planners can see how much confirmed incoming
// stock was deducted from gross forecast demand before scenarios ran.
// UI v2 — visual upgrade only. All logic unchanged.
//
// UPDATE: full master SKU universe support. New Risk_Level values
// (NO_FORECAST_DATA, NO_INVENTORY_DATA, NO_DATA, NOT_TRACKED) are
// data-completeness states, not risk verdicts — they get their own
// visual treatment, their own summary strip, filter options, per-row
// flags, and a plain-language explanation banner in the detail view.
// Scenario A/B/C cards are suppressed for these rows since the
// underlying numbers are trivial (e.g. 0 forecast trivially "met").

import React, { useEffect, useMemo, useState } from "react";
import T from "../theme";

const API_BASE = "/api/risk";

const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.04), 0 6px 18px -12px rgba(16,24,40,0.10)";
const SHADOW_MD = "0 2px 4px rgba(16,24,40,0.05), 0 16px 40px -20px rgba(16,24,40,0.22)";
const SHADOW_LG = "0 4px 10px rgba(16,24,40,0.06), 0 34px 68px -30px rgba(16,24,40,0.30)";

/* ─── Risk config ───────────────────────────────────────────── */
const RISK_CONFIG = {
  SAFE:                   { color: T.green,   label: "Safe",                          icon: "✓" },
  UNDER_RISK:             { color: T.amber,   label: "Under Risk",                    icon: "⚠" },
  WH_TRADE_REQUIRED:      { color: T.orange,  label: "WH Trade Required",             icon: "⚡" },
  WH_INSPECTION_REQUIRED: { color: T.red,     label: "Critical: WH Inspection Required", icon: "!" },
  WH_BLOCKED_REQUIRED:    { color: T.crimson, label: "Critical: WH Blocked Required", icon: "✕" },
  CRITICAL_STOCKOUT:      { color: T.red,     label: "Critical Stockout",             icon: "✕" },
  // Data-completeness states — informational, NOT a risk verdict.
  // Kept visually distinct (blue/muted) so planners never mistake
  // "we have no data" for "confirmed safe" or "confirmed critical".
  NO_FORECAST_DATA:       { color: T.blue,    label: "No Forecast Data",               icon: "◌" },
  NO_INVENTORY_DATA:      { color: T.blue,    label: "No Inventory Data",              icon: "◌" },
  NO_DATA:                { color: T.muted,   label: "No Data Available",             icon: "–" },
  NOT_TRACKED:            { color: T.muted,   label: "Not Tracked (No Physical Code)", icon: "–" },
};

// Rows in these states never ran a real scenario assessment — the
// backend still computes A/B/C for transparency, but the numbers are
// trivial (e.g. 0 forecast trivially "met"), so the UI must not present
// them as a genuine risk verdict.
const DATA_GAP_LEVELS = ["NO_FORECAST_DATA", "NO_INVENTORY_DATA", "NO_DATA", "NOT_TRACKED"];
function isDataGap(level) { return DATA_GAP_LEVELS.includes(level); }

function getRisk(level) {
  return RISK_CONFIG[level] || { color: T.muted, label: level || "Unknown", icon: "?" };
}

let riskMemory = { agency: "", selected: null, rows: [] };

/* ─── Helpers ───────────────────────────────────────────────── */
function parseJsonArray(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

function riskLabel(level)  { return getRisk(level).label; }
function riskColor(level)  { return getRisk(level).color; }
function toNumber(value)   { const n = Number(value); return Number.isFinite(n) ? n : 0; }
function formatNum(value)  { return toNumber(value).toLocaleString(); }

function getScenarioStatus(row, prefix) {
  return {
    met:
      row?.[`${prefix}_met`] === true ||
      row?.[`${prefix}_met`] === "true" ||
      row?.[`${prefix}_met`] === 1,
    unmet:             toNumber(row?.[`${prefix}_unmet`]),
    used_db_no_risk:   toNumber(row?.[`${prefix}_used_db_no_risk`]),
    used_db_short_exp: toNumber(row?.[`${prefix}_used_db_short_exp`]),
    used_wh_trade:     toNumber(row?.[`${prefix}_used_wh_trade`]),
    used_wh_insp:      toNumber(row?.[`${prefix}_used_wh_insp`]),
    used_wh_blocked:   toNumber(row?.[`${prefix}_used_wh_blocked`]),
    flags:     parseJsonArray(row?.[`${prefix}_flags`]),
    reasoning: parseJsonArray(row?.[`${prefix}_reasoning`]),
  };
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
    .ui-scroll::-webkit-scrollbar { height: 8px; width: 8px; }
    .ui-scroll::-webkit-scrollbar-track { background: transparent; }
    .ui-scroll::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 8px; }
    .ui-scroll::-webkit-scrollbar-thumb:hover { background: ${T.muted}66; }
    .risk-main       { display: grid; grid-template-columns: 380px 1fr; gap: 16px; }
    .risk-scenarios  { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
    .risk-kpis       { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
    .risk-kpis-wide  { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 18px; }
    .risk-stock-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 18px; }
    .collapsible { overflow: hidden; transition: max-height 0.4s cubic-bezier(0.4,0,0.2,1), opacity 0.3s ease; }
    .collapsible.open   { opacity: 1; }
    .collapsible.closed { opacity: 0; }
    @media (max-width: 1100px) { .risk-main { grid-template-columns: 1fr !important; } }
    @media (max-width: 900px) {
      .risk-scenarios  { grid-template-columns: 1fr 1fr !important; }
      .risk-kpis       { grid-template-columns: 1fr 1fr !important; }
      .risk-kpis-wide  { grid-template-columns: 1fr 1fr !important; }
      .risk-stock-kpis { grid-template-columns: 1fr 1fr !important; }
    }
    @media (max-width: 600px) {
      .risk-scenarios  { grid-template-columns: 1fr !important; }
      .risk-kpis       { grid-template-columns: 1fr 1fr !important; }
      .risk-kpis-wide  { grid-template-columns: 1fr 1fr !important; }
      .risk-stock-kpis { grid-template-columns: 1fr 1fr !important; }
    }

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
        radial-gradient(760px 380px at 108% 8%, ${T.orange}12, transparent 65%),
        radial-gradient(640px 340px at -8% 88%, ${T.red}0E, transparent 60%);
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
      border-color: ${T.orange}55 !important;
      box-shadow: 0 2px 8px rgba(16,24,40,0.06), 0 30px 60px -26px ${T.orange}4D !important;
    }
    .ui-btn { border-radius: 11px !important; }
    .ui-btn:not(:disabled):hover  { transform: translateY(-1px) scale(1.015); filter: saturate(1.12) brightness(1.03); }
    .ui-btn:not(:disabled):active { transform: translateY(0) scale(0.985); }
    input::placeholder { color: ${T.muted}AA; }
    ::selection { background: ${T.orange}2E; }
  `}</style>
);

/* ─── KPI Card ──────────────────────────────────────────────── */
function KpiCard({ title, value, subtitle, accent }) {
  const col = accent || T.blue;
  return (
    <div className="ui-card" style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", position: "relative", overflow: "hidden", boxShadow: SHADOW_SM }}>
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${col}26, transparent 70%)`, pointerEvents: "none" }} />
      <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 1.4, fontWeight: 800, marginBottom: 9 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 900, color: T.text, fontFamily: FONT_MONO, lineHeight: 1, marginBottom: 6, letterSpacing: -0.5, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {subtitle && <div style={{ fontSize: 10.5, color: T.muted, marginTop: 4, fontWeight: 500 }}>{subtitle}</div>}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${col}, ${col}22 70%, transparent)` }} />
    </div>
  );
}

/* ─── Risk Badge ────────────────────────────────────────────── */
function RiskBadge({ level }) {
  const cfg = getRisk(level);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5,
      padding: "4px 11px", background: cfg.color + "14", border: `1px solid ${cfg.color}3A`,
      color: cfg.color, borderRadius: 999, fontWeight: 800, fontSize: 10,
      textTransform: "uppercase", letterSpacing: 0.8, whiteSpace: "nowrap" }}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

/* ─── Flag List ─────────────────────────────────────────────── */
function FlagList({ items }) {
  if (!items || items.length === 0) return <div style={{ color: T.muted, fontSize: 11 }}>No flags</div>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {items.map((flag, idx) => (
        <span key={idx} style={{ padding: "2px 9px", borderRadius: 999,
          background: T.red + "10", border: `1px solid ${T.red}2E`,
          color: T.red, fontSize: 9, fontWeight: 800, textTransform: "uppercase", letterSpacing: 0.8 }}>
          {String(flag).replace(/_/g, " ")}
        </span>
      ))}
    </div>
  );
}

/* ─── Reason List ───────────────────────────────────────────── */
function ReasonList({ items }) {
  if (!items || items.length === 0) return <div style={{ color: T.muted, fontSize: 11 }}>No reasoning available</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      {items.map((item, idx) => (
        <div key={idx} style={{ fontSize: 10, color: T.muted, lineHeight: 1.6,
          fontFamily: FONT_MONO,
          paddingBottom: idx < items.length - 1 ? 5 : 0,
          borderBottom: idx < items.length - 1 ? `1px solid ${T.border}` : "none" }}>
          {item}
        </div>
      ))}
    </div>
  );
}

/* ─── Stat Cell ─────────────────────────────────────────────── */
function StatCell({ label, value, color }) {
  return (
    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: "11px 13px", position: "relative", overflow: "hidden" }}>
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2,
        background: `linear-gradient(90deg, ${color || T.border}, transparent)` }} />
      <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 800, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 900, color: color || T.text, fontFamily: FONT_MONO, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

/* ─── Scenario Card ─────────────────────────────────────────── */
function ScenarioCard({ title, tag, step, scenario, accent, isActive, showWH = true }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="ui-card" style={{ background: T.card,
      border: `1px solid ${isActive ? accent + "55" : T.border}`,
      borderRadius: 16, padding: "18px 20px", position: "relative", overflow: "hidden",
      boxShadow: isActive ? `0 0 0 3px ${accent}14, ${SHADOW_MD}` : SHADOW_SM }}>
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3,
        background: isActive ? `linear-gradient(90deg, ${accent}, ${accent}22 60%, transparent)` : T.border }} />
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <div style={{ width: 24, height: 24, borderRadius: 8,
              background: `linear-gradient(135deg, ${accent}26, ${accent}0D)`, border: `1px solid ${accent}3A`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 10, fontWeight: 900, color: accent }}>{step}</div>
            <span style={{ fontSize: 11.5, fontWeight: 900, color: T.text }}>{title}</span>
          </div>
          {tag && <div style={{ fontSize: 9, color: T.muted, letterSpacing: 1, textTransform: "uppercase", fontWeight: 700 }}>{tag}</div>}
        </div>
        <div style={{ padding: "3px 10px", borderRadius: 999, fontSize: 10, fontWeight: 800,
          background: scenario.met ? T.green + "14" : T.red + "14",
          border: `1px solid ${scenario.met ? T.green : T.red}3A`,
          color: scenario.met ? T.green : T.red }}>
          {scenario.met ? "✓ Met" : "✕ Unmet"}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
        <StatCell label="Unmet Demand"   value={formatNum(scenario.unmet)}             color={toNumber(scenario.unmet) > 0 ? T.red : T.green} />
        <StatCell label="DB No-Risk"     value={formatNum(scenario.used_db_no_risk)}   color={T.green} />
        <StatCell label="DB Short-Exp"   value={formatNum(scenario.used_db_short_exp)} color={T.amber} />
        {showWH && (
          <>
            <StatCell label="WH Trade"   value={formatNum(scenario.used_wh_trade)}     color={T.blue} />
            <StatCell label="WH Insp."   value={formatNum(scenario.used_wh_insp)}      color={T.orange} />
            <StatCell label="WH Blocked" value={formatNum(scenario.used_wh_blocked)}   color={T.red} />
          </>
        )}
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 800, marginBottom: 6 }}>Flags</div>
        <FlagList items={scenario.flags} />
      </div>

      {scenario.reasoning?.length > 0 && (
        <>
          <button onClick={() => setExpanded(e => !e)} style={{
            background: "none", border: `1px solid ${T.border}`, color: T.muted,
            borderRadius: 999, padding: "6px 10px", fontSize: 10, cursor: "pointer",
            fontFamily: FONT_UI, fontWeight: 700, width: "100%",
            transition: "border-color 0.15s, color 0.15s" }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = accent; e.currentTarget.style.color = accent; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = T.border; e.currentTarget.style.color = T.muted; }}>
            {expanded ? "▲ Hide Reasoning" : "▼ Show Reasoning"}
          </button>
          {expanded && (
            <div style={{ marginTop: 10, background: T.surface, border: `1px solid ${T.border}`, borderRadius: 10, padding: "10px 12px" }}>
              <ReasonList items={scenario.reasoning} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ─── Divider ───────────────────────────────────────────────── */
const Divider = ({ label }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0 14px" }}>
    <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, transparent, ${T.border})` }} />
    {label && <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 900 }}>{label}</span>}
    <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${T.border}, transparent)` }} />
  </div>
);

/* ─── Summary Badge ─────────────────────────────────────────── */
function SummaryBadge({ label, count, color, onClick, active }) {
  return (
    <button className="ui-btn" onClick={onClick} style={{
      background: active ? color + "14" : T.card,
      border: `1px solid ${active ? color + "55" : T.border}`,
      borderRadius: 12, padding: "9px 16px", cursor: "pointer",
      boxShadow: active ? `0 0 0 3px ${color}14` : SHADOW_SM,
      display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
      <span style={{ fontSize: 18, fontWeight: 900, color: active ? color : T.text, fontFamily: FONT_MONO, fontVariantNumeric: "tabular-nums" }}>{count}</span>
      <span style={{ fontSize: 9, color: active ? color : T.muted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 800 }}>{label}</span>
    </button>
  );
}

/* ─── Section Toggle ────────────────────────────────────────── */
function SectionToggle({ expanded, onToggle, label, accent }) {
  const col = accent || T.orange;
  return (
    <button className="ui-btn" onClick={onToggle} style={{
      display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
      width: "100%", padding: "11px 20px",
      background: expanded ? `linear-gradient(135deg, ${col}0A, ${T.card})` : T.card,
      border: `1px solid ${expanded ? col + "55" : T.border}`,
      borderRadius: 12, cursor: "pointer",
      color: expanded ? col : T.muted, fontSize: 12, fontWeight: 800,
      fontFamily: FONT_UI, boxShadow: SHADOW_SM, marginBottom: expanded ? 16 : 0 }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = col + "88"; e.currentTarget.style.color = col; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = expanded ? col + "55" : T.border; e.currentTarget.style.color = expanded ? col : T.muted; }}>
      <div style={{ flex: 1, height: 1, background: expanded ? col + "33" : T.border }} />
      <span style={{ display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
        {expanded
          ? <><span style={{ fontSize: 10 }}>▲</span> {label?.hide || "Hide Details"}</>
          : <><span style={{ fontSize: 10 }}>▼</span> {label?.show || "Show Details"}</>}
      </span>
      <div style={{ flex: 1, height: 1, background: expanded ? col + "33" : T.border }} />
    </button>
  );
}

/* ─── Main ──────────────────────────────────────────────────── */
export default function RiskPage() {
  const [agency, setAgency]         = useState(riskMemory.agency || "");
  const [rows, setRows]             = useState([]);
  const [loading, setLoading]       = useState(false);
  const [running, setRunning]       = useState(false);
  const [error, setError]           = useState("");
  const [selected, setSelected]     = useState(null);
  const [riskFilter, setRiskFilter] = useState("ALL");
  const [detailOpen, setDetailOpen] = useState(false);

  // SKU-wise section state (items WITHIN the selected agency)
  const [itemRows, setItemRows]         = useState([]);
  const [skuQuery, setSkuQuery]         = useState("");
  const [selectedItem, setSelectedItem] = useState(null);

  // CHANGED (agency-wise): rows are agencies; search matches agency name/code
  const filteredRows = useMemo(() => {
    return rows.filter(row => {
      const matchesAgency = !agency ||
        String(row.Agency || "").toLowerCase().includes(agency.toLowerCase()) ||
        String(row.AgencyCode || "").toLowerCase().includes(agency.toLowerCase());
      const matchesRisk = riskFilter === "ALL" || row.Risk_Level === riskFilter;
      return matchesAgency && matchesRisk;
    });
  }, [rows, agency, riskFilter]);

  const summary = useMemo(() => ({
    safe:         rows.filter(r => r.Risk_Level === "SAFE").length,
    underRisk:    rows.filter(r => r.Risk_Level === "UNDER_RISK").length,
    whTrade:      rows.filter(r => r.Risk_Level === "WH_TRADE_REQUIRED").length,
    whInspection: rows.filter(r => r.Risk_Level === "WH_INSPECTION_REQUIRED").length,
    whBlocked:    rows.filter(r => r.Risk_Level === "WH_BLOCKED_REQUIRED").length,
    critical:     rows.filter(r => r.Risk_Level === "CRITICAL_STOCKOUT").length,
    noForecast:   rows.filter(r => r.Risk_Level === "NO_FORECAST_DATA").length,
    noInventory:  rows.filter(r => r.Risk_Level === "NO_INVENTORY_DATA").length,
    noData:       rows.filter(r => r.Risk_Level === "NO_DATA").length,
    notTracked:   rows.filter(r => r.Risk_Level === "NOT_TRACKED").length,
  }), [rows]);

  /* ── API calls ── */
  const runRiskEngine = async () => {
    setRunning(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/run`, { method: "POST", headers: { "Content-Type": "application/json" } });
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; } catch { throw new Error("Backend did not return valid JSON for /run"); }
      if (!res.ok || !result.ok) throw new Error(result.error || "Risk engine failed");
      await fetchResults();
    } catch (err) {
      setError(err.message || "Failed to run risk engine");
    } finally {
      setRunning(false);
    }
  };

  // Pick the first item of an agency (used when the agency selection changes)
  const pickFirstItemOfAgency = (agencyName, items) => {
    const first = (items || []).find(r => String(r.Agency || "") === String(agencyName || "")) || null;
    setSelectedItem(first);
    setSkuQuery(first ? String(first.ItemCode || "") : "");
  };

  // CHANGED (agency-wise + SKU-wise): fetch the agency aggregate rows AND
  // the item-wise rows (enriched with Agency/ProductName) in one go.
  const fetchResults = async () => {
    setLoading(true);
    setError("");
    try {
      const [agRes, itRes] = await Promise.all([
        fetch(`${API_BASE}/results_by_agency`),
        fetch(`${API_BASE}/results`),
      ]);

      const agText = await agRes.text();
      let agResult = {};
      try { agResult = agText ? JSON.parse(agText) : {}; } catch { throw new Error("Backend did not return valid JSON for /results_by_agency"); }
      if (!agRes.ok) throw new Error(agResult.error || "Failed to load risk results");

      const itText = await itRes.text();
      let itResult = {};
      try { itResult = itText ? JSON.parse(itText) : {}; } catch { itResult = { rows: [] }; }
      const items = itRes.ok && Array.isArray(itResult.rows) ? itResult.rows : [];

      const dataRows = Array.isArray(agResult.rows) ? agResult.rows : [];
      setRows(dataRows);
      setItemRows(items);

      // Independent page state — no URL params shared with other pages.
      const currentAgency = riskMemory.agency || "";
      const matchedRow    = currentAgency ? dataRows.find(row => String(row.Agency) === String(currentAgency)) : null;
      const nextSelected  = matchedRow || (dataRows.length > 0 ? dataRows[0] : null);

      setSelected(nextSelected);
      setAgency(nextSelected ? String(nextSelected.Agency || "") : "");
      riskMemory = { agency: nextSelected ? String(nextSelected.Agency || "") : "", selected: nextSelected, rows: dataRows };
      pickFirstItemOfAgency(nextSelected?.Agency, items);
    } catch (err) {
      setError(err.message || "Failed to fetch results");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchResults(); }, []);

  const selectedRow = selected;
  const scenarioA   = selectedRow ? getScenarioStatus(selectedRow, "A") : null;
  const scenarioB   = selectedRow ? getScenarioStatus(selectedRow, "B") : null;
  const scenarioC   = selectedRow ? getScenarioStatus(selectedRow, "C") : null;

  /* ── SKU-wise section derived state ── */
  const agencyItems = useMemo(() => {
    const ag = selectedRow?.Agency;
    if (!ag) return [];
    return itemRows.filter(r => String(r.Agency || "") === String(ag));
  }, [itemRows, selectedRow]);

  const loadSku = (codeArg) => {
    const code = String(codeArg ?? skuQuery).trim();
    if (!code) return;
    const found = agencyItems.find(r => String(r.ItemCode) === code);
    if (found) { setSelectedItem(found); setSkuQuery(code); }
  };

  /* ── Page-level filters (independent of any other page) ── */
  const agencyNames = useMemo(
    () => [...new Set(rows.map(r => String(r.Agency || "")))].filter(Boolean).sort(),
    [rows]
  );

  const selectAgencyByName = (nameArg) => {
    const name = String(nameArg ?? agency).trim();
    if (!name) return;
    const key = name.toLowerCase();
    const row =
      rows.find(r => String(r.Agency || "").toLowerCase() === key) ||
      rows.find(r => String(r.Agency || "").toLowerCase().includes(key)) ||
      rows.find(r => String(r.AgencyCode || "").toLowerCase() === key);
    if (!row) return;
    setSelected(row);
    setAgency(String(row.Agency || ""));
    riskMemory = { ...riskMemory, agency: String(row.Agency || ""), selected: row };
    pickFirstItemOfAgency(row.Agency, itemRows);
  };

  const itemScenarioA = selectedItem ? getScenarioStatus(selectedItem, "A") : null;
  const itemScenarioB = selectedItem ? getScenarioStatus(selectedItem, "B") : null;
  const itemScenarioC = selectedItem ? getScenarioStatus(selectedItem, "C") : null;

  return (
    <div className="page-shell" style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.orange}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.red}0A, transparent 55%),
                   ${T.bg}`,
      color: T.text, padding: "26px 34px 40px", fontFamily: FONT_UI }}>
      <GlobalStyle />

      {/* ── Header (glass) ── */}
      <div className="ui-anim" style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 15 }}>
          <div className="hero-icon" style={{ width: 44, height: 44, background: `linear-gradient(135deg, ${T.orange}, ${T.red})`,
            borderRadius: 13, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, color: "#fff",
            boxShadow: `0 8px 20px -8px ${T.orange}AA` }}>⚡</div>
          <div>
            <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>
              Risk Engine Dashboard
            </div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.5,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              Inventory Projection &amp; Risk Analysis
            </h1>
          </div>
        </div>
        {/* ── Page-level filters — the Inventory page has its OWN agency
             and SKU selectors now (independent of the Forecast page) ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", background: T.card, border: `1px solid ${T.borderHi}`, borderRadius: 10, boxShadow: SHADOW_SM, padding: "9px 14px", gap: 8, width: 230 }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
            <input list="risk-agency-options" value={agency}
              onChange={e => { const v = e.target.value; setAgency(v); riskMemory = { ...riskMemory, agency: v }; }}
              onKeyDown={e => e.key === "Enter" && selectAgencyByName(agency)}
              placeholder={loading ? "Loading agencies..." : "Agency…"}
              style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 12.5, width: "100%", fontFamily: FONT_MONO }} />
            <datalist id="risk-agency-options">{agencyNames.map(a => <option key={a} value={a} />)}</datalist>
          </div>
          <button className="ui-btn" onClick={() => selectAgencyByName(agency)}
            style={{ background: `linear-gradient(135deg, ${T.orange}, ${T.orange}D9)`, border: "none", color: "#fff", fontWeight: 800, fontSize: 12, borderRadius: 10, padding: "10px 16px", cursor: "pointer", fontFamily: FONT_UI }}>
            Load Agency
          </button>
          <div style={{ display: "flex", alignItems: "center", background: T.card, border: `1px solid ${T.borderHi}`, borderRadius: 10, boxShadow: SHADOW_SM, padding: "9px 14px", gap: 8, width: 210 }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
            <input list="risk-sku-options" value={skuQuery} onChange={e => setSkuQuery(e.target.value)}
              onKeyDown={e => e.key === "Enter" && loadSku(skuQuery)}
              placeholder="SKU within agency…"
              style={{ background: "transparent", border: "none", outline: "none", color: T.text, fontSize: 12.5, width: "100%", fontFamily: FONT_MONO }} />
            <datalist id="risk-sku-options">
              {agencyItems.map(r => <option key={r.ItemCode} value={r.ItemCode}>{r.ProductName || ""}</option>)}
            </datalist>
          </div>
          <button className="ui-btn" onClick={() => loadSku(skuQuery)}
            style={{ background: `linear-gradient(135deg, ${T.red}, ${T.red}D9)`, border: "none", color: "#fff", fontWeight: 800, fontSize: 12, borderRadius: 10, padding: "10px 16px", cursor: "pointer", fontFamily: FONT_UI }}>
            Load SKU
          </button>
          <button className="ui-btn" onClick={runRiskEngine} disabled={running} style={{
            background: running ? T.subtle : `linear-gradient(135deg, ${T.orange}, ${T.red})`, border: "none",
            color: running ? T.muted : "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10,
            padding: "11px 20px", cursor: running ? "not-allowed" : "pointer",
            fontFamily: FONT_UI, boxShadow: running ? "none" : `0 8px 18px -8px ${T.orange}AA`,
            display: "inline-flex", alignItems: "center", gap: 8 }}>
            {running ? <><span className="ui-spinner" /> Running…</> : <>▶ Run Risk Engine</>}
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div style={{ background: T.red + "0D", border: `1px solid ${T.red}33`, borderLeft: `4px solid ${T.red}`,
          borderRadius: 12, padding: "13px 18px", color: T.red, marginBottom: 18, fontSize: 13, boxShadow: SHADOW_SM }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Summary badges: real risk verdicts ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        {[
          { label: "Safe",          count: summary.safe,         color: T.green,   key: "SAFE" },
          { label: "Under Risk",    count: summary.underRisk,    color: T.amber,   key: "UNDER_RISK" },
          { label: "WH Trade",      count: summary.whTrade,      color: T.orange,  key: "WH_TRADE_REQUIRED" },
          { label: "WH Inspection", count: summary.whInspection, color: T.red,     key: "WH_INSPECTION_REQUIRED" },
          { label: "WH Blocked",    count: summary.whBlocked,    color: T.crimson, key: "WH_BLOCKED_REQUIRED" },
          { label: "Critical",      count: summary.critical,     color: T.red,     key: "CRITICAL_STOCKOUT" },
          { label: "Total Agencies", count: rows.length,         color: T.blue,    key: "ALL" },
        ].map(({ label, count, color, key }) => (
          <SummaryBadge key={key} label={label} count={count} color={color}
            active={riskFilter === key}
            onClick={() => setRiskFilter(prev => prev === key ? "ALL" : key)} />
        ))}
      </div>

      {/* ── KPI strip
           FIXED: expanded from 4-column to 6-column grid to include
           Incoming Supply and Net Demand alongside the existing four cards.
           When no supply data exists for the selected SKU (incoming = 0),
           Net Demand equals Forecast Qty and the supply card reads "0 (none)".
      ── */}
      <div className="risk-kpis">
        <KpiCard
          title="Selected Agency"
          value={selectedRow?.Agency || "—"}
          subtitle={selectedRow?.SKU_Count != null ? `${formatNum(selectedRow.SKU_Count)} SKUs tracked` : "Focused agency"}
          accent={T.blue}
        />
        <KpiCard
          title="Forecast Qty (Gross)"
          value={selectedRow ? formatNum(selectedRow.Forecast_Qty) : "—"}
          subtitle={`Forecast month: ${selectedRow?.Forecast_Month || "—"}`}
          accent={T.teal}
        />
        <KpiCard
          title="Risk Level"
          value={selectedRow ? riskLabel(selectedRow.Risk_Level) : "—"}
          subtitle={`Base month: ${selectedRow?.Base_Month || "—"}`}
          accent={selectedRow ? riskColor(selectedRow.Risk_Level) : T.muted}
        />
        <KpiCard
          title="Scenario A Unmet"
          value={selectedRow ? formatNum(selectedRow.A_unmet) : "—"}
          subtitle="No-risk unmet demand"
          accent={selectedRow && toNumber(selectedRow.A_unmet) > 0 ? T.red : T.green}
        />
      </div>

      {/* ══ TOGGLE: SKU Detail & Scenario Analysis ════════════ */}
      <SectionToggle
        expanded={detailOpen}
        onToggle={() => setDetailOpen(o => !o)}
        accent={T.orange}
        label={{ show: "Show Agency Details & Scenario Analysis", hide: "Hide Agency Details & Scenario Analysis" }}
      />

      {/* ══ COLLAPSIBLE ═══════════════════════════════════════ */}
      <div className={`collapsible ${detailOpen ? "open" : "closed"}`}
        style={{ maxHeight: detailOpen ? "9999px" : "0px" }}>

        <div className="risk-main">
          {/* Left: SKU list */}
          <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, overflow: "hidden", boxShadow: SHADOW_MD }}>
            <div style={{ padding: "16px 18px", borderBottom: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 12, fontWeight: 900, color: T.text, marginBottom: 2 }}>Risk Results — Agency Wise</div>
              <div style={{ fontSize: 10, color: T.muted }}>Select an agency to inspect aggregated scenario details</div>
            </div>

            <div style={{ padding: "12px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
              {/* Agency text filter moved to the page header — this panel
                  keeps only the risk-level filter. */}
              <select value={riskFilter} onChange={e => setRiskFilter(e.target.value)}
                style={{ background: T.surface, color: T.text, border: `1px solid ${T.borderHi}`,
                  borderRadius: 10, padding: "8px 12px", outline: "none", fontSize: 12,
                  fontFamily: FONT_UI }}>
                <option value="ALL">All Risk Levels</option>
                <option value="SAFE">Safe</option>
                <option value="UNDER_RISK">Under Risk</option>
                <option value="WH_TRADE_REQUIRED">WH Trade Required</option>
                <option value="WH_INSPECTION_REQUIRED">Critical: WH Inspection Required</option>
                <option value="WH_BLOCKED_REQUIRED">Critical: WH Blocked Required</option>
                <option value="CRITICAL_STOCKOUT">Critical Stockout</option>
                <option value="NO_FORECAST_DATA">No Forecast Data</option>
                <option value="NO_INVENTORY_DATA">No Inventory Data</option>
                <option value="NO_DATA">No Data Available</option>
                <option value="NOT_TRACKED">Not Tracked (No Physical Code)</option>
              </select>
            </div>

            <div className="ui-scroll" style={{ maxHeight: 560, overflowY: "auto" }}>
              {loading ? (
                <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>Loading results…</div>
              ) : filteredRows.length === 0 ? (
                <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>No risk results found.</div>
              ) : (
                filteredRows.map((row, index) => {
                  const isActive = String(selectedRow?.Agency) === String(row.Agency);
                  return (
                    <div key={`${row.Agency}-${index}`}
                      onClick={() => { setSelected(row); setAgency(String(row.Agency || ""));
                        riskMemory = { ...riskMemory, agency: String(row.Agency || ""), selected: row };
                        pickFirstItemOfAgency(row.Agency, itemRows); }}
                      style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, cursor: "pointer",
                        background: isActive ? T.blue + "14" : "transparent",
                        borderLeft: isActive ? `3px solid ${T.blue}` : "3px solid transparent",
                        transition: "background 0.15s" }}
                      onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = T.surface; }}
                      onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "transparent"; }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
                        <span style={{ fontWeight: 900, fontSize: 13, color: T.text, fontFamily: FONT_MONO }}>{row.Agency}</span>
                        <RiskBadge level={row.Risk_Level} />
                      </div>
                      {(toNumber(row.Synthetic_Item_Count) > 0 || toNumber(row.No_Inventory_Item_Count) > 0 || toNumber(row.No_Forecast_Item_Count) > 0) && (
                        <div style={{ display: "flex", gap: 5, marginBottom: 8, flexWrap: "wrap" }}>
                          {toNumber(row.Synthetic_Item_Count) > 0 && (
                            <span style={{ fontSize: 8.5, fontWeight: 800, padding: "1px 7px", borderRadius: 999,
                              background: T.muted + "14", border: `1px solid ${T.muted}33`, color: T.muted,
                              textTransform: "uppercase", letterSpacing: 0.6 }}>{formatNum(row.Synthetic_Item_Count)} No Physical Code</span>
                          )}
                          {toNumber(row.No_Inventory_Item_Count) > 0 && (
                            <span style={{ fontSize: 8.5, fontWeight: 800, padding: "1px 7px", borderRadius: 999,
                              background: T.blue + "14", border: `1px solid ${T.blue}33`, color: T.blue,
                              textTransform: "uppercase", letterSpacing: 0.6 }}>{formatNum(row.No_Inventory_Item_Count)} No Inventory</span>
                          )}
                          {toNumber(row.No_Forecast_Item_Count) > 0 && (
                            <span style={{ fontSize: 8.5, fontWeight: 800, padding: "1px 7px", borderRadius: 999,
                              background: T.blue + "14", border: `1px solid ${T.blue}33`, color: T.blue,
                              textTransform: "uppercase", letterSpacing: 0.6 }}>{formatNum(row.No_Forecast_Item_Count)} No Forecast</span>
                          )}
                        </div>
                      )}
                      <div style={{ display: "flex", gap: 16 }}>
                        <div>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>Forecast Qty</div>
                          <div style={{ fontSize: 12, fontWeight: 700, fontFamily: FONT_MONO }}>{formatNum(row.Forecast_Qty)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>A Unmet</div>
                          <div style={{ fontSize: 12, fontWeight: 700, fontFamily: FONT_MONO,
                            color: toNumber(row.A_unmet) > 0 ? T.red : T.green }}>{formatNum(row.A_unmet)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>SKUs</div>
                          <div style={{ fontSize: 12, fontWeight: 700, fontFamily: FONT_MONO }}>{formatNum(row.SKU_Count)}</div>
                        </div>
                        <div style={{ marginLeft: "auto" }}>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>Base / Forecast</div>
                          <div style={{ fontSize: 10, color: T.muted }}>{row.Base_Month || "—"} → {row.Forecast_Month || "—"}</div>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Right: SKU detail */}
          <div>
            {!selectedRow ? (
              <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: 30,
                color: T.muted, fontSize: 13, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", minHeight: 200, gap: 10, boxShadow: SHADOW_SM }}>
                <div style={{ width: 64, height: 64, borderRadius: 18, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26, color: T.orange, background: `linear-gradient(135deg, ${T.orange}14, ${T.red}0D)`, border: `1px solid ${T.orange}22` }}>⚡</div>
                <div>Select an agency from the list to inspect aggregated scenario details.</div>
              </div>
            ) : (
              <>
                {/* SKU header */}
                <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "18px 20px", marginBottom: 14, boxShadow: SHADOW_MD, position: "relative", overflow: "hidden" }}>
                  <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.orange}, ${T.orange}22 60%, transparent)` }} />
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                    <div>
                      <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 4 }}>Agency Detail</div>
                      <div style={{ fontSize: 22, fontWeight: 900, color: T.text, fontFamily: FONT_MONO }}>
                        {selectedRow.Agency}
                        {selectedRow.AgencyCode ? <span style={{ fontSize: 12, color: T.muted, marginLeft: 8 }}>({selectedRow.AgencyCode})</span> : null}
                      </div>
                      <div style={{ fontSize: 10, color: T.muted, marginTop: 4 }}>
                        Base Month: <span style={{ color: T.text }}>{selectedRow.Base_Month || "—"}</span>
                        {" · "}
                        Forecast Month: <span style={{ color: T.text }}>{selectedRow.Forecast_Month || "—"}</span>
                        {" · "}
                        SKUs: <span style={{ color: T.text }}>{formatNum(selectedRow.SKU_Count)}</span>
                      </div>
                    </div>
                    <RiskBadge level={selectedRow.Risk_Level} />
                  </div>

                  {(toNumber(selectedRow.Synthetic_Item_Count) > 0 || toNumber(selectedRow.No_Inventory_Item_Count) > 0 || toNumber(selectedRow.No_Forecast_Item_Count) > 0) && (
                    <div style={{ marginBottom: 16, padding: "9px 13px", borderRadius: 10,
                      background: T.blue + "0D", border: `1px solid ${T.blue}2E`, color: T.blue,
                      fontSize: 11, fontWeight: 600, lineHeight: 1.5 }}>
                      Data coverage in this agency:{" "}
                      {toNumber(selectedRow.Synthetic_Item_Count) > 0 && `${formatNum(selectedRow.Synthetic_Item_Count)} item(s) with no physical code (budget placeholder — stock can't be tracked). `}
                      {toNumber(selectedRow.No_Inventory_Item_Count) > 0 && `${formatNum(selectedRow.No_Inventory_Item_Count)} item(s) with no inventory record this month (buckets counted as zero — may be a data gap). `}
                      {toNumber(selectedRow.No_Forecast_Item_Count) > 0 && `${formatNum(selectedRow.No_Forecast_Item_Count)} item(s) with no forecast this month (counted as zero demand).`}
                    </div>
                  )}

                  {/* FIXED: 6-cell grid — added Incoming Supply and Net Demand
                      so the supply deduction context is visible alongside scenario unmet values */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, flexWrap: "wrap" }}>
                    <StatCell label="Forecast Qty"      value={formatNum(selectedRow.Forecast_Qty)}  color={T.teal} />
                    <StatCell label="Scenario A Unmet"  value={formatNum(selectedRow.A_unmet)}        color={toNumber(selectedRow.A_unmet) > 0 ? T.red : T.green} />
                    <StatCell label="Scenario B Unmet"  value={formatNum(selectedRow.B_unmet)}        color={toNumber(selectedRow.B_unmet) > 0 ? T.red : T.green} />
                    <StatCell label="Scenario C Unmet"  value={formatNum(selectedRow.C_unmet)}        color={toNumber(selectedRow.C_unmet) > 0 ? T.red : T.green} />
                  </div>
                </div>

                <Divider label="Stock Buckets — Agency Total" />
                <div className="risk-stock-kpis">
                  <KpiCard title="DB No-Risk"      value={formatNum(selectedRow.Distributor_NoRisk_Qty)}   accent={T.green} />
                  <KpiCard title="DB Short Exp"    value={formatNum(selectedRow.Distributor_ShortExp_Qty)} accent={T.amber} />
                  <KpiCard title="DB Trade"        value={formatNum(selectedRow.Distributor_Trade_Qty)}    accent={T.teal} />
                  <KpiCard title="DB Expired"      value={formatNum(selectedRow.Distributor_Expired_Qty)}  accent={T.red} />
                  <KpiCard title="WH No-Risk"      value={formatNum(selectedRow.Primary_NoRisk_Qty)}       accent={T.green} />
                  <KpiCard title="WH Short Exp"    value={formatNum(selectedRow.Primary_ShortExp_Qty)}     accent={T.amber} />
                  <KpiCard title="WH Trade"        value={formatNum(selectedRow.Primary_Trade_Qty)}        accent={T.blue} />
                  <KpiCard title="WH Inspection"   value={formatNum(selectedRow.Inspection_Stock_Qty)}     accent={T.orange} />
                  <KpiCard title="WH Blocked"      value={formatNum(selectedRow.Blocked_Stock_Qty)}        accent={T.crimson} />
                  <KpiCard title="WH Expired"      value={formatNum(selectedRow.Primary_Expired_Qty)}      accent={T.red} />
                </div>

                <Divider label="Scenario Analysis — Agency Aggregate" />
                {isDataGap(selectedRow.Risk_Level) ? (
                  <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16,
                    padding: "26px 20px", color: T.muted, fontSize: 12, textAlign: "center", boxShadow: SHADOW_SM }}>
                    Scenario analysis isn't meaningful for this agency yet — {riskLabel(selectedRow.Risk_Level).toLowerCase()}.
                  </div>
                ) : (
                  <div className="risk-scenarios">
                    <ScenarioCard title="Scenario A" tag="DB No-Risk Only"
                      step="A" scenario={scenarioA} accent={T.green}
                      isActive={selectedRow.Risk_Level === "SAFE"} showWH={false} />
                    <ScenarioCard title="Scenario B" tag="DB Trade"
                      step="B" scenario={scenarioB} accent={T.amber}
                      isActive={selectedRow.Risk_Level === "UNDER_RISK"} showWH={false} />
                    <ScenarioCard title="Scenario C" tag="DB + WH Stock"
                      step="C" scenario={scenarioC}
                      accent={selectedRow.Risk_Level === "CRITICAL_STOCKOUT" ? T.red : T.orange}
                      isActive={["WH_TRADE_REQUIRED","WH_INSPECTION_REQUIRED","WH_BLOCKED_REQUIRED","CRITICAL_STOCKOUT"].includes(selectedRow.Risk_Level)}
                      showWH={true} />
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ══ SKU-WISE SECTION — items within the selected agency ══
           Same pattern as the Forecast page: agency overview above,
           per-SKU detail below. This is the ORIGINAL item-wise view
           (stock buckets + scenario A/B/C with flags/reasoning) —
           no logic changed, just scoped to the selected agency. ══ */}
      {selectedRow && (
        <>
          <Divider label={`SKU Detail — ${selectedRow.Agency}`} />

          {/* Subheading — item name + code (SKU filter lives in the page header) */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 9, color: T.blue, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>SKU Detail — {selectedRow.Agency}</div>
              <div style={{ fontSize: 16, fontWeight: 900, color: T.text }}>
                {selectedItem ? (selectedItem.ProductName || `SKU ${selectedItem.ItemCode}`) : "Select a SKU"}
              </div>
              <div style={{ fontSize: 10.5, color: T.muted, marginTop: 2, fontFamily: FONT_MONO }}>
                {selectedItem ? selectedItem.ItemCode : "—"}
                {agencyItems.length > 0 && <span>  ·  {agencyItems.length} SKUs in agency</span>}
              </div>
            </div>
          </div>

          {!selectedItem ? (
            <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: 26,
              color: T.muted, fontSize: 12.5, textAlign: "center", boxShadow: SHADOW_SM }}>
              No risk rows found for this agency's SKUs yet. Run the risk engine first.
            </div>
          ) : (
            <>
              {/* SKU header — original item-wise detail */}
              <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16, padding: "18px 20px", marginBottom: 14, boxShadow: SHADOW_MD, position: "relative", overflow: "hidden" }}>
                <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: `linear-gradient(90deg, ${T.orange}, ${T.orange}22 60%, transparent)` }} />
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                  <div>
                    <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 900, marginBottom: 4 }}>SKU Detail</div>
                    <div style={{ fontSize: 22, fontWeight: 900, color: T.text, fontFamily: FONT_MONO }}>
                      {selectedItem.ItemCode}
                      {selectedItem.ProductName && <span style={{ fontSize: 12, color: T.muted, marginLeft: 10, fontFamily: FONT_UI, fontWeight: 600 }}>{selectedItem.ProductName}</span>}
                    </div>
                    <div style={{ fontSize: 10, color: T.muted, marginTop: 4 }}>
                      Base Month: <span style={{ color: T.text }}>{selectedItem.Base_Month || "—"}</span>
                      {" · "}
                      Forecast Month: <span style={{ color: T.text }}>{selectedItem.Forecast_Month || "—"}</span>
                    </div>
                  </div>
                  <RiskBadge level={selectedItem.Risk_Level} />
                </div>

                {(selectedItem.Is_Synthetic_Code === 1 || selectedItem.Has_Inventory_Data === 0 || selectedItem.Has_Forecast_Data === 0) && (
                  <div style={{ marginBottom: 16, padding: "9px 13px", borderRadius: 10,
                    background: T.blue + "0D", border: `1px solid ${T.blue}2E`, color: T.blue,
                    fontSize: 11, fontWeight: 600, lineHeight: 1.5 }}>
                    {selectedItem.Is_Synthetic_Code === 1
                      ? "This SKU has no real ItemCode yet (budget placeholder) — physical stock can't be tracked, so no risk assessment applies."
                      : selectedItem.Has_Inventory_Data === 0 && selectedItem.Has_Forecast_Data === 0
                      ? "No inventory record and no forecast found for this SKU this month — nothing to assess yet."
                      : selectedItem.Has_Inventory_Data === 0
                      ? "No inventory record found for this SKU this month. Stock buckets below are shown as zero, but this may reflect a data gap rather than confirmed zero stock."
                      : "No forecast found for this SKU this month — scenario results below assume zero demand."}
                  </div>
                )}

                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, flexWrap: "wrap" }}>
                  <StatCell label="Forecast Qty"      value={formatNum(selectedItem.Forecast_Qty)} color={T.teal} />
                  <StatCell label="Scenario A Unmet"  value={formatNum(selectedItem.A_unmet)}      color={toNumber(selectedItem.A_unmet) > 0 ? T.red : T.green} />
                  <StatCell label="Scenario B Unmet"  value={formatNum(selectedItem.B_unmet)}      color={toNumber(selectedItem.B_unmet) > 0 ? T.red : T.green} />
                  <StatCell label="Scenario C Unmet"  value={formatNum(selectedItem.C_unmet)}      color={toNumber(selectedItem.C_unmet) > 0 ? T.red : T.green} />
                </div>
              </div>

              <Divider label="Stock Buckets — SKU" />
              <div className="risk-stock-kpis">
                <KpiCard title="DB No-Risk"    value={formatNum(selectedItem.Distributor_NoRisk_Qty)}   accent={T.green} />
                <KpiCard title="DB Short Exp"  value={formatNum(selectedItem.Distributor_ShortExp_Qty)} accent={T.amber} />
                <KpiCard title="DB Trade"      value={formatNum(selectedItem.Distributor_Trade_Qty)}    accent={T.teal} />
                <KpiCard title="DB Expired"    value={formatNum(selectedItem.Distributor_Expired_Qty)}  accent={T.red} />
                <KpiCard title="WH No-Risk"    value={formatNum(selectedItem.Primary_NoRisk_Qty)}       accent={T.green} />
                <KpiCard title="WH Short Exp"  value={formatNum(selectedItem.Primary_ShortExp_Qty)}     accent={T.amber} />
                <KpiCard title="WH Trade"      value={formatNum(selectedItem.Primary_Trade_Qty)}        accent={T.blue} />
                <KpiCard title="WH Inspection" value={formatNum(selectedItem.Inspection_Stock_Qty)}     accent={T.orange} />
                <KpiCard title="WH Blocked"    value={formatNum(selectedItem.Blocked_Stock_Qty)}        accent={T.crimson} />
                <KpiCard title="WH Expired"    value={formatNum(selectedItem.Primary_Expired_Qty)}      accent={T.red} />
              </div>

              <Divider label="Scenario Analysis — SKU" />
              {isDataGap(selectedItem.Risk_Level) ? (
                <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 16,
                  padding: "26px 20px", color: T.muted, fontSize: 12, textAlign: "center", boxShadow: SHADOW_SM }}>
                  Scenario analysis isn't meaningful for this SKU yet — {riskLabel(selectedItem.Risk_Level).toLowerCase()}.
                </div>
              ) : (
                <div className="risk-scenarios">
                  <ScenarioCard title="Scenario A" tag="DB No-Risk Only"
                    step="A" scenario={itemScenarioA} accent={T.green}
                    isActive={selectedItem.Risk_Level === "SAFE"} showWH={false} />
                  <ScenarioCard title="Scenario B" tag="DB Trade"
                    step="B" scenario={itemScenarioB} accent={T.amber}
                    isActive={selectedItem.Risk_Level === "UNDER_RISK"} showWH={false} />
                  <ScenarioCard title="Scenario C" tag="DB + WH Stock"
                    step="C" scenario={itemScenarioC}
                    accent={selectedItem.Risk_Level === "CRITICAL_STOCKOUT" ? T.red : T.orange}
                    isActive={["WH_TRADE_REQUIRED","WH_INSPECTION_REQUIRED","WH_BLOCKED_REQUIRED","CRITICAL_STOCKOUT"].includes(selectedItem.Risk_Level)}
                    showWH={true} />
                </div>
              )}
            </>
          )}
        </>
      )}

      {/* ── DATA COVERAGE — moved to the BOTTOM of the page ──
           Data-completeness gaps (not a risk verdict). Clicking a badge
           still filters the agency list above. ── */}
      <Divider label="Data Coverage" />
      <div style={{ display: "flex", gap: 8, marginBottom: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800, marginRight: 2 }}>
          Data Coverage
        </span>
        {[
          { label: "No Forecast",  count: summary.noForecast,  color: T.blue,  key: "NO_FORECAST_DATA" },
          { label: "No Inventory", count: summary.noInventory, color: T.blue,  key: "NO_INVENTORY_DATA" },
          { label: "No Data",      count: summary.noData,      color: T.muted, key: "NO_DATA" },
          { label: "Not Tracked",  count: summary.notTracked,  color: T.muted, key: "NOT_TRACKED" },
        ].map(({ label, count, color, key }) => (
          <SummaryBadge key={key} label={label} count={count} color={color}
            active={riskFilter === key}
            onClick={() => setRiskFilter(prev => prev === key ? "ALL" : key)} />
        ))}
      </div>
    </div>
  );
}