// src/pages/Insights.jsx
//
// UI v2 — visual upgrade only. All KPIs, data flow, sorting, filtering,
// pagination and calculations are IDENTICAL to v1.

import React, { useEffect, useMemo, useState } from "react";
import T from "../theme";

const API_BASE = "/api/insights";
const PAGE_SIZE = 20;

/* ─── Helpers (unchanged) ────────────────────────────────────── */
function toNumber(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function formatNum(v, d = 0) { return toNumber(v).toLocaleString(undefined, { maximumFractionDigits: d }); }
function pct(v) { return toNumber(v).toFixed(1) + "%"; }
/* Compact display for large money values: 64,471,465,913 -> 64.47B.
   Full figure goes in the sub-line / tooltip. */
function formatCompact(v) {
  const n = toNumber(v);
  if (Math.abs(n) >= 1e6) {
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
  }
  return n.toLocaleString();
}
function accColor(v) {
  if (v == null) return T.muted;
  const n = toNumber(v);
  return n >= 90 ? T.green : n >= 75 ? T.amber : T.red;
}
function accLabel(v) { return v == null ? "N/A" : toNumber(v).toFixed(1) + "%"; }

/* Budget reach colour: ≥100% green, ≥80% amber, <80% red */
function budgetReachColor(v) {
  if (v == null) return T.muted;
  const n = toNumber(v);
  return n >= 100 ? T.green : n >= 80 ? T.amber : T.red;
}

/* ─── Design tokens (UI only) ────────────────────────────────── */
const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.05)";
const SHADOW_MD = "0 1px 3px rgba(16,24,40,0.06), 0 12px 28px -16px rgba(16,24,40,0.18)";
const SHADOW_LG = "0 2px 6px rgba(16,24,40,0.06), 0 24px 48px -24px rgba(16,24,40,0.22)";

/* Global CSS: fonts, keyframes, scrollbars, row hover */
const GlobalStyle = () => (
  <style>{`
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700;800&display=swap');

    @keyframes ins-fade-up {
      from { opacity: 0; transform: translateY(10px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes ins-fade-in {
      from { opacity: 0; }
      to   { opacity: 1; }
    }
    @keyframes ins-shimmer {
      0%   { background-position: -400px 0; }
      100% { background-position: 400px 0; }
    }
    @keyframes ins-spin {
      to { transform: rotate(360deg); }
    }
    @keyframes ins-gauge {
      from { stroke-dashoffset: var(--gauge-circ); }
    }
    @keyframes ins-pulse-dot {
      0%, 100% { opacity: 1; transform: scale(1); }
      50%      { opacity: 0.55; transform: scale(0.82); }
    }

    .ins-anim   { animation: ins-fade-up 0.45s cubic-bezier(0.22,1,0.36,1) both; }
    .ins-fade   { animation: ins-fade-in 0.35s ease both; }

    .ins-kpi {
      transition: transform 0.22s cubic-bezier(0.22,1,0.36,1), box-shadow 0.22s ease, border-color 0.22s ease;
    }
    .ins-kpi:hover {
      transform: translateY(-3px);
      box-shadow: ${SHADOW_LG};
    }

    .ins-scroll::-webkit-scrollbar { height: 8px; width: 8px; }
    .ins-scroll::-webkit-scrollbar-track { background: transparent; }
    .ins-scroll::-webkit-scrollbar-thumb { background: ${T.border}; border-radius: 8px; }
    .ins-scroll::-webkit-scrollbar-thumb:hover { background: ${T.muted}66; }

    .ins-row { transition: background 0.14s ease, box-shadow 0.14s ease; }

    .ins-btn { transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease; }
    .ins-btn:not(:disabled):hover  { transform: translateY(-1px); box-shadow: ${SHADOW_MD}; }
    .ins-btn:not(:disabled):active { transform: translateY(0); }

    .ins-spinner {
      width: 13px; height: 13px; border-radius: 50%;
      border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff;
      display: inline-block; animation: ins-spin 0.7s linear infinite;
      vertical-align: -2px;
    }
    .ins-skel {
      background: linear-gradient(90deg, ${T.border}55 25%, ${T.border}AA 37%, ${T.border}55 63%);
      background-size: 400px 100%;
      animation: ins-shimmer 1.3s ease infinite;
      border-radius: 6px;
    }
    .ins-live-dot { animation: ins-pulse-dot 1.8s ease infinite; }
  `}</style>
);

/* ─── Small glyph icon in a tinted rounded square ────────────── */
function GlyphIcon({ glyph, color, size = 26 }) {
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

/* ─── KPI Card (same data, elevated presentation) ────────────── */
function Kpi({ label, value, color, sub, glyph, delay = 0 }) {
  const col = color || T.blue;
  return (
    <div className="ins-kpi ins-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 140, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM }}>
      {/* soft corner glow */}
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${col}26, transparent 70%)`,
        pointerEvents: "none" }} />
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

/* ─── Radial gauge (SVG) for ratio KPI cards ─────────────────── */
function Gauge({ value, color, size = 62, stroke = 6 }) {
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const frac = value == null ? 0 : Math.min(Math.max(toNumber(value), 0), 100) / 100;
  return (
    <svg width={size} height={size} style={{ transform: "rotate(-90deg)", flexShrink: 0 }}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={T.border} strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={circ}
        strokeDashoffset={circ * (1 - frac)}
        style={{ "--gauge-circ": circ, animation: "ins-gauge 0.9s cubic-bezier(0.22,1,0.36,1) both",
          transition: "stroke-dashoffset 0.4s ease" }} />
    </svg>
  );
}

/* ─── Ratio KPI card with gauge (same values as before) ──────── */
function RatioKpi({ label, value, sub, delay = 0 }) {
  const col = budgetReachColor(value);
  const over = value != null && toNumber(value) > 100;
  return (
    <div className="ins-kpi ins-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 170, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM,
      display: "flex", alignItems: "center", gap: 14 }}>
      <div style={{ position: "relative", width: 62, height: 62, flexShrink: 0 }}>
        <Gauge value={value} color={col} />
        <div style={{ position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center" }}>
          <span style={{ fontSize: 8.5, fontWeight: 900, color: col, fontFamily: FONT_MONO }}>
            {value != null ? Math.round(toNumber(value)) + "%" : "—"}
          </span>
        </div>
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800, marginBottom: 5 }}>{label}</div>
        <div style={{ fontSize: 21, fontWeight: 900, fontFamily: FONT_MONO, lineHeight: 1,
          color: col, fontVariantNumeric: "tabular-nums" }}>
          {value != null ? pct(value) : "—"}
          {over && <span style={{ fontSize: 11, marginLeft: 4 }}>▲</span>}
        </div>
        {sub && <div style={{ fontSize: 10, color: T.muted, marginTop: 5, fontWeight: 500,
          overflow: "hidden", textOverflow: "ellipsis" }}>{sub}</div>}
      </div>
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 3,
        background: `linear-gradient(90deg, ${col}, ${col}22 70%, transparent)` }} />
    </div>
  );
}

/* ─── Budget reach mini-bar (unchanged logic) ────────────────── */
function BudgetBar({ reach, label }) {
  if (reach == null) return <span style={{ color: T.muted, fontSize: 10 }}>—</span>;
  const n    = Math.min(toNumber(reach), 100);   // cap at 100 for bar width
  const over = toNumber(reach) > 100;
  const col  = budgetReachColor(reach);
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}>{label || "Reach"}</span>
        <span style={{ fontSize: 10, fontWeight: 900, color: col, fontFamily: FONT_MONO }}>
          {pct(reach)}{over && <span style={{ fontSize: 8, marginLeft: 2 }}>▲</span>}
        </span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: T.border, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${n}%`,
          background: `linear-gradient(90deg, ${col}CC, ${col})`,
          borderRadius: 3, transition: "width 0.4s cubic-bezier(0.22,1,0.36,1)" }} />
      </div>
    </div>
  );
}

/* ─── Section label ──────────────────────────────────────────── */
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

/* ─── Segmented tab button ───────────────────────────────────── */
function Tab({ active, onClick, children, badge, badgeColor, accent }) {
  const acc = accent || T.purple;
  return (
    <button className="ins-btn" onClick={onClick} style={{
      background: active ? T.card : "transparent",
      border: active ? `1px solid ${T.border}` : "1px solid transparent",
      boxShadow: active ? SHADOW_MD : "none",
      borderRadius: 10, padding: "9px 18px", cursor: "pointer",
      fontSize: 12.5, fontWeight: active ? 800 : 600,
      color: active ? acc : T.muted,
      fontFamily: FONT_UI,
      display: "flex", alignItems: "center", gap: 8,
      position: "relative",
    }}>
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

/* ─── Accuracy Bar ───────────────────────────────────────────── */
function AccBar({ value, color }) {
  return (
    <div style={{ marginTop: 5, height: 4, borderRadius: 2, background: T.border, overflow: "hidden", minWidth: 56 }}>
      <div style={{ height: "100%", width: `${Math.min(toNumber(value), 100)}%`,
        background: `linear-gradient(90deg, ${color}CC, ${color})`,
        borderRadius: 2, transition: "width 0.4s cubic-bezier(0.22,1,0.36,1)" }} />
    </div>
  );
}

/* ─── Pending Badge ──────────────────────────────────────────── */
function PendingBadge({ label = "Pending" }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 9px",
      borderRadius: 999, background: T.muted + "14", border: `1px solid ${T.muted}3A`,
      color: T.muted, fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.8 }}>
      ◷ {label}
    </span>
  );
}

/* ─── Info Tag ───────────────────────────────────────────────── */
function InfoTag({ text, color }) {
  return (
    <span style={{ fontSize: 9, fontWeight: 800, letterSpacing: 0.8, textTransform: "uppercase",
      color: color || T.muted, background: (color || T.muted) + "14",
      border: `1px solid ${(color || T.muted)}2E`, borderRadius: 999, padding: "2px 8px" }}>{text}</span>
  );
}

/* ─── Dual Accuracy Cell (same content, refined chrome) ──────── */
function DualAccuracyCell({ modelAcc, realisedAcc, realisedAvailable, modelUsed, bg }) {
  return (
    <td style={{ borderBottom: `1px solid ${T.border}`, padding: "12px 14px",
      background: bg, verticalAlign: "top", minWidth: 200 }}>
      <div style={{ marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>Model Accuracy</span>
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {modelUsed && (
              <span style={{ fontSize: 8, fontWeight: 800, letterSpacing: 0.8, textTransform: "uppercase",
                color: T.purple, background: T.purple + "14", border: `1px solid ${T.purple}2E`,
                borderRadius: 999, padding: "1px 6px" }}>{modelUsed}</span>
            )}
            <InfoTag text="Eval" color={T.blue} />
          </div>
        </div>
        {modelAcc == null ? (
          <span style={{ color: T.muted, fontSize: 11, fontFamily: FONT_MONO }}>N/A</span>
        ) : (
          <>
            <div style={{ color: accColor(modelAcc), fontWeight: 900, fontSize: 13.5, fontFamily: FONT_MONO }}>{accLabel(modelAcc)}</div>
            <AccBar value={modelAcc} color={accColor(modelAcc)} />
            <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>Last 4-month holdout WMAPE</div>
          </>
        )}
      </div>
      <div style={{ height: 1, background: T.border, margin: "0 0 8px 0" }} />
      <div>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 9, color: T.muted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>Realised Accuracy</span>
          <InfoTag text="Actual" color={T.teal} />
        </div>
        {!realisedAvailable ? (
          <><PendingBadge /><div style={{ fontSize: 9, color: T.muted, marginTop: 4 }}>Forecast month not yet closed</div></>
        ) : realisedAcc == null ? (
          <><PendingBadge label="No Sales" /><div style={{ fontSize: 9, color: T.muted, marginTop: 4 }}>Zero actual sales recorded</div></>
        ) : (
          <>
            <div style={{ color: accColor(realisedAcc), fontWeight: 900, fontSize: 13.5, fontFamily: FONT_MONO }}>{accLabel(realisedAcc)}</div>
            <AccBar value={realisedAcc} color={accColor(realisedAcc)} />
            <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>Forecast vs actual sales</div>
          </>
        )}
      </div>
    </td>
  );
}

/* ─── Loss breakdown cell (same content) ─────────────────────── */
function LossCell({ row, bg }) {
  const raw      = toNumber(row.Raw_Loss_Qty);
  const stockout = toNumber(row.Stockout_Loss_Qty);
  const other    = toNumber(row.Other_Loss_Qty);
  const reason   = row.Loss_Reason || "None";

  const cellStyle = { borderBottom: `1px solid ${T.border}`, padding: "12px 14px",
    background: bg, verticalAlign: "top", minWidth: 160 };

  if (raw <= 0) {
    return (
      <td style={{ ...cellStyle, textAlign: "right" }}>
        <span style={{ color: T.green, fontSize: 11, fontWeight: 800, fontFamily: FONT_MONO }}>—</span>
        <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>No loss</div>
      </td>
    );
  }

  const isStockout = reason === "Stockout";
  return (
    <td style={{ ...cellStyle, textAlign: "right" }}>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
        <InfoTag text={isStockout ? "Stockout" : "Other"} color={isStockout ? T.red : T.amber} />
      </div>
      <div style={{ color: T.red, fontWeight: 900, fontSize: 13.5, fontFamily: FONT_MONO }}>
        {formatNum(raw)}
      </div>
      {isStockout ? (
        <div style={{ fontSize: 9, color: T.muted, marginTop: 4, lineHeight: 1.6 }}>
          <span style={{ color: T.red }}>■</span> Stockout {formatNum(stockout)}
          {other > 0 && <><br /><span style={{ color: T.amber }}>■</span> Other {formatNum(other)}</>}
        </div>
      ) : (
        <div style={{ fontSize: 9, color: T.muted, marginTop: 4 }}>
          <span style={{ color: T.amber }}>■</span> Other {formatNum(other)}
        </div>
      )}
    </td>
  );
}

/* ─── Reason badge ───────────────────────────────────────────── */
function ReasonBadge({ reason }) {
  const cfg = {
    Stockout: { color: T.red,   label: "Stockout" },
    Other:    { color: T.amber, label: "Other"    },
    None:     { color: T.green, label: "No Loss"  },
  };
  const c = cfg[reason] || cfg.None;
  return <InfoTag text={c.label} color={c.color} />;
}

/* ─── Pagination (same logic, refreshed look) ────────────────── */
function Pagination({ page, setPage, total, pageSize = PAGE_SIZE, accent }) {
  const totalPages = Math.ceil(total / pageSize);
  if (totalPages <= 1) return null;

  const acc = accent || T.purple;
  const start = (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  // Windowed page numbers: first, last, and a few around current
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
    fontSize: 11, fontWeight: active ? 900 : 600,
    fontFamily: FONT_MONO,
    cursor: "pointer",
    boxShadow: active ? "none" : SHADOW_SM,
  });

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
      marginTop: 14, flexWrap: "wrap", gap: 10 }}>
      <div style={{ fontSize: 10.5, color: T.muted }}>
        Showing <span style={{ color: T.text, fontWeight: 800 }}>{start}–{end}</span> of{" "}
        <span style={{ color: T.text, fontWeight: 800 }}>{total}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button className="ins-btn"
          onClick={() => setPage(p => Math.max(1, p - 1))}
          disabled={page === 1}
          style={{ ...btnStyle(false), cursor: page === 1 ? "not-allowed" : "pointer", opacity: page === 1 ? 0.4 : 1 }}
        >‹</button>
        {pages.map((p, i) =>
          p === "…" ? (
            <span key={`e-${i}`} style={{ color: T.muted, fontSize: 11, padding: "0 2px" }}>…</span>
          ) : (
            <button key={p} className="ins-btn" onClick={() => setPage(p)} style={btnStyle(p === page)}>{p}</button>
          )
        )}
        <button className="ins-btn"
          onClick={() => setPage(p => Math.min(totalPages, p + 1))}
          disabled={page === totalPages}
          style={{ ...btnStyle(false), cursor: page === totalPages ? "not-allowed" : "pointer", opacity: page === totalPages ? 0.4 : 1 }}
        >›</button>
      </div>
    </div>
  );
}

/* ─── Loading skeleton ───────────────────────────────────────── */
function TableSkeleton({ rows = 6 }) {
  return (
    <div style={{ padding: "8px 4px" }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: "flex", gap: 12, padding: "10px 8px",
          borderBottom: i < rows - 1 ? `1px solid ${T.border}55` : "none" }}>
          <div className="ins-skel" style={{ width: 30, height: 12 }} />
          <div className="ins-skel" style={{ width: "16%", height: 12 }} />
          <div className="ins-skel" style={{ width: "10%", height: 12 }} />
          <div className="ins-skel" style={{ width: "26%", height: 12 }} />
          <div className="ins-skel" style={{ flex: 1, height: 12 }} />
          <div className="ins-skel" style={{ width: "12%", height: 12 }} />
        </div>
      ))}
    </div>
  );
}

/* ─── Empty state ────────────────────────────────────────────── */
function EmptyState({ glyph = "◌", title, hint }) {
  return (
    <div className="ins-fade" style={{ padding: "44px 20px", textAlign: "center" }}>
      <div style={{ fontSize: 30, color: T.muted, opacity: 0.5, marginBottom: 10 }}>{glyph}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: T.text, marginBottom: 4 }}>{title}</div>
      {hint && <div style={{ fontSize: 11, color: T.muted }}>{hint}</div>}
    </div>
  );
}

/* ─── Shared table styles ────────────────────────────────────── */
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

const BUDGET_COLS = [
  "#", "Agency", "Item Code", "Item Name", "Price",
  "Budget (Month)", "Budget Value", "Actual Sales", "Achievement",
  "Cur Forecast", "Possible (Fcst)",
  "Annual Budget", "FYTD Sales", "Annual Progress",
];

const PERF_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Last Month Sales", "Current Sales",
  "Cur Forecast", "Next Forecast",
  "Accuracy", "MoM Growth", "L3M Avg", "SHP",
  "Loss",
];

const LOSS_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Forecast", "Actual Sales", "Trade Stock",
  "Raw Loss", "Stockout Loss", "Other Loss", "Reason",
];

function SortBtn({ col, label, sortCol, setSortCol, accent }) {
  const active = sortCol === col;
  const acc = accent || T.purple;
  return (
    <button className="ins-btn" onClick={() => setSortCol(col)} style={{
      background: active ? acc + "18" : T.card,
      border: `1px solid ${active ? acc + "55" : T.border}`,
      color: active ? acc : T.muted,
      borderRadius: 999, padding: "5px 12px", cursor: "pointer",
      fontSize: 10.5, fontWeight: active ? 800 : 600, fontFamily: FONT_UI,
      display: "inline-flex", alignItems: "center", gap: 5,
      boxShadow: active ? "none" : SHADOW_SM,
    }}>
      {active && <span style={{ fontSize: 8 }}>▼</span>}
      {label}
    </button>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT — all state / data logic identical to v1
════════════════════════════════════════════════════════════════ */
export default function Insights() {
  const [rows, setRows]             = useState([]);
  const [budgetRows, setBudgetRows] = useState([]);
  const [meta, setMeta]             = useState(null);
  const [agency, setAgency]         = useState("");
  const [activeTab, setTab]         = useState("budget");
  const [sortBudget, setSortBudget] = useState("Budget_Qty");
  const [sortPerf, setSortPerf]     = useState("Model_Accuracy_%");
  const [sortLoss, setSortLoss]     = useState("Raw_Loss_Qty");
  const [loading, setLoading]       = useState(false);
  const [running, setRunning]       = useState(false);
  const [error, setError]           = useState("");

  // Pagination — one page counter per table, only used when "All Agencies" is selected
  const [budgetPage, setBudgetPage] = useState(1);
  const [perfPage, setPerfPage]     = useState(1);
  const [lossPage, setLossPage]     = useState(1);

  const fetchResults = async () => {
    setLoading(true); setError("");
    try {
      const res    = await fetch(`${API_BASE}/results`);
      const result = await res.json();
      if (!res.ok || !result.ok) throw new Error(result.error || "Failed to load");
      setRows(Array.isArray(result.rows) ? result.rows : []);
      setBudgetRows(Array.isArray(result.budget_rows) ? result.budget_rows : []);
      setMeta(result.meta || null);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const runEngine = async () => {
    setRunning(true); setError("");
    try {
      const res    = await fetch(`${API_BASE}/run`, { method: "POST", headers: { "Content-Type": "application/json" } });
      const result = await res.json();
      if (!res.ok || !result.ok) throw new Error(result.error || "Engine failed");
      await fetchResults();
    } catch (e) { setError(e.message); }
    finally { setRunning(false); }
  };

  useEffect(() => { fetchResults(); }, []);

  // Reset all pagination whenever the agency filter changes
  useEffect(() => { setBudgetPage(1); setPerfPage(1); setLossPage(1); }, [agency]);
  // Reset the relevant page whenever its sort changes
  useEffect(() => { setBudgetPage(1); }, [sortBudget]);
  useEffect(() => { setPerfPage(1); }, [sortPerf]);
  useEffect(() => { setLossPage(1); }, [sortLoss]);

  const agencies = useMemo(() => {
    const set = new Set([
      ...rows.map(r => r.Agency).filter(Boolean),
      ...budgetRows.map(r => r.Agency).filter(Boolean),
    ]);
    return [...set].sort();
  }, [rows, budgetRows]);

  const filtered = useMemo(() =>
    agency ? rows.filter(r => r.Agency === agency) : rows, [rows, agency]);

  const filteredBudget = useMemo(() =>
    agency ? budgetRows.filter(r => r.Agency === agency) : budgetRows,
  [budgetRows, agency]);

  const budgetTableRows = useMemo(() =>
    [...filteredBudget].sort((a, b) => {
      const av = a[sortBudget], bv = b[sortBudget];
      if (av == null) return 1; if (bv == null) return -1;
      return toNumber(bv) - toNumber(av);
    }), [filteredBudget, sortBudget]);

  const perfRows = useMemo(() =>
    [...filtered].sort((a, b) => {
      const av = a[sortPerf], bv = b[sortPerf];
      if (av == null) return 1; if (bv == null) return -1;
      return toNumber(bv) - toNumber(av);
    }), [filtered, sortPerf]);

  const lossRows = useMemo(() =>
    [...filtered]
      .filter(r => toNumber(r.Raw_Loss_Qty) > 0)
      .sort((a, b) => toNumber(b[sortLoss]) - toNumber(a[sortLoss])),
  [filtered, sortLoss]);

  // Paginated slices: full list when an agency is selected, 20/page otherwise
  const budgetPaged = useMemo(() =>
    agency ? budgetTableRows : budgetTableRows.slice((budgetPage - 1) * PAGE_SIZE, budgetPage * PAGE_SIZE),
  [budgetTableRows, agency, budgetPage]);

  const perfPaged = useMemo(() =>
    agency ? perfRows : perfRows.slice((perfPage - 1) * PAGE_SIZE, perfPage * PAGE_SIZE),
  [perfRows, agency, perfPage]);

  const lossPaged = useMemo(() =>
    agency ? lossRows : lossRows.slice((lossPage - 1) * PAGE_SIZE, lossPage * PAGE_SIZE),
  [lossRows, agency, lossPage]);

  /* ── KPI summaries (identical to v1) ── */
  const kpi = useMemo(() => {
    const d = filtered;
    const totalSales    = d.reduce((s, r) => s + toNumber(r.Current_Month_Sales), 0);
    const totalCurFcst  = d.reduce((s, r) => s + toNumber(r.Current_Month_Forecast), 0);

    // Budget totals from ALL budgeted items (some have budget but no sale)
    const bd = filteredBudget;
    const totalBudget       = bd.reduce((s, r) => s + toNumber(r.Budget_Qty), 0);
    const totalAnnualBudget = bd.reduce((s, r) => s + toNumber(r.Annual_Budget_Qty), 0);
    const totalFytdSales    = bd.reduce((s, r) => s + toNumber(r.FYTD_Sales_Qty), 0);

    // Value totals (budgeted unit price x qty; sales valued at budget price)
    const totalBudgetValue       = bd.reduce((s, r) => s + toNumber(r.Budget_Value), 0);
    const totalAnnualBudgetValue = bd.reduce((s, r) => s + toNumber(r.Annual_Budget_Value), 0);
    const totalFytdSalesValue    = bd.reduce((s, r) => s + toNumber(r.FYTD_Sales_Value), 0);

    // Aggregate gaps for the Performance KPI strip
    const budgetVsActualLoss   = Math.max(totalBudget  - totalSales, 0);
    const forecastVsActualLoss = Math.max(totalCurFcst - totalSales, 0);

    const totalRaw      = d.reduce((s, r) => s + toNumber(r.Raw_Loss_Qty), 0);
    const totalStockout = d.reduce((s, r) => s + toNumber(r.Stockout_Loss_Qty), 0);
    const totalOther    = d.reduce((s, r) => s + toNumber(r.Other_Loss_Qty), 0);
    const stockoutSkus  = d.filter(r => toNumber(r.Stockout_Loss_Qty) > 0).length;
    const otherSkus     = d.filter(r => r.Loss_Reason === "Other").length;
    const affectedAgencies = new Set(
      d.filter(r => toNumber(r.Raw_Loss_Qty) > 0).map(r => r.Agency).filter(Boolean)
    ).size;

    const budgetReach  = totalBudget > 0 ? (totalSales   / totalBudget) * 100 : null;
    const fcstVsBudget = totalBudget > 0 ? (totalCurFcst / totalBudget) * 100 : null;

    // FY progress: FYTD actual sales vs full-year budget
    const annualReach = totalAnnualBudget > 0 ? (totalFytdSales / totalAnnualBudget) * 100 : null;

    return {
      totalSales, totalCurFcst,
      totalBudget, totalAnnualBudget, totalFytdSales,
      totalBudgetValue, totalAnnualBudgetValue, totalFytdSalesValue,
      budgetVsActualLoss, forecastVsActualLoss,
      budgetReach, fcstVsBudget, annualReach,
      totalRaw, totalStockout, totalOther,
      stockoutSkus, otherSkus, affectedAgencies,
      recoverablePct: totalRaw > 0 ? (totalOther / totalRaw) * 100 : null,
      unrecoverablePct: totalRaw > 0 ? (totalStockout / totalRaw) * 100 : null,
    };
  }, [filtered, filteredBudget]);

  const tabAccent = activeTab === "loss" ? T.red : activeTab === "performance" ? T.blue : T.purple;

  return (
    <div style={{ minHeight: "100vh",
      background: `radial-gradient(1100px 500px at 85% -10%, ${T.purple}0E, transparent 60%),
                   radial-gradient(900px 420px at -10% 0%, ${T.blue}0C, transparent 55%),
                   ${T.bg}`,
      color: T.text, fontFamily: FONT_UI, padding: "26px 34px 40px" }}>
      <GlobalStyle />

      {/* ── Header (glass) ── */}
      <div className="ins-anim" style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        gap: 16, flexWrap: "wrap",
        background: `linear-gradient(135deg, ${T.card}F2, ${T.card}D9)`,
        backdropFilter: "blur(10px)",
        border: `1px solid ${T.border}`, borderRadius: 16,
        boxShadow: SHADOW_MD, padding: "18px 22px", marginBottom: 22 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 15 }}>
          <div style={{ width: 44, height: 44, borderRadius: 13, flexShrink: 0,
            background: `linear-gradient(135deg, ${T.purple}, ${T.blue})`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 20, color: "#fff",
            boxShadow: `0 8px 20px -8px ${T.purple}AA` }}>◈</div>
          <div>
            <div style={{ fontSize: 9, color: T.purple, letterSpacing: 3,
              textTransform: "uppercase", fontWeight: 900, marginBottom: 3 }}>
              Agency Performance & Stockout Insights
            </div>
            <h1 style={{ margin: 0, fontSize: 21, fontWeight: 900, letterSpacing: -0.6,
              background: `linear-gradient(90deg, ${T.text}, ${T.text}B3)`,
              WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
              Agency-Wise Performance & Loss Analysis
            </h1>
            <div style={{ marginTop: 5, color: T.muted, fontSize: 11, display: "flex",
              alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              {meta ? (
                <>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5,
                    background: T.green + "14", border: `1px solid ${T.green}30`,
                    borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.green }}>
                    <span className="ins-live-dot" style={{ width: 6, height: 6, borderRadius: "50%",
                      background: T.green, display: "inline-block" }} />
                    Data up to {meta.data_available_upto}
                  </span>
                  <span style={{ background: T.blue + "12", border: `1px solid ${T.blue}2E`,
                    borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.blue }}>
                    Next forecast · {meta.forecast_month}
                  </span>
                  {meta.current_month_label && (
                    <span style={{ background: T.purple + "12", border: `1px solid ${T.purple}2E`,
                      borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.purple }}>
                      Budget month · {meta.current_month_label}
                    </span>
                  )}
                </>
              ) : "SKU-wise sales, forecast accuracy, budget reach, and stockout loss."}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6,
            background: T.card, border: `1px solid ${T.border}`, borderRadius: 10,
            boxShadow: SHADOW_SM, padding: "4px 4px 4px 12px" }}>
            <span style={{ fontSize: 9, color: T.muted, fontWeight: 800,
              textTransform: "uppercase", letterSpacing: 1 }}>Agency</span>
            <select value={agency} onChange={e => setAgency(e.target.value)}
              style={{ background: "transparent", border: "none", color: T.text,
                fontSize: 13, fontFamily: FONT_UI, fontWeight: 600,
                padding: "6px 8px", cursor: "pointer", outline: "none", minWidth: 200 }}>
              <option value="">All Agencies ({agencies.length})</option>
              {agencies.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <button className="ins-btn" onClick={runEngine} disabled={running} style={{
            background: running ? T.subtle : `linear-gradient(135deg, ${T.purple}, ${T.blue})`,
            color: running ? T.muted : "#fff",
            border: "none", borderRadius: 10, padding: "11px 20px", fontSize: 13,
            fontWeight: 800, cursor: running ? "not-allowed" : "pointer",
            fontFamily: FONT_UI, whiteSpace: "nowrap",
            boxShadow: running ? "none" : `0 8px 18px -8px ${T.purple}AA`,
            display: "inline-flex", alignItems: "center", gap: 8 }}>
            {running ? <><span className="ins-spinner" /> Running…</> : <>▶ Run Engine</>}
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div className="ins-fade" style={{ background: T.red + "0D", border: `1px solid ${T.red}33`,
          borderLeft: `4px solid ${T.red}`, borderRadius: 12,
          padding: "13px 18px", color: T.red, marginBottom: 18, fontSize: 13,
          display: "flex", alignItems: "center", gap: 10, boxShadow: SHADOW_SM }}>
          <span style={{ fontSize: 15 }}>⚠</span> {error}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════
          KPI STRIP — Row 1: Performance (same KPIs)
      ═════════════════════════════════════════════════════════= */}
      <SectionLabel accent={T.blue}>Performance — {agency || "All Agencies"}</SectionLabel>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 20 }}>
        <Kpi glyph="▦" delay={0}   label="Budget (Month QTY)"     value={formatNum(kpi.totalBudget)}  color={T.purple} sub="All budgeted items" />
        <Kpi glyph="◧" delay={40}  label="Actual Sales (QTY)" value={formatNum(kpi.totalSales)}   color={T.sky} />
        <Kpi glyph="◔" delay={80}  label="Current Forecast (QTY)"   value={formatNum(kpi.totalCurFcst)} color={T.amber} />
        <Kpi glyph="▼" delay={120} label="Loss — Budget vs Actual"
          value={kpi.budgetVsActualLoss > 0 ? formatNum(kpi.budgetVsActualLoss) : "—"}
          color={kpi.budgetVsActualLoss > 0 ? T.red : T.green}
          sub="Budget − Actual sales" />
        <Kpi glyph="▽" delay={160} label="Loss — Forecast vs Actual"
          value={kpi.forecastVsActualLoss > 0 ? formatNum(kpi.forecastVsActualLoss) : "—"}
          color={kpi.forecastVsActualLoss > 0 ? T.red : T.green}
          sub="Forecast − Actual sales" />
      </div>

      {/* ══════════════════════════════════════════════════════════
          KPI STRIP — Row 2: Budget (same KPIs, gauges for ratios)
      ═════════════════════════════════════════════════════════= */}
      <SectionLabel accent={T.purple}>Budget — {agency || "All Agencies"}
        {meta?.current_month_label && <span style={{ marginLeft: 6, fontWeight: 900 }}>({meta.current_month_label})</span>}
      </SectionLabel>
      {/* Row A — quantities + reach gauges */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <Kpi glyph="◆" delay={0} label={`Annual Budget ${agency ? `— ${agency}` : ""}`}
          value={formatNum(kpi.totalAnnualBudget)}
          color={T.purple}
          sub="Full FY planned units" />

        <Kpi glyph="◨" delay={40} label="FYTD Sales"
          value={formatNum(kpi.totalFytdSales)}
          color={T.sky}
          sub={`FY to date · through ${meta?.current_month_label || "current month"}`} />

        <RatioKpi delay={80} label="FYTD / Annual Budget"
          value={kpi.annualReach}
          sub={`${formatNum(kpi.totalFytdSales)} of ${formatNum(kpi.totalAnnualBudget)} · left ${formatNum(Math.max(kpi.totalAnnualBudget - kpi.totalFytdSales, 0))}`} />

        <RatioKpi delay={120} label="Actual / Budget"
          value={kpi.budgetReach}
          sub="Monthly reach" />

        <RatioKpi delay={160} label="Forecast / Budget"
          value={kpi.fcstVsBudget}
          sub="Forecast alignment" />
      </div>

      {/* Row B — value (budgeted unit price × qty); compact display, full figure in sub */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 22 }}>
        <Kpi glyph="＄" delay={60} label="Annual Budget Value"
          value={<span title={formatNum(kpi.totalAnnualBudgetValue)}>{formatCompact(kpi.totalAnnualBudgetValue)}</span>}
          color={T.indigo || T.purple}
          sub={`${formatNum(kpi.totalAnnualBudgetValue)} · at budget price`} />

        <Kpi glyph="＄" delay={90} label="Budget Value (Month)"
          value={<span title={formatNum(kpi.totalBudgetValue)}>{formatCompact(kpi.totalBudgetValue)}</span>}
          color={T.purple}
          sub={`${formatNum(kpi.totalBudgetValue)} · ${meta?.current_month_label || "current month"}`} />

        <Kpi glyph="＄" delay={120} label="FYTD Sales Value"
          value={<span title={formatNum(kpi.totalFytdSalesValue)}>{formatCompact(kpi.totalFytdSalesValue)}</span>}
          color={T.teal}
          sub={`${formatNum(kpi.totalFytdSalesValue)} · at budget price`} />
      </div>

      {/* ══════════════════════════════════════════════════════════
          KPI STRIP — Row 3: Stockout Loss (same KPIs)
      ═════════════════════════════════════════════════════════= */}
      <SectionLabel accent={T.red}>
        Stockout Loss
        {meta?.current_month_label && (
          <span style={{ color: T.red, fontWeight: 900, marginLeft: 4 }}>— {meta.current_month_label}</span>
        )}
      </SectionLabel>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 26 }}>
        <Kpi glyph="▣" delay={0}   label="Total Raw Loss"    value={formatNum(kpi.totalRaw)}      color={T.red}   sub="Forecast − Actual sales" />
        <Kpi glyph="⊘" delay={30}  label="Stockout Loss"     value={formatNum(kpi.totalStockout)} color={T.red}   sub="Beyond available stock" />
        <Kpi glyph="◐" delay={60}  label="Other Loss"        value={formatNum(kpi.totalOther)}    color={T.amber} sub="Stock available — other reason" />
        <Kpi glyph="№" delay={90}  label="Stockout SKUs"     value={kpi.stockoutSkus}             color={T.red}   sub="True supply gap" />
        <Kpi glyph="№" delay={120} label="Other-reason SKUs" value={kpi.otherSkus}                color={T.amber} sub="Stock existed, sales short" />
        <Kpi glyph="%" delay={150} label="Unrecoverable %"   value={kpi.unrecoverablePct != null ? pct(kpi.unrecoverablePct) : "—"} color={T.red}   sub="Stockout / Raw loss" />
        <Kpi glyph="%" delay={180} label="Recoverable %"     value={kpi.recoverablePct != null ? pct(kpi.recoverablePct) : "—"}     color={T.amber} sub="Other / Raw loss" />
        <Kpi glyph="⌂" delay={210} label="Affected Agencies" value={kpi.affectedAgencies}         color={T.red}   sub="Agencies with any loss" />
      </div>

      {/* ══════════════════════════════════════════════════════════
          TABS — segmented control
      ═════════════════════════════════════════════════════════= */}
      <div style={{ display: "inline-flex", gap: 4, padding: 5, marginBottom: 14,
        background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 13, boxShadow: SHADOW_SM }}>
        <Tab active={activeTab === "budget"} onClick={() => setTab("budget")}
          badge={budgetTableRows.length} badgeColor={T.purple} accent={T.purple}>
          Budget Analysis
        </Tab>
        <Tab active={activeTab === "performance"} onClick={() => setTab("performance")}
          badge={perfRows.length} badgeColor={T.blue} accent={T.blue}>
          Agency Performance
        </Tab>
        <Tab active={activeTab === "loss"} onClick={() => setTab("loss")}
          badge={lossRows.length} badgeColor={kpi.stockoutSkus > 0 ? T.red : T.amber} accent={T.red}>
          Loss Analysis
        </Tab>
      </div>

      <div className="ins-fade" key={activeTab} style={{ background: T.card, border: `1px solid ${T.border}`,
        borderRadius: 16, padding: "18px 20px", boxShadow: SHADOW_MD,
        position: "relative", overflow: "hidden" }}>
        {/* accent top edge */}
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3,
          background: `linear-gradient(90deg, ${tabAccent}, ${tabAccent}22 60%, transparent)` }} />

        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 14, gap: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 10, color: tabAccent,
              textTransform: "uppercase", letterSpacing: 2, fontWeight: 900, marginBottom: 3 }}>
              {activeTab === "budget"
                ? `Budget Analysis ${agency ? `— ${agency}` : "— All Agencies"}`
                : activeTab === "performance"
                ? `SKU-Wise Performance ${agency ? `— ${agency}` : "— All Agencies"}`
                : `Loss Breakdown ${agency ? `— ${agency}` : "— All Agencies"}`}
            </div>
            <div style={{ fontSize: 10.5, color: T.muted }}>
              {activeTab === "budget"
                ? "All budgeted items · monthly achievement (actual & forecast vs budget) · annual progress"
                : activeTab === "performance"
                ? "Forecast accuracy · MoM growth · stockout loss · SHP"
                : "Per-SKU loss decomposition: Raw = Stockout + Other (stock-covered gap)"}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, color: T.muted, fontWeight: 800, textTransform: "uppercase", letterSpacing: 1 }}>Sort</span>
            {activeTab === "budget" ? (
              <>
                <SortBtn col="Budget_Qty"             label="Budget"       sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Budget_Value"           label="Value"        sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Achievement_%"          label="Achievement"  sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Possible_Achievement_%" label="Possible"     sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Annual_Budget_Qty"      label="Annual"       sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Annual_Reach_%"         label="Annual Reach" sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
              </>
            ) : activeTab === "performance" ? (
              <>
                <SortBtn col="Model_Accuracy_%"       label="Model Acc"     sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Realised_Accuracy_%"    label="Realised Acc"  sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Current_Month_Sales"    label="Sales"         sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Current_Month_Forecast" label="Cur Forecast"  sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Next_Month_Forecast"    label="Next Forecast" sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Raw_Loss_Qty"           label="Loss"          sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
              </>
            ) : (
              <>
                <SortBtn col="Raw_Loss_Qty"      label="Raw Loss" sortCol={sortLoss} setSortCol={setSortLoss} accent={T.red} />
                <SortBtn col="Stockout_Loss_Qty" label="Stockout" sortCol={sortLoss} setSortCol={setSortLoss} accent={T.red} />
                <SortBtn col="Other_Loss_Qty"    label="Other"    sortCol={sortLoss} setSortCol={setSortLoss} accent={T.red} />
              </>
            )}
            <span style={{ background: tabAccent + "14",
              border: `1px solid ${tabAccent}30`,
              color: tabAccent,
              borderRadius: 999, padding: "4px 12px", fontSize: 11, fontWeight: 900,
              fontFamily: FONT_MONO, marginLeft: 4 }}>
              {activeTab === "budget" ? budgetTableRows.length
                : activeTab === "performance" ? perfRows.length : lossRows.length} SKUs
            </span>
          </div>
        </div>

        {loading ? (
          <TableSkeleton rows={7} />
        ) : activeTab === "budget" ? (

          /* ── BUDGET ANALYSIS TABLE (all budgeted items) ── */
          budgetTableRows.length === 0 ? (
            <EmptyState glyph="▦" title="No budget records found"
              hint="Run the engine to build the budget analysis." />
          ) : (
            <>
              <div className="ins-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
                <table style={{ width: "100%", borderCollapse: "collapse",
                  fontFamily: FONT_MONO, fontSize: 11 }}>
                  <thead>
                    <tr>{BUDGET_COLS.map(h => <th key={h} style={thStyle}>{h}</th>)}</tr>
                    <tr>
                      {Array.from({ length: 4 }).map((_, i) => (
                        <th key={i} style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      ))}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.muted }}>Budgeted unit</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Cur month plan</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Qty × price</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Actual secondary</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Actual / Budget</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>M+1 from history</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>Forecast / Budget</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Full FY plan</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>FY to date</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>FYTD / Annual · left</th>
                    </tr>
                  </thead>
                  <tbody>
                    {budgetPaged.map((r, idx) => {
                      const rowNum  = agency ? idx + 1 : (budgetPage - 1) * PAGE_SIZE + idx + 1;
                      const bg      = idx % 2 === 0 ? T.card : T.surface + "66";
                      const budget  = toNumber(r.Budget_Qty);
                      const price   = toNumber(r.Budget_Price);
                      const bValue  = toNumber(r.Budget_Value);
                      const annual  = toNumber(r.Annual_Budget_Qty);
                      const ach     = r["Achievement_%"];
                      const poss    = r["Possible_Achievement_%"];
                      const reach   = r["Annual_Reach_%"];
                      const left    = toNumber(r.Annual_Remaining_Qty);
                      const fcst    = r.Current_Month_Forecast;
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.purple + "0D"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>{r.ItemCode}</td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right" }}>
                            {price > 0 ? formatNum(price, 2) : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, textAlign: "right" }}>
                            {budget > 0 ? formatNum(budget) : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, textAlign: "right" }}>
                            {bValue > 0 ? formatNum(bValue) : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.sky, fontWeight: 800, textAlign: "right" }}>
                            {formatNum(r.Current_Month_Sales)}
                          </td>
                          <td style={{ ...tdBase, background: bg, minWidth: 150, verticalAlign: "middle" }}>
                            <BudgetBar reach={ach} label="Actual / Budget" />
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.amber, fontWeight: 800, textAlign: "right" }}>
                            {fcst != null && toNumber(fcst) > 0
                              ? formatNum(fcst)
                              : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, minWidth: 150, verticalAlign: "middle" }}>
                            <BudgetBar reach={poss} label="Forecast / Budget" />
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, textAlign: "right" }}>
                            {annual > 0 ? formatNum(annual) : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.sky, textAlign: "right" }}>
                            {formatNum(r.FYTD_Sales_Qty)}
                          </td>
                          <td style={{ ...tdBase, background: bg, minWidth: 170, verticalAlign: "middle" }}>
                            <BudgetBar reach={reach} label="FYTD / Annual" />
                            <div style={{ fontSize: 9, color: T.muted, marginTop: 4 }}>
                              {annual > 0
                                ? <>Left to annual: <span style={{ color: T.text, fontWeight: 800 }}>{formatNum(left)}</span></>
                                : "No annual budget"}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={budgetPage} setPage={setBudgetPage} total={budgetTableRows.length} accent={T.purple} />
            </>
          )

        ) : activeTab === "performance" ? (

          /* ── PERFORMANCE TABLE ── */
          perfRows.length === 0 ? (
            <EmptyState glyph="◧" title="No SKU records found"
              hint="Run the engine to build agency performance." />
          ) : (
            <>
              <div className="ins-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
                <table style={{ width: "100%", borderCollapse: "collapse",
                  fontFamily: FONT_MONO, fontSize: 11 }}>
                  <thead>
                    <tr>{PERF_COLS.map(h => <th key={h} style={thStyle}>{h}</th>)}</tr>
                    <tr>
                      {Array.from({ length: 8 }).map((_, i) => (
                        <th key={i} style={{ ...thStyle, fontSize: 8, color: T.muted, fontWeight: 600, padding: "3px 14px", top: 34 }} />
                      ))}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>
                        <div style={{ display: "flex", gap: 6 }}>
                          <span style={{ color: T.blue }}>■ Model</span>
                          <span style={{ color: T.teal }}>■ Realised</span>
                        </div>
                      </th>
                      {/* MoM, L3M, SHP */}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      {/* Loss sub-header */}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>
                        <div style={{ display: "flex", gap: 4 }}>
                          <span style={{ color: T.red }}>■ SO</span>
                          <span style={{ color: T.amber }}>■ Other</span>
                        </div>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {perfPaged.map((r, idx) => {
                      const rowNum  = agency ? idx + 1 : (perfPage - 1) * PAGE_SIZE + idx + 1;
                      const momRaw  = r["MoM_Growth_%"];
                      const momNull = momRaw == null;
                      const growth  = momNull ? 0 : toNumber(momRaw);
                      const bg      = idx % 2 === 0 ? T.card : T.surface + "66";
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.blue + "0D"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>{r.ItemCode}</td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>{formatNum(r.Last_Month_Sales)}</td>
                          <td style={{ ...tdBase, background: bg, color: T.sky, fontWeight: 800, textAlign: "right" }}>{formatNum(r.Current_Month_Sales)}</td>
                          <td style={{ ...tdBase, background: bg, color: T.amber, fontWeight: 800, textAlign: "right" }}>{formatNum(r.Current_Month_Forecast)}</td>
                          <td style={{ ...tdBase, background: bg, color: T.green, fontWeight: 800, textAlign: "right" }}>{formatNum(r.Next_Month_Forecast)}</td>
                          <DualAccuracyCell
                            modelAcc={r["Model_Accuracy_%"]} realisedAcc={r["Realised_Accuracy_%"]}
                            realisedAvailable={r.Realised_Accuracy_Available} modelUsed={r.Model_Used} bg={bg} />
                          <td style={{ ...tdBase, background: bg, textAlign: "right",
                            color: momNull ? T.muted : growth >= 0 ? T.green : T.red, fontWeight: 900 }}>
                            {momNull ? <span style={{ color: T.muted, fontWeight: 400 }}>N/A</span>
                              : <>{growth >= 0 ? "▲" : "▼"} {Math.abs(growth).toFixed(1)}%</>}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {r.L3M_Moving_Avg != null ? formatNum(r.L3M_Moving_Avg, 1)
                              : <span style={{ color: T.muted }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {r.Current_SHP != null
                              ? <span style={{ fontWeight: 800, color: r.Current_SHP >= 2 ? T.green : r.Current_SHP >= 1 ? T.amber : T.red }}>
                                  {Number(r.Current_SHP).toFixed(2)}
                                </span>
                              : <span style={{ color: T.muted }}>—</span>}
                          </td>
                          <LossCell row={r} bg={bg} />
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={perfPage} setPage={setPerfPage} total={perfRows.length} accent={T.blue} />
            </>
          )

        ) : (

          /* ── LOSS ANALYSIS TABLE ── */
          lossRows.length === 0 ? (
            <EmptyState glyph="✓" title="No loss records found"
              hint="Every SKU met or exceeded its forecast this month." />
          ) : (
            <>
              <div className="ins-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
                <table style={{ width: "100%", borderCollapse: "collapse",
                  fontFamily: FONT_MONO, fontSize: 11 }}>
                  <thead>
                    <tr>{LOSS_COLS.map(h => <th key={h} style={thStyle}>{h}</th>)}</tr>
                    <tr>
                      {Array.from({ length: 4 }).map((_, i) => (
                        <th key={i} style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      ))}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>M+1 from history</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Actual secondary</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }}>
                        <div style={{ display: "flex", gap: 4 }}>
                          <span style={{ color: T.blue }}>WH</span>
                          <span style={{ color: T.teal }}>+ DB</span>
                        </div>
                      </th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.red }}>Fcst − Sales</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.red }}>Raw &gt; Stock</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>Stock absorbed</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {lossPaged.map((r, idx) => {
                      const rowNum   = agency ? idx + 1 : (lossPage - 1) * PAGE_SIZE + idx + 1;
                      const bg       = idx % 2 === 0 ? T.card : T.surface + "66";
                      const raw      = toNumber(r.Raw_Loss_Qty);
                      const stockout = toNumber(r.Stockout_Loss_Qty);
                      const other    = toNumber(r.Other_Loss_Qty);
                      const stock    = toNumber(r.Trade_Stock_Qty);
                      const soWidth  = raw > 0 ? Math.round((stockout / raw) * 100) : 0;
                      const otherW   = raw > 0 ? Math.round((other / raw) * 100) : 0;
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.red + "08"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>{r.ItemCode}</td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.amber, fontWeight: 800, textAlign: "right" }}>
                            {r.Current_Month_Forecast != null && toNumber(r.Current_Month_Forecast) > 0
                              ? formatNum(r.Current_Month_Forecast)
                              : <span style={{ color: T.muted }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.sky, fontWeight: 800, textAlign: "right" }}>
                            {formatNum(r.Current_Month_Sales)}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right", verticalAlign: "top" }}>
                            <div style={{ fontWeight: 800, color: stock > 0 ? T.text : T.muted }}>{formatNum(stock)}</div>
                            <div style={{ fontSize: 9, color: T.muted, marginTop: 2 }}>
                              WH {formatNum(r.WH_Stock_Current)} + DB {formatNum(r.DB_Stock_Current)}
                            </div>
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right", verticalAlign: "top" }}>
                            <div style={{ color: T.red, fontWeight: 900, fontSize: 13 }}>{formatNum(raw)}</div>
                            <div style={{ marginTop: 4, height: 5, borderRadius: 3,
                              background: T.border, overflow: "hidden", minWidth: 60 }}>
                              <div style={{ height: "100%", display: "flex" }}>
                                <div style={{ width: `${soWidth}%`, background: T.red, transition: "width 0.3s" }} />
                                <div style={{ width: `${otherW}%`, background: T.amber, transition: "width 0.3s" }} />
                              </div>
                            </div>
                            <div style={{ fontSize: 8, color: T.muted, marginTop: 2, display: "flex", gap: 6, justifyContent: "flex-end" }}>
                              <span style={{ color: T.red }}>■ {soWidth}%</span>
                              <span style={{ color: T.amber }}>■ {otherW}%</span>
                            </div>
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right",
                            color: stockout > 0 ? T.red : T.muted, fontWeight: stockout > 0 ? 900 : 400 }}>
                            {stockout > 0 ? formatNum(stockout) : "—"}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right",
                            color: other > 0 ? T.amber : T.muted, fontWeight: other > 0 ? 800 : 400 }}>
                            {other > 0 ? formatNum(other) : "—"}
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <ReasonBadge reason={r.Loss_Reason} />
                            {r.Loss_Reason === "Stockout" && (
                              <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>
                                Stock short by {formatNum(stockout)} units
                              </div>
                            )}
                            {r.Loss_Reason === "Other" && (
                              <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>
                                Stock existed · execution gap
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={lossPage} setPage={setLossPage} total={lossRows.length} accent={T.red} />
            </>
          )
        )}
      </div>
    </div>
  );
}