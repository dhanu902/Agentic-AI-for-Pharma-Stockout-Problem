// src/pages/Insights.jsx
//
// UI v8 — BUDGET vs ACTUAL vs FORECAST, all priced via DistributorPrice.xlsx.
//
// Business change (confirmed): Budget.xlsx's qty IS "primary budgeted qty",
// and primary qty is ~= the secondary (RD/distributor sell-through) sales
// TARGET qty — so there's no separate Primary Sales stream any more.
// SECONDARY sales (Current_Month_Secondary_Sales) is now simply "Actual
// Sales" — the ONE headline number compared to budget everywhere. Forecast
// is back in scope as a third KPI: Current Forecast + Loss (Forecast vs
// Actual), sourced from forecast_master_mapped.csv (the AI-model +
// trend-baseline forecast already mapped onto every budgeted SKU).
//
// PRICING: ONE shared distributor price (DistributorPrice.xlsx, priced
// per-SKU-per-month) values Budget, Actual AND Forecast alike — the old
// two-price-base split (primary/BudgetPrice vs RD/Inventory.xlsx) is gone.
//
// "SKU Mapping & Coverage" section (below the tab table) fed by
// meta.mapping_diagnostics from insights_engine.py: fully-mapped SKUs,
// budget-without-product-code (SYN), no-distributor-price. Each category is
// an expandable card listing affected SKUs; respects the agency filter;
// ends with a pricing-rule footnote.

import React, { useEffect, useMemo, useState } from "react";
import T from "../theme";

const API_BASE = "/api/insights";
const PAGE_SIZE = 20;

/* ─── Helpers ─────────────────────────────────────────────────── */
function toNumber(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
function formatNum(v, d = 0) { return toNumber(v).toLocaleString(undefined, { maximumFractionDigits: d }); }
function pct(v) { return toNumber(v).toFixed(1) + "%"; }

/* Compact display for large plain numbers: 64,471,465,913 -> 64.47B. */
function formatCompact(v) {
  const n = toNumber(v);
  if (Math.abs(n) >= 1e6) {
    return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n);
  }
  return n.toLocaleString();
}

/* Money formatting — every monetary KPI on the page goes through these two
   so the "LKR" prefix and compacting behaviour are identical everywhere. */
function formatMoney(v) { return "LKR " + toNumber(v).toLocaleString(undefined, { maximumFractionDigits: 0 }); }
function formatMoneyCompact(v) { return "LKR " + formatCompact(v); }

/* Budget reach colour: ≥100% green, ≥80% amber, <80% red */
/* Stock-holding period, in months of L3M demand the stock on hand covers.
   Under 1 month is thin cover (red), 1–2 workable (amber), 2+ comfortable. */
function shpColor(v) {
  if (v == null) return T.muted;
  const n = toNumber(v);
  return n >= 2 ? T.green : n >= 1 ? T.amber : T.red;
}

/* Over-100% is its OWN state, not "extra green": exceeding budget is a
   different situation from hitting it, and the business wants it visually
   distinct. Black marks it; 100 exactly still reads as on-target green. */
function budgetReachColor(v) {
  if (v == null) return T.muted;
  const n = toNumber(v);
  if (n > 100) return T.text;              // over-achievement — black
  return n >= 100 ? T.green : n >= 80 ? T.amber : T.red;
}

/* A row counts toward master-scoped KPI totals unless explicitly flagged
   false. Undefined/null (older cached data, or tables that never carry
   the flag) defaults to "included" so nothing silently zeroes out. */
function isMasterScoped(r) { return r.Is_In_Master !== false; }

/* ─── Design tokens (UI only) ────────────────────────────────── */
const FONT_UI   = "'Inter', 'IBM Plex Sans', sans-serif";
const FONT_MONO = "'JetBrains Mono', monospace";
const SHADOW_SM = "0 1px 2px rgba(16,24,40,0.04), 0 6px 18px -12px rgba(16,24,40,0.10)";
const SHADOW_MD = "0 2px 4px rgba(16,24,40,0.05), 0 16px 40px -20px rgba(16,24,40,0.22)";
const SHADOW_LG = "0 4px 10px rgba(16,24,40,0.06), 0 34px 68px -30px rgba(16,24,40,0.30)";

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

    /* Table <-> graph flip. Only the panel face animates; the KPI strip
       above it is a separate subtree and never re-renders on flip. */
    @keyframes ins-flip-in {
      from { opacity: 0; transform: perspective(1400px) rotateY(-14deg) scale(0.985); }
      to   { opacity: 1; transform: perspective(1400px) rotateY(0deg) scale(1); }
    }
    .ins-flip-face { animation: ins-flip-in 0.42s cubic-bezier(0.22,1,0.36,1) both; transform-origin: center; }
    @media (prefers-reduced-motion: reduce) {
      .ins-flip-face { animation: ins-fade-in 0.2s ease both; }
    }

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
        radial-gradient(760px 380px at 108% 8%, ${T.purple}12, transparent 65%),
        radial-gradient(640px 340px at -8% 88%, ${T.blue}0E, transparent 60%);
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
    .ins-kpi { border-radius: 18px !important; }
    .ins-kpi:hover {
      transform: translateY(-3px);
      border-color: ${T.purple}55 !important;
      box-shadow: 0 2px 8px rgba(16,24,40,0.06), 0 30px 60px -26px ${T.purple}4D !important;
    }
    .ins-btn { border-radius: 11px !important; }
    .ins-btn:not(:disabled):hover  { transform: translateY(-1px) scale(1.015); filter: saturate(1.12) brightness(1.03); }
    .ins-btn:not(:disabled):active { transform: translateY(0) scale(0.985); }
    .ins-row:hover td { box-shadow: inset 0 0 0 999px ${T.purple}08; }
    input::placeholder { color: ${T.muted}AA; }
    ::selection { background: ${T.purple}2E; }
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

/* ─── Plain (non-monetary) KPI card — counts, percentages, qty-only gaps ── */
function Kpi({ label, value, color, sub, glyph, delay = 0 }) {
  const col = color || T.blue;
  return (
    <div className="ins-kpi ins-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 150, position: "relative",
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

/* ─── THE monetary KPI card format — used for every value+qty pair on the
   page. label / LKR value / qty + price source tag, always in that order. ── */
function ValueKpi({ label, value, qty, qtyLabel, color, glyph, delay = 0, priceNote }) {
  const col = color || T.blue;
  return (
    <div className="ins-kpi ins-anim" style={{
      animationDelay: `${delay}ms`,
      background: T.card, border: `1px solid ${T.border}`, borderRadius: 14,
      padding: "16px 18px", flex: 1, minWidth: 190, position: "relative",
      overflow: "hidden", boxShadow: SHADOW_SM }}>
      <div style={{ position: "absolute", top: -30, right: -30, width: 110, height: 110,
        background: `radial-gradient(circle, ${col}26, transparent 70%)`,
        pointerEvents: "none" }} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        {glyph && <GlyphIcon glyph={glyph} color={col} size={24} />}
        <span style={{ fontSize: 9.5, color: T.muted, textTransform: "uppercase",
          letterSpacing: 1.4, fontWeight: 800 }}>{label}</span>
      </div>
      <div title={formatMoney(value)} style={{ fontSize: 21, fontWeight: 900, color: col,
        fontFamily: FONT_MONO, lineHeight: 1, letterSpacing: -0.5,
        fontVariantNumeric: "tabular-nums" }}>
        {formatMoneyCompact(value)}
      </div>
      <div style={{ fontSize: 10.5, color: T.muted, marginTop: 6, fontWeight: 600,
        display: "flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
        <span style={{ color: T.text, fontWeight: 800, fontFamily: FONT_MONO }}>{formatNum(qty)}</span>
        <span>{qtyLabel || "units"}</span>
        {priceNote && (
          <span style={{ marginLeft: "auto", fontSize: 8.5, fontWeight: 800,
            color: T.muted, textTransform: "uppercase", letterSpacing: 0.6 }}>{priceNote}</span>
        )}
      </div>
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

/* ─── Ratio KPI card with gauge (percentages have no single "price", so
   they keep their own compact layout rather than the value+qty format) ── */
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

/* ─── Budget reach mini-bar ───────────────────────────────────── */
/* `reach` is null when there is NO budget to measure against — that is an
   undefined ratio, not 0% and not 200%, so it renders as an explicit "No
   budget" rather than a number that would read as real performance.
   Over-100% shows its TRUE value (450% stays 450%, never capped); only
   the BAR WIDTH saturates at 100, since a bar can't overflow its track. */
function BudgetBar({ reach, label, noneLabel }) {
  if (reach == null) {
    return (
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}>{label || "Reach"}</span>
          <span style={{ fontSize: 9, fontWeight: 700, color: T.muted, fontFamily: FONT_UI }}>
            {noneLabel || "No budget"}
          </span>
        </div>
        <div style={{ height: 5, borderRadius: 3, background: T.border, overflow: "hidden" }}>
          <div style={{ height: "100%", width: "100%",
            background: `repeating-linear-gradient(90deg, ${T.border}, ${T.border} 3px, transparent 3px, transparent 6px)` }} />
        </div>
      </div>
    );
  }
  const raw  = toNumber(reach);
  const n    = Math.min(raw, 100);   // bar width only — the FIGURE is uncapped
  const over = raw > 100;
  const col  = budgetReachColor(reach);
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}>{label || "Reach"}</span>
        <span style={{ fontSize: 10, fontWeight: 900, color: col, fontFamily: FONT_MONO }}
          title={over ? "Exceeded budget" : undefined}>
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

/* ─── Deviation bar — same label-left / figure-right / filled-track
   layout as BudgetBar, but for a SIGNED gap (forecast vs a baseline).
   Bar width is |deviation| capped at 100%; colour is green when the gap
   is small, amber mid, red when it's large. `qty` shows the raw unit gap
   under the bar, since the % alone hides scale. ─────────────────── */
function DeviationBar({ pct: pctVal, qty, label }) {
  if (pctVal == null && qty == null) {
    return <span style={{ color: T.muted, fontSize: 10 }}>—</span>;
  }
  const hasPct = pctVal != null;
  const n      = hasPct ? toNumber(pctVal) : 0;
  const mag    = Math.min(Math.abs(n), 100);
  // Under-forecast (negative) and over-forecast (positive) are both
  // errors — colour on magnitude, not direction.
  const col = !hasPct ? T.muted
    : mag <= 10 ? T.green
    : mag <= 25 ? T.amber
    : T.red;
  const q = toNumber(qty);
  return (
    <div style={{ minWidth: 118 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, gap: 8 }}>
        <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 10, fontWeight: 900, color: col, fontFamily: FONT_MONO }}>
          {hasPct ? `${n > 0 ? "+" : ""}${n.toFixed(1)}%` : "—"}
        </span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: T.border, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${mag}%`,
          background: `linear-gradient(90deg, ${col}CC, ${col})`,
          borderRadius: 3, transition: "width 0.4s cubic-bezier(0.22,1,0.36,1)" }} />
      </div>
      {qty != null && (
        <div style={{ fontSize: 8.5, color: T.muted, marginTop: 3, fontFamily: FONT_MONO }}>
          {q > 0 ? "+" : ""}{formatNum(q)} units
        </div>
      )}
    </div>
  );
}

/* ─── Accuracy bar — 0-100 score, higher is better (so the colour scale
   runs the opposite way to DeviationBar). Null when there was no actual
   to score the forecast against. ──────────────────────────────── */
function AccuracyBar({ value, label }) {
  if (value == null) {
    return (
      <div style={{ minWidth: 110 }}>
        <div style={{ fontSize: 9, color: T.muted, fontWeight: 600, marginBottom: 4 }}>{label}</div>
        <span style={{ color: T.muted, fontSize: 9.5, fontStyle: "italic" }}>Not scorable</span>
      </div>
    );
  }
  const n   = Math.min(Math.max(toNumber(value), 0), 100);
  const col = n >= 80 ? T.green : n >= 60 ? T.amber : T.red;
  return (
    <div style={{ minWidth: 110 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, gap: 8 }}>
        <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 10, fontWeight: 900, color: col, fontFamily: FONT_MONO }}>
          {n.toFixed(1)}%
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
function SectionLabel({ children, accent, right }) {
  const col = accent || T.muted;
  return (
    <div style={{ fontSize: 10, color: col, textTransform: "uppercase",
      letterSpacing: 2, fontWeight: 900, marginBottom: 10, display: "flex",
      alignItems: "center", gap: 8 }}>
      <span style={{ width: 18, height: 3, borderRadius: 2, display: "inline-block",
        background: `linear-gradient(90deg, ${col}, ${col}44)` }} />
      {children}
      <span style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${T.border}, transparent)` }} />
      {right}
    </div>
  );
}

/* ─── KPI carousel nav — arrow buttons + dot pagination (UI only; which
   KPI section shows is just activeTab, the SAME state the table tabs
   below already use). ────────────────────────────────────────────── */
function KpiNav({ tabs, active, onGo, onStep }) {
  const arrowStyle = {
    width: 26, height: 26, borderRadius: "50%", border: "none", cursor: "pointer",
    background: T.text, color: T.bg, fontSize: 13, fontWeight: 900,
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    boxShadow: SHADOW_SM, flexShrink: 0,
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0, marginLeft: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        {tabs.map(t => (
          <button key={t} className="ins-btn" onClick={() => onGo(t)}
            aria-label={`Show ${t} KPIs`} title={`Show ${t} KPIs`}
            style={{ border: "none", cursor: "pointer", padding: 0,
              width: active === t ? 20 : 6, height: 6, borderRadius: 999,
              background: active === t ? T.text : T.border,
              transition: "width 0.25s cubic-bezier(0.22,1,0.36,1), background 0.25s ease" }} />
        ))}
      </div>
      <button className="ins-btn" onClick={() => onStep(-1)} aria-label="Previous KPI section" style={arrowStyle}>‹</button>
      <button className="ins-btn" onClick={() => onStep(1)} aria-label="Next KPI section" style={arrowStyle}>›</button>
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

/* ─── Trend chart — FY-to-date Actual vs Budget ───────────────────
   Hand-rolled inline SVG rather than a charting dependency: two series
   over a handful of months needs an axis, two polylines and dots, and
   this keeps the page free of another bundle. Both lines share one Y
   scale (they're the same unit) so their gap is read directly. ─── */
function TrendChart({ months, actual, budget, metric, height = 300 }) {
  const [hover, setHover] = useState(null);
  if (!months || months.length === 0) {
    return <EmptyState glyph="◇" title="No trend data"
      hint="Run the engine to build the FY-to-date series." />;
  }

  const W = 900, H = height, PAD_L = 68, PAD_R = 20, PAD_T = 24, PAD_B = 46;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const maxVal = Math.max(...actual, ...budget, 1);
  // Round the top of the scale up to a clean number so gridline labels
  // read as round figures instead of arbitrary maxima.
  const mag  = Math.pow(10, Math.floor(Math.log10(maxVal)));
  const top  = Math.ceil(maxVal / (mag / 2)) * (mag / 2);

  const n = months.length;
  const x = (i) => PAD_L + (n === 1 ? innerW / 2 : (i * innerW) / (n - 1));
  const y = (v) => PAD_T + innerH - (toNumber(v) / top) * innerH;

  const line = (arr) => arr.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const area = (arr) =>
    `${PAD_L},${PAD_T + innerH} ${line(arr)} ${x(n - 1)},${PAD_T + innerH}`;

  const ticks = 4;
  const fmtTick = (v) =>
    metric === "value" ? formatCompact(v) : formatCompact(v);
  const label = (m) => {
    const [yy, mm] = String(m).split("-");
    return `${["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(mm)]} ${String(yy).slice(2)}`;
  };

  /* Hover: an invisible full-height band per month captures the pointer,
     so the tooltip appears anywhere in that month's column rather than
     only within a few pixels of the line itself. */
  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const vx   = ((e.clientX - rect.left) / rect.width) * W;   // client px -> viewBox units
    if (vx < PAD_L - 12 || vx > W - PAD_R + 12) { setHover(null); return; }
    let best = 0, bestD = Infinity;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(x(i) - vx);
      if (d < bestD) { bestD = d; best = i; }
    }
    setHover(best);
  };

  const hv = hover != null && hover < n ? hover : null;
  // Flip the card to the left of the crosshair near the right edge so it
  // never runs off the panel.
  const hvLeftPct = hv != null ? (x(hv) / W) * 100 : 0;
  const flip = hvLeftPct > 62;

  return (
    <div style={{ width: "100%", overflowX: "auto", position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", minWidth: 520, display: "block" }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="ins-actual-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor={T.sky} stopOpacity="0.22" />
            <stop offset="100%" stopColor={T.sky} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* gridlines + Y labels */}
        {Array.from({ length: ticks + 1 }).map((_, i) => {
          const v  = (top / ticks) * i;
          const yy = y(v);
          return (
            <g key={i}>
              <line x1={PAD_L} y1={yy} x2={W - PAD_R} y2={yy}
                stroke={T.border} strokeWidth="1" strokeDasharray={i === 0 ? "0" : "3 4"} />
              <text x={PAD_L - 10} y={yy + 3.5} textAnchor="end"
                fill={T.muted} fontSize="10" fontFamily={FONT_MONO}>{fmtTick(v)}</text>
            </g>
          );
        })}

        {/* crosshair — drawn under the lines so it never obscures them */}
        {hv != null && (
          <line x1={x(hv)} y1={PAD_T} x2={x(hv)} y2={PAD_T + innerH}
            stroke={T.muted} strokeWidth="1" strokeOpacity="0.55" />
        )}

        {/* budget — dashed, it's the plan not the outcome */}
        <polyline points={line(budget)} fill="none" stroke={T.purple}
          strokeWidth="2.5" strokeDasharray="6 5" strokeLinejoin="round" strokeLinecap="round" />
        {/* actual — solid + filled, it's the headline */}
        <polygon points={area(actual)} fill="url(#ins-actual-fill)" />
        <polyline points={line(actual)} fill="none" stroke={T.sky}
          strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />

        {months.map((m, i) => (
          <g key={m}>
            <circle cx={x(i)} cy={y(budget[i])} r={hv === i ? 5 : 3.5}
              fill={T.card} stroke={T.purple} strokeWidth={hv === i ? 3 : 2} />
            <circle cx={x(i)} cy={y(actual[i])} r={hv === i ? 6.5 : 4.5}
              fill={T.sky} stroke={T.card} strokeWidth={hv === i ? 3 : 2} />
            <text x={x(i)} y={H - PAD_B + 20} textAnchor="middle"
              fill={hv === i ? T.text : T.muted} fontSize="10.5" fontFamily={FONT_UI}
              fontWeight={hv === i ? 900 : 700}>{label(m)}</text>
          </g>
        ))}
      </svg>

      {/* Tooltip card — HTML rather than SVG so it can use the same
          shadows/typography as the rest of the page. Positioned as a % of
          the container so it tracks the crosshair at any chart width. */}
      {hv != null && (
        <div style={{
          position: "absolute", top: 54,
          left: `${hvLeftPct}%`,
          transform: flip ? "translateX(calc(-100% - 14px))" : "translateX(14px)",
          background: T.card, border: `1px solid ${T.border}`, borderRadius: 12,
          boxShadow: SHADOW_LG, padding: "12px 16px", pointerEvents: "none",
          minWidth: 168, zIndex: 5,
        }}>
          <div style={{ fontSize: 12.5, fontWeight: 900, color: T.text,
            fontFamily: FONT_MONO, marginBottom: 8 }}>{months[hv]}</div>
          {[["Budget", budget[hv], T.purple], ["Actual", actual[hv], T.sky]].map(([lbl, v, c]) => (
            <div key={lbl} style={{ display: "flex", justifyContent: "space-between",
              alignItems: "baseline", gap: 18, marginTop: 3 }}>
              <span style={{ fontSize: 11.5, color: c, fontWeight: 700, fontFamily: FONT_UI }}>{lbl}</span>
              <span style={{ fontSize: 13, color: T.text, fontWeight: 900, fontFamily: FONT_MONO }}>
                {formatNum(v)}
              </span>
            </div>
          ))}
          <div style={{ marginTop: 8, paddingTop: 7, borderTop: `1px solid ${T.border}`,
            display: "flex", justifyContent: "space-between", gap: 18 }}>
            <span style={{ fontSize: 10, color: T.muted, fontWeight: 700, fontFamily: FONT_UI }}>
              {metric === "value" ? "LKR" : "units"} · vs budget
            </span>
            <span style={{ fontSize: 11, fontWeight: 900, fontFamily: FONT_MONO,
              color: toNumber(budget[hv]) > 0
                ? (toNumber(actual[hv]) >= toNumber(budget[hv]) ? T.green : T.red)
                : T.muted }}>
              {toNumber(budget[hv]) > 0
                ? `${((toNumber(actual[hv]) / toNumber(budget[hv])) * 100).toFixed(1)}%`
                : "—"}
            </span>
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 18, justifyContent: "center", marginTop: 6 }}>
        <span style={{ fontSize: 11, color: T.muted, fontWeight: 700, fontFamily: FONT_UI,
          display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 18, height: 3, background: T.sky, borderRadius: 2 }} /> Actual
        </span>
        <span style={{ fontSize: 11, color: T.muted, fontWeight: 700, fontFamily: FONT_UI,
          display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 18, height: 3, borderTop: `3px dashed ${T.purple}` }} /> Budget
        </span>
      </div>
    </div>
  );
}

/* ─── Qty + value in one cell ─────────────────────────────────────
   Units on top in the column's accent, LKR underneath in muted grey.
   One figure, two units — so one column, not two. `dash` renders when
   the qty is absent, keeping "nothing here" visually distinct from a
   real zero. ─────────────────────────────────────────────────── */
function QtyValue({ qty, value, color, alwaysShow }) {
  const q = toNumber(qty);
  const v = toNumber(value);
  const showQty = alwaysShow || qty != null;
  return (
    <div style={{ textAlign: "right", lineHeight: 1.35 }}>
      <div style={{ color: showQty ? (color || T.text) : T.muted,
        fontWeight: showQty ? 800 : 400, fontSize: 11.5 }}>
        {showQty ? formatNum(q) : "—"}
      </div>
      <div style={{ color: T.muted, fontWeight: 500, fontSize: 9.5, marginTop: 1 }}>
        {v > 0 ? `LKR ${formatNum(v)}` : "—"}
      </div>
    </div>
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

/* ─── Pagination ──────────────────────────────────────────────── */
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

/* ─── SKU Mapping & Coverage (rendered below the tab table) ───── */
const MAP_SEVERITY = {
  ok:    { label: "OK",    glyph: "✓" },
  info:  { label: "Info",  glyph: "ℹ" },
  warn:  { label: "Check", glyph: "△" },
  error: { label: "Fix",   glyph: "✕" },
};
function mapSevColor(sev) {
  return sev === "ok" ? T.green : sev === "warn" ? T.amber : sev === "error" ? T.red : T.blue;
}

/* One expandable category card: header (severity tag · label · count),
   description, then SKU chips — first 12, "show all" to expand. */
function MappingCategoryCard({ cat, agency }) {
  const [open, setOpen] = useState(false);
  const sev  = MAP_SEVERITY[cat.severity] || MAP_SEVERITY.info;
  const col  = mapSevColor(cat.severity);
  const all  = Array.isArray(cat.items) ? cat.items : [];
  // Respect the page-level agency filter; unknown-agency items always shown.
  const items = agency ? all.filter(i => !i.Agency || i.Agency === agency) : all;
  const shown = open ? items : items.slice(0, 12);
  const hasIssue = cat.severity !== "ok";

  return (
    <div style={{ background: T.card, border: `1px solid ${T.border}`,
      borderLeft: `4px solid ${col}`, borderRadius: 12, padding: "14px 16px",
      boxShadow: SHADOW_SM, flex: "1 1 340px", minWidth: 300 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ width: 20, height: 20, borderRadius: 6, flexShrink: 0,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 11, color: col, background: col + "16", border: `1px solid ${col}30` }}>
          {sev.glyph}
        </span>
        <span style={{ fontSize: 11.5, fontWeight: 800, color: T.text, fontFamily: FONT_UI }}>
          {cat.label}
        </span>
        <span style={{ marginLeft: "auto", fontFamily: FONT_MONO, fontSize: 14,
          fontWeight: 900, color: col }}>
          {formatNum(items.length)}
          {agency && items.length !== all.length && (
            <span style={{ fontSize: 9, color: T.muted, fontWeight: 600 }}> / {formatNum(all.length)} all</span>
          )}
        </span>
        <InfoTag text={sev.label} color={col} />
      </div>

      <div style={{ fontSize: 10.5, color: T.muted, marginTop: 7, lineHeight: 1.55 }}>
        {cat.description}
      </div>

      {items.length > 0 && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 10 }}>
            {shown.map(it => (
              <span key={it.ItemCode}
                title={`${it.ItemName || "(no name)"} · ${it.Agency || "Unknown agency"}`}
                style={{ fontSize: 9.5, fontFamily: FONT_MONO, fontWeight: 700,
                  color: hasIssue ? col : T.muted,
                  background: (hasIssue ? col : T.muted) + "0E",
                  border: `1px solid ${(hasIssue ? col : T.muted)}26`,
                  borderRadius: 6, padding: "3px 7px", maxWidth: 220,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {it.ItemCode}{it.ItemName ? ` · ${it.ItemName}` : ""}
              </span>
            ))}
          </div>
          {items.length > 12 && (
            <button className="ins-btn" onClick={() => setOpen(o => !o)}
              style={{ marginTop: 8, background: T.card, border: `1px solid ${T.border}`,
                borderRadius: 999, padding: "4px 12px", fontSize: 10, fontWeight: 700,
                color: T.muted, cursor: "pointer", fontFamily: FONT_UI }}>
              {open ? "Show less" : `Show all ${formatNum(items.length)}`}
            </button>
          )}
        </>
      )}
      {items.length === 0 && (
        <div style={{ fontSize: 10, color: T.muted, marginTop: 8, fontStyle: "italic" }}>
          {agency ? "None for this agency." : "None — nothing in this bucket."}
        </div>
      )}
    </div>
  );
}

/* Whole diagnostics section: source-coverage strip + category cards +
   pricing-rule footnote. Rendered once, below the tab card. */
function MappingDiagnostics({ diagnostics, agency }) {
  if (!diagnostics || !Array.isArray(diagnostics.categories) || diagnostics.categories.length === 0) {
    return null;
  }
  const src = diagnostics.sources || {};
  const srcChips = [
    ["SKU master",           src.sku_master],
    ["Budget",               src.budget],
    ["Distributor price",    src.distributor_price],
    ["Focus sales",          src.focus_sales],
    ["Leftover (raw) sales", src.all_sku_sales],
  ].filter(([, v]) => v != null);

  // Issues first (error > warn > info), fully-mapped last.
  const order = { error: 0, warn: 1, info: 2, ok: 3 };
  const cats = [...diagnostics.categories].sort(
    (a, b) => (order[a.severity] ?? 2) - (order[b.severity] ?? 2)
  );

  return (
    <div style={{ marginTop: 28 }}>
      <SectionLabel accent={T.muted}>
        SKU Mapping & Coverage — {agency || "All Agencies"}
        {diagnostics.month && <span style={{ marginLeft: 6, fontWeight: 900 }}>({diagnostics.month})</span>}
      </SectionLabel>

      {/* Source universes: how many distinct SKUs each source contributes.
          Everything is keyed through the SKU master, so these counts show
          where the universes diverge. */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
        {srcChips.map(([label, v]) => (
          <span key={label} style={{ fontSize: 10, color: T.muted, fontWeight: 700,
            background: T.surface, border: `1px solid ${T.border}`,
            borderRadius: 999, padding: "4px 12px", fontFamily: FONT_UI }}>
            {label}: <span style={{ color: T.text, fontWeight: 900, fontFamily: FONT_MONO }}>{formatNum(v)}</span> SKUs
          </span>
        ))}
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {cats.map(cat => (
          <MappingCategoryCard key={cat.key} cat={cat} agency={agency} />
        ))}
      </div>

      {/* Pricing-rule footnote — one shared price, never a flat annual rate. */}
      <div style={{ marginTop: 14, background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 10, padding: "10px 14px", fontSize: 10.5, color: T.muted,
        lineHeight: 1.6 }}>
        <span style={{ fontWeight: 900, color: T.text, textTransform: "uppercase",
          letterSpacing: 1, fontSize: 9 }}>Pricing rule · </span>
        <span style={{ color: T.purple, fontWeight: 800 }}>Budget, Actual & Forecast</span>
        {" are ALL valued at the SAME "}<span style={{ fontWeight: 800 }}>distributor price</span>
        {" (DistributorPrice.xlsx, priced per SKU per month from its CreationDate) — "}
        {"budget qty ≈ secondary sales target qty, so a direct apples-to-apples comparison "}
        {"needs only one price base. Multi-month totals (FYTD, Annual) price EACH month "}
        {"independently and sum, rather than one flat rate across the range. SKUs with no "}
        {"distributor price on file at or before the target month show value fields as 0 "}
        {"(flagged above)."}
      </div>
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

/* Budget table surfaces ONE shared price (Distributor_Unit_Price, from
   DistributorPrice.xlsx) that values Budget, Actual (secondary) AND
   Forecast alike — per the pricing rule in insights_engine.py: budget qty
   ≈ secondary sales target qty, so everything shares one price base. */
/* Every qty/value pair shares ONE column — units on top, LKR beneath (see
   QtyValue). Splitting them across two columns doubled the table's width
   for what is really one figure expressed two ways. */
const BUDGET_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Distributor Price (LKR)",
  "Budget", "Actual Sales", "Achievement (%)",
  "Annual Budget", "FYTD Actual", "Annual Progress (%)",
];

/* SKU-wise Performance — qty AND value side by side for each of the three
   monthly lenses. Loss lives on its own tab now (it was duplicated here);
   L3M Avg excludes the reporting month and SHP is that month's opening
   no-risk stock over it — see build_l3m_and_shp() in the engine. */
const PERF_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Last Month Actual", "Current Actual", "Current Forecast",
  "MoM Growth (%)", "L3M Avg (units)", "SHP (months)",
];

const LOSS_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Actual Sales (units)", "Budget (units)",
  "WH Stock (units)", "DB Stock (units)", "Trade Stock (units)",
  "WH SHP (months)", "DB SHP (months)",
  "Raw Loss (units)", "Stockout Loss (units)", "Other Loss (units)", "Reason",
];

/* Forecast Analysis — our model's forecast vs the external (business-
   supplied) forecast, both scored against the SAME month's budget and
   actual. Deviations are qty-based only (never value), per the business
   rule; accuracy is a per-SKU score vs that month's actual. */
const FORECAST_COLS = [
  "#", "Agency", "Item Code", "Item Name",
  "Distributor Price (LKR)",
  "Budget (units)", "Actual (units)",
  "Sys Forecast", "Sales Forecast",
  "Sys Forecast vs Budget", "Sys Forecast vs Actual",
  "Sales Forecast vs Budget", "Sales Forecast vs Actual",
  "Sys Model Accuracy",
];

/* KPI carousel order — also the table-tab order; the two are the same
   state (activeTab), so picking a KPI section switches the default table
   below to match, and vice versa. */
const KPI_TABS = ["performance", "budget", "forecast", "loss"];

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
   MAIN COMPONENT
════════════════════════════════════════════════════════════════ */
export default function Insights() {
  const [rows, setRows]             = useState([]);
  const [budgetRows, setBudgetRows] = useState([]);
  const [forecastRows, setForecastRows] = useState([]);
  const [meta, setMeta]             = useState(null);
  const [agency, setAgency]         = useState("");
  const [activeTab, setTab]         = useState("performance");
  const [sortBudget, setSortBudget] = useState("Budget_Qty");
  const [sortPerf, setSortPerf]     = useState("Current_Month_Secondary_Sales");
  const [sortLoss, setSortLoss]     = useState("Raw_Loss_Qty");
  const [sortForecast, setSortForecast] = useState("Model_Forecast_Qty");
  const [loading, setLoading]       = useState(false);
  const [running, setRunning]       = useState(false);
  const [error, setError]           = useState("");
  const [showMapping, setShowMapping] = useState(false); // SKU Mapping & Coverage — collapsed by default

  // Pagination — one page counter per table, only used when "All Agencies" is selected
  const [budgetPage, setBudgetPage] = useState(1);
  const [perfPage, setPerfPage]     = useState(1);
  const [lossPage, setLossPage]     = useState(1);
  const [forecastPage, setForecastPage] = useState(1);

  // ── Trend chart (Agency Performance flip side) ──
  // `showTrend` flips ONLY the SKU-wise Performance panel; the KPI strip
  // above and every other tab are untouched.
  //
  // Scope is an explicit drill-down: All -> Agency -> SKU. Each level
  // narrows the one above it, so the SKU picker only ever offers SKUs
  // inside the chosen agency and you can't land on a combination that
  // has no data.
  const [showTrend, setShowTrend]   = useState(false);
  const [trendLevel, setTrendLevel] = useState("all");     // "all" | "agency" | "sku"
  const [trendAgency, setTrendAgency] = useState("");
  const [trendItem, setTrendItem]   = useState("");
  const [trendData, setTrendData]   = useState(null);
  const [trendMetric, setTrendMetric] = useState("qty");   // "qty" | "value"
  const [trendLoading, setTrendLoading] = useState(false);

  const fetchResults = async () => {
    setLoading(true); setError("");
    try {
      const res    = await fetch(`${API_BASE}/results`);
      const result = await res.json();
      if (!res.ok || !result.ok) throw new Error(result.error || "Failed to load");
      setRows(Array.isArray(result.rows) ? result.rows : []);
      setBudgetRows(Array.isArray(result.budget_rows) ? result.budget_rows : []);
      setForecastRows(Array.isArray(result.forecast_rows) ? result.forecast_rows : []);
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

  /* Trend series — its own endpoint (the data is per-SKU-per-month, far
     too big to ship inside /results for one tab's chart). Fetched lazily:
     nothing loads until the panel is actually flipped to the graph. */
  const fetchTrend = async (ag, item) => {
    setTrendLoading(true);
    try {
      const qs = new URLSearchParams();
      if (ag)   qs.set("agency", ag);
      if (item) qs.set("item", item);
      const res    = await fetch(`${API_BASE}/trend?${qs.toString()}`);
      const result = await res.json();
      if (!res.ok || !result.ok) throw new Error(result.error || "Failed to load trend");
      setTrendData(result);
    } catch (e) { setTrendData({ ok: false, months: [], series: {}, error: e.message }); }
    finally { setTrendLoading(false); }
  };

  // Each level only sends the filters that level actually implies:
  // "all" sends none, "agency" sends the agency, "sku" sends both.
  useEffect(() => {
    if (!showTrend) return;
    const ag = trendLevel === "all" ? "" : trendAgency;
    const it = trendLevel === "sku" ? trendItem : "";
    fetchTrend(ag, it);
  }, [showTrend, trendLevel, trendAgency, trendItem]);

  // The page-level agency filter seeds the chart's scope, so flipping to
  // the graph while filtered to one agency shows that agency, not "all".
  useEffect(() => {
    setTrendAgency(agency || "");
    setTrendLevel(agency ? "agency" : "all");
    setTrendItem("");
  }, [agency]);

  // Changing agency invalidates a SKU picked from the previous one.
  useEffect(() => { setTrendItem(""); }, [trendAgency]);

  // KPI carousel — steps activeTab forward/back through KPI_TABS (wraps
  // around at either end). Table tab below follows automatically since
  // it's the same state.
  const stepKpiSection = (delta) => {
    const i = KPI_TABS.indexOf(activeTab);
    const next = KPI_TABS[(i + delta + KPI_TABS.length) % KPI_TABS.length];
    setTab(next);
  };

  // Reset all pagination whenever the agency filter changes
  useEffect(() => { setBudgetPage(1); setPerfPage(1); setLossPage(1); setForecastPage(1); }, [agency]);
  // Reset the relevant page whenever its sort changes
  useEffect(() => { setBudgetPage(1); }, [sortBudget]);
  useEffect(() => { setPerfPage(1); }, [sortPerf]);
  useEffect(() => { setLossPage(1); }, [sortLoss]);
  useEffect(() => { setForecastPage(1); }, [sortForecast]);

  const agencies = useMemo(() => {
    const set = new Set([
      ...rows.map(r => r.Agency).filter(Boolean),
      ...budgetRows.map(r => r.Agency).filter(Boolean),
      ...forecastRows.map(r => r.Agency).filter(Boolean),
    ]);
    return [...set].sort();
  }, [rows, budgetRows, forecastRows]);

  const filtered = useMemo(() =>
    agency ? rows.filter(r => r.Agency === agency) : rows, [rows, agency]);

  const filteredBudget = useMemo(() =>
    agency ? budgetRows.filter(r => r.Agency === agency) : budgetRows,
  [budgetRows, agency]);

  const filteredForecast = useMemo(() =>
    agency ? forecastRows.filter(r => r.Agency === agency) : forecastRows,
  [forecastRows, agency]);

  // KPI totals scope to master-SKU rows only (Is_In_Master !== false) —
  // mirrors insights_engine.py: a SKU with sales activity but no budget
  // entry still appears in the table below, but is excluded from summed
  // KPIs so totals don't mix two different SKU universes. budget_rows are
  // already entirely master-scoped by construction (built from
  // sku_master_full.csv), so no extra filter is needed there.
  const masterScoped = useMemo(() => filtered.filter(isMasterScoped), [filtered]);
  const excludedCount = filtered.length - masterScoped.length;

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

  const forecastTableRows = useMemo(() =>
    [...filteredForecast].sort((a, b) => {
      const av = a[sortForecast], bv = b[sortForecast];
      if (av == null) return 1; if (bv == null) return -1;
      return toNumber(bv) - toNumber(av);
    }), [filteredForecast, sortForecast]);

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

  /* Agency + SKU pickers for the trend chart. Both are built from the
     rows already on hand (no extra request). The SKU list is scoped to
     the chart's OWN agency selection, so drilling to SKU level can only
     offer SKUs that actually sit inside the chosen agency. */
  const trendAgencyOptions = useMemo(
    () => [...new Set(rows.map(r => r.Agency).filter(Boolean))].sort(),
    [rows]);

  const trendSkuOptions = useMemo(() => {
    const scoped = trendAgency ? rows.filter(r => r.Agency === trendAgency) : rows;
    const seen = new Map();
    for (const r of scoped) {
      const code = r.ItemCode == null ? "" : String(r.ItemCode);
      if (code && !seen.has(code)) seen.set(code, r.ItemName || "—");
    }
    return [...seen.entries()]
      .map(([code, name]) => ({ code, name }))
      .sort((a, b) => a.code.localeCompare(b.code, undefined, { numeric: true }));
  }, [rows, trendAgency]);

  const forecastPaged = useMemo(() =>
    agency ? forecastTableRows
           : forecastTableRows.slice((forecastPage - 1) * PAGE_SIZE, forecastPage * PAGE_SIZE),
  [forecastTableRows, agency, forecastPage]);

  /* ── KPI summaries ──
     ACTUAL (secondary) sales is the headline comparison basis vs budget —
     budget qty ≈ secondary sales target qty (business confirmed), so both
     are valued at the SAME distributor price. Forecast (Current_Forecast_*)
     is a third lens, restored alongside Budget vs Actual — v10: this now
     reads the EXTERNAL forecast (Forecast.xlsx, current reporting month),
     the SAME source as the Forecast tab's "External Forecast", so the two
     "current forecast" readings on the page always agree. Loss (Raw/
     Stockout/Other, and Forecast-vs-Actual) is computed backend-side at
     that same distributor price. */
  const kpi = useMemo(() => {
    const d = masterScoped;
    const totalActualQty      = d.reduce((s, r) => s + toNumber(r.Current_Month_Secondary_Sales), 0);
    const totalActualValue    = d.reduce((s, r) => s + toNumber(r.Current_Month_Secondary_Sales_Value), 0);
    const totalForecastQty    = d.reduce((s, r) => s + toNumber(r.Current_Forecast_Qty), 0);
    const totalForecastValue  = d.reduce((s, r) => s + toNumber(r.Current_Forecast_Value), 0);

    // Budget totals from ALL budgeted items (some have budget but no sale) —
    // already master-scoped by construction.
    const bd = filteredBudget;
    const totalBudget       = bd.reduce((s, r) => s + toNumber(r.Budget_Qty), 0);
    const totalAnnualBudget = bd.reduce((s, r) => s + toNumber(r.Annual_Budget_Qty), 0);
    const totalFytdActual    = bd.reduce((s, r) => s + toNumber(r.FYTD_Secondary_Sales_Qty), 0);

    // Budget + Actual value: ALWAYS distributor price (DistributorPrice.xlsx).
    const totalBudgetValue       = bd.reduce((s, r) => s + toNumber(r.Budget_Value), 0);
    const totalAnnualBudgetValue = bd.reduce((s, r) => s + toNumber(r.Annual_Budget_Value), 0);
    const totalFytdActualValue   = bd.reduce((s, r) => s + toNumber(r.FYTD_Secondary_Sales_Value), 0);

    const totalRaw      = d.reduce((s, r) => s + toNumber(r.Raw_Loss_Qty), 0);
    const totalStockout = d.reduce((s, r) => s + toNumber(r.Stockout_Loss_Qty), 0);
    const totalOther    = d.reduce((s, r) => s + toNumber(r.Other_Loss_Qty), 0);

    // Loss value — computed backend-side at distributor price (same basis
    // as the qty it decomposes).
    const totalRawValue      = d.reduce((s, r) => s + toNumber(r.Raw_Loss_Value), 0);
    const totalStockoutValue = d.reduce((s, r) => s + toNumber(r.Stockout_Loss_Value), 0);
    const totalOtherValue    = d.reduce((s, r) => s + toNumber(r.Other_Loss_Value), 0);

    // Forecast vs Actual gap (qty + value) — restored KPI.
    const totalForecastLossQty   = d.reduce((s, r) => s + toNumber(r.Forecast_Vs_Actual_Loss_Qty), 0);
    const totalForecastLossValue = d.reduce((s, r) => s + toNumber(r.Forecast_Vs_Actual_Loss_Value), 0);

    const stockoutSkus  = d.filter(r => toNumber(r.Stockout_Loss_Qty) > 0).length;
    const otherSkus     = d.filter(r => r.Loss_Reason === "Other").length;
    const affectedAgencies = new Set(
      d.filter(r => toNumber(r.Raw_Loss_Qty) > 0).map(r => r.Agency).filter(Boolean)
    ).size;

    const actualReach = totalBudget > 0 ? (totalActualQty / totalBudget) * 100 : null;

    // FY progress: FYTD ACTUAL sales vs full-year budget
    const annualReach = totalAnnualBudget > 0 ? (totalFytdActual / totalAnnualBudget) * 100 : null;

    return {
      totalActualQty, totalActualValue, totalForecastQty, totalForecastValue,
      totalBudget, totalAnnualBudget, totalFytdActual,
      totalBudgetValue, totalAnnualBudgetValue, totalFytdActualValue,
      totalForecastLossQty, totalForecastLossValue,
      actualReach, annualReach,
      totalRaw, totalStockout, totalOther,
      totalRawValue, totalStockoutValue, totalOtherValue,
      stockoutSkus, otherSkus, affectedAgencies,
      recoverablePct: totalRaw > 0 ? (totalOther / totalRaw) * 100 : null,
      unrecoverablePct: totalRaw > 0 ? (totalStockout / totalRaw) * 100 : null,
    };
  }, [masterScoped, filteredBudget]);

  /* Forecast KPI summary — agency-aware (meta's own averages are global,
     so recompute here so the strip respects the agency filter). Accuracy
     averages skip SKUs that couldn't be scored (no actual to score
     against) rather than counting them as 0. */
  const fcKpi = useMemo(() => {
    const f = filteredForecast;
    const myQty    = f.reduce((s, r) => s + toNumber(r.Model_Forecast_Qty), 0);
    const myValue  = f.reduce((s, r) => s + toNumber(r.Model_Forecast_Value), 0);
    const extQty   = f.reduce((s, r) => s + toNumber(r.External_Forecast_Qty), 0);
    const extValue = f.reduce((s, r) => s + toNumber(r.External_Forecast_Value), 0);

    const mean = (key) => {
      const vals = f.map(r => r[key]).filter(v => v != null).map(toNumber);
      return vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    };
    const myAcc  = mean("Model_Accuracy_%");
    const extAcc = mean("External_Accuracy_%");
    const scoredMy  = f.filter(r => r["Model_Accuracy_%"] != null).length;
    const scoredExt = f.filter(r => r["External_Accuracy_%"] != null).length;

    return { myQty, myValue, extQty, extValue, myAcc, extAcc, scoredMy, scoredExt };
  }, [filteredForecast]);

  const tabAccent = activeTab === "loss" ? T.red
    : activeTab === "performance" ? T.blue
    : activeTab === "forecast" ? T.teal
    : T.purple;

  return (
    <div className="page-shell" style={{ minHeight: "100vh",
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
          <div className="hero-icon" style={{ width: 44, height: 44, borderRadius: 13, flexShrink: 0,
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
              Budget vs Actual vs Forecast — Agency Performance
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
                  {meta.current_month_label && (
                    <span style={{ background: T.purple + "12", border: `1px solid ${T.purple}2E`,
                      borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.purple }}>
                      Budget month · {meta.current_month_label}
                    </span>
                  )}
                  {excludedCount > 0 && (
                    <span title="SKUs with sales activity but no entry in the budget master list — excluded from KPI totals below, still visible in the Performance table"
                      style={{ background: T.amber + "12", border: `1px solid ${T.amber}2E`,
                      borderRadius: 999, padding: "2px 10px", fontWeight: 700, color: T.amber }}>
                      {excludedCount} SKU{excludedCount === 1 ? "" : "s"} excluded from totals (not budgeted)
                    </span>
                  )}
                </>
              ) : "SKU-wise actual sales, forecast, budget reach, and stockout loss."}
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
          KPI CAROUSEL — one section visible at a time: Performance →
          Budget → Stockout Loss. Arrow buttons / dots (KpiNav, in the
          SectionLabel's right slot) step through KPI_TABS; this is just
          activeTab, the SAME state the table tabs below use, so the
          active KPI section and the default table always match (and
          clicking a table tab below flips the carousel to match, too).
          Pure UI reorganisation — no KPI math changed.
      ═════════════════════════════════════════════════════════= */}
      <div className="ins-fade">
        {activeTab === "performance" && (
          <>
            <SectionLabel accent={T.blue}
              right={<KpiNav tabs={KPI_TABS} active={activeTab} onGo={setTab} onStep={stepKpiSection} />}>
              Performance — {agency || "All Agencies"}
              {meta?.current_month_label && <span style={{ marginLeft: 6, fontWeight: 900 }}>({meta.current_month_label})</span>}
            </SectionLabel>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 26 }}>
              <ValueKpi glyph="▦" delay={0}
                label="Budget (Month)"
                value={kpi.totalBudgetValue}
                qty={kpi.totalBudget} qtyLabel="units"
                color={T.purple}
                priceNote="DB price" />

              <ValueKpi glyph="◧" delay={40}
                label="Actual Sales"
                value={kpi.totalActualValue}
                qty={kpi.totalActualQty} qtyLabel="units"
                color={T.sky}
                priceNote="DB price" />

              <ValueKpi glyph="◔" delay={80}
                label="Current Forecast (Sales)"
                value={kpi.totalForecastValue}
                qty={kpi.totalForecastQty} qtyLabel="units"
                color={T.teal}
                priceNote="DB price" />

              {/* Both loss cards use the SAME value+qty format as the three
                  above. The Budget-vs-Actual figure is the per-SKU floored
                  sum (kpi.totalRaw*) — identical to "Total Raw Loss" in the
                  Stockout strip. It is NOT aggregate budget − aggregate
                  actual, which would let over-performing SKUs cancel out
                  under-performing ones and make the two cards disagree. */}
              <ValueKpi glyph="▼" delay={120}
                label="Loss (Budget vs Actual)"
                value={kpi.totalRawValue}
                qty={kpi.totalRaw} qtyLabel="units"
                color={T.red}
                priceNote="DB price" />

              <ValueKpi glyph="◇" delay={160}
                label="Loss (Sales Forecast vs Actual)"
                value={kpi.totalForecastLossValue}
                qty={kpi.totalForecastLossQty} qtyLabel="units"
                color={T.amber}
                priceNote="DB price" />
            </div>
          </>
        )}

        {activeTab === "budget" && (
          <>
            <SectionLabel accent={T.purple}
              right={<KpiNav tabs={KPI_TABS} active={activeTab} onGo={setTab} onStep={stepKpiSection} />}>
              Budget — {agency || "All Agencies"}
              {meta?.current_month_label && <span style={{ marginLeft: 6, fontWeight: 900 }}>({meta.current_month_label})</span>}
            </SectionLabel>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 26 }}>
              <ValueKpi glyph="◆" delay={0}
                label="Annual Budget"
                value={kpi.totalAnnualBudgetValue}
                qty={kpi.totalAnnualBudget} qtyLabel="full FY planned units"
                color={T.purple}
                priceNote="Distributor price" />

              <ValueKpi glyph="◨" delay={40}
                label="FYTD Actual Sales"
                value={kpi.totalFytdActualValue}
                qty={kpi.totalFytdActual} qtyLabel={`sold through ${meta?.current_month_label || "current month"}`}
                color={T.sky}
                priceNote="Distributor price" />

              <RatioKpi delay={80} label="FYTD Actual / Annual Budget"
                value={kpi.annualReach}
                sub={`${formatNum(kpi.totalFytdActual)} of ${formatNum(kpi.totalAnnualBudget)} · left ${formatNum(Math.max(kpi.totalAnnualBudget - kpi.totalFytdActual, 0))}`} />

              <RatioKpi delay={120} label="Actual Sales / Budget"
                value={kpi.actualReach}
                sub="Monthly reach" />
            </div>
          </>
        )}

        {activeTab === "forecast" && (
          <>
            <SectionLabel accent={T.teal}
              right={<KpiNav tabs={KPI_TABS} active={activeTab} onGo={setTab} onStep={stepKpiSection} />}>
              Forecast — {agency || "All Agencies"}
              {meta?.forecast_eval_month && <span style={{ marginLeft: 6, fontWeight: 900 }}>({meta.forecast_eval_month})</span>}
            </SectionLabel>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 26 }}>
              <ValueKpi glyph="◔" delay={0}
                label="Sys Forecast"
                value={fcKpi.myValue}
                qty={fcKpi.myQty} qtyLabel="units"
                color={T.teal}
                priceNote="DB price" />

              <ValueKpi glyph="◑" delay={40}
                label="Sales Forecast"
                value={fcKpi.extValue}
                qty={fcKpi.extQty} qtyLabel="units"
                color={T.amber}
                priceNote="DB price" />

              <RatioKpi delay={80} label="Sys Model Accuracy"
                value={fcKpi.myAcc}
                sub={fcKpi.scoredMy > 0
                  ? `Avg across ${formatNum(fcKpi.scoredMy)} scored SKUs`
                  : "No SKUs scorable this month"} />

              <RatioKpi delay={120} label="Sales Accuracy"
                value={fcKpi.extAcc}
                sub={fcKpi.scoredExt > 0
                  ? `Avg across ${formatNum(fcKpi.scoredExt)} scored SKUs`
                  : "No external forecast this month"} />
            </div>
          </>
        )}

        {activeTab === "loss" && (
          <>
            <SectionLabel accent={T.red}
              right={<KpiNav tabs={KPI_TABS} active={activeTab} onGo={setTab} onStep={stepKpiSection} />}>
              Stockout Loss
              {meta?.current_month_label && (
                <span style={{ color: T.red, fontWeight: 900, marginLeft: 4 }}>— {meta.current_month_label}</span>
              )}
            </SectionLabel>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 26 }}>
              {/* Same figure as "Loss (Budget vs Actual)" in the Performance
                  strip — both read kpi.totalRaw* so they always agree. */}
              <ValueKpi glyph="▣" delay={0}
                label="Total Raw Loss (Budget vs Actual)"
                value={kpi.totalRawValue}
                qty={kpi.totalRaw} qtyLabel="units"
                color={T.red}
                priceNote="DB price" />

              <ValueKpi glyph="⊘" delay={30}
                label="Stockout Loss"
                value={kpi.totalStockoutValue}
                qty={kpi.totalStockout} qtyLabel="units"
                color={T.red}
                priceNote="DB price" />

              <ValueKpi glyph="◐" delay={60}
                label="Other Loss"
                value={kpi.totalOtherValue}
                qty={kpi.totalOther} qtyLabel="units"
                color={T.amber}
                priceNote="DB price" />

              <Kpi glyph="№" delay={90}  label="Stockout SKUs"     value={kpi.stockoutSkus}             color={T.red}   sub="True supply gap" />
              <Kpi glyph="№" delay={120} label="Other-reason SKUs" value={kpi.otherSkus}                color={T.amber} sub="Stock existed, sales short" />
              <Kpi glyph="%" delay={150} label="Unrecoverable %"   value={kpi.unrecoverablePct != null ? pct(kpi.unrecoverablePct) : "—"} color={T.red}   sub="Stockout / Raw loss" />
              <Kpi glyph="%" delay={180} label="Recoverable %"     value={kpi.recoverablePct != null ? pct(kpi.recoverablePct) : "—"}     color={T.amber} sub="Other / Raw loss" />
              <Kpi glyph="⌂" delay={210} label="Affected Agencies" value={kpi.affectedAgencies}         color={T.red}   sub="Agencies with any loss" />
            </div>
          </>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════
          TABS — segmented control
      ═════════════════════════════════════════════════════════= */}
      <div style={{ display: "inline-flex", gap: 4, padding: 5, marginBottom: 14,
        background: T.surface, border: `1px solid ${T.border}`,
        borderRadius: 13, boxShadow: SHADOW_SM }}>
        <Tab active={activeTab === "performance"} onClick={() => setTab("performance")}
          badge={perfRows.length} badgeColor={T.blue} accent={T.blue}>
          Agency Performance
        </Tab>
        <Tab active={activeTab === "budget"} onClick={() => setTab("budget")}
          badge={budgetTableRows.length} badgeColor={T.purple} accent={T.purple}>
          Budget Analysis
        </Tab>
        <Tab active={activeTab === "forecast"} onClick={() => setTab("forecast")}
          badge={forecastTableRows.length} badgeColor={T.teal} accent={T.teal}>
          Forecast Analysis
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
                ? `${showTrend ? "Actual vs Budget Trend" : "SKU-Wise Performance"} ${agency ? `— ${agency}` : "— All Agencies"}`
                : activeTab === "forecast"
                ? `Forecast Analysis ${agency ? `— ${agency}` : "— All Agencies"}`
                : `Loss Breakdown ${agency ? `— ${agency}` : "— All Agencies"}`}
            </div>
            <div style={{ fontSize: 10.5, color: T.muted }}>
              {activeTab === "budget"
                ? "All budgeted items · budget & actual @ shared distributor price · annual progress"
                : activeTab === "performance"
                ? (showTrend
                    ? "Fiscal year to date · actual vs budget by month · all SKUs or one"
                    : "Actual, forecast & last month in units and value · MoM growth · L3M avg (prior 3 months) · SHP on this month's opening no-risk stock")
                : activeTab === "forecast"
                ? `My forecast vs external forecast${meta?.forecast_eval_month ? ` for ${meta.forecast_eval_month}` : ""} · deviations vs budget & actual (qty basis) · per-SKU model accuracy`
                : "Per-SKU loss decomposition: Raw (Budget − Actual Sales) = Stockout + Other (stock-covered gap)"}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {/* Flip control — Agency Performance ONLY. Deliberately not
                rendered on the other tabs: the trend series is an
                Actual-vs-Budget view that belongs to this tab alone. */}
            {activeTab === "performance" && (
              <button className="ins-btn" onClick={() => setShowTrend(v => !v)}
                title={showTrend ? "Back to the SKU table" : "Flip to the Actual vs Budget trend"}
                style={{
                  background: showTrend ? T.blue : T.card,
                  border: `1px solid ${showTrend ? T.blue : T.border}`,
                  color: showTrend ? "#fff" : T.blue,
                  borderRadius: 999, padding: "6px 14px", cursor: "pointer",
                  fontSize: 10.5, fontWeight: 800, fontFamily: FONT_UI,
                  display: "inline-flex", alignItems: "center", gap: 6,
                  marginRight: 4, boxShadow: showTrend ? "none" : SHADOW_SM,
                }}>
                <span style={{ fontSize: 12, lineHeight: 1 }}>⇄</span>
                {showTrend ? "Show table" : "Show graph"}
              </button>
            )}
            {activeTab === "performance" && showTrend ? null : (
            <>
            <span style={{ fontSize: 10, color: T.muted, fontWeight: 800, textTransform: "uppercase", letterSpacing: 1 }}>Sort</span>
            {activeTab === "budget" ? (
              <>
                <SortBtn col="Budget_Qty"                             label="Budget"         sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Budget_Value"                           label="Value"          sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Current_Month_Secondary_Sales_Value"    label="Actual Value"   sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Achievement_%"                          label="Achievement"    sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Annual_Budget_Qty"                      label="Annual"         sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
                <SortBtn col="Annual_Reach_%"                         label="Annual Reach"   sortCol={sortBudget} setSortCol={setSortBudget} accent={T.purple} />
              </>
            ) : activeTab === "performance" ? (
              <>
                <SortBtn col="Current_Month_Secondary_Sales" label="Actual Sales" sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Current_Forecast_Qty"          label="Forecast"     sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="MoM_Growth_%"                  label="MoM Growth"   sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="L3M_Moving_Avg"                label="L3M Avg"      sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
                <SortBtn col="Current_SHP"                   label="SHP"          sortCol={sortPerf} setSortCol={setSortPerf} accent={T.blue} />
              </>
            ) : activeTab === "forecast" ? (
              <>
                <SortBtn col="Model_Forecast_Qty"    label="Sys Forecast"   sortCol={sortForecast} setSortCol={setSortForecast} accent={T.teal} />
                <SortBtn col="External_Forecast_Qty" label="Sales Forecast" sortCol={sortForecast} setSortCol={setSortForecast} accent={T.teal} />
                <SortBtn col="Model_Accuracy_%"      label="Sys Accuracy"   sortCol={sortForecast} setSortCol={setSortForecast} accent={T.teal} />
                <SortBtn col="Budget_Qty"            label="Budget"        sortCol={sortForecast} setSortCol={setSortForecast} accent={T.teal} />
                <SortBtn col="Actual_Qty"            label="Actual"        sortCol={sortForecast} setSortCol={setSortForecast} accent={T.teal} />
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
                : activeTab === "performance" ? perfRows.length
                : activeTab === "forecast" ? forecastTableRows.length
                : lossRows.length} SKUs
            </span>
            </>
            )}
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
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.muted }}>Effective this month</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple, textAlign: "right" }}>Cur month plan · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky, textAlign: "right" }}>Sell-through · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Actual / Budget</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple, textAlign: "right" }}>Full FY plan · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky, textAlign: "right" }}>FY to date · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>FYTD / Annual · left</th>
                    </tr>
                  </thead>
                  <tbody>
                    {budgetPaged.map((r, idx) => {
                      const rowNum      = agency ? idx + 1 : (budgetPage - 1) * PAGE_SIZE + idx + 1;
                      const bg          = idx % 2 === 0 ? T.card : T.surface + "66";
                      const budget      = toNumber(r.Budget_Qty);
                      const distPrice   = r.Distributor_Unit_Price;
                      const bValue      = toNumber(r.Budget_Value);
                      const actualValue = toNumber(r.Current_Month_Secondary_Sales_Value);
                      const fytdValue   = toNumber(r.FYTD_Secondary_Sales_Value);
                      const annual      = toNumber(r.Annual_Budget_Qty);
                      const annualValue = toNumber(r.Annual_Budget_Value);
                      const ach         = r["Achievement_%"];
                      const reach       = r["Annual_Reach_%"];
                      const left        = toNumber(r.Annual_Remaining_Qty);
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.purple + "0D"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>{r.ItemCode}</td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {distPrice != null
                              ? <span style={{ color: T.sky, fontWeight: 700 }}>{formatNum(distPrice, 2)}</span>
                              : <span style={{ color: T.muted, fontWeight: 400 }} title="No distributor price on file at or before this month">—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={budget > 0 ? budget : null} value={bValue} color={T.purple} />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={r.Current_Month_Secondary_Sales} value={actualValue}
                              color={T.sky} alwaysShow />
                          </td>
                          <td style={{ ...tdBase, background: bg, minWidth: 150, verticalAlign: "middle" }}>
                            <BudgetBar reach={ach} label="Actual / Budget" />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={annual > 0 ? annual : null} value={annualValue} color={T.purple} />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={r.FYTD_Secondary_Sales_Qty} value={fytdValue}
                              color={T.sky} alwaysShow />
                          </td>
                          <td style={{ ...tdBase, background: bg, minWidth: 170, verticalAlign: "middle" }}>
                            <BudgetBar reach={reach} label="FYTD / Annual" noneLabel="No annual budget" />
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

        ) : activeTab === "forecast" ? (

          /* ── FORECAST ANALYSIS TABLE ──
             My model's forecast vs the external (business-supplied)
             forecast, both scored against the SAME month's budget and
             actual. Deviations are qty-based (never value) and rendered as
             labelled bars; accuracy is a per-SKU 0-100 score. */
          forecastTableRows.length === 0 ? (
            <EmptyState glyph="◔" title="No forecast records found"
              hint="Run the engine to build the forecast analysis." />
          ) : (
            <>
              <div className="ins-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
                <table style={{ width: "100%", borderCollapse: "collapse",
                  fontFamily: FONT_MONO, fontSize: 11 }}>
                  <thead>
                    <tr>{FORECAST_COLS.map(h => <th key={h} style={thStyle}>{h}</th>)}</tr>
                    <tr>
                      {Array.from({ length: 4 }).map((_, i) => (
                        <th key={i} style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34 }} />
                      ))}
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.muted }}>Effective this month</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Plan</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Sold</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.teal, textAlign: "right" }}>AI model · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber, textAlign: "right" }}>Forecast.xlsx · units / LKR</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.teal }}>Qty gap</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.teal }}>Qty gap</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>Qty gap</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.amber }}>Qty gap</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.green }}>vs actual</th>
                    </tr>
                  </thead>
                  <tbody>
                    {forecastPaged.map((r, idx) => {
                      const rowNum    = agency ? idx + 1 : (forecastPage - 1) * PAGE_SIZE + idx + 1;
                      const bg        = idx % 2 === 0 ? T.card : T.surface + "66";
                      const distPrice = r.Distributor_Unit_Price;
                      const myQty     = toNumber(r.Model_Forecast_Qty);
                      const myValue   = toNumber(r.Model_Forecast_Value);
                      const extQty    = toNumber(r.External_Forecast_Qty);
                      const extValue  = toNumber(r.External_Forecast_Value);
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.teal + "0D"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>{r.ItemCode}</td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {distPrice != null
                              ? <span style={{ color: T.sky, fontWeight: 700 }}>{formatNum(distPrice, 2)}</span>
                              : <span style={{ color: T.muted, fontWeight: 400 }} title="No distributor price on file at or before this month">—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, textAlign: "right" }}>
                            {formatNum(r.Budget_Qty)}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.sky, fontWeight: 800, textAlign: "right" }}>
                            {formatNum(r.Actual_Qty)}
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={myQty > 0 ? myQty : null} value={myValue} color={T.teal} />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={extQty > 0 ? extQty : null} value={extValue} color={T.amber} />
                          </td>
                          <td style={{ ...tdBase, background: bg, verticalAlign: "middle" }}>
                            <DeviationBar pct={r["Model_Dev_Vs_Budget_%"]} qty={r.Model_Dev_Vs_Budget_Qty} label="vs Budget" />
                          </td>
                          <td style={{ ...tdBase, background: bg, verticalAlign: "middle" }}>
                            <DeviationBar pct={r["Model_Dev_Vs_Actual_%"]} qty={r.Model_Dev_Vs_Actual_Qty} label="vs Actual" />
                          </td>
                          <td style={{ ...tdBase, background: bg, verticalAlign: "middle" }}>
                            <DeviationBar pct={r["External_Dev_Vs_Budget_%"]} qty={r.External_Dev_Vs_Budget_Qty} label="vs Budget" />
                          </td>
                          <td style={{ ...tdBase, background: bg, verticalAlign: "middle" }}>
                            <DeviationBar pct={r["External_Dev_Vs_Actual_%"]} qty={r.External_Dev_Vs_Actual_Qty} label="vs Actual" />
                          </td>
                          <td style={{ ...tdBase, background: bg, verticalAlign: "middle", minWidth: 130 }}>
                            <AccuracyBar value={r["Model_Accuracy_%"]} label="My model" />
                            {r["External_Accuracy_%"] != null && (
                              <div style={{ marginTop: 6 }}>
                                <AccuracyBar value={r["External_Accuracy_%"]} label="External" />
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={forecastPage} setPage={setForecastPage} total={forecastTableRows.length} accent={T.teal} />
            </>
          )

        ) : activeTab === "performance" ? (

          /* ── PERFORMANCE TABLE / TREND GRAPH (flip) ──
             Same slot, two faces. The flip is scoped to THIS panel only:
             the KPI strip above and the other three tabs never re-render
             from `showTrend`. */
          perfRows.length === 0 ? (
            <EmptyState glyph="◧" title="No SKU records found"
              hint="Run the engine to build agency performance." />
          ) : showTrend ? (
            <div className="ins-flip-face">
              <div style={{ border: `1px solid ${T.border}`, borderRadius: 12,
                background: T.card, padding: "18px 20px 14px", boxShadow: SHADOW_SM }}>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10,
                  alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                  <div>
                    <div style={{ fontSize: 12.5, fontWeight: 900, color: T.text,
                      fontFamily: FONT_UI, letterSpacing: 0.2 }}>
                      Actual vs Budget — {trendLevel === "all" ? "All Agencies"
                        : trendLevel === "agency" ? (trendAgency || "All Agencies")
                        : `${trendItem || "SKU"}`}
                    </div>
                    <div style={{ fontSize: 10.5, color: T.muted, marginTop: 3, fontFamily: FONT_UI }}>
                      Fiscal year to date
                      {trendLevel === "sku" && trendAgency
                        ? <> · {trendAgency}</> : null}
                      {trendLevel === "sku"
                        ? null
                        : <> · {formatNum(trendData?.scope?.sku_count || 0)} SKUs combined</>}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    {/* Drill-down level: All -> Agency -> SKU */}
                    <div style={{ display: "inline-flex", background: T.surface,
                      border: `1px solid ${T.border}`, borderRadius: 999, padding: 3 }}>
                      {[["all", "All"], ["agency", "Agency"], ["sku", "SKU"]].map(([k, lbl]) => (
                        <button key={k} className="ins-btn"
                          onClick={() => {
                            // Stepping down a level needs a selection at
                            // that level; default to the first available
                            // rather than leaving the chart empty.
                            if (k !== "all" && !trendAgency && trendAgencyOptions.length) {
                              setTrendAgency(trendAgencyOptions[0]);
                            }
                            if (k === "sku" && !trendItem && trendSkuOptions.length) {
                              setTrendItem(trendSkuOptions[0].code);
                            }
                            setTrendLevel(k);
                          }}
                          style={{ border: "none", cursor: "pointer", borderRadius: 999,
                            padding: "5px 13px", fontSize: 10.5, fontWeight: 800,
                            fontFamily: FONT_UI,
                            background: trendLevel === k ? T.blue : "transparent",
                            color: trendLevel === k ? "#fff" : T.muted }}>{lbl}</button>
                      ))}
                    </div>

                    {trendLevel !== "all" && (
                      <select value={trendAgency} onChange={e => setTrendAgency(e.target.value)}
                        style={{ background: T.card, border: `1px solid ${T.border}`,
                          borderRadius: 9, padding: "7px 11px", fontSize: 11.5,
                          fontFamily: FONT_UI, fontWeight: 700, color: T.text,
                          cursor: "pointer", maxWidth: 220 }}>
                        {trendAgencyOptions.map(a => <option key={a} value={a}>{a}</option>)}
                      </select>
                    )}

                    {trendLevel === "sku" && (
                      <select value={trendItem} onChange={e => setTrendItem(e.target.value)}
                        style={{ background: T.card, border: `1px solid ${T.border}`,
                          borderRadius: 9, padding: "7px 11px", fontSize: 11.5,
                          fontFamily: FONT_UI, fontWeight: 700, color: T.text,
                          cursor: "pointer", maxWidth: 260 }}>
                        {trendSkuOptions.map(o => (
                          <option key={o.code} value={o.code}>{o.code} — {o.name}</option>
                        ))}
                      </select>
                    )}

                    <div style={{ display: "inline-flex", background: T.surface,
                      border: `1px solid ${T.border}`, borderRadius: 999, padding: 3 }}>
                      {[["qty", "Units"], ["value", "LKR"]].map(([k, lbl]) => (
                        <button key={k} className="ins-btn" onClick={() => setTrendMetric(k)}
                          style={{ border: "none", cursor: "pointer", borderRadius: 999,
                            padding: "5px 13px", fontSize: 10.5, fontWeight: 800,
                            fontFamily: FONT_UI,
                            background: trendMetric === k ? T.blue : "transparent",
                            color: trendMetric === k ? "#fff" : T.muted }}>{lbl}</button>
                      ))}
                    </div>
                  </div>
                </div>

                {trendLoading
                  ? <div style={{ padding: "60px 0", textAlign: "center", color: T.muted,
                      fontSize: 12, fontFamily: FONT_UI }}>
                      <span className="ins-spinner" /> Loading trend…
                    </div>
                  : <TrendChart
                      months={trendData?.months || []}
                      actual={(trendMetric === "value"
                        ? trendData?.series?.actual_value : trendData?.series?.actual_qty) || []}
                      budget={(trendMetric === "value"
                        ? trendData?.series?.budget_value : trendData?.series?.budget_qty) || []}
                      metric={trendMetric} />}
              </div>
            </div>
          ) : (
            <div className="ins-flip-face">
              <div className="ins-scroll" style={{ overflowX: "auto", borderRadius: 12, border: `1px solid ${T.border}` }}>
                <table style={{ width: "100%", borderCollapse: "collapse",
                  fontFamily: FONT_MONO, fontSize: 11 }}>
                  <thead>
                    <tr>{PERF_COLS.map(h => <th key={h} style={thStyle}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {perfPaged.map((r, idx) => {
                      const rowNum  = agency ? idx + 1 : (perfPage - 1) * PAGE_SIZE + idx + 1;
                      const momRaw  = r["MoM_Growth_%"];
                      const momNull = momRaw == null;
                      const growth  = momNull ? 0 : toNumber(momRaw);
                      const bg      = idx % 2 === 0 ? T.card : T.surface + "66";
                      const notBudgeted = r.Is_In_Master === false;
                      return (
                        <tr key={`${r.ItemCode}-${rowNum}`} className="ins-row"
                          onMouseEnter={e => e.currentTarget.style.background = T.blue + "0D"}
                          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                          <td style={{ ...tdBase, background: bg, color: T.muted, textAlign: "right", minWidth: 36 }}>{rowNum}</td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, fontFamily: FONT_UI }}>{r.Agency || "—"}</td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, fontWeight: 900 }}>
                            {r.ItemCode}
                            {notBudgeted && (
                              <div style={{ marginTop: 3 }}>
                                <InfoTag text="No budget" color={T.amber} />
                              </div>
                            )}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.text, minWidth: 200, fontFamily: FONT_UI, fontWeight: 500 }}>{r.ItemName || "—"}</td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={r.Last_Month_Secondary_Sales}
                              value={r.Last_Month_Secondary_Sales_Value} alwaysShow />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue qty={r.Current_Month_Secondary_Sales}
                              value={r.Current_Month_Secondary_Sales_Value}
                              color={T.purple} alwaysShow />
                          </td>
                          <td style={{ ...tdBase, background: bg }}>
                            <QtyValue
                              qty={r.Forecast_Source && r.Forecast_Source !== "NO_FORECAST"
                                ? r.Current_Forecast_Qty : null}
                              value={r.Current_Forecast_Value} color={T.teal} />
                          </td>
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
                              ? <span style={{ fontWeight: 800, color: shpColor(r.Current_SHP) }}>
                                  {Number(r.Current_SHP).toFixed(2)}
                                </span>
                              : <span style={{ color: T.muted }}>—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination page={perfPage} setPage={setPerfPage} total={perfRows.length} accent={T.blue} />
            </div>
          )

        ) : (

          /* ── LOSS ANALYSIS TABLE ── */
          lossRows.length === 0 ? (
            <EmptyState glyph="✓" title="No loss records found"
              hint="Every SKU met or exceeded its budget this month." />
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
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.sky }}>Distributor sell-through</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.purple }}>Cur month plan</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.blue }}>No-risk opening</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.teal }}>No-risk opening</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.muted }}>WH + DB on hand</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.blue }}>WH / L3M avg</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.teal }}>DB / L3M avg</th>
                      <th style={{ ...thStyle, fontSize: 8, padding: "3px 14px", top: 34, color: T.red }}>Budget − Actual</th>
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
                          <td style={{ ...tdBase, background: bg, color: T.sky, fontWeight: 800, textAlign: "right" }}>
                            {formatNum(r.Current_Month_Secondary_Sales)}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.purple, fontWeight: 800, textAlign: "right" }}>
                            {toNumber(r.Budget_Qty) > 0
                              ? formatNum(r.Budget_Qty)
                              : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.blue, textAlign: "right" }}>
                            {r.WH_Stock != null
                              ? formatNum(r.WH_Stock)
                              : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, color: T.teal, textAlign: "right" }}>
                            {r.DB_Stock != null
                              ? formatNum(r.DB_Stock)
                              : <span style={{ color: T.muted, fontWeight: 400 }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right", verticalAlign: "top" }}>
                            <div style={{ fontWeight: 800, color: stock > 0 ? T.text : T.muted }}>{formatNum(stock)}</div>
                            <div style={{ fontSize: 9, color: T.muted, marginTop: 2 }}>
                              WH {formatNum(r.WH_Stock_Current)} + DB {formatNum(r.DB_Stock_Current)}
                            </div>
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {r.WH_SHP != null
                              ? <span style={{ fontWeight: 800, color: shpColor(r.WH_SHP) }}>{Number(r.WH_SHP).toFixed(2)}</span>
                              : <span style={{ color: T.muted }}>—</span>}
                          </td>
                          <td style={{ ...tdBase, background: bg, textAlign: "right" }}>
                            {r.DB_SHP != null
                              ? <span style={{ fontWeight: 800, color: shpColor(r.DB_SHP) }}>{Number(r.DB_SHP).toFixed(2)}</span>
                              : <span style={{ color: T.muted }}>—</span>}
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
                          {/* A SKU can be BOTH at once — part of the gap
                              unsellable for lack of stock, part missed
                              despite stock being on hand. Show every reason
                              that applies, each with its own unit split. */}
                          <td style={{ ...tdBase, background: bg, minWidth: 150 }}>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                              {stockout > 0 && <ReasonBadge reason="Stockout" />}
                              {other    > 0 && <ReasonBadge reason="Other" />}
                              {stockout <= 0 && other <= 0 && <ReasonBadge reason="None" />}
                            </div>
                            {stockout > 0 && (
                              <div style={{ fontSize: 9, color: T.muted, marginTop: 3 }}>
                                Stock short by {formatNum(stockout)} units
                              </div>
                            )}
                            {other > 0 && (
                              <div style={{ fontSize: 9, color: T.muted, marginTop: 2 }}>
                                {formatNum(other)} units sellable from stock · execution gap
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

      {/* ── SKU Mapping & Coverage — collapsed by default, toggle to view ── */}
      <div style={{ marginTop: 28, display: "flex", justifyContent: "center" }}>
        <button className="ins-btn" onClick={() => setShowMapping(s => !s)}
          style={{ background: T.card, border: `1px solid ${T.border}`, borderRadius: 999,
            padding: "8px 18px", fontSize: 10.5, fontWeight: 800, color: T.muted,
            textTransform: "uppercase", letterSpacing: 1, cursor: "pointer",
            fontFamily: FONT_UI, display: "inline-flex", alignItems: "center", gap: 7,
            boxShadow: SHADOW_SM }}>
          <span style={{ fontSize: 9 }}>{showMapping ? "▲" : "▼"}</span>
          {showMapping ? "Hide" : "Show"} SKU Mapping &amp; Coverage
        </button>
      </div>
      {showMapping && (
        <MappingDiagnostics diagnostics={meta?.mapping_diagnostics} agency={agency} />
      )}
    </div>
  );
}