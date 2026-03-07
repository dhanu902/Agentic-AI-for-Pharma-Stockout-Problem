import React, { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

const API_BASE = "/api/risk";

/* ─── Tokens (matches app theme) ────────────────────────────── */
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

/* ─── Risk config ────────────────────────────────────────────── */
const RISK_CONFIG = {
  NO_RISK:               { color: T.green,  label: "No Risk",               icon: "✓" },
  SHORT_EXPIRY_REQUIRED: { color: T.amber,  label: "Short Expiry Required", icon: "⚠" },
  USABLE_STOCK_REQUIRED: { color: T.orange, label: "Usable Stock Required", icon: "⚡" },
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

/* ─── Helpers ───────────────────── */
function parseJsonArray(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}

function riskLabel(level) {
  return getRisk(level).label;
}

function riskColor(level) {
  return getRisk(level).color;
}

function toNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function formatNum(value) {
  return toNumber(value).toLocaleString();
}

function getScenarioStatus(row, prefix) {
  return {
    met: row?.[`${prefix}_met`] === true || row?.[`${prefix}_met`] === "true" || row?.[`${prefix}_met`] === 1,
    unmet: toNumber(row?.[`${prefix}_unmet`]),
    used_dist: toNumber(row?.[`${prefix}_used_dist`]),
    used_trade: toNumber(row?.[`${prefix}_used_trade`]),
    used_insp: row?.[`${prefix}_used_insp`] !== undefined ? toNumber(row?.[`${prefix}_used_insp`]) : undefined,
    used_block: row?.[`${prefix}_used_block`] !== undefined ? toNumber(row?.[`${prefix}_used_block`]) : undefined,
    flags: parseJsonArray(row?.[`${prefix}_flags`]),
    reasoning: parseJsonArray(row?.[`${prefix}_reasoning`]),
  };
}

/* ─── KPI Card ───────────────────────────────────────────────── */
function KpiCard({ title, value, subtitle, accent }) {
  const col = accent || T.blue;
  return (
    <div style={{
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 10,
      padding: "16px 18px", position: "relative", overflow: "hidden",
      transition: "border-color 0.2s",
    }}
      onMouseEnter={e => e.currentTarget.style.borderColor = col + "66"}
      onMouseLeave={e => e.currentTarget.style.borderColor = T.border}
    >
      <div style={{ position: "absolute", top: -20, right: -20, width: 80, height: 80, background: col, borderRadius: "50%", opacity: 0.05, filter: "blur(20px)", pointerEvents: "none" }} />
      <div style={{ fontSize: 10, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace", lineHeight: 1, marginBottom: 6 }}>{value}</div>
      {subtitle && <div style={{ fontSize: 11, color: T.muted, marginTop: 4 }}>{subtitle}</div>}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, ${col}, transparent)` }} />
    </div>
  );
}

/* ─── Risk Badge ─────────────────────────────────────────────── */
function RiskBadge({ level }) {
  const cfg = getRisk(level);
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "4px 10px",
      background: cfg.color + "18",
      border: `1px solid ${cfg.color}44`,
      color: cfg.color,
      borderRadius: 6,
      fontWeight: 800, fontSize: 10,
      textTransform: "uppercase", letterSpacing: 0.8,
      whiteSpace: "nowrap",
    }}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

/* ─── Flag List ──────────────────────────────────────────────── */
function FlagList({ items }) {
  if (!items || items.length === 0)
    return <div style={{ color: T.muted, fontSize: 11 }}>No flags</div>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {items.map((flag, idx) => (
        <span key={idx} style={{
          padding: "2px 8px", borderRadius: 4,
          background: T.red + "18", border: `1px solid ${T.red}33`,
          color: T.red, fontSize: 9, fontWeight: 700,
          textTransform: "uppercase", letterSpacing: 0.8,
        }}>
          {flag.replace(/_/g, " ")}
        </span>
      ))}
    </div>
  );
}

/* ─── Reason List ────────────────────────────────────────────── */
function ReasonList({ items }) {
  if (!items || items.length === 0)
    return <div style={{ color: T.muted, fontSize: 11 }}>No reasoning available</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      {items.map((item, idx) => (
        <div key={idx} style={{
          fontSize: 10, color: T.muted, lineHeight: 1.6,
          fontFamily: "'JetBrains Mono', monospace",
          paddingBottom: idx < items.length - 1 ? 5 : 0,
          borderBottom: idx < items.length - 1 ? `1px solid ${T.border}` : "none",
        }}>{item}</div>
      ))}
    </div>
  );
}

/* ─── Stat Cell ──────────────────────────────────────────────── */
function StatCell({ label, value, color }) {
  return (
    <div style={{ background: T.surface, border: `1px solid ${T.border}`, borderRadius: 8, padding: "10px 12px" }}>
      <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 800, color: color || T.text, fontFamily: "'JetBrains Mono', monospace" }}>{value}</div>
    </div>
  );
}

/* ─── Scenario Card ──────────────────────────────────────────── */
function ScenarioCard({ title, tag, step, scenario, accent, isActive }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div style={{
      background: T.card,
      border: `1px solid ${isActive ? accent + "66" : T.border}`,
      borderTop: `2px solid ${isActive ? accent : T.border}`,
      borderRadius: 12, padding: "18px 20px",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
            <div style={{
              width: 22, height: 22, borderRadius: 6,
              background: accent + "22", border: `1px solid ${accent}44`,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 10, fontWeight: 900, color: accent,
            }}>{step}</div>
            <span style={{ fontSize: 11, fontWeight: 800, color: T.text }}>{title}</span>
          </div>
          {tag && <div style={{ fontSize: 9, color: T.muted, letterSpacing: 1, textTransform: "uppercase" }}>{tag}</div>}
        </div>
        <div style={{
          padding: "3px 8px", borderRadius: 5, fontSize: 10, fontWeight: 800,
          background: scenario.met ? T.green + "18" : T.red + "18",
          border: `1px solid ${scenario.met ? T.green : T.red}44`,
          color: scenario.met ? T.green : T.red,
        }}>
          {scenario.met ? "✓ Met" : "✕ Unmet"}
        </div>
      </div>

      {/* Stats grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
        <StatCell label="Unmet Demand" value={formatNum(scenario.unmet)}     color={toNumber(scenario.unmet) > 0 ? T.red : T.green} />
        <StatCell label="Used Dist."   value={formatNum(scenario.used_dist)} color={T.teal} />
        <StatCell label="Used Trade"   value={formatNum(scenario.used_trade)} color={T.blue} />
        {scenario.used_insp  !== undefined && <StatCell label="Used Insp."  value={formatNum(scenario.used_insp)}  color={T.amber} />}
        {scenario.used_block !== undefined && <StatCell label="Used Block"  value={formatNum(scenario.used_block)} color={T.orange} />}
      </div>

      {/* Flags */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>Flags</div>
        <FlagList items={scenario.flags} />
      </div>

      {/* Reasoning toggle */}
      {scenario.reasoning?.length > 0 && (
        <>
          <button
            onClick={() => setExpanded(e => !e)}
            style={{
              background: "none", border: `1px solid ${T.border}`, color: T.muted,
              borderRadius: 6, padding: "5px 10px", fontSize: 10, cursor: "pointer",
              fontFamily: "'IBM Plex Sans', sans-serif", fontWeight: 600, width: "100%",
              transition: "border-color 0.15s, color 0.15s",
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = accent; e.currentTarget.style.color = accent; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = T.border; e.currentTarget.style.color = T.muted; }}
          >
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

/* ─── Divider ────────────────────────────────────────────────── */
const Divider = ({ label }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "20px 0 14px" }}>
    <div style={{ flex: 1, height: 1, background: T.border }} />
    {label && <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 2, fontWeight: 700 }}>{label}</span>}
    <div style={{ flex: 1, height: 1, background: T.border }} />
  </div>
);

/* ─── Summary Badge ──────────────────────────────────────────── */
function SummaryBadge({ label, count, color, onClick, active }) {
  return (
    <button onClick={onClick} style={{
      background: active ? color + "18" : T.card,
      border: `1px solid ${active ? color + "55" : T.border}`,
      borderRadius: 8, padding: "8px 14px",
      cursor: "pointer", transition: "all 0.15s",
      display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
    }}>
      <span style={{ fontSize: 18, fontWeight: 900, color: active ? color : T.text, fontFamily: "'JetBrains Mono', monospace" }}>{count}</span>
      <span style={{ fontSize: 9, color: active ? color : T.muted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>{label}</span>
    </button>
  );
}

/* ─── Main ───────────────────────────────────────────────────── */
export default function RiskPage() {
  /* ── state — ALL IDENTICAL TO ORIGINAL ── */
  const [searchParams] = useSearchParams();
  const [sku, setSku] = useState(riskMemory.sku || "");
  const [rows, setRows]           = useState([]);
  const [loading, setLoading]     = useState(false);
  const [running, setRunning]     = useState(false);
  const [error, setError]         = useState("");
  const [selected, setSelected]   = useState(null);
  const [riskFilter, setRiskFilter] = useState("ALL");

  /* ── derived — ALL IDENTICAL TO ORIGINAL ── */
  const filteredRows = useMemo(() => {
    return rows.filter((row) => {
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

  /* ── API calls — ALL IDENTICAL TO ORIGINAL ── */
  const runRiskEngine = async () => {
    setRunning(true);
    setError("");
    try {
      const url = `${API_BASE}/run`;
      console.log("RUN URL:", url);
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } });
      const text = await res.text();
      console.log("RUN STATUS:", res.status);
      console.log("RUN RAW RESPONSE:", text);
      let result = {};
      try { result = text ? JSON.parse(text) : {}; }
      catch { throw new Error("Backend did not return valid JSON for /run"); }
      if (!res.ok || !result.ok) throw new Error(result.error || "Risk engine failed");
      await fetchResults();
    } catch (err) {
      console.error("runRiskEngine error:", err);
      setError(err.message || "Failed to run risk engine");
    } finally {
      setRunning(false);
    }
  };

  const fetchResults = async () => {
    setLoading(true);
    setError("");
    try {
      const url = `${API_BASE}/results`;
      console.log("RESULTS URL:", url);
      const res = await fetch(url);
      const text = await res.text();
  
      console.log("RESULTS STATUS:", res.status);
      console.log("RESULTS RAW RESPONSE:", text);
  
      let result = {};
      try {
        result = text ? JSON.parse(text) : {};
      } catch {
        throw new Error("Backend did not return valid JSON for /results");
      }
  
      if (!res.ok) throw new Error(result.error || "Failed to load risk results");
  
      const dataRows = Array.isArray(result.rows) ? result.rows : [];
      setRows(dataRows);
  
      const urlSku = searchParams.get("sku") || "";
      const currentSku = urlSku || riskMemory.sku || "";
  
      const matchedRow = currentSku
        ? dataRows.find(row => String(row.ItemCode) === String(currentSku))
        : null;
  
      const nextSelected = matchedRow || (dataRows.length > 0 ? dataRows[0] : null);
      setSelected(nextSelected);
  
      riskMemory = {
        sku: currentSku,
        selected: nextSelected,
        rows: dataRows,
      };
  
      if (urlSku && !matchedRow && dataRows.length > 0) {
        setError(`SKU ${urlSku} was not found in risk results. Showing first available result.`);
      }
    } catch (err) {
      console.error("fetchResults error:", err);
      setError(err.message || "Failed to fetch results");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchResults(); }, []);

  /* ── scenario data — IDENTICAL TO ORIGINAL ── */
  const selectedRow = selected;
  const scenarioA = selectedRow ? getScenarioStatus(selectedRow, "A") : null;
  const scenarioB = selectedRow ? getScenarioStatus(selectedRow, "B") : null;
  const scenarioC = selectedRow ? getScenarioStatus(selectedRow, "C") : null;

  /* ── render ── */
  return (
    <div style={{ minHeight: "100vh", background: T.bg, color: T.text, padding: "28px 32px", fontFamily: "'IBM Plex Sans', sans-serif" }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      <style>{`
        .inv-main { display: grid; grid-template-columns: 380px 1fr; gap: 16px; }
        .inv-scenarios { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
        .inv-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
        @media (max-width: 1100px) { .inv-main { grid-template-columns: 1fr !important; } }
        @media (max-width: 900px)  { .inv-scenarios { grid-template-columns: 1fr 1fr !important; } .inv-kpis { grid-template-columns: 1fr 1fr !important; } }
        @media (max-width: 600px)  { .inv-scenarios { grid-template-columns: 1fr !important; } .inv-kpis { grid-template-columns: 1fr 1fr !important; } }
      `}</style>

      {/* ── Header ── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 20, paddingBottom: 20, borderBottom: `1px solid ${T.border}`,
        flexWrap: "wrap", gap: 16,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{
            width: 36, height: 36,
            background: `linear-gradient(135deg, ${T.orange}, ${T.red})`,
            borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18,
          }}>⚡</div>
          <div>
            <div style={{ fontSize: 9, color: T.orange, letterSpacing: 3, textTransform: "uppercase", fontWeight: 700, marginBottom: 2 }}>
              Risk Engine Dashboard
            </div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: T.text, letterSpacing: -0.5 }}>
              Inventory Projection & Risk Analysis
            </h1>
          </div>
        </div>
        <button
          onClick={runRiskEngine}
          disabled={running}
          style={{
            background: running ? T.subtle : T.orange,
            border: "none", color: running ? T.muted : "#fff",
            fontWeight: 800, fontSize: 13, borderRadius: 10,
            padding: "10px 20px", cursor: running ? "not-allowed" : "pointer",
            transition: "background 0.2s", fontFamily: "'IBM Plex Sans', sans-serif",
          }}
        >
          {running ? "Running…" : "▶ Run Risk Engine"}
        </button>
      </div>

      {/* ── Error ── */}
      {error && (
        <div style={{
          background: T.card, border: `1px solid ${T.red}44`, borderLeft: `3px solid ${T.red}`,
          borderRadius: 10, padding: "12px 16px", color: T.red, marginBottom: 18, fontSize: 13,
        }}>⚠ {error}</div>
      )}

      {/* ── Summary strip ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
        {[
          { label: "No Risk",       count: summary.noRisk,      color: T.green,  key: "NO_RISK" },
          { label: "Short Expiry",  count: summary.shortExpiry, color: T.amber,  key: "SHORT_EXPIRY_REQUIRED" },
          { label: "Usable Stock",  count: summary.usable,      color: T.orange, key: "USABLE_STOCK_REQUIRED" },
          { label: "Critical",      count: summary.critical,    color: T.red,    key: "CRITICAL_STOCKOUT" },
          { label: "Total SKUs",    count: rows.length,         color: T.blue,   key: "ALL" },
        ].map(({ label, count, color, key }) => (
          <SummaryBadge
            key={key}
            label={label}
            count={count}
            color={color}
            active={riskFilter === key}
            onClick={() => setRiskFilter(prev => prev === key ? "ALL" : key)}
          />
        ))}
      </div>

      {/* ── Top KPIs for selected SKU ── */}
      <div className="inv-kpis">
        <KpiCard
          title="Selected SKU"
          value={selectedRow?.ItemCode || "—"}
          subtitle="Focused item"
          accent={T.blue}
        />
        <KpiCard
          title="Forecast Qty"
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

      {/* ── Main: list + detail ── */}
      <div className="inv-main">

        {/* Left: SKU List */}
        <div style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 12, overflow: "hidden" }}>

          {/* List header */}
          <div style={{ padding: "16px 18px", borderBottom: `1px solid ${T.border}` }}>
            <div style={{ fontSize: 12, fontWeight: 800, color: T.text, marginBottom: 2 }}>Risk Results</div>
            <div style={{ fontSize: 10, color: T.muted }}>Select a SKU to inspect scenario details</div>
          </div>

          {/* Search + filter */}
          <div style={{ padding: "12px 14px", borderBottom: `1px solid ${T.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{
              display: "flex", alignItems: "center",
              background: T.surface, border: `1px solid ${T.borderHi}`,
              borderRadius: 8, padding: "8px 12px", gap: 8,
            }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5">
                <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
              </svg>
              <input
                type="text"
                placeholder="Search ItemCode…"
                value={sku}
                onChange={e => {
                  const value = e.target.value;
                  setSku(value);
                  riskMemory = {
                    ...riskMemory,
                    sku: value,
                  };
                }}
                style={{
                  background: "transparent", border: "none", outline: "none",
                  color: T.text, fontSize: 12, width: "100%",
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              />
            </div>
            <select
              value={riskFilter}
              onChange={e => setRiskFilter(e.target.value)}
              style={{
                background: T.surface, color: T.text,
                border: `1px solid ${T.borderHi}`, borderRadius: 8,
                padding: "8px 12px", outline: "none", fontSize: 12,
                fontFamily: "'IBM Plex Sans', sans-serif",
              }}
            >
              <option value="ALL">All Risk Levels</option>
              <option value="NO_RISK">No Risk</option>
              <option value="SHORT_EXPIRY_REQUIRED">Short Expiry Required</option>
              <option value="USABLE_STOCK_REQUIRED">Usable Stock Required</option>
              <option value="CRITICAL_STOCKOUT">Critical Stockout</option>
            </select>
          </div>

          {/* SKU rows */}
          <div style={{ maxHeight: 560, overflowY: "auto" }}>
            {loading ? (
              <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>Loading results…</div>
            ) : filteredRows.length === 0 ? (
              <div style={{ padding: 18, color: T.muted, fontSize: 12 }}>No risk results found.</div>
            ) : (
              filteredRows.map((row, index) => {
                const isActive = String(selectedRow?.ItemCode) === String(row.ItemCode);
                const cfg = getRisk(row.Risk_Level);
                return (
                  <div
                    key={`${row.ItemCode}-${index}`}
                    onClick={() => {
                      setSelected(row);
                      setSku(String(row.ItemCode || ""));
                      riskMemory = {
                        ...riskMemory,
                        sku: String(row.ItemCode || ""),
                        selected: row,
                      };
                    }}
                    style={{
                      padding: "14px 16px",
                      borderBottom: `1px solid ${T.border}`,
                      cursor: "pointer",
                      background: isActive ? T.blue + "0f" : "transparent",
                      borderLeft: isActive ? `3px solid ${T.blue}` : "3px solid transparent",
                      transition: "background 0.15s",
                    }}
                    onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = T.surface; }}
                    onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "transparent"; }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
                      <span style={{ fontWeight: 800, fontSize: 13, color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>
                        {row.ItemCode}
                      </span>
                      <RiskBadge level={row.Risk_Level} />
                    </div>
                    <div style={{ display: "flex", gap: 16 }}>
                      <div>
                        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>Forecast Qty</div>
                        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" }}>{formatNum(row.Forecast_Qty)}</div>
                      </div>
                      <div>
                        <div style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 2 }}>A Unmet</div>
                        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "'JetBrains Mono', monospace", color: toNumber(row.A_unmet) > 0 ? T.red : T.green }}>
                          {formatNum(row.A_unmet)}
                        </div>
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

        {/* Right: Detail */}
        <div>
          {!selectedRow ? (
            <div style={{
              background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
              padding: 30, color: T.muted, fontSize: 13,
              display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
              minHeight: 200, gap: 10,
            }}>
              <div style={{ fontSize: 32, opacity: 0.3 }}>⚡</div>
              <div>Select a SKU from the list to inspect scenario details.</div>
            </div>
          ) : (
            <>
              {/* SKU header */}
              <div style={{
                background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
                padding: "18px 20px", marginBottom: 14,
              }}>
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

                {/* Unmet grid */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                  <StatCell label="Forecast Qty"    value={formatNum(selectedRow.Forecast_Qty)} color={T.blue} />
                  <StatCell label="Scenario A Unmet" value={formatNum(selectedRow.A_unmet)} color={toNumber(selectedRow.A_unmet) > 0 ? T.red : T.green} />
                  <StatCell label="Scenario B Unmet" value={formatNum(selectedRow.B_unmet)} color={toNumber(selectedRow.B_unmet) > 0 ? T.red : T.green} />
                  <StatCell label="Scenario C Unmet" value={formatNum(selectedRow.C_unmet)} color={toNumber(selectedRow.C_unmet) > 0 ? T.red : T.green} />
                </div>
              </div>

              {/* Scenario cards */}
              <Divider label="Scenario Analysis" />
              <div className="inv-scenarios">
                <ScenarioCard title="Scenario A" tag="No-Risk Stock Only"  step="A" scenario={scenarioA} accent={T.green}  isActive={selectedRow.Risk_Level === "NO_RISK"} />
                <ScenarioCard title="Scenario B" tag="Trade Stock Allowed" step="B" scenario={scenarioB} accent={T.amber}  isActive={selectedRow.Risk_Level === "SHORT_EXPIRY_REQUIRED"} />
                <ScenarioCard title="Scenario C" tag="Total Usable Stock"  step="C" scenario={scenarioC}
                  accent={selectedRow.Risk_Level === "CRITICAL_STOCKOUT" ? T.red : T.orange}
                  isActive={["USABLE_STOCK_REQUIRED", "CRITICAL_STOCKOUT"].includes(selectedRow.Risk_Level)}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}