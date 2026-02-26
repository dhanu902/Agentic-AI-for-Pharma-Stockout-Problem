import { useState, useMemo, useRef, useEffect } from "react";
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer, Area, ReferenceLine, Scatter
} from "recharts";

import axios from "axios";

// ── Synthetic demo data generator ──────────────────────────────────────────
const SKUS = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005", "SKU-007", "SKU-012"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function rng(seed) {
  let s = seed;
  return () => { s = (s * 16807 + 0) % 2147483647; return (s - 1) / 2147483646; };
}

function generateSKUData(skuId) {
  const seed = skuId.split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  const rand = rng(seed);
  const baseLevel = 500 + rand() * 2000;
  const trend = (rand() - 0.4) * 30;
  const data = [];

  for (let yr = 2023; yr <= 2025; yr++) {
    for (let m = 0; m < 12; m++) {
      if (yr === 2025 && m > 5) break; // actual data up to Jun 2025
      const t = (yr - 2023) * 12 + m;
      const seasonal = Math.sin(2 * Math.PI * m / 12) * baseLevel * 0.15 +
                       Math.cos(2 * Math.PI * m / 12) * baseLevel * 0.07;
      const bonusSpike = (m === 10 || m === 3) && rand() > 0.5;
      const supplyDrop = rand() > 0.85;
      const actual = Math.max(0, Math.round(
        baseLevel + trend * t + seasonal +
        (bonusSpike ? baseLevel * 0.35 : 0) +
        (supplyDrop ? -baseLevel * 0.25 : 0) +
        (rand() - 0.5) * baseLevel * 0.12
      ));
      const primaryInv = Math.round(actual * (1.5 + rand() * 1.2));
      const distInv = Math.round(actual * (0.6 + rand() * 0.8));
      const bonusFlag = bonusSpike ? 1 : 0;
      const supplyFlag = supplyDrop ? 1 : 0;

      data.push({
        period: `${yr}-${String(m + 1).padStart(2, "0")}`,
        label: `${MONTHS[m]} ${yr}`,
        actual,
        predicted: null,
        primaryInventory: primaryInv,
        distInventory: distInv,
        bonusQty: bonusFlag ? Math.round(actual * (0.2 + rand() * 0.2)) : 0,
        bonusFlag,
        supplyFlag,
        year: yr,
        month: m + 1,
      });
    }
  }

  // Generate forecast for Jul–Dec 2025
  const lastActual = data[data.length - 1];
  const r2 = rng(seed + 999);
  for (let m = 6; m < 12; m++) {
    const t = 2 * 12 + m;
    const seasonal = Math.sin(2 * Math.PI * m / 12) * baseLevel * 0.15 +
                     Math.cos(2 * Math.PI * m / 12) * baseLevel * 0.07;
    const predicted = Math.max(0, Math.round(
      baseLevel + trend * t + seasonal + (r2() - 0.5) * baseLevel * 0.08
    ));
    data.push({
      period: `2025-${String(m + 1).padStart(2, "0")}`,
      label: `${MONTHS[m]} 2025`,
      actual: null,
      predicted,
      primaryInventory: Math.round(predicted * (1.3 + r2() * 1.0)),
      distInventory: Math.round(predicted * (0.5 + r2() * 0.7)),
      bonusQty: 0,
      bonusFlag: 0,
      supplyFlag: 0,
      year: 2025,
      month: m + 1,
      isForecast: true,
    });
  }
  return data;
}

// ── Custom Tooltip ──────────────────────────────────────────────────────────
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "#0d1117", border: "1px solid #30363d",
      borderRadius: 8, padding: "10px 14px", fontSize: 12,
      boxShadow: "0 8px 32px rgba(0,0,0,0.6)"
    }}>
      <p style={{ color: "#8b949e", marginBottom: 6, fontWeight: 600 }}>{label}</p>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, marginBottom: 2 }}>
          <span style={{ opacity: 0.7 }}>{p.name}: </span>
          <strong>{typeof p.value === "number" ? p.value.toLocaleString() : "—"}</strong>
        </div>
      ))}
    </div>
  );
};

// ── KPI Card ────────────────────────────────────────────────────────────────
const KPICard = ({ label, value, sub, accent }) => (
  <div style={{
    background: "#161b22", border: `1px solid ${accent}33`,
    borderRadius: 10, padding: "14px 18px", flex: 1, minWidth: 130,
    borderTop: `3px solid ${accent}`,
  }}>
    <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: 1, marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 800, color: "#e6edf3", fontFamily: "'DM Mono', monospace" }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: accent, marginTop: 3 }}>{sub}</div>}
  </div>
);

// ── Section Header ──────────────────────────────────────────────────────────
const SectionHeader = ({ title, subtitle }) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ fontSize: 13, fontWeight: 700, color: "#e6edf3", letterSpacing: 0.5 }}>{title}</div>
    {subtitle && <div style={{ fontSize: 11, color: "#6e7681", marginTop: 2 }}>{subtitle}</div>}
  </div>
);

// ── Main Dashboard ──────────────────────────────────────────────────────────
export default function App() {
  const [search, setSearch] = useState("");
  const [selectedSKU, setSelectedSKU] = useState("SKU-001");
  const [showDropdown, setShowDropdown] = useState(false);
  const [animKey, setAnimKey] = useState(0);
  const inputRef = useRef();

  const filtered = useMemo(() =>
    SKUS.filter(s => s.toLowerCase().includes(search.toLowerCase())),
    [search]
  );

  const skuData = useMemo(() => generateSKUData(selectedSKU), [selectedSKU]);

  const kpis = useMemo(() => {
    const actuals = skuData.filter(d => d.actual !== null);
    const forecasts = skuData.filter(d => d.isForecast);
    const nextMonth = forecasts[0];
    const lastActual = actuals[actuals.length - 1];
    const prevActual = actuals[actuals.length - 2];
    const mom = lastActual && prevActual
      ? ((lastActual.actual - prevActual.actual) / (prevActual.actual || 1) * 100).toFixed(1)
      : "—";
    const avgActual = actuals.length
      ? Math.round(actuals.reduce((s, d) => s + d.actual, 0) / actuals.length)
      : 0;
    const bonusMonths = actuals.filter(d => d.bonusFlag).length;
    const supplyHits = actuals.filter(d => d.supplyFlag).length;
    return { nextMonth, lastActual, mom, avgActual, bonusMonths, supplyHits };
  }, [skuData]);

  const handleSelect = (sku) => {
    setSelectedSKU(sku);
    setSearch("");
    setShowDropdown(false);
    setAnimKey(k => k + 1);
  };

  const trendPositive = parseFloat(kpis.mom) > 0;

  return (
    <div style={{
      minHeight: "100vh", background: "#0d1117",
      fontFamily: "'IBM Plex Sans', sans-serif",
      color: "#e6edf3", padding: "24px 28px",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet" />

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, flexWrap: "wrap", gap: 16 }}>
        <div>
          <div style={{ fontSize: 9, letterSpacing: 4, color: "#388bfd", textTransform: "uppercase", marginBottom: 4 }}>
            ◈ Sales Intelligence Platform
          </div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 800, color: "#e6edf3", letterSpacing: -0.5 }}>
            SKU Forecast Dashboard
          </h1>
          <div style={{ fontSize: 12, color: "#6e7681", marginTop: 4 }}>
            XGBoost · Tweedie Objective · Optuna-Tuned · v1.0
          </div>
        </div>

        {/* Search Bar */}
        <div style={{ position: "relative", width: 280 }}>
          <div style={{
            display: "flex", alignItems: "center",
            background: "#161b22", border: "1px solid #30363d",
            borderRadius: 10, padding: "8px 14px", gap: 8,
            boxShadow: showDropdown ? "0 0 0 2px #388bfd55" : "none",
            transition: "box-shadow 0.2s"
          }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#6e7681" strokeWidth="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <input
              ref={inputRef}
              value={search}
              onChange={e => { setSearch(e.target.value); setShowDropdown(true); }}
              onFocus={() => setShowDropdown(true)}
              onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
              placeholder="Search SKU / Item Code…"
              style={{
                background: "transparent", border: "none", outline: "none",
                color: "#e6edf3", fontSize: 13, width: "100%",
                fontFamily: "'DM Mono', monospace"
              }}
            />
            {selectedSKU && (
              <span style={{
                fontSize: 10, background: "#388bfd22", color: "#388bfd",
                padding: "2px 8px", borderRadius: 12, whiteSpace: "nowrap", fontWeight: 700
              }}>{selectedSKU}</span>
            )}
          </div>
          {showDropdown && filtered.length > 0 && (
            <div style={{
              position: "absolute", top: "calc(100% + 6px)", left: 0, right: 0,
              background: "#161b22", border: "1px solid #30363d", borderRadius: 10,
              overflow: "hidden", zIndex: 100, boxShadow: "0 16px 48px rgba(0,0,0,0.6)"
            }}>
              {filtered.map(sku => (
                <div key={sku} onMouseDown={() => handleSelect(sku)} style={{
                  padding: "10px 14px", fontSize: 13, cursor: "pointer",
                  fontFamily: "'DM Mono', monospace",
                  background: sku === selectedSKU ? "#388bfd15" : "transparent",
                  color: sku === selectedSKU ? "#388bfd" : "#e6edf3",
                  borderLeft: sku === selectedSKU ? "3px solid #388bfd" : "3px solid transparent",
                  transition: "background 0.1s"
                }}
                  onMouseEnter={e => e.currentTarget.style.background = "#388bfd10"}
                  onMouseLeave={e => e.currentTarget.style.background = sku === selectedSKU ? "#388bfd15" : "transparent"}
                >
                  {sku}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* KPI Row */}
      <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
        <KPICard
          label="Next Month Forecast"
          value={kpis.nextMonth ? kpis.nextMonth.predicted.toLocaleString() : "—"}
          sub={kpis.nextMonth?.label}
          accent="#388bfd"
        />
        <KPICard
          label="Last Actual"
          value={kpis.lastActual ? kpis.lastActual.actual.toLocaleString() : "—"}
          sub={kpis.lastActual?.label}
          accent="#3fb950"
        />
        <KPICard
          label="MoM Change"
          value={`${kpis.mom > 0 ? "+" : ""}${kpis.mom}%`}
          sub={trendPositive ? "▲ Growing" : "▼ Declining"}
          accent={trendPositive ? "#3fb950" : "#f85149"}
        />
        <KPICard
          label="Avg Monthly Sales"
          value={kpis.avgActual.toLocaleString()}
          sub="Historical mean"
          accent="#d2a8ff"
        />
        <KPICard
          label="Bonus Events"
          value={kpis.bonusMonths}
          sub="Months with bonus spike"
          accent="#ffa657"
        />
        <KPICard
          label="Supply Shocks"
          value={kpis.supplyHits}
          sub="Stockout events"
          accent="#f85149"
        />
      </div>

      {/* Chart 1: Sales Trend */}
      <div key={`sales-${animKey}`} style={{
        background: "#161b22", border: "1px solid #21262d",
        borderRadius: 12, padding: "20px 20px 10px", marginBottom: 16,
      }}>
        <SectionHeader
          title="Sales Trend — Actual vs Forecast"
          subtitle="Monthly secondary sales quantity · Shaded area = forecast horizon (Jul–Dec 2025)"
        />
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={skuData} margin={{ top: 10, right: 20, bottom: 0, left: 10 }}>
            <defs>
              <linearGradient id="predGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#388bfd" stopOpacity={0.25} />
                <stop offset="100%" stopColor="#388bfd" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "#6e7681", fontSize: 10 }} tickLine={false} axisLine={false}
              interval={2} />
            <YAxis tick={{ fill: "#6e7681", fontSize: 10 }} tickLine={false} axisLine={false}
              tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v} width={45} />
            <Tooltip content={<CustomTooltip />} />
            <Legend wrapperStyle={{ fontSize: 11, color: "#8b949e", paddingTop: 8 }} />
            <ReferenceLine x="Jul 2025" stroke="#388bfd" strokeDasharray="4 4" strokeOpacity={0.5}
              label={{ value: "Forecast →", fill: "#388bfd", fontSize: 10, position: "insideTopRight" }} />
            <Area dataKey="predicted" name="Predicted" fill="url(#predGrad)" stroke="#388bfd"
              strokeWidth={2} strokeDasharray="6 3" dot={false} connectNulls={false} />
            <Line dataKey="actual" name="Actual Sales" stroke="#3fb950" strokeWidth={2.5}
              dot={{ r: 3, fill: "#3fb950", strokeWidth: 0 }} activeDot={{ r: 5 }} connectNulls={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Chart 2 & 3 side by side */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>

        {/* Chart 2: Inventory */}
        <div key={`inv-${animKey}`} style={{
          background: "#161b22", border: "1px solid #21262d",
          borderRadius: 12, padding: "20px 20px 10px",
        }}>
          <SectionHeader
            title="Inventory Positions"
            subtitle="Primary warehouse stock vs distributor stock"
          />
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={skuData} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="primGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#d2a8ff" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#d2a8ff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="distGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ffa657" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#ffa657" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: "#6e7681", fontSize: 9 }} tickLine={false} axisLine={false} interval={3} />
              <YAxis tick={{ fill: "#6e7681", fontSize: 9 }} tickLine={false} axisLine={false}
                tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v} width={40} />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: 10, color: "#8b949e", paddingTop: 6 }} />
              <Area dataKey="primaryInventory" name="Primary Inventory" stroke="#d2a8ff"
                strokeWidth={2} fill="url(#primGrad)" dot={false} />
              <Area dataKey="distInventory" name="Distributor Stock" stroke="#ffa657"
                strokeWidth={2} fill="url(#distGrad)" dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>

        {/* Chart 3: Bonus & Shock Events */}
        <div key={`bonus-${animKey}`} style={{
          background: "#161b22", border: "1px solid #21262d",
          borderRadius: 12, padding: "20px 20px 10px",
        }}>
          <SectionHeader
            title="Bonus & Demand Shock Events"
            subtitle="Bonus quantity lifted per month · Supply constraint flags overlaid"
          />
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={skuData} margin={{ top: 10, right: 10, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis dataKey="label" tick={{ fill: "#6e7681", fontSize: 9 }} tickLine={false} axisLine={false} interval={3} />
              <YAxis yAxisId="left" tick={{ fill: "#6e7681", fontSize: 9 }} tickLine={false} axisLine={false}
                tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v} width={40} />
              <YAxis yAxisId="right" orientation="right" tick={{ fill: "#6e7681", fontSize: 9 }}
                tickLine={false} axisLine={false} domain={[0, 1.5]} hide />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={{ fontSize: 10, color: "#8b949e", paddingTop: 6 }} />
              <Bar yAxisId="left" dataKey="bonusQty" name="Bonus Qty" fill="#ffa657" opacity={0.85}
                radius={[3, 3, 0, 0]} maxBarSize={18} />
              <Bar yAxisId="right" dataKey="supplyFlag" name="Supply Shock" fill="#f85149" opacity={0.7}
                radius={[3, 3, 0, 0]} maxBarSize={10} />
              <Bar yAxisId="right" dataKey="bonusFlag" name="Bonus Flag" fill="#e3b341" opacity={0.7}
                radius={[3, 3, 0, 0]} maxBarSize={10} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Footer metadata */}
      <div style={{
        display: "flex", gap: 20, flexWrap: "wrap",
        borderTop: "1px solid #21262d", paddingTop: 14, marginTop: 4,
        fontSize: 11, color: "#6e7681"
      }}>
        {[
          ["Model", "XGBoost · Tweedie Objective"],
          ["Features", "Lag 1/2/3/6/12 · Rolling 3M/6M · Momentum · Seasonality · Inventory Pressure"],
          ["Demand Signal", "Effective Demand → Cleansed → ABC-Classified"],
          ["Tuning", "Optuna · 40 Trials · 2-Fold WalkForward"],
          ["Horizon", "Next Month (T+1)"],
        ].map(([k, v]) => (
          <div key={k}>
            <span style={{ color: "#388bfd", fontWeight: 700 }}>{k}: </span>{v}
          </div>
        ))}
      </div>
    </div>
  );
}
