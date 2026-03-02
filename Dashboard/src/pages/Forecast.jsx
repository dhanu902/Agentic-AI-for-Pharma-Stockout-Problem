import { useMemo, useState } from "react";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ReferenceLine
} from "recharts";

const API_BASE = "http://127.0.0.1:5000";

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

/* ─── Tooltip ────────────────────────────────────────────────── */
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "#0e1420",
      border: `1px solid ${T.borderHi}`,
      borderRadius: 8,
      padding: "10px 14px",
      fontSize: 12,
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
    background: T.card,
    border: `1px solid ${T.border}`,
    borderRadius: 10,
    padding: "16px 18px",
    flex: 1,
    minWidth: 140,
    position: "relative",
    overflow: "hidden",
    transition: "border-color 0.2s",
  }}
    onMouseEnter={e => e.currentTarget.style.borderColor = accent + "66"}
    onMouseLeave={e => e.currentTarget.style.borderColor = T.border}
  >
    {/* glow blob */}
    <div style={{
      position: "absolute", top: -20, right: -20,
      width: 80, height: 80,
      background: accent,
      borderRadius: "50%",
      opacity: 0.05,
      filter: "blur(20px)",
      pointerEvents: "none",
    }} />

    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
      {icon && <span style={{ fontSize: 13 }}>{icon}</span>}
      <div style={{
        fontSize: 10, color: T.muted,
        textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700,
      }}>{label}</div>
    </div>

    <div style={{
      fontSize: 24, fontWeight: 800, color: T.text,
      fontFamily: "'JetBrains Mono', monospace",
      lineHeight: 1,
      marginBottom: 6,
    }}>{value}</div>

    {sub && (
      <div style={{
        fontSize: 11, color: accent, fontWeight: 600,
        display: "flex", alignItems: "center", gap: 4,
      }}>{sub}</div>
    )}

    {/* bottom accent line */}
    <div style={{
      position: "absolute", bottom: 0, left: 0, right: 0,
      height: 2, background: `linear-gradient(90deg, ${accent}, transparent)`,
    }} />
  </div>
);

/* ─── Signal Card (compact) ──────────────────────────────────── */
const SignalCard = ({ label, value, sub, accent }) => (
  <div style={{
    background: T.card,
    border: `1px solid ${T.border}`,
    borderLeft: `3px solid ${accent}`,
    borderRadius: 8,
    padding: "12px 16px",
  }}>
    <div style={{ fontSize: 10, color: T.muted, textTransform: "uppercase", letterSpacing: 1.2, fontWeight: 700, marginBottom: 6 }}>
      {label}
    </div>
    <div style={{ fontSize: 18, fontWeight: 800, color: T.text, fontFamily: "'JetBrains Mono', monospace" }}>
      {value}
    </div>
    {sub && <div style={{ fontSize: 10, color: accent, marginTop: 3, fontWeight: 500 }}>{sub}</div>}
  </div>
);

/* ─── Chart Panel ────────────────────────────────────────────── */
const Panel = ({ children, style = {} }) => (
  <div style={{
    background: T.card,
    border: `1px solid ${T.border}`,
    borderRadius: 12,
    padding: "20px 22px 12px",
    ...style,
  }}>
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
    background: color + "22",
    border: `1px solid ${color}44`,
    color: color,
    borderRadius: 4,
    padding: "2px 7px",
    fontSize: 9,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: 1,
  }}>{label}</span>
);

/* ─── Main ───────────────────────────────────────────────────── */
export default function Forecast() {
  const [itemCode, setItemCode] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleForecast = async () => {

    if (!itemCode.trim()) return;
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}`/dashboard, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_code: itemCode.trim() }),
      });

      const data = await response.json();
      if (!response.ok) {
        setResult({ error: data?.error || "Failed to fetch dashboard" });
        return;
      }
      setResult(data);

    } catch (error) {
      console.error("Error:", error);
      setResult({ error: "Failed to fetch dashboard" });

    } finally {
      setLoading(false);
    }

  };

  const salesTrend     = useMemo(() => result?.sales_trend     || [], [result]);
  const inventoryTrend = useMemo(() => result?.inventory_trend || [], [result]);
  const shockTrend     = useMemo(() => result?.shock_trend     || [], [result]);

  const mom = result?.mom_change;
  const momPositive = typeof mom === "number" ? mom > 0 : false;

  const forecastSplitLabel = useMemo(() => {
    if (!salesTrend?.length) return null;
    const firstForecast = salesTrend.find(d => d?.isForecast);
    return firstForecast?.label || null;
  }, [salesTrend]);

  return (
    <div style={{
      minHeight: "100vh",
      background: T.bg,
      fontFamily: "'IBM Plex Sans', sans-serif",
      color: T.text,
      padding: "28px 32px",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" />

      {/* ── Top Bar ── */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 28,
        paddingBottom: 20,
        borderBottom: `1px solid ${T.border}`,
        flexWrap: "wrap",
        gap: 16,
      }}>
        {/* Brand */}
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{
            width: 36, height: 36,
            background: `linear-gradient(135deg, ${T.blue}, ${T.teal})`,
            borderRadius: 10,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 18,
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
            <Badge label="XGBoost" color={T.blue} />
            <Badge label="Tweedie" color={T.teal} />
            <Badge label="Optuna" color={T.purple} />
            <Badge label="v1.0" color={T.muted} />
          </div>
        </div>

        {/* Search area */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {result?.as_of && (
            <div style={{ fontSize: 11, color: T.muted, marginRight: 6 }}>
              As of <span style={{ color: T.text, fontWeight: 600 }}>{result.as_of}</span>
            </div>
          )}
          <div style={{
            display: "flex", alignItems: "center",
            background: T.surface,
            border: `1px solid ${T.borderHi}`,
            borderRadius: 10,
            padding: "9px 14px",
            gap: 8,
            width: 260,
          }}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke={T.muted} strokeWidth="2.5">
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
            </svg>
            <input
              value={itemCode}
              onChange={e => setItemCode(e.target.value)}
              placeholder="SKU / Item Code…"
              onKeyDown={e => e.key === "Enter" && handleForecast()}
              style={{
                background: "transparent", border: "none", outline: "none",
                color: T.text, fontSize: 13, width: "100%",
                fontFamily: "'JetBrains Mono', monospace",
              }}
            />
          </div>
          <button
            onClick={handleForecast}
            disabled={loading}
            style={{
              background: loading ? T.subtle : T.blue,
              border: "none",
              color: loading ? T.muted : "#fff",
              fontWeight: 700,
              fontSize: 13,
              borderRadius: 10,
              padding: "10px 20px",
              cursor: loading ? "not-allowed" : "pointer",
              transition: "background 0.2s",
              fontFamily: "'IBM Plex Sans', sans-serif",
              letterSpacing: 0.3,
            }}
          >
            {loading ? "Loading…" : "Run Forecast"}
          </button>
        </div>
      </div>

      {/* ── Error ── */}
      {result?.error && (
        <div style={{
          background: T.card, border: `1px solid ${T.red}44`,
          borderLeft: `3px solid ${T.red}`,
          borderRadius: 10, padding: "12px 16px",
          color: T.red, marginBottom: 20, fontSize: 13,
        }}>
          ⚠ {result.error}
        </div>
      )}

      {result && !result.error && (<>

        {/* ── SECTION 1: Hero Layout — KPIs left, big chart right ── */}
        <style>{`
          .hero-grid { display: grid; grid-template-columns: minmax(220px, 300px) 1fr; gap: 16px; margin-bottom: 16px; }
          .kpi-stack { display: flex; flex-direction: column; gap: 10px; }
          .signal-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
          .signal-row > * { flex: 1; min-width: 180px; }
          .bottom-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
          @media (max-width: 900px) {
            .hero-grid { grid-template-columns: 1fr !important; }
            .kpi-stack { flex-direction: row !important; flex-wrap: wrap; }
            .kpi-stack > * { min-width: 160px; flex: 1; }
          }
          @media (max-width: 620px) {
            .bottom-grid { grid-template-columns: 1fr !important; }
            .kpi-stack > * { min-width: 140px; }
          }
        `}</style>
        <div className="hero-grid">

          {/* Left: Primary KPI stack */}
          <div className="kpi-stack">
            <KPICard
              label="Next Month Forecast"
              value={fmt(result.next_month_forecast)}
              sub={result.next_month_label}
              accent={T.blue}
              icon="📈"
            />
            <KPICard
              label="Current Month Actual"
              value={fmt(result.current_month_actual)}
              sub={result.current_month_label}
              accent={T.green}
              icon="✓"
            />
            <KPICard
              label="MoM Change"
              value={`${momPositive ? "+" : ""}${fmt(result.mom_change)}%`}
              sub={momPositive ? "▲ Growing" : "▼ Declining"}
              accent={momPositive ? T.green : T.red}
              icon={momPositive ? "↑" : "↓"}
            />
            <KPICard
              label="Avg Monthly Sales"
              value={fmt(result.avg_monthly_sales)}
              sub="Historical mean"
              accent={T.purple}
              icon="∅"
            />
          </div>

          {/* Right: Sales Trend (hero chart) */}
          <Panel>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
              <SectionHeader
                title="Sales Trend — Actual vs Forecast"
                subtitle="Past 12 months Clean_Demand + next-month forecast"
              />
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
                  <linearGradient id="actGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={T.green} stopOpacity={0.1} />
                    <stop offset="100%" stopColor={T.green} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: T.muted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
                  tickLine={false} axisLine={false} interval={2}
                />
                <YAxis
                  tick={{ fill: T.muted, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}
                  tickLine={false} axisLine={false} width={40}
                  tickFormatter={fmtK}
                />
                <Tooltip content={<CustomTooltip />} />
                {forecastSplitLabel && (
                  <ReferenceLine
                    x={forecastSplitLabel}
                    stroke={T.blue}
                    strokeDasharray="5 4"
                    strokeOpacity={0.6}
                    label={{
                      value: "▶ Forecast",
                      fill: T.blue,
                      fontSize: 10,
                      fontWeight: 700,
                      position: "insideTopRight",
                    }}
                  />
                )}
                <Area
                  dataKey="predicted"
                  name="Predicted"
                  fill="url(#predGrad)"
                  stroke={T.blue}
                  strokeWidth={2.5}
                  strokeDasharray="7 4"
                  dot={false}
                  connectNulls={false}
                />
                <Line
                  dataKey="actual"
                  name="Actual"
                  stroke={T.green}
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: T.green, strokeWidth: 0 }}
                  activeDot={{ r: 6, fill: T.green, stroke: T.bg, strokeWidth: 2 }}
                  connectNulls={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>
        </div>

        {/* ── SECTION 2: Last Month + Disruption Signals row ── */}
        <div className="signal-row">
          <SignalCard
            label="Last Month Actual"
            value={fmt(result.last_month_actual)}
            sub={result.last_month_label}
            accent={T.teal}
          />
          <SignalCard
            label="Bonus Qty (Cur / Last)"
            value={`${fmt(result.bonus_qty_current_month)} / ${fmt(result.bonus_qty_last_month)}`}
            sub={`Shock: ${result.bonus_shock_current_month} / ${result.bonus_shock_last_month}`}
            accent={T.amber}
          />
          <SignalCard
            label="Supply Shock (Cur / Last)"
            value={`${fmt(result.supply_shock_current_month)} / ${fmt(result.supply_shock_last_month)}`}
            sub="Recent stockout indicators"
            accent={T.red}
          />
        </div>

        {/* ── SECTION 3: Inventory + Shock charts ── */}
        <Divider label="Market Signals" />
        <div className="bottom-grid">

          {/* Inventory */}
          <Panel>
            <SectionHeader
              title="Inventory Positions"
              subtitle="Primary inventory vs distributor stock — past 12 months"
            />
            <ResponsiveContainer width="100%" height={230}>
              <ComposedChart data={inventoryTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }}
                  tickLine={false} axisLine={false} interval={3}
                />
                <YAxis
                  tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }}
                  tickLine={false} axisLine={false} width={42}
                  tickFormatter={fmtK}
                />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }}
                  iconType="circle" iconSize={7}
                />
                <Line dataKey="primaryInventory" name="Primary Inventory" stroke={T.purple} strokeWidth={2} dot={false} />
                <Line dataKey="distInventory" name="Distributor Stock" stroke={T.amber} strokeWidth={2} dot={false} strokeDasharray="5 3" />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>

          {/* Bonus & Shock */}
          <Panel>
            <SectionHeader
              title="Bonus & Demand Shock Events"
              subtitle="Free_Qty per month + disruption flags — past 12 months"
            />
            <ResponsiveContainer width="100%" height={230}>
              <ComposedChart data={shockTrend} margin={{ top: 6, right: 10, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={T.border} vertical={false} />
                <XAxis
                  dataKey="label"
                  tick={{ fill: T.muted, fontSize: 9, fontFamily: "'JetBrains Mono', monospace" }}
                  tickLine={false} axisLine={false} interval={3}
                />
                <YAxis yAxisId="left" tick={{ fill: T.muted, fontSize: 9 }} tickLine={false} axisLine={false} width={42} tickFormatter={fmtK} />
                <YAxis yAxisId="right" orientation="right" domain={[0, 1.5]} hide />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 10, color: T.muted, paddingTop: 8 }}
                  iconType="circle" iconSize={7}
                />
                <Bar yAxisId="left" dataKey="bonusQty"  name="Bonus Qty"    fill={T.amber} opacity={0.85} maxBarSize={16} radius={[3,3,0,0]} />
                <Bar yAxisId="right" dataKey="bonusFlag"  name="Bonus Flag"  fill="#e3b341"  opacity={0.7}  maxBarSize={10} radius={[3,3,0,0]} />
                <Bar yAxisId="right" dataKey="supplyFlag" name="Supply Shock" fill={T.red}   opacity={0.7}  maxBarSize={10} radius={[3,3,0,0]} />
              </ComposedChart>
            </ResponsiveContainer>
          </Panel>

        </div>

      </>)}

      {/* ── Empty state ── */}
      {!result && !loading && (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          marginTop: 80, gap: 14, color: T.muted,
        }}>
          <div style={{ fontSize: 40, opacity: 0.3 }}>◈</div>
          <div style={{ fontSize: 14, fontWeight: 600 }}>Enter a SKU to load the forecast dashboard</div>
          <div style={{ fontSize: 12, opacity: 0.6 }}>Powered by XGBoost · Tweedie · Optuna</div>
        </div>
      )}
    </div>
  );
}
