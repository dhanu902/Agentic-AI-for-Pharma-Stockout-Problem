// src/pages/Recommendations.jsx
//
// AI Planner — UI v2, visually aligned with Insights.jsx.
// All data flow, filtering, tabs, search and pagination logic unchanged.
// Consumes GET /api/recommendation/results.
//
// Layout (planner-style — situation first, actions second):
//   1. Glass header + confidence pill + refresh
//   2. Planner summary: Forecast | Inventory | Licences | Coverage gauge
//   3. Action KPI strip
//   4. Factor coverage chips
//   5. Segmented action tabs + search -> paginated table (20/page)
import React, { useEffect, useMemo, useState } from "react";
import T from "../theme";

const API_URL = "/api/recommendation/results";
const PAGE_SIZE = 20;

/* ─── Helpers ────────────────────────────────────────────────── */
function toNumber(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function formatNum(v, d = 0) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return toNumber(v).toLocaleString(undefined, { maximumFractionDigits: d });
}
function fmtDec(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(d);
}

/* ─── Design tokens (UI only — matches Insights.jsx) ─────────── */
const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.05)";
const SHADOW_MD = "0 1px 3px rgba(16,24,40,0.06), 0 12px 28px -16px rgba(16,24,40,0.18)";
const SHADOW_LG = "0 2px 6px rgba(16,24,40,0.06), 0 24px 48px -24px rgba(16,24,40,0.22)";

const ACTION_META = {
  STOP_PROCUREMENT:     { label: "Stop Procurement",     color: T.red },
  RENEW_IMPORT_LICENCE: { label: "Renew Import Licence", color: T.amber },
  REORDER_URGENT:       { label: "Reorder Urgent",       color: T.red },
  REORDER_REVIEW:       { label: "Reorder Review",       color: T.amber },
  MONITOR:              { label: "Monitor",              color: T.blue },
  OK:                   { label: "OK",                   color: T.muted },
};
const LICENCE_ACTIONS = ["STOP_PROCUREMENT", "RENEW_IMPORT_LICENCE"];
const TABS = [
  { key: "ALL",             label: "All Actions",         accent: T.purple },
  { key: "LICENCE",         label: "Licence / Regulatory", accent: T.purple },
  { key: "REORDER_URGENT",  label: "Urgent",              accent: T.red },
  { key: "REORDER_REVIEW",  label: "Reorder Review",      accent: T.amber },
  { key: "MONITOR",         label: "Monitor",             accent: T.blue },
];
const CONFIDENCE_COLOR = { HIGH: T.green, MEDIUM: T.amber, LOW: T.red };
const PRIORITY_COLOR = (p) =>
  p === "CRITICAL" || p === "HIGH" ? T.red : p === "MEDIUM" ? T.amber : T.muted;
// 5-factor design: budget & item expiry removed by design decision
const FACTOR_LABELS = {
  cover: "Cover (No-Risk)", forecast_trust: "Forecast Trust",
  licence: "Licence", po_pipeline: "PO Pipeline",
  grn_reliability: "GRN Reliability",
};
const reasonColor = (code) => {
  if (code.startsWith("COVER_CRITICAL")) return T.red;
  if (code.includes("EXPIRED")) return T.red;
  if (code.includes("_RISK_")) return T.red;      // licence < 1 yr
  if (code.includes("_ALERT_")) return T.amber;   // licence 1–1.5 yr
  if (code.startsWith("RULE_CONFLICT")) return T.purple;
  if (code.startsWith("COVER_") || code.startsWith("UNDER_FORECAST") ||
      code.startsWith("FORECAST_")) return T.amber;
  return T.muted;
};

/* ─── Global CSS: fonts, keyframes, scrollbars ───────────────── */
const GlobalStyle = () => (
  <style>{`
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700;800&display=swap');
    @keyframes rec-fade-up {
      from { opacity: 0; transform: translateY(10px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes rec-fade-in { from { opacity: 0; } to { opacity: 1; } }
    @keyframes rec-shimmer {
      0%   { background-position: -400px 0; }
      100% { background-position: 400px 0; }
    }
    @keyframes rec-spin { to { transform: rotate(360deg); } }
    @keyframes rec-gauge { from { stroke-dashoffset: var(--gauge-circ); } }
    @keyframes rec-pulse-dot {
      0%, 100% { opacity: 1; transform: scale(1); }
      50%      { opacity: 0.55; transform: scale(0.82); }
    }
    .rec-anim { animation: rec-fade-up 0.45s cubic-bezier(0.22,1,0.36,1) both; }
    .rec-fade { animation: rec-fade-in 0.35s ease both; }
    .rec-kpi  { transition: transform 0.22s cubic-bezier(0.22,1,0.36,1), box-shadow 0.22s ease; }
    .rec-kpi:hover { transform: translateY(-3px); box-shadow: ${SHADOW_LG}; }
    .rec-scroll::-webkit-scrollbar { height: 8px; width: 8px; }
    .rec-scroll::-webkit-scrollbar-track { background: transparent; }
    .rec-scroll::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 8px; }
    .rec-scroll::-webkit-scrollbar-thumb:hover { background: ${T.muted}66; }
    .rec-row { transition: background 0.14s ease; }
    .rec-btn { transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease; }
    .rec-btn:not(:disabled):hover  { transform: translateY(-1px); box-shadow: ${SHADOW_MD}; }
    .rec-btn:not(:disabled):active { transform: translateY(0); }
    .rec-spinner {
      width: 13px; height: 13px; border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff;
      display: inline-block; animation: rec-spin 0.7s linear infinite;
      vertical-align: -2px;
    }
    .rec-skel {
      background: linear-gradient(90deg, ${T.border}55 25%, ${T.border}AA 37%, ${T.border}55 63%);
      background-size: 400px 100%;
      animation: rec-shimmer 1.3s ease infinite;
      border-radius: 6px;
    }
    .rec-live-dot { animation: rec-pulse-dot 1.8s ease infinite; }
  `}</style>
);

/* ─── Small building blocks (Insights v2 chrome) ─────────────── */
function GlyphIcon({ glyph, color, size = 24 }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: 8, flexShrink: 0,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      fontSize: size * 0.5, color,
      background: `linear-gradient(135deg, ${color}22, ${color}0D)`,
      border: `1px solid ${color}2E`,
    }}>{glyph}</span>
  );
}

function InfoTag({ text, color }) {
  return (
    <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: 0.8, textTransform: "uppercase",
      color: color || T.muted, background: (color || T.muted) + "14",
      border: `1px solid ${(color || T.muted)}2E`, borderRadius: 999, padding: "2px 8px",
      whiteSpace: "nowrap" }}>{text}</span>
  );
}

function Kpi({ label, value, color, sub, glyph, delay = 0 }) {
  const col = color || T.blue;
  return (
    <div className="rec-kpi rec-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 140, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM }}>
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${col}26, transparent 70%)`, pointerEvents: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        {glyph && <GlyphIcon glyph={glyph} color={col} size={24} />}
        <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800 }}>{label}</span>
      </div>
      <div style={{ fontSize: 22, fontWeight: 900, color: col, fontFamily: FONT_MONO,
        lineHeight: 1, letterSpacing: -0.5, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {sub && <div style={{ fontSize: 10.5, color: T.muted, marginTop: 6, fontWeight: 500 }}>{sub}</div>}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3,
        background: `linear-gradient(90deg, ${col}, ${col}22 70%, transparent)` }} />
    </div>
  );
}

/* Summary card: title + rows of [label, value, valueColor] */
function SummaryCard({ title, accent, glyph, rows, delay = 0 }) {
  return (
    <div className="rec-kpi rec-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 240, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM }}>
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${accent}22, transparent 70%)`, pointerEvents: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <GlyphIcon glyph={glyph} color={accent} size={24} />
        <span style={{ fontSize: 9.5, color: accent, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 900 }}>{title}</span>
      </div>
      {rows.map(([label, value, color]) => (
        <div key={label} style={{ display: "flex", justifyContent: "space-between",
          alignItems: "center", padding: "3.5px 0", fontSize: 11.5 }}>
          <span style={{ color: T.muted, fontWeight: 600 }}>{label}</span>
          <span style={{ fontFamily: FONT_MONO, color: color || T.text, fontWeight: 800,
            fontVariantNumeric: "tabular-nums" }}>{value}</span>
        </div>
      ))}
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3,
        background: `linear-gradient(90deg, ${accent}, ${accent}22 70%, transparent)` }} />
    </div>
  );
}

/* Radial gauge (factor coverage) */
function Gauge({ value, color, size = 62, stroke = 6 }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const frac = value == null ? 0 : Math.min(Math.max(toNumber(value), 0), 100) / 100;
  return (
    <svg width={size} height={size} style={{ transform: "rotate(-90deg)", flexShrink: 0 }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={T.border} strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={circ} strokeDashoffset={circ * (1 - frac)}
        style={{ "--gauge-circ": circ, animation: "rec-gauge 0.9s cubic-bezier(0.22,1,0.36,1) both" }} />
    </svg>
  );
}

function CoverageCard({ weight, confidence, availability, delay = 0 }) {
  const col = CONFIDENCE_COLOR[confidence] || T.muted;
  const pctVal = Math.round((weight || 0) * 100);
  const liveCount = Object.values(availability).filter(Boolean).length;
  const totalCount = Object.keys(FACTOR_LABELS).length;
  return (
    <div className="rec-kpi rec-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 200, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM,
      display: "flex", alignItems: "center", gap: 14 }}>
      <div style={{ position: "relative", width: 62, height: 62, flexShrink: 0 }}>
        <Gauge value={pctVal} color={col} />
        <div style={{ position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center" }}>
          <span style={{ fontSize: 9, fontWeight: 900, color: col, fontFamily: FONT_MONO }}>{pctVal}%</span>
        </div>
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800, marginBottom: 5 }}>Factor Coverage</div>
        <div style={{ fontSize: 17, fontWeight: 900, fontFamily: FONT_MONO, color: col }}>{confidence}</div>
        <div style={{ fontSize: 10, color: T.muted, marginTop: 5, fontWeight: 500 }}>
          {liveCount} of {totalCount} factors live
        </div>
      </div>
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3,
        background: `linear-gradient(90deg, ${col}, ${col}22 70%, transparent)` }} />
    </div>
  );
}

function SectionLabel({ children, accent }) {
  const col = accent || T.muted;
  return (
    <div style={{ fontSize: 10, color: col, textTransform: "uppercase",
      letterSpacing: 2, fontWeight: 900, marginBottom: 10, display: "flex",
      alignItems: "center", gap: 8 }}>
      <span style={{ width: 18, height: 3, borderRadius: 2, display: "inline-block",
        background: `linear-gradient(90deg, ${col}, ${col}44)` }} />
      {children}
      <span style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${T.border}, transparent)` }} />
    </div>
  );
}

function Tab({ active, onClick, children, badge, badgeColor, accent }) {
  const acc = accent || T.purple;
  return (
    <button className="rec-btn" onClick={onClick} style={{
      background: active ? T.card : "transparent",
      border: active ? `1px solid ${T.border}` : "1px solid transparent",
      boxShadow: active ? SHADOW_MD : "none",
      borderRadius: 10, padding: "9px 18px", cursor: "pointer",
      fontSize: 12.5, fontWeight: active ? 800 : 600,
      color: active ? acc : T.muted, fontFamily: FONT_UI,
      display: "flex", alignItems: "center", gap: 8 }}>
      {active && <span style={{ width: 6, height: 6, borderRadius: "50%", background: acc,
        boxShadow: `0 0 0 3px ${acc}22` }} />}
      {children}
      {badge != null && (
        <span style={{ background: (badgeColor || acc) + "1A",
          color: badgeColor || acc, border: `1px solid ${(badgeColor || acc)}30`,
          borderRadius: 999, padding: "1px 8px", fontSize: 9.5, fontWeight: 900,
          fontFamily: FONT_MONO }}>{badge}</span>
      )}
    </button>
  );
}

/* Risk score bar */
function RiskBar({ score }) {
  const color = score >= 75 ? T.red : score >= 50 ? T.amber : score >= 25 ? T.blue : T.muted;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}>
      <div style={{ width: 54, height: 5, background: T.border, borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(toNumber(score), 100)}%`, height: "100%",
          background: `linear-gradient(90deg, ${color}CC, ${color})`, borderRadius: 3,
          transition: "width 0.4s cubic-bezier(0.22,1,0.36,1)" }} />
      </div>
      <span style={{ fontFamily: FONT_MONO, color, minWidth: 40, textAlign: "right",
        fontWeight: 900, fontSize: 11.5 }}>{toNumber(score).toFixed(1)}</span>
    </div>
  );
}

/* Licence cell: current date vs expiry, shown in months/years.
   expired -> EXP (red) · <1yr RISK (red) · 1-1.5yr ALERT (amber) ·
   >=1.5yr safe (green, still shows time left, e.g. "2.3y") */
function LicenceCell({ impDays, regDays, bg }) {
  const part = (label, days) => {
    if (days === null || days === undefined)
      return <span style={{ color: T.muted }}>{label} —</span>;
    const d = toNumber(days);
    const color = d < 0 ? T.red : d < 365 ? T.red : d < 548 ? T.amber : T.green;
    const left = d < 0 ? "EXP" : d < 365 ? `${Math.round(d / 30.44)}mo` : `${(d / 365).toFixed(1)}y`;
    return (
      <span style={{ color, fontWeight: d < 548 ? 800 : 600 }}>
        {label} {left}
      </span>
    );
  };
  return (
    <td style={{ borderBottom: `1px solid ${T.border}`, padding: "12px 14px",
      background: bg, fontFamily: FONT_MONO, fontSize: 10.5, whiteSpace: "nowrap" }}>
      {part("Imp", impDays)}
      <span style={{ color: T.muted, margin: "0 5px" }}>/</span>
      {part("Reg", regDays)}
    </td>
  );
}

function Chip({ code }) {
  const col = reasonColor(code);
  return (
    <span style={{
      display: "inline-block", padding: "2px 7px", margin: "1px 3px 1px 0",
      borderRadius: 999, fontSize: 8.5, fontFamily: FONT_MONO, fontWeight: 800,
      letterSpacing: 0.4, color: col, background: `${col}14`,
      border: `1px solid ${col}2E`, whiteSpace: "nowrap",
    }}>{code}</span>
  );
}

/* Windowed pagination (same as Insights) */
function Pagination({ page, setPage, total, pageSize = PAGE_SIZE, accent }) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;
  const acc = accent || T.purple;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);
  const pages = [];
  const windowSize = 1;
  for (let p = 1; p <= totalPages; p++) {
    if (p === 1 || p === totalPages || (p >= page - windowSize && p <= page + windowSize)) {
      pages.push(p);
    } else if (pages[pages.length - 1] !== "…") {
      pages.push("…");
    }
  }
  const btnStyle = (active) => ({
    minWidth: 28, height: 28, padding: "0 7px",
    borderRadius: 8, border: `1px solid ${active ? acc + "55" : T.border}`,
    background: active ? acc + "18" : T.card,
    color: active ? acc : T.muted,
    fontSize: 11, fontWeight: active ? 900 : 600, fontFamily: FONT_MONO,
    cursor: "pointer", boxShadow: active ? "none" : SHADOW_SM,
  });
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
      marginTop: 14, flexWrap: "wrap", gap: 10 }}>
      <div style={{ fontSize: 10.5, color: T.muted }}>
        Showing <span style={{ color: T.text, fontWeight: 800 }}>{start}–{end}</span> of{" "}
        <span style={{ color: T.text, fontWeight: 800 }}>{total}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button className="rec-btn" onClick={() => setPage(p => Math.max(1, p - 1))}
          disabled={page === 1}
          style={{ ...btnStyle(false), cursor: page === 1 ? "not-allowed" : "pointer", opacity: page === 1 ? 0.4 : 1 }}
        >‹</button>
        {pages.map((p, i) =>
          p === "…" ? (
            <span key={`e-${i}`} style={{ color: T.muted, fontSize: 11, padding: "0 2px" }}>…</span>
          ) : (
            <button key={p} className="rec-btn" onClick={() => setPage(p)} style={btnStyle(p === page)}>{p}</button>
          )
        )}
        <button className="rec-btn" onClick={() => setPage(p => Math.min(totalPages, p + 1))}
          disabled={page === totalPages}
          style={{ ...btnStyle(false), cursor: page === totalPages ? "not-allowed" : "pointer", opacity: page === totalPages ? 0.4 : 1 }}
        >›</button>
      </div>
    </div>
  );
}

function TableSkeleton({ rows = 6 }) {
  return (
    <div style={{ padding: "8px 4px" }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: "flex", gap: 12, padding: "10px 8px",
          borderBottom: i < rows - 1 ? `1px solid ${T.border}55` : "none" }}>
          <div className="rec-skel" style={{ width: 60, height: 12 }} />
          <div className="rec-skel" style={{ width: "12%", height: 12 }} />
          <div className="rec-skel" style={{ width: "14%", height: 12 }} />
          <div className="rec-skel" style={{ width: "8%", height: 12 }} />
          <div className="rec-skel" style={{ flex: 1, height: 12 }} />
          <div className="rec-skel" style={{ width: "18%", height: 12 }} />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ glyph = "◌", title, hint }) {
  return (
    <div className="rec-fade" style={{ padding: "44px 20px", textAlign: "center" }}>
      <div style={{ fontSize: 30, color: T.muted, opacity: 0.5, marginBottom: 10 }}>{glyph}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{title}</div>
      {hint && <div style={{ fontSize: 11, color: T.muted }}>{hint}</div>}
    </div>
  );
}

/* Shared table styles */
const thStyle = {
  borderBottom: `1px solid ${T.border}`, background: T.surface, color: T.muted,
  padding: "11px 14px", textAlign: "left", fontSize: 9, fontWeight: 800,
  textTransform: "uppercase", letterSpacing: 1.1, whiteSpace: "nowrap",
  position: "sticky", top: 0, zIndex: 1,
};
const tdBase = {
  borderBottom: `1px solid ${T.border}`, padding: "12px 14px", color: T.muted,
  whiteSpace: "nowrap", fontFamily: FONT_MONO, fontSize: 11,
  fontVariantNumeric: "tabular-nums",
};

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT — data logic identical to previous version
════════════════════════════════════════════════════════════════ */
export default function Recommendations() {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");
  const [tab, setTab]         = useState("ALL");
  const [search, setSearch]   = useState("");
  const [page, setPage]       = useState(1);

  const fetchResults = async () => {
    setLoading(true); setError("");
    try {
      const res = await fetch(API_URL);
      const j = await res.json();
      if (!res.ok) throw new Error(j.error || `HTTP ${res.status}`);
      setData(j);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchResults(); }, []);
  useEffect(() => setPage(1), [tab, search]);

  const rows = useMemo(() => {
    if (!data) return [];
    let r = data.recommendations || [];
    if (tab === "LICENCE") r = r.filter((x) => LICENCE_ACTIONS.includes(x.action));
    else if (tab !== "ALL") r = r.filter((x) => x.action === tab);
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      r = r.filter((x) => String(x.ItemCode).toLowerCase().includes(q));
    }
    return r;
  }, [data, tab, search]);

  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageRows = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const tabCount = (key) => {
    if (!data) return 0;
    const r = data.recommendations || [];
    if (key === "ALL") return r.length;
    if (key === "LICENCE") return r.filter((x) => LICENCE_ACTIONS.includes(x.action)).length;
    return r.filter((x) => x.action === key).length;
  };

  const { summary = {}, kpis = {}, factor_coverage = {}, run_meta = {} } = data || {};
  const availability = factor_coverage.availability || {};
  const confidence = factor_coverage.confidence || "LOW";
  const f = summary.forecast || {};
  const inv = summary.inventory || {};
  const lic = summary.licences || {};
  const invStatusColor = inv.status === "LOW" ? T.red : inv.status === "WATCH" ? T.amber : T.green;
  const tabAccent = (TABS.find(t => t.key === tab) || {}).accent || T.purple;

  return (
    <div style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.purple}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.blue}0C, transparent 55%),
                   ${T.bg}`,
      color: T.text, fontFamily: FONT_UI, padding: "26px 34px 40px" }}>
      <GlobalStyle />

      {/* ── Header (glass) ── */}
      <div className="rec-anim" style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 22 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 15 }}>
          <div style={{ width: 44, height: 44, borderRadius: 13, flexShrink: 0,
            background: `linear-gradient(135deg, ${T.red}, ${T.purple})`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 20, color: "#fff",
            boxShadow: `0 8px 20px -8px ${T.red}AA` }}>◎</div>
          <div>
            <div style={{ fontSize: 9, color: T.red, letterSpacing: 3,
              textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>
              AI Planner · Stockout Prevention
            </div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.6,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              Replenishment & Risk Recommendations
            </h1>
            <div style={{ marginTop: 5, color: T.muted, fontSize: 11, display: "flex",
              alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {data ? (
                <>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5,
                    background: (CONFIDENCE_COLOR[confidence] || T.muted) + "14",
                    border: `1px solid ${(CONFIDENCE_COLOR[confidence] || T.muted)}30`,
                    borderRadius: 999, padding: "2px 10px", fontWeight: 700,
                    color: CONFIDENCE_COLOR[confidence] || T.muted }}>
                    <span className="rec-live-dot" style={{ width: 6, height: 6, borderRadius: "50%",
                      background: CONFIDENCE_COLOR[confidence] || T.muted, display: "inline-block" }} />
                    Confidence · {confidence} ({Math.round((factor_coverage.weight_covered || 0) * 100)}%)
                  </span>
                  <span style={{ background: T.blue + "12", border: `1px solid ${T.blue}2E`,
                    borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.blue }}>
                    {formatNum(run_meta.n_items_scored)} SKUs scored
                  </span>
                  <span style={{ background: T.purple + "12", border: `1px solid ${T.purple}2E`,
                    borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.purple }}>
                    {formatNum(run_meta.n_recommendations)} actions
                  </span>
                </>
              ) : "Forecast · inventory · licence signals → prioritised prevention actions."}
            </div>
          </div>
        </div>
        <button className="rec-btn" onClick={fetchResults} disabled={loading} style={{
          background: loading ? T.subtle : `linear-gradient(135deg, ${T.red}, ${T.purple})`,
          color: loading ? T.muted : "#fff",
          border: "none", borderRadius: 10, padding: "11px 20px", fontSize: 13,
          fontWeight: 800, cursor: loading ? "not-allowed" : "pointer",
          fontFamily: FONT_UI, whiteSpace: "nowrap", flexShrink: 0,
          boxShadow: loading ? "none" : `0 8px 18px -8px ${T.red}AA`,
          display: "inline-flex", alignItems: "center", gap: 8 }}>
          {loading ? <><span className="rec-spinner" /> Running…</> : <>▶ Run Planner</>}
        </button>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="rec-fade" style={{ background: T.red + "0D", border: `1px solid ${T.red}33`,
          borderLeft: `4px solid ${T.red}`, borderRadius: 12,
          padding: "13px 18px", color: T.red, marginBottom: 18, fontSize: 13,
          display: "flex", alignItems: "center", gap: 10, boxShadow: SHADOW_SM }}>
          <span style={{ fontSize: 15 }}>⚠</span> {error}
        </div>
      )}

      {/* ── Planner summary ── */}
      <SectionLabel accent={T.blue}>Situation — Planner Summary</SectionLabel>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
        <SummaryCard title="Forecast Summary" accent={T.blue} glyph="◔" delay={0}
          rows={[
            ["Forecast Month", f.month ?? "—"],
            ["Forecast Qty", formatNum(f.total_forecast_qty)],
            ["Expected Growth",
              f.growth_pct == null ? "—" : `${f.growth_pct > 0 ? "+" : ""}${f.growth_pct}%`,
              f.growth_pct > 0 ? T.green : f.growth_pct < 0 ? T.red : T.text],
          ]} />
        <SummaryCard title="Inventory Status" accent={T.amber} glyph="▦" delay={40}
          rows={[
            ["Warehouse (Trade-NoRisk)", formatNum(inv.wh_no_risk)],
            ["Distributor (Trade-NoRisk)", formatNum(inv.db_no_risk)],
            ["No-Risk Stock", formatNum(inv.no_risk_stock), T.green],
            ["Median Cover (No-Risk)", inv.median_cover_months == null ? "—"
              : `${fmtDec(inv.median_cover_months)} mo`, invStatusColor],
            ["Status", inv.status ?? "—", invStatusColor],
          ]} />
        <SummaryCard title="Supply Constraints — Licences" accent={T.purple} glyph="✦" delay={80}
          rows={lic.available ? [
            ["Import Expired", formatNum(lic.import?.expired), toNumber(lic.import?.expired) > 0 ? T.red : T.text],
            ["Import < 1 yr (Risk)", formatNum(lic.import?.risk_1y), toNumber(lic.import?.risk_1y) > 0 ? T.red : T.text],
            ["Import 1–1.5 yr (Alert)", formatNum(lic.import?.alert_18m), toNumber(lic.import?.alert_18m) > 0 ? T.amber : T.text],
            ["Import > 1.5 yr (Safe)", formatNum(lic.import?.safe), T.green],
            ["Import No Data", formatNum(lic.import?.no_data), T.muted],
            ["Reg. Expired", formatNum(lic.registration?.expired), toNumber(lic.registration?.expired) > 0 ? T.red : T.text],
            ["Reg. < 1 yr (Risk)", formatNum(lic.registration?.risk_1y), toNumber(lic.registration?.risk_1y) > 0 ? T.red : T.text],
            ["Reg. 1–1.5 yr (Alert)", formatNum(lic.registration?.alert_18m), toNumber(lic.registration?.alert_18m) > 0 ? T.amber : T.text],
            ["Reg. > 1.5 yr (Safe)", formatNum(lic.registration?.safe), T.green],
            ["Reg. No Data", formatNum(lic.registration?.no_data), T.muted],
          ] : [["Licence data", "NOT COLLECTED", T.muted]]} />
        <CoverageCard weight={factor_coverage.weight_covered} confidence={confidence}
          availability={availability} delay={120} />
      </div>

      {/* ── Action KPI strip ── */}
      <SectionLabel accent={T.red}>Recommended Actions</SectionLabel>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
        <Kpi glyph="⊘" delay={0}   label="Stop Procurement" value={formatNum(kpis.stop_procurement)} color={T.red}    sub="Registration expired" />
        <Kpi glyph="✦" delay={30}  label="Renew Licence"    value={formatNum(kpis.renew_licence)}    color={T.amber}  sub="Import licence < 1 yr" />
        <Kpi glyph="▲" delay={60}  label="Reorder Urgent"   value={formatNum(kpis.critical)}         color={T.red}    sub="Cover critically low" />
        <Kpi glyph="◔" delay={90}  label="Reorder Review"   value={formatNum(kpis.reorder_review)}   color={T.amber}  sub="Replenishment due" />
        <Kpi glyph="◉" delay={120} label="Monitor"          value={formatNum(kpis.monitor)}          color={T.blue}   sub="Watchlist" />
      </div>

      {/* ── Factor coverage chips ── */}
      <div className="rec-anim" style={{
        background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
        padding: "10px 16px", marginBottom: 22, boxShadow: SHADOW_SM,
        display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800, marginRight: 4 }}>Factors</span>
        {Object.entries(FACTOR_LABELS).map(([key, label]) => {
          const live = !!availability[key];
          const col = live ? T.green : T.muted;
          return (
            <span key={key}
              title={live ? "Data live — contributing to scores" : "Data not collected yet — pending"}
              style={{ fontSize: 10, fontWeight: 700, padding: "3px 10px", borderRadius: 999,
                color: col, background: live ? `${col}14` : "transparent",
                border: `1px solid ${live ? `${col}40` : T.border}` }}>
              {live ? "●" : "○"} {label}
            </span>
          );
        })}
      </div>

      {/* ── Tabs (segmented) ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 10, marginBottom: 14 }}>
        <div style={{ display: "inline-flex", gap: 4, padding: 5,
          background: T.surface, border: `1px solid ${T.border}`,
          borderRadius: 13, boxShadow: SHADOW_SM, flexWrap: "wrap" }}>
          {TABS.map((t) => (
            <Tab key={t.key} active={tab === t.key} onClick={() => setTab(t.key)}
              accent={t.accent} badge={tabCount(t.key)}
              badgeColor={t.key === "ALL" ? T.purple : (ACTION_META[t.key]?.color || t.accent)}>
              {t.label}
            </Tab>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6,
          background: T.card, border: `1px solid ${T.border}`, borderRadius: 10,
          boxShadow: SHADOW_SM, padding: "4px 12px" }}>
          <span style={{ fontSize: 9, color: T.muted, fontWeight: 800,
            textTransform: "uppercase", letterSpacing: 1 }}>Search</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Item code…"
            style={{ background: "transparent", border: "none", color: T.text,
              fontSize: 12.5, fontFamily: FONT_MONO, fontWeight: 600,
              padding: "6px 4px", outline: "none", width: 150 }} />
        </div>
      </div>

      {/* ── Table card ── */}
      <div className="rec-fade" key={tab} style={{ background: T.card, border: `1px solid ${T.border}`,
        borderRadius: 16, padding: "18px 20px", boxShadow: SHADOW_MD,
        position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3,
          background: `linear-gradient(90deg, ${tabAccent}, ${tabAccent}22 60%, transparent)` }} />

        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 14, gap: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 10, color: tabAccent, textTransform: "uppercase",
              letterSpacing: 2, fontWeight: 900, marginBottom: 3 }}>
              {(TABS.find(t => t.key === tab) || {}).label} — Prevention Actions
            </div>
            <div style={{ fontSize: 10.5, color: T.muted }}>
              Gated decision logic: regulatory → licence renewal → reorder · sorted by priority & risk
            </div>
          </div>
          <span style={{ background: tabAccent + "14", border: `1px solid ${tabAccent}30`,
            color: tabAccent, borderRadius: 999, padding: "4px 12px", fontSize: 11,
            fontWeight: 900, fontFamily: FONT_MONO }}>
            {rows.length} SKUs
          </span>
        </div>

        {loading ? (
          <TableSkeleton rows={7} />
        ) : rows.length === 0 ? (
          <EmptyState glyph="✓" title="No recommendations in this view"
            hint="No SKUs currently need this action." />
        ) : (
          <>
            <div className="rec-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
              <table style={{ width: "100%", borderCollapse: "collapse",
                fontFamily: FONT_MONO, fontSize: 11 }}>
                <thead>
                  <tr>
                    {["#", "Item Code", "Risk Score", "Action", "Priority",
                      "Cover (Mo)", "Gap Qty", "Suggested Order", "Licence (Imp/Reg)", "Reasons"]
                      .map(h => <th key={h} style={thStyle}>{h}</th>)}
                  </tr>
                  <tr>
                    {Array.from({ length: 2 }).map((_, i) => (
                      <th key={i} style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                    ))}
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.red }}>Weighted factors</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>Gated decision</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>No-risk stock ÷ demand</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>To target cover</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.green }}>MOQ / multiple applied</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Days remaining</th>
                    <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((r, idx) => {
                    const rowNum = (page - 1) * PAGE_SIZE + idx + 1;
                    const bg = idx % 2 === 0 ? T.card : T.surface + "66";
                    const meta = ACTION_META[r.action] || ACTION_META.OK;
                    const cover = r.cover_months;
                    const coverColor =
                      cover == null ? T.muted :
                      cover < 0.5 ? T.red :
                      cover < 1 ? T.amber : T.text;
                    return (
                      <tr key={`${r.ItemCode}-${rowNum}`} className="rec-row"
                        onMouseEnter={e => e.currentTarget.style.background = meta.color + "0D"}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                        <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                        {/* Unmapped/new products carry synthetic keys "label::agency::product" —
                            display the label + product, keep the full key in the tooltip */}
                        <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}
                          title={String(r.ItemCode)}>
                          {String(r.ItemCode).includes("::") ? (
                            <span>
                              {String(r.ItemCode).split("::")[0]}
                              <InfoTag text="New" color={T.teal} />
                              <div style={{ fontSize: 9, color: T.muted, fontWeight: 500,
                                fontFamily: FONT_UI, marginTop: 2, maxWidth: 160,
                                overflow: "hidden", textOverflow: "ellipsis" }}>
                                {String(r.ItemCode).split("::")[2] || String(r.ItemCode).split("::")[1]}
                              </div>
                            </span>
                          ) : r.ItemCode}
                        </td>
                        <td style={{ ...tdBase, background: bg, minWidth: 120 }}>
                          <RiskBar score={r.risk_score} />
                        </td>
                        <td style={{ ...tdBase, background: bg }}>
                          <InfoTag text={meta.label} color={meta.color} />
                        </td>
                        <td style={{ ...tdBase, background: bg, color: PRIORITY_COLOR(r.priority),
                          fontWeight: r.priority === "CRITICAL" || r.priority === "HIGH" ? 900 : 600 }}>
                          {r.priority}
                        </td>
                        <td style={{ ...tdBase, background: bg, textAlign: "right",
                          color: coverColor, fontWeight: cover != null && cover < 1 ? 900 : 600 }}>
                          {cover == null ? "∞" : fmtDec(cover)}
                        </td>
                        <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                          {toNumber(r.gap_qty) > 0 ? formatNum(r.gap_qty) : <span style={{ color: T.muted }}>—</span>}
                        </td>
                        <td style={{ ...tdBase, background: bg, textAlign: "right",
                          color: r.action === "STOP_PROCUREMENT" ? T.muted
                            : toNumber(r.suggested_qty) > 0 ? T.green : T.muted,
                          fontWeight: toNumber(r.suggested_qty) > 0 ? 900 : 400 }}>
                          {r.action === "STOP_PROCUREMENT" ? "—"
                            : toNumber(r.suggested_qty) > 0 ? formatNum(r.suggested_qty) : "—"}
                        </td>
                        <LicenceCell impDays={r.import_days} regDays={r.reg_days} bg={bg} />
                        <td style={{ ...tdBase, background: bg, whiteSpace: "normal", maxWidth: 300, minWidth: 200 }}>
                          {(r.reasons || []).map((c) => <Chip key={c} code={c} />)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={page} setPage={setPage} total={rows.length} accent={tabAccent} />
          </>
        )}
      </div>

      {/* ── Pending-factors footnote ── */}
      {(run_meta.factors_pending || []).length > 0 && (
        <div style={{ marginTop: 12, fontSize: 10.5, color: T.muted }}>
          Scores exclude pending factors:{" "}
          <span style={{ fontWeight: 700 }}>
            {(run_meta.factors_pending || []).map((k) => FACTOR_LABELS[k] || k).join(", ")}
          </span>. Confidence rises automatically as their data collection begins.
        </div>
      )}
    </div>
  );
}