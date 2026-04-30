import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

const API_BASE = "/api/risk";

/* ─── Tokens ────────────────────────────────────────────────── */
const T = {
  bg:       "#080c12",
  surface:  "#0e1420",
  card:     "#111827",
  panel:    "#0e1420",
  border:   "#1e2a3a",
  borderHi: "#2a3a52",
  text:     "#e2eaf6",
  muted:    "#4a6080",
  subtle:   "#243044",
  blue:     "#3b82f6",
  green:    "#22c55e",
  amber:    "#f59e0b",
  orange:   "#f97316",
  red:      "#ef4444",
  crimson:  "#991b1b",
  purple:   "#a78bfa",
  teal:     "#2dd4bf",
};


/* ─── Risk config ───────────────────────────────────────────── */
const RISK_CONFIG = {
  NO_RISK:               { color: T.green,  label: "Covered by No Risk",               icon: "✓" },
  SHORT_EXPIRY_REQUIRED: { color: T.amber,  label: "DB Short-Expiry Needed", icon: "⚠" },
  USABLE_STOCK_REQUIRED: { color: T.orange, label: "WH Stock Needed", icon: "⚡" },
  CRITICAL_STOCKOUT:     { color: T.red,    label: "Critical Stockout",     icon: "✕" },
};

function getRisk(level) {
  return RISK_CONFIG[level] || { color: T.muted, label: level || "Unknown", icon: "?" };
}

let riskMemory = {
  sku: "",
  selected: null,
  rows: [],
};

/* ─── Helpers — ALL IDENTICAL ───────────────────────────────── */
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

/* ─── KPI Card ──────────────────────────────────────────────── */
function KpiCard({ title, value, subtitle, accent }) {
  const col = accent || T.blue;
  return (
    <div
      style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 10,
        padding: "16px 18px", position: "relative", overflow: "hidden", transition: "border-color 0.2s" }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = col + "66")}
      onMouseLeave={e => (e.currentTarget.style.borderColor = T.border)}
    >
      <div style={{ position: "absolute", top: -20, right: -20, width: 80, height: 80,
        background: col, borderRadius: "50%", opacity: 0.05, filter: "blur(20px)", pointerEvents: "none" }} />
      <div style={{ fontSize: 10, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1, marginBottom: 6 }}>{value}</div>
      {subtitle && <div style={{ fontSize: 11, color: T.muted, marginTop: 4 }}>{subtitle}</div>}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, ${col}, transparent)` }} />
    </div>
  );
}

/* ─── Risk Badge ────────────────────────────────────────────── */
function RiskBadge({ level }) {
  const cfg = getRisk(level);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5,
      padding: "4px 10px", background: cfg.color + "18", border: `1px solid ${cfg.color}44`,
      color: cfg.color, borderRadius: 6, fontWeight: 800, fontSize: 10,
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
        <span key={idx} style={{ padding: "2px 8px", borderRadius: 4,
          background: T.red + "18", border: `1px solid ${T.red}33`,
          color: T.red, fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8 }}>
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
          fontFamily: "'JetBrains Mono', monospace",
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
    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "10px 12px" }}>
      <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 800, color: color || T.text, fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    </div>
  );
}

/* ─── Scenario Card ─────────────────────────────────────────── */
function ScenarioCard({ title, tag, step, scenario, accent, isActive, showWH = true }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{ background: T.card,
      border: `1px solid ${isActive ? accent + "66" : T.border}`,
      borderTop: `2px solid ${isActive ? accent : T.border}`,
      borderRadius: 12, padding: "18px 20px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <div style={{ width: 22, height: 22, borderRadius: 6,
              background: accent + "22", border: `1px solid ${accent}44`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 10, fontWeight: 900, color: accent }}>{step}</div>
            <span style={{ fontSize: 11, fontWeight: 800, color: T.text }}>{title}</span>
          </div>
          {tag && <div style={{ fontSize: 9, color: T.muted, letterSpacing: 1, textTransform: "uppercase" }}>{tag}</div>}
        </div>
        <div style={{ padding: "3px 8px", borderRadius: 5, fontSize: 10, fontWeight: 800,
          background: scenario.met ? T.green + "18" : T.red + "18",
          border: `1px solid ${scenario.met ? T.green : T.red}44`,
          color: scenario.met ? T.green : T.red }}>
          {scenario.met ? "✓ Met" : "✕ Unmet"}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
        <StatCell label="Unmet Demand" value={formatNum(scenario.unmet)} color={toNumber(scenario.unmet) > 0 ? T.red : T.green} />
        <StatCell label="DB No-Risk" value={formatNum(scenario.used_db_no_risk)} color={T.green} />
        <StatCell label="DB Short-Exp" value={formatNum(scenario.used_db_short_exp)} color={T.amber} />

        {showWH && (
          <>
            <StatCell label="WH Trade" value={formatNum(scenario.used_wh_trade)} color={T.blue} />
            <StatCell label="WH Insp." value={formatNum(scenario.used_wh_insp)} color={T.orange} />
            <StatCell label="WH Blocked" value={formatNum(scenario.used_wh_blocked)} color={T.red} />
          </>
        )}
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>Flags</div>
        <FlagList items={scenario.flags} />
      </div>

      {scenario.reasoning?.length > 0 && (
        <>
          <button onClick={() => setExpanded(e => !e)} style={{
            background: "none", border: `1px solid ${T.border}`, color: T.muted,
            borderRadius: 6, padding: "5px 10px", fontSize: 10, cursor: "pointer",
            fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600, width: "100%",
            transition: "border-color 0.15s, color 0.15s" }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = accent; e.currentTarget.style.color = accent; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = T.border; e.currentTarget.style.color = T.muted; }}>
            {expanded ? "▲ Hide Reasoning" : "▼ Show Reasoning"}
          </button>
          {expanded && (
            <div style={{ marginTop: 10, background: "#060a10", border: `1px solid ${T.border}`, borderRadius: 8, padding: "10px 12px" }}>
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
  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "20px 0 14px" }}>
    <div style={{ flex: 1, height: 1, background: T.border }} />
    {label && <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 700 }}>{label}</span>}
    <div style={{ flex: 1, height: 1, background: T.border }} />
  </div>
);

/* ─── Summary Badge ─────────────────────────────────────────── */
function SummaryBadge({ label, count, color, onClick, active }) {
  return (
    <button onClick={onClick} style={{
      background: active ? color + "18" : T.card,
      border: `1px solid ${active ? color + "55" : T.border}`,
      borderRadius: 8, padding: "8px 14px", cursor: "pointer", transition: "all 0.15s",
      display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
      <span style={{ fontSize: 18, fontWeight: 900, color: active ? color : T.text, fontFamily: "'JetBrains Mono', monospace" }}>{count}</span>
      <span style={{ fontSize: 9, color: active ? color : T.muted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>{label}</span>
    </button>
  );
}

/* ─── Section Toggle ────────────────────────────────────────── */
function SectionToggle({ expanded, onToggle, label, accent }) {
  const col = accent || T.orange;
  return (
    <button
      onClick={onToggle}
      style={{
        display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
        width: "100%", padding: "10px 20px",
        background: expanded ? T.surface : T.card,
        border: `1px solid ${expanded ? col + "55" : T.border}`,
        borderRadius: 10, cursor: "pointer",
        color: expanded ? col : T.muted, fontSize: 12, fontWeight: 700,
        fontFamily: "'IBM Plex Sans', sans-serif",
        transition: "all 0.2s",
        marginBottom: expanded ? 16 : 0,
      }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = col + "88"; e.currentTarget.style.color = col; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = expanded ? col + "55" : T.border; e.currentTarget.style.color = expanded ? col : T.muted; }}
    >
      <div style={{ flex: 1, height: 1, background: expanded ? col + "33" : T.border }} />
      <span style={{ display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
        {expanded
          ? <><span style={{ fontSize: 10 }}>▲</span> {label?.hide || "Hide Details"}</>
          : <><span style={{ fontSize: 10 }}>▼</span> {label?.show || "Show Details"}</>
        }
      </span>
      <div style={{ flex: 1, height: 1, background: expanded ? col + "33" : T.border }} />
    </button>
  );
}

/* ─── Horizon Panel — logic IDENTICAL, UI improved ─────────── */
/* Table styles defined outside component to avoid recreation */
const thBase = {
  border: `1px solid ${T.border}`,
  background: T.surface,
  color: T.text,
  padding: "10px 12px",
  textAlign: "center",
  verticalAlign: "top",
  minWidth: 135,
  position: "sticky",
  top: 0,
  zIndex: 2,
};

const tdBase = {
  border: `1px solid ${T.border}`,
  padding: "10px 12px",
  textAlign: "right",
};

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
};

function HorizonPanel({ selectedSku, rows, loading, running, onRun }) {
  const skuRows = useMemo(() => {
    if (!selectedSku) return [];
    return rows
      .filter(r => String(r.ItemCode) === String(selectedSku))
      .sort((a, b) => toNumber(a.Horizon) - toNumber(b.Horizon));
  }, [rows, selectedSku]);

  const monthCols = skuRows.slice(0, 6);

  const metricRows = [
    ["Sec Sales / Forecast", "Forecast_Qty"],
    ["DB No Risk",           "Distributor_NoRisk_Qty"],
    ["DB Short Exp",         "Distributor_ShortExp_Qty"],
    ["WH Trade",             "Primary_Trade_Qty"],
    ["WH Insp",              "Inspection_Stock_Qty"],
    ["WH Block",             "Blocked_Stock_Qty"],
    ["Incoming Qty",         "Incoming_Qty"],
    ["Opening Stock",        "Opening_Total_Stock"],
    ["Closing Stock",        "Closing_Total_Stock"],
    ["Unmet Qty",            "Unmet_Qty"],
  ];

  /* zebra-stripe alternating rows */
  const rowBg = (idx) => idx % 2 === 0 ? T.card : T.surface + "99";

  return (
    <div style={{
      background: T.card,
      border: `1px solid ${T.border}`,
      borderTop: `2px solid ${T.purple}`,
      borderRadius: 12,
      padding: "18px 20px",
    }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3, textTransform: "uppercase", fontWeight: 800, marginBottom: 4 }}>
            Horizon Projection
          </div>
          <div style={{ fontSize: 15, fontWeight: 900, color: T.text }}>
            6-Month Inventory Projection
          </div>
          <div style={{ fontSize: 10, color: T.muted, marginTop: 3 }}>
            M+1 uses model forecast. M+2 to M+6 currently use repeated forecast until true horizon model is added.
          </div>
        </div>
        <button
          onClick={onRun}
          disabled={running}
          style={{
            background: running ? T.subtle : T.purple,
            color: running ? T.muted : "#fff",
            border: "none", borderRadius: 10,
            padding: "9px 16px", fontSize: 12, fontWeight: 800,
            cursor: running ? "not-allowed" : "pointer",
            fontFamily: "'IBM Plex Sans', sans-serif",
            display: "flex", alignItems: "center", gap: 6,
          }}
        >
          {running && (
            <>
              <style>{`@keyframes hspin { to { transform: rotate(360deg); } }`}</style>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                style={{ animation: "hspin 0.7s linear infinite", flexShrink: 0 }}>
                <circle cx="12" cy="12" r="9" stroke="#fff" strokeOpacity="0.3" strokeWidth="3" />
                <path d="M12 3a9 9 0 0 1 9 9" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
              </svg>
            </>
          )}
          {running ? "Running…" : "▶ Run Horizon Engine"}
        </button>
      </div>

      {/* Body */}
      {loading ? (
        <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>Loading horizon projection…</div>
      ) : !selectedSku ? (
        <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>Select a SKU to view horizon projection.</div>
      ) : monthCols.length === 0 ? (
        <div style={{ color: T.muted, fontSize: 12, padding: 16 }}>
          No horizon data found for SKU {selectedSku}. Run horizon engine first.
        </div>
      ) : (
        <div style={{ overflowX: "auto", borderRadius: 8, border: `1px solid ${T.border}` }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
            <thead>
              <tr>
                {/* Sticky label column header */}
                <th style={{ ...thBase, textAlign: "left", left: 0, zIndex: 3, minWidth: 190 }}>
                  <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1 }}>KPI</span>
                </th>
                {monthCols.map(r => {
                  const riskCfg = getRisk(r.Risk_Level);
                  return (
                    <th key={r.Horizon} style={{
                      ...thBase,
                      background: riskCfg.color + "0d",           /* subtle risk tint */
                      borderTop: `2px solid ${riskCfg.color}`,
                    }}>
                      <div style={{ fontWeight: 900, color: T.text, marginBottom: 3 }}>M+{r.Horizon}</div>
                      <div style={{ color: T.muted, fontSize: 9, marginBottom: 6 }}>{r.Forecast_Month}</div>
                      <RiskBadge level={r.Risk_Level} />
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {metricRows.map(([label, key], rowIdx) => {
                const isUnmet   = key === "Unmet_Qty";
                const isClosing = key === "Closing_Total_Stock";
                const bg        = rowBg(rowIdx);
                return (
                  <tr key={key}>
                    <td style={{ ...tdLabelBase, background: bg }}>{label}</td>
                    {monthCols.map(r => {
                      const val = toNumber(r[key]);
                      const color =
                        isUnmet   && val > 0  ? T.red   :
                        isClosing && val <= 0 ? T.red   :
                        isClosing && val > 0  ? T.green :
                        T.text;
                      return (
                        <td key={`${r.Horizon}-${key}`} style={{ ...tdBase, background: bg, color }}>
                          {formatNum(r[key])}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ─── Main ──────────────────────────────────────────────────── */
export default function RiskPage() {
  const [searchParams] = useSearchParams();

  /* ── state — ALL IDENTICAL ── */
  const [sku, setSku]               = useState(riskMemory.sku || "");
  const [rows, setRows]             = useState([]);
  const [loading, setLoading]       = useState(false);
  const [running, setRunning]       = useState(false);
  const [error, setError]           = useState("");
  const [selected, setSelected]     = useState(null);
  const [riskFilter, setRiskFilter] = useState("ALL");

  const [detailOpen, setDetailOpen]   = useState(false);
  const [horizonOpen, setHorizonOpen] = useState(false);   /* NEW: own toggle for horizon panel */

  const [horizonRows, setHorizonRows]       = useState([]);
  const [horizonLoading, setHorizonLoading] = useState(false);
  const [horizonRunning, setHorizonRunning] = useState(false);

  const filteredRows = useMemo(() => {
    return rows.filter(row => {
      const matchesSku  = !sku || String(row.ItemCode || "").toLowerCase().includes(sku.toLowerCase());
      const matchesRisk = riskFilter === "ALL" || row.Risk_Level === riskFilter;
      return matchesSku && matchesRisk;
    });
  }, [rows, sku, riskFilter]);

  const summary = useMemo(() => ({
    noRisk:      rows.filter(r => r.Risk_Level === "NO_RISK").length,
    shortExpiry: rows.filter(r => r.Risk_Level === "SHORT_EXPIRY_REQUIRED").length,
    usable:      rows.filter(r => r.Risk_Level === "USABLE_STOCK_REQUIRED").length,
    critical:    rows.filter(r => r.Risk_Level === "CRITICAL_STOCKOUT").length,
  }), [rows]);

  /* ── API calls — ALL IDENTICAL ── */
  const runRiskEngine = async () => {
    setRunning(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/run`, { method: "POST", headers: { "Content-Type": "application/json" } });
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; }
      catch { throw new Error("Backend did not return valid JSON for /run"); }
      if (!res.ok || !result.ok) throw new Error(result.error || "Risk engine failed");
      await fetchResults();
    } catch (err) {
      setError(err.message || "Failed to run risk engine");
    } finally {
      setRunning(false);
    }
  };

  const fetchResults = async () => {
    setLoading(true);
    setError("");
    try {
      const res  = await fetch(`${API_BASE}/results`);
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; }
      catch { throw new Error("Backend did not return valid JSON for /results"); }
      if (!res.ok) throw new Error(result.error || "Failed to load risk results");

      const dataRows = Array.isArray(result.rows) ? result.rows : [];
      setRows(dataRows);

      const urlSku      = searchParams.get("sku") || "";
      const currentSku  = urlSku || riskMemory.sku || "";
      const matchedRow  = currentSku ? dataRows.find(row => String(row.ItemCode) === String(currentSku)) : null;
      const nextSelected = matchedRow || (dataRows.length > 0 ? dataRows[0] : null);

      setSelected(nextSelected);
      riskMemory = { sku: currentSku, selected: nextSelected, rows: dataRows };

      if (urlSku && !matchedRow && dataRows.length > 0)
        setError(`SKU ${urlSku} was not found in risk results. Showing first available result.`);
    } catch (err) {
      setError(err.message || "Failed to fetch results");
    } finally {
      setLoading(false);
    }
  };

  const runHorizonEngine = async () => {
    setHorizonRunning(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/horizon/run`, { method: "POST", headers: { "Content-Type": "application/json" } });
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; }
      catch { throw new Error("Backend did not return valid JSON for /horizon/run"); }
      if (!res.ok || !result.ok) throw new Error(result.error || "Horizon engine failed");
      await fetchHorizonResults();
    } catch (err) {
      setError(err.message || "Failed to run horizon engine");
    } finally {
      setHorizonRunning(false);
    }
  };

  const fetchHorizonResults = async () => {
    setHorizonLoading(true);
    try {
      const res  = await fetch(`${API_BASE}/horizon/results`);
      const text = await res.text();
      let result = {};
      try { result = text ? JSON.parse(text) : {}; }
      catch { throw new Error("Backend did not return valid JSON for /horizon/results"); }
      if (!res.ok) throw new Error(result.error || "Failed to load horizon results");
      setHorizonRows(Array.isArray(result.rows) ? result.rows : []);
    } catch (err) {
      console.warn("Horizon results not loaded:", err.message);
    } finally {
      setHorizonLoading(false);
    }
  };

  useEffect(() => {
    fetchResults();
    fetchHorizonResults();
  }, []);

  const selectedRow = selected;
  const scenarioA   = selectedRow ? getScenarioStatus(selectedRow, "A") : null;
  const scenarioB   = selectedRow ? getScenarioStatus(selectedRow, "B") : null;
  const scenarioC   = selectedRow ? getScenarioStatus(selectedRow, "C") : null;

  return (
    <div style={{ minHeight: "100vh", background: T.bg, color: T.text, padding: "28px 32px", fontFamily: "'IBM Plex Sans', sans-serif" }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      <style>{`
        .risk-main       { display: grid; grid-template-columns: 380px 1fr; gap: 16px; }
        .risk-scenarios  { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
        .risk-kpis       { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
        .risk-stock-kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 18px; }
        .collapsible {
          overflow: hidden;
          transition: max-height 0.4s cubic-bezier(0.4,0,0.2,1), opacity 0.3s ease;
        }
        .collapsible.open   { opacity: 1; }
        .collapsible.closed { opacity: 0; }
        @media (max-width: 1100px) { .risk-main { grid-template-columns: 1fr !important; } }
        @media (max-width: 900px) {
          .risk-scenarios  { grid-template-columns: 1fr 1fr !important; }
          .risk-kpis       { grid-template-columns: 1fr 1fr !important; }
          .risk-stock-kpis { grid-template-columns: 1fr 1fr !important; }
        }
        @media (max-width: 600px) {
          .risk-scenarios  { grid-template-columns: 1fr !important; }
          .risk-kpis       { grid-template-columns: 1fr 1fr !important; }
          .risk-stock-kpis { grid-template-columns: 1fr 1fr !important; }
        }
      `}</style>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 20, paddingBottom: 20, borderBottom: `1px solid ${T.border}`, flexWrap: "wrap", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ width: 36, height: 36, background: `linear-gradient(135deg, ${T.orange}, ${T.red})`,
            borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>⚡</div>
          <div>
            <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 700, marginBottom: 2 }}>
              Risk Engine Dashboard
            </div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: T.text, letterSpacing: -0.5 }}>
              Inventory Projection & Risk Analysis
            </h1>
          </div>
        </div>
        <button onClick={runRiskEngine} disabled={running} style={{
          background: running ? T.subtle : T.orange, border: "none",
          color: running ? T.muted : "#fff", fontWeight: 800, fontSize: 13, borderRadius: 10,
          padding: "10px 20px", cursor: running ? "not-allowed" : "pointer",
          transition: "background 0.2s", fontFamily: "'IBM Plex Sans', sans-serif" }}>
          {running ? "Running…" : "▶ Run Risk Engine"}
        </button>
      </div>

      {/* ── Error ── */}
      {error && (
        <div style={{ background: T.card, border: `1px solid ${T.red}44`, borderLeft: `3px solid ${T.red}`,
          borderRadius: 10, padding: "12px 16px", color: T.red, marginBottom: 18, fontSize: 13 }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Summary badges ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        {[
          { label: "No Risk",      count: summary.noRisk,      color: T.green,  key: "NO_RISK" },
          { label: "Short Expiry", count: summary.shortExpiry, color: T.amber,  key: "SHORT_EXPIRY_REQUIRED" },
          { label: "Usable Stock", count: summary.usable,      color: T.orange, key: "USABLE_STOCK_REQUIRED" },
          { label: "Critical",     count: summary.critical,    color: T.red,    key: "CRITICAL_STOCKOUT" },
          { label: "Total SKUs",   count: rows.length,         color: T.blue,   key: "ALL" },
        ].map(({ label, count, color, key }) => (
          <SummaryBadge key={key} label={label} count={count} color={color}
            active={riskFilter === key}
            onClick={() => setRiskFilter(prev => prev === key ? "ALL" : key)} />
        ))}
      </div>

      {/* ── KPI strip ── */}
      <div className="risk-kpis">
        <KpiCard title="Selected SKU"     value={selectedRow?.ItemCode || "—"} subtitle="Focused item" accent={T.blue} />
        <KpiCard title="Forecast Qty"     value={selectedRow ? formatNum(selectedRow.Forecast_Qty) : "—"}
          subtitle={`Forecast month: ${selectedRow?.Forecast_Month || "—"}`} accent={T.teal} />
        <KpiCard title="Risk Level"       value={selectedRow ? riskLabel(selectedRow.Risk_Level) : "—"}
          subtitle={`Base month: ${selectedRow?.Base_Month || "—"}`}
          accent={selectedRow ? riskColor(selectedRow.Risk_Level) : T.muted} />
        <KpiCard title="Scenario A Unmet" value={selectedRow ? formatNum(selectedRow.A_unmet) : "—"}
          subtitle="No-risk unmet demand"
          accent={selectedRow && toNumber(selectedRow.A_unmet) > 0 ? T.red : T.green} />
      </div>

      {/* ══ TOGGLE 1: SKU Detail & Scenario Analysis ════════════ */}
      <SectionToggle
        expanded={detailOpen}
        onToggle={() => setDetailOpen(o => !o)}
        accent={T.orange}
        label={{ show: "Show SKU Details & Scenario Analysis", hide: "Hide SKU Details & Scenario Analysis" }}
      />

      {/* ══ COLLAPSIBLE 1 ═══════════════════════════════════════ */}
      <div className={`collapsible ${detailOpen ? "open" : "closed"}`}
        style={{ maxHeight: detailOpen ? "9999px" : "0px" }}>

        <div className="risk-main">
          {/* Left: SKU list */}
          <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden" }}>
            <div style={{ padding: "16px 18px", borderBottom: `1px solid ${T.border}` }}>
              <div style={{ fontSize: 12, fontWeight: 800, color: T.text, marginBottom: 2 }}>Risk Results</div>
              <div style={{ fontSize: 10, color: T.muted }}>Select a SKU to inspect scenario details</div>
            </div>

            <div style={{ padding: "12px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", background: T.surface,
                border: `1px solid ${T.borderHi}`, borderRadius: 8, padding: "8px 12px", gap: 8 }}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5">
                  <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
                </svg>
                <input type="text" placeholder="Search ItemCode…" value={sku}
                  onChange={e => { const v = e.target.value; setSku(v); riskMemory = { ...riskMemory, sku: v }; }}
                  style={{ background: "transparent", border: "none", outline: "none",
                    color: T.text, fontSize: 12, width: "100%", fontFamily: "'JetBrains Mono', monospace" }} />
              </div>

              <select value={riskFilter} onChange={e => setRiskFilter(e.target.value)}
                style={{ background: T.surface, color: T.text, border: `1px solid ${T.borderHi}`,
                  borderRadius: 8, padding: "8px 12px", outline: "none", fontSize: 12,
                  fontFamily: "'IBM Plex Sans', sans-serif" }}>
                <option value="ALL">All Risk Levels</option>
                <option value="NO_RISK">No Risk</option>
                <option value="SHORT_EXPIRY_REQUIRED">Short Expiry Required</option>
                <option value="USABLE_STOCK_REQUIRED">Usable Stock Required</option>
                <option value="CRITICAL_STOCKOUT">Critical Stockout</option>
              </select>
            </div>

            <div style={{ maxHeight: 560, overflowY: "auto" }}>
              {loading ? (
                <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>Loading results…</div>
              ) : filteredRows.length === 0 ? (
                <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>No risk results found.</div>
              ) : (
                filteredRows.map((row, index) => {
                  const isActive = String(selectedRow?.ItemCode) === String(row.ItemCode);
                  return (
                    <div key={`${row.ItemCode}-${index}`}
                      onClick={() => { setSelected(row); setSku(String(row.ItemCode || ""));
                        riskMemory = { ...riskMemory, sku: String(row.ItemCode || ""), selected: row }; }}
                      style={{ padding: "14px 16px", borderBottom: `1px solid ${T.border}`, cursor: "pointer",
                        background: isActive ? T.blue + "0f" : "transparent",
                        borderLeft: isActive ? `3px solid ${T.blue}` : "3px solid transparent",
                        transition: "background 0.15s" }}
                      onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = T.surface; }}
                      onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "transparent"; }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
                        <span style={{ fontWeight: 800, fontSize: 13, color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>{row.ItemCode}</span>
                        <RiskBadge level={row.Risk_Level} />
                      </div>
                      <div style={{ display: "flex", gap: 16 }}>
                        <div>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>Forecast Qty</div>
                          <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatNum(row.Forecast_Qty)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>A Unmet</div>
                          <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace",
                            color: toNumber(row.A_unmet) > 0 ? T.red : T.green }}>{formatNum(row.A_unmet)}</div>
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
              <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: 30,
                color: T.muted, fontSize: 13, display: "flex", flexDirection: "column",
                alignItems: "center", justifyContent: "center", minHeight: 200, gap: 10 }}>
                <div style={{ fontSize: 32, opacity: 0.3 }}>⚡</div>
                <div>Select a SKU from the list to inspect scenario details.</div>
              </div>
            ) : (
              <>
                <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, padding: "18px 20px", marginBottom: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                    <div>
                      <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 700, marginBottom: 4 }}>SKU Detail</div>
                      <div style={{ fontSize: 22, fontWeight: 900, color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>{selectedRow.ItemCode}</div>
                      <div style={{ fontSize: 10, color: T.muted, marginTop: 4 }}>
                        Base Month: <span style={{ color: T.text }}>{selectedRow.Base_Month || "—"}</span>
                        {" · "}
                        Forecast Month: <span style={{ color: T.text }}>{selectedRow.Forecast_Month || "—"}</span>
                      </div>
                    </div>
                    <RiskBadge level={selectedRow.Risk_Level} />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                    <StatCell label="Forecast Qty"     value={formatNum(selectedRow.Forecast_Qty)} color={T.blue} />
                    <StatCell label="Scenario A Unmet" value={formatNum(selectedRow.A_unmet)} color={toNumber(selectedRow.A_unmet) > 0 ? T.red : T.green} />
                    <StatCell label="Scenario B Unmet" value={formatNum(selectedRow.B_unmet)} color={toNumber(selectedRow.B_unmet) > 0 ? T.red : T.green} />
                    <StatCell label="Scenario C Unmet" value={formatNum(selectedRow.C_unmet)} color={toNumber(selectedRow.C_unmet) > 0 ? T.red : T.green} />
                  </div>
                </div>

                <Divider label="Stock Buckets" />
                <div className="risk-stock-kpis">
                  <KpiCard title="DB No-Risk"   value={formatNum(selectedRow.Distributor_NoRisk_Qty)}   accent={T.green} />
                  <KpiCard title="DB Short Exp" value={formatNum(selectedRow.Distributor_ShortExp_Qty)} accent={T.amber} />
                  <KpiCard title="DB Trade"     value={formatNum(selectedRow.Distributor_Trade_Qty)}    accent={T.teal} />
                  <KpiCard title="DB Expired"   value={formatNum(selectedRow.Distributor_Expired_Qty)}  accent={T.red} />
                  
                  <KpiCard title="WH No-Risk"   value={formatNum(selectedRow.Primary_NoRisk_Qty)}       accent={T.green} />
                  <KpiCard title="WH Short Exp" value={formatNum(selectedRow.Primary_ShortExp_Qty)}     accent={T.amber} />
                  <KpiCard title="WH Trade"     value={formatNum(selectedRow.Primary_Trade_Qty)}        accent={T.blue} />          
                  <KpiCard title="WH Inspection"   value={formatNum(selectedRow.Inspection_Stock_Qty)}     accent={T.orange} />
                  <KpiCard title="WH Blocked"      value={formatNum(selectedRow.Blocked_Stock_Qty)}        accent={T.crimson} />
                  <KpiCard title="WH Expired"   value={formatNum(selectedRow.Primary_Expired_Qty)}      accent={T.red} />
                </div>

                <Divider label="Scenario Analysis" />
                <div className="risk-scenarios">
                  <ScenarioCard title="Scenario A" tag="Distributor No-Risk Only"             
                    step="A" scenario={scenarioA} accent={T.green}
                    isActive={selectedRow.Risk_Level === "NO_RISK"} showWH={false}/>
                  <ScenarioCard title="Scenario B" tag="Distributor Trade"   
                    step="B" scenario={scenarioB} accent={T.amber}
                    isActive={selectedRow.Risk_Level === "DB SHORT_EXPIRY_REQUIRED"} showWH={false}/>
                  <ScenarioCard title="Scenario C" tag="Distributor + Warehouse Stock" 
                    step="C" scenario={scenarioC}
                    accent={selectedRow.Risk_Level === "CRITICAL_STOCKOUT" ? T.red : T.orange}
                    isActive={["USABLE_STOCK_REQUIRED", "CRITICAL_STOCKOUT"].includes(selectedRow.Risk_Level)} showWH={true}/>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ══ TOGGLE 2: Horizon Projection ════════════════════════ */}
      <div style={{ marginTop: 16 }}>
        <SectionToggle
          expanded={horizonOpen}
          onToggle={() => setHorizonOpen(o => !o)}
          accent={T.purple}
          label={{ show: "Show 6-Month Horizon Projection", hide: "Hide 6-Month Horizon Projection" }}
        />
      </div>

      {/* ══ COLLAPSIBLE 2: Horizon ══════════════════════════════ */}
      <div className={`collapsible ${horizonOpen ? "open" : "closed"}`}
        style={{ maxHeight: horizonOpen ? "9999px" : "0px" }}>
        <HorizonPanel
          selectedSku={selectedRow?.ItemCode}
          rows={horizonRows}
          loading={horizonLoading}
          running={horizonRunning}
          onRun={runHorizonEngine}
        />
      </div>
    </div>
  );
}